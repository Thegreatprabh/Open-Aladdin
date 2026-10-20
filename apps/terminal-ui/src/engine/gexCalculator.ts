// OPEN-ALADDIN — Dealer Gamma Exposure (GEX) analytics.
// Per-strike GEX from option chain OI, net GEX profile, zero-gamma flip level
// (bisection on spot), and call/put wall detection.

import type { GexProfile, OptionContract } from '../types/market';
import { bsGreeks, bsPrice, normCdf } from './blackScholes';
import { LOT_SIZES, RISK_FREE_RATE } from '../types/market';

/** ₹ Cr of dealer GEX per contract: gamma × OI × lot × spot² / 1e7. */
export function contractGex(
  contract: OptionContract,
  spot: number,
  lotSize: number,
): number {
  return (contract.greeks.gamma * contract.oi * lotSize * spot * spot) / 1e7;
}

/** Net dealer GEX (₹ Cr) at a given spot for a chain. Calls +, puts − (dealer short gamma). */
export function netGexAt(
  contracts: OptionContract[],
  spot: number,
  lotSize: number,
): number {
  let net = 0;
  for (const c of contracts) {
    const g = c.greeks.gamma;
    const gex = (g * c.oi * lotSize * spot * spot) / 1e7;
    net += c.optionType === 'CE' ? gex : -gex;
  }
  return net;
}

/** Zero-gamma flip: spot level where net dealer GEX crosses zero (bisection). */
export function findZeroGamma(
  contracts: OptionContract[],
  spot: number,
  lotSize: number,
): number {
  let lo = spot * 0.8;
  let hi = spot * 1.2;
  let fLo = netGexAt(contracts, lo, lotSize);
  const fHi = netGexAt(contracts, hi, lotSize);
  if (fLo === 0) return lo;
  // If no sign change, return the level of minimum |netGEX| on the grid.
  if (fLo * fHi > 0) {
    let best = spot;
    let bestAbs = Math.abs(netGexAt(contracts, spot, lotSize));
    for (let i = 0; i <= 100; i++) {
      const s = lo + ((hi - lo) * i) / 100;
      const a = Math.abs(netGexAt(contracts, s, lotSize));
      if (a < bestAbs) {
        bestAbs = a;
        best = s;
      }
    }
    return best;
  }
  for (let i = 0; i < 80; i++) {
    const mid = (lo + hi) / 2;
    const fMid = netGexAt(contracts, mid, lotSize);
    if (Math.abs(fMid) < 1e-9) return mid;
    if (fLo * fMid < 0) hi = mid;
    else {
      lo = mid;
      fLo = fMid;
    }
  }
  return (lo + hi) / 2;
}

export interface ChainParams {
  underlying: string;
  spot: number;
  expiryDays: number; // DTE for the front expiry
  strikes?: number;
  strikeStep?: number;
  baseIv?: number;
  seed?: number;
}

/** Deterministic pseudo-random for reproducible chain OI. */
function hashRand(n: number): number {
  let x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

/**
 * Generate a realistic 21-strike option chain around spot with an IV smile
 * (parabolic in log-moneyness) plus term-structure, OI concentrated at
 * round strikes, and bid/ask spreads. Greeks computed via exact BSM.
 */
export function generateOptionChain(p: ChainParams): OptionContract[] {
  const n = p.strikes ?? 21;
  const step = p.strikeStep ?? (p.underlying === 'BANKNIFTY' ? 500 : 50);
  const baseIv = p.baseIv ?? 0.13;
  const seed = p.seed ?? 7;
  const T = Math.max(p.expiryDays, 0.25) / 365;
  const r = RISK_FREE_RATE;
  const lot = LOT_SIZES[p.underlying] ?? 25;
  void lot;

  const atm = Math.round(p.spot / step) * step;
  const contracts: OptionContract[] = [];
  const expiryISO = new Date(Date.now() + p.expiryDays * 86400000).toISOString().slice(0, 10);

  for (let i = 0; i < n; i++) {
    const strike = atm + (i - Math.floor(n / 2)) * step;
    const m = Math.log(strike / p.spot); // log-moneyness
    // IV smile: wings bid up + slight put skew + term structure lift
    const iv =
      baseIv * (1 + 2.2 * m * m + (m < 0 ? -1.6 * m : 0.4 * m)) *
      (1 + 0.35 * Math.sqrt(p.expiryDays / 365));
    for (const optionType of ['CE', 'PE'] as const) {
      const theo = bsPrice({ S: p.spot, K: strike, T, r, sigma: iv, type: optionType });
      const spread = Math.max(theo * 0.04, 0.5);
      const ltp = Math.max(theo, 0.05);
      // OI: heavy at ATM & round strikes, deterministic shape
      const dist = Math.abs(i - Math.floor(n / 2));
      const roundBoost = strike % (step * 2) === 0 ? 1.6 : 1;
      const oi = Math.round(
        (900000 / (1 + dist * dist * 0.55)) * roundBoost * (0.75 + 0.5 * hashRand(strike + seed + (optionType === 'CE' ? 1 : 2))),
      );
      const volume = Math.round(oi * (0.15 + 0.5 * hashRand(strike * 3 + seed)));
      contracts.push({
        symbol: `${p.underlying}${strike}${optionType}`,
        underlying: p.underlying,
        strike,
        expiry: expiryISO,
        optionType,
        dte: p.expiryDays,
        iv,
        bid: Math.max(ltp - spread / 2, 0.05),
        ask: ltp + spread / 2,
        ltp,
        oi,
        volume,
        greeks: bsGreeks({ S: p.spot, K: strike, T, r, sigma: iv, type: optionType }),
      });
    }
  }
  return contracts;
}

/** Build the full per-strike GEX profile for a chain. */
export function buildGexProfile(
  contracts: OptionContract[],
  spot: number,
  underlying: string,
): GexProfile {
  const lotSize = LOT_SIZES[underlying] ?? 25;
  const strikeMap = new Map<number, { call: number; put: number }>();
  for (const c of contracts) {
    const gex = contractGex(c, spot, lotSize);
    const e = strikeMap.get(c.strike) ?? { call: 0, put: 0 };
    if (c.optionType === 'CE') e.call += gex;
    else e.put += gex;
    strikeMap.set(c.strike, e);
  }
  const strikes = [...strikeMap.keys()].sort((a, b) => a - b);
  const callGex = strikes.map((s) => strikeMap.get(s)!.call);
  const putGex = strikes.map((s) => strikeMap.get(s)!.put);
  const netGex = strikes.map((_, i) => callGex[i] - putGex[i]);

  const totalCallGex = callGex.reduce((a, b) => a + b, 0);
  const totalPutGex = putGex.reduce((a, b) => a + b, 0);

  let callWall = strikes[0];
  let maxCall = -Infinity;
  let putWall = strikes[0];
  let maxPut = -Infinity;
  strikes.forEach((s, i) => {
    if (callGex[i] > maxCall) {
      maxCall = callGex[i];
      callWall = s;
    }
    if (putGex[i] > maxPut) {
      maxPut = putGex[i];
      putWall = s;
    }
  });

  return {
    strikes,
    callGex,
    putGex,
    netGex,
    totalCallGex,
    totalPutGex,
    netGexTotal: totalCallGex - totalPutGex,
    zeroGammaLevel: findZeroGamma(contracts, spot, lotSize),
    callWall,
    putWall,
    spot,
  };
}

/** Probability-OTM style helper used by chain views (risk-neutral). */
export function probITM(spot: number, strike: number, dteDays: number, iv: number, type: 'CE' | 'PE'): number {
  const T = Math.max(dteDays, 0.25) / 365;
  const r = RISK_FREE_RATE;
  const d2 = (Math.log(spot / strike) + (r - 0.5 * iv * iv) * T) / (iv * Math.sqrt(T));
  return type === 'CE' ? normCdf(d2) : normCdf(-d2);
}
