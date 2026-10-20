/**
 * Black-Scholes correctness — the same canonical reference vector and
 * no-arbitrage identities the Python engine is tested against
 * (quant-engine/tests/test_bsm_correctness.py), so the two implementations
 * cannot silently drift apart.
 */
import { describe, expect, it } from 'vitest';
import { bsGreeks, bsPrice, impliedVol, normCdf } from './blackScholes';

const CANON = { S: 100, K: 100, T: 1, r: 0.05, sigma: 0.2 };

describe('bsPrice against the canonical reference vector', () => {
  it('ATM call = 10.4506 (Hull)', () => {
    expect(bsPrice({ ...CANON, type: 'CE' })).toBeCloseTo(10.4506, 3);
  });
  it('ATM put = 5.5735', () => {
    expect(bsPrice({ ...CANON, type: 'PE' })).toBeCloseTo(5.5735, 3);
  });
  it('delta = 0.6368 and gamma = 0.01876', () => {
    const g = bsGreeks({ ...CANON, type: 'CE' });
    expect(g.delta).toBeCloseTo(0.6368, 3);
    expect(g.gamma).toBeCloseTo(0.01876, 4);
  });
  it('put delta = call delta − 1', () => {
    const c = bsGreeks({ ...CANON, type: 'CE' });
    const p = bsGreeks({ ...CANON, type: 'PE' });
    expect(p.delta).toBeCloseTo(c.delta - 1, 10);
    expect(p.gamma).toBeCloseTo(c.gamma, 12);
  });
});

describe('no-arbitrage identities', () => {
  it('put-call parity holds across a strike grid', () => {
    for (const K of [80, 90, 100, 110, 125]) {
      const c = bsPrice({ ...CANON, K, type: 'CE' });
      const p = bsPrice({ ...CANON, K, type: 'PE' });
      expect(c - p).toBeCloseTo(CANON.S - K * Math.exp(-CANON.r * CANON.T), 8);
    }
  });
  it('price respects no-arbitrage bounds', () => {
    const c = bsPrice({ ...CANON, type: 'CE' });
    expect(c).toBeGreaterThanOrEqual(
      Math.max(CANON.S - CANON.K * Math.exp(-CANON.r * CANON.T), 0),
    );
    expect(c).toBeLessThanOrEqual(CANON.S);
  });
  it('price is monotone in spot and vol', () => {
    const base = bsPrice({ ...CANON, type: 'CE' });
    expect(bsPrice({ ...CANON, S: 105, type: 'CE' })).toBeGreaterThan(base);
    expect(bsPrice({ ...CANON, sigma: 0.3, type: 'CE' })).toBeGreaterThan(base);
  });
  it('expiry collapses to intrinsic value', () => {
    expect(bsPrice({ ...CANON, T: 0, type: 'CE' })).toBe(0);
    expect(bsPrice({ ...CANON, S: 120, T: 0, type: 'CE' })).toBe(20);
    expect(bsPrice({ ...CANON, S: 120, T: 0, type: 'PE' })).toBe(0);
  });
  it('normCdf is sane', () => {
    expect(normCdf(0)).toBeCloseTo(0.5, 6);
    expect(normCdf(8)).toBeCloseTo(1, 6);
    expect(normCdf(-8)).toBeCloseTo(0, 6);
  });
});

describe('impliedVol', () => {
  it('round-trips a known vol', () => {
    const px = bsPrice({ ...CANON, sigma: 0.25, type: 'CE' });
    expect(impliedVol(px, 100, 100, 1, 0.05, 'CE')).toBeCloseTo(0.25, 4);
  });
  it('returns 0 for an expired option', () => {
    expect(impliedVol(5, 100, 100, 0, 0.05, 'CE')).toBe(0);
  });
});

describe('invalid-input contract', () => {
  it('NaN/negative/non-finite inputs yield NaN, never a fabricated price', () => {
    expect(bsPrice({ ...CANON, S: Number.NaN, type: 'CE' })).toBeNaN();
    expect(bsPrice({ ...CANON, S: -100, type: 'CE' })).toBeNaN();
    expect(bsPrice({ ...CANON, K: 0, type: 'CE' })).toBeNaN();
    expect(bsPrice({ ...CANON, sigma: Number.NaN, type: 'CE' })).toBeNaN();
    expect(bsPrice({ ...CANON, r: Number.POSITIVE_INFINITY, type: 'CE' })).toBeNaN();
  });
  it('greeks collapse to the expiry step for T=0 instead of NaN soup', () => {
    const g = bsGreeks({ ...CANON, S: 120, T: 0, type: 'CE' });
    expect(g.delta).toBe(1);
    expect(g.gamma).toBe(0);
    expect(g.vega).toBe(0);
  });
});
