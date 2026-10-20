/**
 * Paper-ledger reconciliation tests.
 *
 * The terminal's money math, pinned end-to-end: a fill becomes a position
 * (weighted-average cost), margin is blocked and released pro-rata, realized
 * P&L is booked on closes (including side flips), and mark-to-market agrees
 * with the position state. Cash is reconciled after every step — a ledger
 * that cannot account for every rupee is a bug, not a rounding choice.
 */
import { describe, expect, it } from 'vitest';
import {
  applyFill,
  closePosition,
  markPositions,
  planFill,
  positionKey,
  totalMarginUsed,
  totalMtm,
} from './paperLedger';
import { bsPrice } from './blackScholes';
import type { NewOrder, Position, Quote } from '../types/market';

const START_CASH = 10_000_000; // ₹1 Cr, the terminal's paper capital
let idSeq = 0;
const nextId = () => `POS${++idSeq}`;

function order(partial: Partial<NewOrder>): NewOrder {
  return {
    symbol: 'RELIANCE',
    underlying: 'RELIANCE',
    side: 'BUY',
    qty: 100,
    orderType: 'MARKET',
    instrumentType: 'EQUITY',
    ...partial,
  } as NewOrder;
}

function fill(
  state: { cash: number; positions: Position[] },
  o: NewOrder,
  price: number,
  lot = 1,
) {
  const planned = planFill(state, o, price, lot);
  if (!planned.ok) throw new Error(planned.reason);
  return applyFill(state, o, price, lot, planned.plan, nextId);
}

const empty = () => ({ cash: START_CASH, positions: [] as Position[] });

const quote = (symbol: string, price: number): Quote => ({ symbol, price }) as Quote;

describe('position keys', () => {
  it('distinguishes strikes/expiries on the same symbol', () => {
    const a = positionKey({ symbol: 'NIFTY', strike: 25000, expiry: '2026-10-29', optionType: 'CE' });
    const b = positionKey({ symbol: 'NIFTY', strike: 25100, expiry: '2026-10-29', optionType: 'CE' });
    const c = positionKey({ symbol: 'NIFTY' });
    expect(a).not.toBe(b);
    expect(a).not.toBe(c);
  });
});

describe('open → average → partial close (equity)', () => {
  it('reconciles cash, margin and realized P&L at every step', () => {
    let acct = empty();

    // Buy 100 @ 2500 → margin 20% of notional = 50,000 blocked
    acct = fill(acct, order({ qty: 100 }), 2500);
    expect(acct.cash).toBe(START_CASH - 50_000);
    expect(acct.positions).toHaveLength(1);
    expect(acct.positions[0]).toMatchObject({ side: 'LONG', qty: 100, avgPrice: 2500, marginUsed: 50_000 });

    // Buy 100 more @ 2600 → weighted average 2550, margins add up
    acct = fill(acct, order({ qty: 100 }), 2600);
    expect(acct.positions[0].qty).toBe(200);
    expect(acct.positions[0].avgPrice).toBe(2550);
    expect(acct.positions[0].marginUsed).toBe(102_000);
    expect(acct.cash).toBe(START_CASH - 102_000);

    // Sell 50 @ 2700 → realized (2700−2550)×50 = 7,500; margin released pro-rata 25,500
    const before = acct.cash;
    acct = fill(acct, order({ side: 'SELL', qty: 50 }), 2700);
    expect(acct.cash).toBe(before + 25_500 + 7_500);
    expect(acct.positions[0]).toMatchObject({ qty: 150, avgPrice: 2550, marginUsed: 76_500 });
  });
});

describe('side flip', () => {
  it('closes the long, books the loss, and opens a short with the remainder', () => {
    let acct = empty();
    acct = fill(acct, order({ qty: 100 }), 1000); // long 100 @1000, margin 20,000
    const cashAfterOpen = acct.cash;

    // Sell 150 @ 900: close 100 (realized −10,000, release 20,000), open short 50 (margin 9,000)
    acct = fill(acct, order({ side: 'SELL', qty: 150 }), 900);
    expect(acct.positions).toHaveLength(1);
    expect(acct.positions[0]).toMatchObject({ side: 'SHORT', qty: 50, avgPrice: 900, marginUsed: 9_000 });
    expect(acct.cash).toBe(cashAfterOpen + 20_000 - 10_000 - 9_000);
  });

  it('short first, then cover at a profit', () => {
    let acct = empty();
    acct = fill(acct, order({ side: 'SELL', qty: 100 }), 500); // short @500, margin 10,000
    expect(acct.positions[0].side).toBe('SHORT');
    acct = fill(acct, order({ side: 'BUY', qty: 100 }), 450); // cover
    expect(acct.positions).toHaveLength(0);
    // released 10,000 + realized (500−450)×100 = 5,000
    expect(acct.cash).toBe(START_CASH + 5_000);
  });
});

describe('margin gate', () => {
  it('rejects an order whose opening margin exceeds cash, changing nothing', () => {
    const poor = { cash: 10_000, positions: [] as Position[] };
    const planned = planFill(poor, order({ qty: 100 }), 1000, 1); // needs 20,000
    expect(planned.ok).toBe(false);
    if (!planned.ok) expect(planned.reason).toContain('Insufficient margin');
  });

  it('closing never needs margin, even with empty cash', () => {
    let acct = empty();
    acct = fill(acct, order({ qty: 100 }), 1000);
    const broke = { cash: 0, positions: acct.positions };
    const planned = planFill(broke, order({ side: 'SELL', qty: 100 }), 1100, 1);
    expect(planned.ok).toBe(true);
  });
});

describe('options (lot-based) accounting', () => {
  it('margin and realized P&L scale by lot size', () => {
    let acct = empty();
    const ce = order({
      symbol: 'NIFTY 25000 CE',
      underlying: 'NIFTY',
      qty: 2,
      instrumentType: 'OPTION',
      strike: 25000,
      expiry: '2026-10-29',
      optionType: 'CE',
    });
    // premium 100, 2 contracts, lot 50, rate 15% → 100×2×50×0.15 = 1,500
    acct = fill(acct, ce, 100, 50);
    expect(acct.positions[0]).toMatchObject({ qty: 2, lotSize: 50, marginUsed: 1_500 });

    // close 1 contract @ 120 → realized (120−100)×1×50 = 1,000, release 750
    const before = acct.cash;
    acct = fill(acct, { ...ce, side: 'SELL', qty: 1 }, 120, 50);
    expect(acct.cash).toBe(before + 750 + 1_000);
    expect(acct.positions[0]).toMatchObject({ qty: 1, marginUsed: 750 });
  });
});

describe('mark-to-market', () => {
  it('equity P&L follows the latest quote; shorts invert', () => {
    let acct = empty();
    acct = fill(acct, order({ qty: 100 }), 2500);
    acct = fill(acct, order({ symbol: 'TCS', underlying: 'TCS', side: 'SELL', qty: 50 }), 3000);
    const marked = markPositions(acct.positions, [quote('RELIANCE', 2600), quote('TCS', 3100)]);
    const rel = marked.find((p) => p.symbol === 'RELIANCE')!;
    const tcs = marked.find((p) => p.symbol === 'TCS')!;
    expect(rel.pnl).toBe(10_000); // (2600−2500)×100
    expect(tcs.pnl).toBe(-5_000); // short loses as price rises
    expect(totalMtm(marked)).toBe(5_000);
    expect(totalMarginUsed(marked)).toBe(rel.marginUsed + tcs.marginUsed);
  });

  it('option positions revalue through Black–Scholes when spot moves', () => {
    let acct = empty();
    const ce = order({
      symbol: 'NIFTY 25000 CE',
      underlying: 'NIFTY',
      qty: 1,
      instrumentType: 'OPTION',
      strike: 25000,
      expiry: '2027-10-29',
      optionType: 'CE',
      iv: 0.13,
    });
    // Buy AT the model price (spot 25000) so marks above/below spot 25000
    // must straddle the entry — an arbitrary premium would break the check.
    const modelPrice = bsPrice({
      S: 25000, K: 25000, T: (new Date('2027-10-29').getTime() - Date.now()) / 86400000 / 365,
      r: 0.065, sigma: 0.13, type: 'CE',
    });
    acct = fill(acct, ce, modelPrice, 50);
    const dearer = markPositions(acct.positions, [quote('NIFTY', 26000)]);
    expect(dearer[0].ltp).toBeGreaterThan(modelPrice);
    expect(dearer[0].pnl).toBeGreaterThan(0);
    const cheaper = markPositions(acct.positions, [quote('NIFTY', 24000)]);
    expect(cheaper[0].pnl).toBeLessThan(0);
  });
});

describe('square-off', () => {
  it('returns margin plus current P&L to cash and removes the position', () => {
    let acct = empty();
    acct = fill(acct, order({ qty: 100 }), 2500);
    const marked = markPositions(acct.positions, [quote('RELIANCE', 2600)]);
    const pos = marked[0]; // pnl 10,000, marginUsed 50,000
    const closed = closePosition({ cash: acct.cash, positions: marked }, pos);
    expect(closed.positions).toHaveLength(0);
    expect(closed.cash).toBe(acct.cash + 50_000 + 10_000);
  });
});

describe('ledger hygiene', () => {
  it('applyFill does not mutate its input state', () => {
    const s0 = empty();
    const snapshot = JSON.stringify(s0);
    fill(s0, order({ qty: 10 }), 100);
    expect(JSON.stringify(s0)).toBe(snapshot);
  });
});
