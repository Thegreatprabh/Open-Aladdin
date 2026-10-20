// OPEN-ATLAS — Quant backtest engine.
// Historical strategy execution on generated OHLCV with full tearsheet math
// (Sharpe, Sortino, Calmar, max drawdown, monthly returns) and a
// 1,000-run Monte Carlo bootstrap of the return distribution.

import type { BacktestResult, Candle } from '../types/market';

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gauss(rng: () => number): number {
  let u = 0;
  let v = 0;
  while (u === 0) u = rng();
  while (v === 0) v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

/** Synthetic daily OHLCV history (GBM + regime drift) for backtests. */
export function generateOHLCV(
  seed = 11,
  days = 750,
  startPrice = 18250,
  sigma = 0.155,
): Candle[] {
  const rng = mulberry32(seed);
  const candles: Candle[] = [];
  let price = startPrice;
  const t0 = Date.now() - days * 86400000;
  const dt = 1 / 252;
  for (let i = 0; i < days; i++) {
    // Slow regime drift so trend & mean-reversion both have texture
    const regime = Math.sin(i / 90) * 0.35 + Math.sin(i / 23) * 0.1;
    const mu = 0.06 + regime * 0.25;
    const open = price;
    const ret = (mu - 0.5 * sigma * sigma) * dt + sigma * Math.sqrt(dt) * gauss(rng);
    price = Math.max(price * Math.exp(ret), 1);
    candles.push({
      source: 'SIMULATED',
      t: t0 + i * 86400000,
      o: open,
      h: Math.max(open, price) * (1 + 0.004 * rng()),
      l: Math.min(open, price) * (1 - 0.004 * rng()),
      c: price,
      v: Math.round(1_000_000 * (0.6 + rng() * 0.9)),
    });
  }
  return candles;
}

export function ema(values: number[], period: number): number[] {
  const k = 2 / (period + 1);
  const out: number[] = [];
  values.forEach((v, i) => {
    out.push(i === 0 ? v : v * k + out[i - 1] * (1 - k));
  });
  return out;
}

export function sma(values: number[], period: number): number[] {
  return values.map((_, i) => {
    if (i < period - 1) return NaN;
    let s = 0;
    for (let j = i - period + 1; j <= i; j++) s += values[j];
    return s / period;
  });
}

export type StrategyId = 'ema-trend' | 'boll-meanrev' | 'donchian-breakout';

export const STRATEGIES: { id: StrategyId; name: string; desc: string }[] = [
  { id: 'ema-trend', name: 'EMA Trend Rider', desc: 'Long when Close > EMA20 and EMA20 > EMA50; exit on Close < EMA20.' },
  { id: 'boll-meanrev', name: 'Bollinger Mean Reversion', desc: 'Long at lower band touch, short at upper band; exit at the 20-day mean.' },
  { id: 'donchian-breakout', name: 'Donchian Breakout', desc: 'Long on 20-day high breakout; exit on 10-day low break.' },
];

/** Signal generator: returns target position series in {-1, 0, 1} (fraction of capital). */
function generateSignals(candles: Candle[], strategy: StrategyId): number[] {
  const closes = candles.map((c) => c.c);
  const n = closes.length;
  const pos = new Array<number>(n).fill(0);

  if (strategy === 'ema-trend') {
    const e20 = ema(closes, 20);
    const e50 = ema(closes, 50);
    for (let i = 1; i < n; i++) {
      if (pos[i - 1] === 0 && closes[i] > e20[i] && e20[i] > e50[i]) pos[i] = 1;
      else if (pos[i - 1] === 1 && closes[i] < e20[i]) pos[i] = 0;
      else pos[i] = pos[i - 1];
    }
  } else if (strategy === 'boll-meanrev') {
    const mid = sma(closes, 20);
    const sd = closes.map((_, i) => {
      if (i < 19) return NaN;
      const m = mid[i];
      let s = 0;
      for (let j = i - 19; j <= i; j++) s += (closes[j] - m) ** 2;
      return Math.sqrt(s / 20);
    });
    for (let i = 1; i < n; i++) {
      if (Number.isNaN(sd[i])) {
        pos[i] = 0;
        continue;
      }
      const lower = mid[i] - 2 * sd[i];
      const upper = mid[i] + 2 * sd[i];
      if (pos[i - 1] === 0 && closes[i] < lower) pos[i] = 1;
      else if (pos[i - 1] === 0 && closes[i] > upper) pos[i] = -1;
      else if (pos[i - 1] === 1 && closes[i] >= mid[i]) pos[i] = 0;
      else if (pos[i - 1] === -1 && closes[i] <= mid[i]) pos[i] = 0;
      else pos[i] = pos[i - 1];
    }
  } else {
    // Donchian
    for (let i = 1; i < n; i++) {
      if (i < 20) {
        pos[i] = 0;
        continue;
      }
      const hi20 = Math.max(...candles.slice(i - 20, i).map((c) => c.h));
      const lo10 = Math.min(...candles.slice(Math.max(0, i - 10), i).map((c) => c.l));
      if (pos[i - 1] === 0 && closes[i] > hi20) pos[i] = 1;
      else if (pos[i - 1] === 1 && closes[i] < lo10) pos[i] = 0;
      else pos[i] = pos[i - 1];
    }
  }
  return pos;
}

function std(values: number[]): number {
  if (values.length < 2) return 0;
  const m = values.reduce((a, b) => a + b, 0) / values.length;
  return Math.sqrt(values.reduce((a, v) => a + (v - m) ** 2, 0) / (values.length - 1));
}

export function monteCarloBootstrap(
  dailyReturns: number[],
  runs = 1000,
  seed = 99,
): BacktestResult['mc'] {
  const rng = mulberry32(seed);
  const n = dailyReturns.length;
  const paths: number[][] = [];
  for (let r = 0; r < runs; r++) {
    const path = new Array<number>(n);
    let v = 1;
    for (let i = 0; i < n; i++) {
      v *= 1 + dailyReturns[Math.floor(rng() * n)];
      path[i] = v;
    }
    paths.push(path);
  }
  const pct = (q: number): number[] => {
    const col = new Array<number>(n);
    for (let i = 0; i < n; i++) {
      const sorted = paths.map((p) => p[i]).sort((a, b) => a - b);
      col[i] = sorted[Math.min(n - 1, Math.floor(q * runs))];
    }
    return col;
  };
  return { p5: pct(0.05), p25: pct(0.25), p50: pct(0.5), p75: pct(0.75), p95: pct(0.95) };
}

/** Execute a strategy backtest and return the full tearsheet result. */
export function runBacktest(
  candles: Candle[],
  strategy: StrategyId,
  initialCapital = 1_00_00_000,
): BacktestResult {
  if (!Number.isFinite(initialCapital) || initialCapital <= 0) {
    throw new Error('runBacktest: initialCapital must be positive and finite');
  }
  // Sanitize closes: a non-finite or non-positive close must never poison
  // the equity curve with NaN. Carry the last valid close forward; if the
  // series has no valid close at all, the backtest is meaningless.
  let lastValid = Number.NaN;
  const closes = candles.map((c) => {
    if (Number.isFinite(c.c) && c.c > 0) lastValid = c.c;
    return lastValid;
  });
  if (candles.length > 0 && !Number.isFinite(lastValid)) {
    throw new Error('runBacktest: candle series contains no valid close');
  }
  const safeCandles = candles.map((c, i) => ({ ...c, c: closes[i] }));
  const pos = generateSignals(safeCandles, strategy);
  const frac = 0.95; // 95% capital deployed when in position
  const costBps = 0.0004; // 4 bps round-trip cost on position changes

  const equity: number[] = [initialCapital];
  const dailyReturns: number[] = [];
  let trades = 0;
  let wins = 0;
  let entryEquity = 0;
  for (let i = 1; i < safeCandles.length; i++) {
    const assetRet = closes[i] / closes[i - 1] - 1;
    let r = pos[i - 1] * frac * assetRet;
    if (pos[i] !== pos[i - 1]) {
      r -= costBps * Math.abs(pos[i] - pos[i - 1]);
      if (pos[i - 1] !== 0 && pos[i] === 0) {
        trades++;
        if (equity[equity.length - 1] > entryEquity) wins++;
      }
      if (pos[i] !== 0 && pos[i - 1] === 0) entryEquity = equity[equity.length - 1];
    }
    dailyReturns.push(r);
    equity.push(equity[equity.length - 1] * (1 + r));
  }
  if (pos[pos.length - 1] !== 0) trades++;

  // Drawdown
  let peak = -Infinity;
  const drawdown = equity.map((v) => {
    peak = Math.max(peak, v);
    return v / peak - 1;
  });
  const maxDrawdown = Math.abs(Math.min(...drawdown));

  // Stats
  const years = candles.length / 252;
  const totalReturn = equity[equity.length - 1] / initialCapital - 1;
  const cagr = (1 + totalReturn) ** (1 / years) - 1;
  const meanDaily = dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length;
  const sdDaily = std(dailyReturns);
  const downside = dailyReturns.filter((r) => r < 0);
  const sdDown = downside.length > 1 ? std(downside) : sdDaily;
  const sharpe = sdDaily > 0 ? ((meanDaily - 0.06 / 252) / sdDaily) * Math.sqrt(252) : 0;
  const sortino = sdDown > 0 ? ((meanDaily - 0.06 / 252) / sdDown) * Math.sqrt(252) : 0;
  const calmar = maxDrawdown > 0 ? cagr / maxDrawdown : 0;

  // Monthly returns
  const monthMap = new Map<string, number>();
  candles.forEach((c, i) => {
    if (i === 0) return;
    const key = new Date(c.t).toISOString().slice(0, 7);
    monthMap.set(key, (monthMap.get(key) ?? 1) * (1 + (dailyReturns[i - 1] ?? 0)));
  });
  const monthlyReturns = [...monthMap.entries()].map(([month, v]) => ({ month, ret: v - 1 }));

  return {
    equityCurve: equity,
    drawdown,
    dates: candles.map((c) => new Date(c.t).toISOString().slice(0, 10)),
    monthlyReturns,
    sharpe,
    sortino,
    calmar,
    maxDrawdown,
    totalReturn,
    cagr,
    winRate: trades > 0 ? wins / trades : 0,
    trades,
    mc: monteCarloBootstrap(dailyReturns, 1000),
  };
}

/** Serialize a backtest result's equity curve + monthly returns to CSV. */
export function backtestToCsv(result: BacktestResult, initialCapital: number): string {
  const lines: string[] = ['date,equity,drawdown_pct'];
  result.equityCurve.forEach((v, i) => {
    lines.push(`${result.dates[Math.min(i, result.dates.length - 1)]},${v.toFixed(2)},${(result.drawdown[i] * 100).toFixed(3)}`);
  });
  lines.push('');
  lines.push('month,return_pct');
  result.monthlyReturns.forEach((m) => lines.push(`${m.month},${(m.ret * 100).toFixed(3)}`));
  lines.push('');
  lines.push(`initial_capital,${initialCapital}`);
  lines.push(`total_return_pct,${(result.totalReturn * 100).toFixed(3)}`);
  lines.push(`sharpe,${result.sharpe.toFixed(3)}`);
  lines.push(`sortino,${result.sortino.toFixed(3)}`);
  lines.push(`calmar,${result.calmar.toFixed(3)}`);
  lines.push(`max_drawdown_pct,${(result.maxDrawdown * 100).toFixed(3)}`);
  return lines.join('\n');
}
