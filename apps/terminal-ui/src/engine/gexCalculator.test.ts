/**
 * GEX sign convention + chain generation sanity. Convention (matching the
 * README): dealer GEX counts calls positive and puts negative.
 */
import { describe, expect, it } from 'vitest';
import { contractGex, generateOptionChain, netGexAt } from './gexCalculator';
import type { OptionContract } from '../types/market';

function fakeContract(optionType: 'CE' | 'PE', gamma: number, oi: number): OptionContract {
  return {
    symbol: `FAKE-${optionType}`,
    underlying: 'NIFTY',
    strike: 24000,
    optionType,
    expiry: '2026-12-31',
    dte: 7,
    iv: 0.13,
    bid: 99,
    ask: 101,
    ltp: 100,
    oi,
    volume: 0,
    greeks: { delta: 0.5, gamma, theta: -1, vega: 10, rho: 0, vanna: 0, charm: 0, vomma: 0 },
  };
}

describe('netGexAt sign convention', () => {
  it('a call-only chain is positive', () => {
    const chain = [fakeContract('CE', 0.01, 1000)];
    expect(netGexAt(chain, 24000, 25)).toBeGreaterThan(0);
  });
  it('a put-only chain is negative', () => {
    const chain = [fakeContract('PE', 0.01, 1000)];
    expect(netGexAt(chain, 24000, 25)).toBeLessThan(0);
  });
  it('balanced call/put exposure nets to zero', () => {
    const chain = [fakeContract('CE', 0.01, 1000), fakeContract('PE', 0.01, 1000)];
    expect(netGexAt(chain, 24000, 25)).toBeCloseTo(0, 10);
  });
  it('contractGex matches the documented formula', () => {
    const c = fakeContract('CE', 0.02, 500);
    expect(contractGex(c, 24000, 25)).toBeCloseTo(
      (0.02 * 500 * 25 * 24000 * 24000) / 1e7,
      8,
    );
  });
});

describe('generateOptionChain', () => {
  it('produces a two-sided chain straddling spot', () => {
    const chain = generateOptionChain({
      underlying: 'NIFTY',
      spot: 24300,
      expiryDays: 7,
      baseIv: 0.13,
    });
    expect(chain.length).toBeGreaterThan(4);
    expect(chain.some((c) => c.optionType === 'CE')).toBe(true);
    expect(chain.some((c) => c.optionType === 'PE')).toBe(true);
    for (const c of chain) {
      expect(c.ltp).toBeGreaterThanOrEqual(0);
      expect(c.oi).toBeGreaterThanOrEqual(0);
    }
  });
});
