/**
 * Backtest engine: determinism, output sanity, and — most importantly —
 * causality. The equity curve at index i must depend only on candles up
 * to i: running the engine on a prefix of the data must reproduce the
 * prefix of the full run exactly (no look-ahead bias).
 */
import { describe, expect, it } from 'vitest';
import {
  generateOHLCV,
  monteCarloBootstrap,
  runBacktest,
  type StrategyId,
} from './backtestEngine';

const STRATEGIES: StrategyId[] = ['ema-trend', 'boll-meanrev', 'donchian-breakout'];
const CAPITAL = 1_00_00_000;

describe('generateOHLCV', () => {
  it('is deterministic per seed', () => {
    const a = generateOHLCV(42, 120);
    const b = generateOHLCV(42, 120);
    expect(a.map((c) => c.c)).toEqual(b.map((c) => c.c));
  });
  it('every candle is internally consistent and SIMULATED-sourced', () => {
    for (const c of generateOHLCV(7, 60)) {
      expect(c.h).toBeGreaterThanOrEqual(Math.max(c.o, c.c));
      expect(c.l).toBeLessThanOrEqual(Math.min(c.o, c.c));
      expect(c.source).toBe('SIMULATED');
    }
  });
});

describe('runBacktest causality (no look-ahead)', () => {
  const full = generateOHLCV(11, 750);
  const PREFIX = 400;
  for (const s of STRATEGIES) {
    it(`${s}: prefix of the equity curve is invariant to future candles`, () => {
      const fullRun = runBacktest(full, s, CAPITAL);
      const prefixRun = runBacktest(full.slice(0, PREFIX), s, CAPITAL);
      expect(prefixRun.equityCurve).toEqual(fullRun.equityCurve.slice(0, PREFIX));
    });
  }
});

describe('runBacktest output sanity', () => {
  for (const s of STRATEGIES) {
    it(`${s}: finite metrics, curve starts at capital`, () => {
      const candles = generateOHLCV(3, 300);
      const res = runBacktest(candles, s, CAPITAL);
      expect(res.equityCurve[0]).toBe(CAPITAL);
      expect(res.equityCurve).toHaveLength(candles.length);
      expect(res.equityCurve.every(Number.isFinite)).toBe(true);
      expect(Number.isFinite(res.sharpe)).toBe(true);
      expect(res.maxDrawdown).toBeGreaterThanOrEqual(0);
      expect(res.maxDrawdown).toBeLessThanOrEqual(1);
      expect(res.trades).toBeGreaterThanOrEqual(0);
    });
  }
});

describe('monteCarloBootstrap', () => {
  const rets = Array.from({ length: 200 }, (_, i) => Math.sin(i * 0.7) * 0.01);
  it('is deterministic for a fixed seed', () => {
    const a = monteCarloBootstrap(rets, 200, 99);
    const b = monteCarloBootstrap(rets, 200, 99);
    expect(a.p50).toEqual(b.p50);
  });
  it('percentile bands are ordered at every step', () => {
    const mc = monteCarloBootstrap(rets, 300, 5);
    for (let i = 0; i < rets.length; i++) {
      expect(mc.p5[i]).toBeLessThanOrEqual(mc.p25[i]);
      expect(mc.p25[i]).toBeLessThanOrEqual(mc.p50[i]);
      expect(mc.p50[i]).toBeLessThanOrEqual(mc.p75[i]);
      expect(mc.p75[i]).toBeLessThanOrEqual(mc.p95[i]);
    }
  });
});
