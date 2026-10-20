/**
 * paperLedger.ts — the terminal's paper-account accounting, as pure functions.
 *
 * Extracted verbatim from App.tsx so the money math can be unit-tested
 * directly: order fills → position updates (weighted-average cost, partial
 * closes, side flips) → realized P&L and margin release → mark-to-market.
 *
 * Accounting rules (unchanged from the original inline implementation):
 *  - Margin is a percentage of notional: options 15%, equity 20%. Only the
 *    OPENING part of an order consumes margin; the closing part releases the
 *    position's margin pro-rata and books realized P&L.
 *  - A fill opposite to an existing position closes up to the position's
 *    quantity; any remainder opens a new position in the order's direction
 *    (a flip), at the fill price.
 *  - Mark-to-market values options by Black–Scholes at the current spot and
 *    equity at the latest quote; P&L = (ltp − avgPrice) × dir × qty × lotSize.
 */
import { bsPrice } from './blackScholes';
import { RISK_FREE_RATE } from '../types/market';
import type { InstrumentType, NewOrder, Position, Quote } from '../types/market';

export function positionKey(p: {
  symbol: string;
  strike?: number;
  expiry?: string;
  optionType?: string;
}): string {
  return `${p.symbol}|${p.strike ?? ''}|${p.expiry ?? ''}|${p.optionType ?? ''}`;
}

export function marginRateFor(instrumentType: InstrumentType): number {
  return instrumentType === 'OPTION' ? 0.15 : 0.2;
}

export interface FillPlan {
  key: string;
  orderDir: 1 | -1;
  closingQty: number;
  openingQty: number;
  openingMargin: number;
}

export type FillPlanResult =
  | { ok: true; plan: FillPlan }
  | { ok: false; reason: string };

/** Split an incoming order into its closing and opening parts and price the
 *  margin the opening part needs. Pure: no state is mutated. */
export function planFill(
  state: { cash: number; positions: Position[] },
  o: NewOrder,
  fillPrice: number,
  lot: number,
): FillPlanResult {
  const key = positionKey(o);
  const existing = state.positions.find((p) => positionKey(p) === key);
  const orderDir: 1 | -1 = o.side === 'BUY' ? 1 : -1;
  const existingDir = existing ? (existing.side === 'LONG' ? 1 : -1) : 0;

  const sameDirection = !existing || existingDir === orderDir;
  const closingQty = existing && !sameDirection ? Math.min(o.qty, existing.qty) : 0;
  const openingQty = o.qty - closingQty;
  const openingMargin = fillPrice * openingQty * lot * marginRateFor(o.instrumentType);
  if (openingMargin > state.cash) {
    return {
      ok: false,
      reason: `Insufficient margin: need ₹${Math.round(openingMargin).toLocaleString('en-IN')}`,
    };
  }
  return { ok: true, plan: { key, orderDir, closingQty, openingQty, openingMargin } };
}

export interface FillResult {
  cash: number;
  positions: Position[];
  realizedPnl: number;
}

/** Apply a planned fill to the account. Mirrors the original accounting:
 *  close first (release margin pro-rata, book realized P&L), then open or
 *  merge (weighted-average price). Returns fresh objects; inputs untouched. */
export function applyFill(
  state: { cash: number; positions: Position[] },
  o: NewOrder,
  fillPrice: number,
  lot: number,
  plan: FillPlan,
  newPositionId: () => string,
): FillResult {
  let cash = state.cash;
  let realizedPnl = 0;
  const positions = state.positions.map((p) => ({ ...p }));
  const idx = positions.findIndex((p) => positionKey(p) === plan.key);

  if (idx >= 0 && plan.closingQty > 0) {
    const p = positions[idx];
    const dirP = p.side === 'LONG' ? 1 : -1;
    const realized = (fillPrice - p.avgPrice) * dirP * plan.closingQty * p.lotSize;
    const released = p.marginUsed * (plan.closingQty / p.qty);
    cash += released + realized;
    realizedPnl = realized;
    p.qty -= plan.closingQty;
    p.marginUsed -= released;
    if (p.qty <= 0) positions.splice(idx, 1);
  }
  if (plan.openingQty > 0) {
    cash -= plan.openingMargin;
    const j = positions.findIndex((p) => positionKey(p) === plan.key);
    if (j >= 0) {
      const p = positions[j];
      p.avgPrice = (p.avgPrice * p.qty + fillPrice * plan.openingQty) / (p.qty + plan.openingQty);
      p.qty += plan.openingQty;
      p.marginUsed += plan.openingMargin;
    } else {
      positions.push({
        id: newPositionId(),
        symbol: o.symbol,
        underlying: o.underlying,
        side: plan.orderDir === 1 ? 'LONG' : 'SHORT',
        qty: plan.openingQty,
        lotSize: lot,
        avgPrice: fillPrice,
        ltp: fillPrice,
        instrumentType: o.instrumentType,
        strike: o.strike,
        expiry: o.expiry,
        optionType: o.optionType,
        iv: o.iv,
        mtm: 0,
        pnl: 0,
        marginUsed: plan.openingMargin,
      });
    }
  }
  return { cash, positions, realizedPnl };
}

/** Close one position outright (square-off): its blocked margin returns to
 *  cash together with its current mark-to-market P&L. */
export function closePosition(
  state: { cash: number; positions: Position[] },
  position: Position,
): { cash: number; positions: Position[] } {
  return {
    cash: state.cash + position.marginUsed + position.pnl,
    positions: state.positions.filter((x) => x.id !== position.id),
  };
}

/** Mark every position to market using current quotes / BSM for options. */
export function markPositions(positions: Position[], quotes: Quote[]): Position[] {
  const qmap = new Map(quotes.map((q) => [q.symbol, q]));
  return positions.map((p) => {
    let ltp = p.ltp;
    if (p.instrumentType === 'OPTION' && p.strike != null && p.optionType) {
      const spot = qmap.get(p.underlying)?.price ?? 0;
      const dte = Math.max((new Date(p.expiry ?? '').getTime() - Date.now()) / 86400000, 0.02);
      ltp = bsPrice({
        S: spot,
        K: p.strike,
        T: dte / 365,
        r: RISK_FREE_RATE,
        sigma: p.iv ?? 0.13,
        type: p.optionType,
      });
    } else {
      ltp = qmap.get(p.symbol)?.price ?? qmap.get(p.underlying)?.price ?? p.ltp;
    }
    const dir = p.side === 'LONG' ? 1 : -1;
    const pnl = (ltp - p.avgPrice) * dir * p.qty * p.lotSize;
    return { ...p, ltp, pnl, mtm: pnl };
  });
}

export function totalMarginUsed(positions: Position[]): number {
  return positions.reduce((a, p) => a + p.marginUsed, 0);
}

export function totalMtm(positions: Position[]): number {
  return positions.reduce((a, p) => a + p.pnl, 0);
}
