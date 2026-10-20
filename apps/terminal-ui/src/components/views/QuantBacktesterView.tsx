// Quant Backtester — algo sandbox with equity curve, drawdown, monthly
// heatmap, 1,000-run Monte Carlo fan, Sharpe/Sortino/Calmar and CSV export.

import React, { useMemo, useRef, useState } from 'react';
import { useTerminal } from '../../state/TerminalContext';
import {
  STRATEGIES,
  backtestToCsv,
  generateOHLCV,
  runBacktest,
  type StrategyId,
} from '../../engine/backtestEngine';
import type { BacktestResult } from '../../types/market';
import { fmt, fmtPct } from '../../lib/format';

function useLineChart(
  _ref: React.RefObject<HTMLCanvasElement>,
  series: number[][],
  opts: { colors: string[]; fill?: boolean; min?: number; max?: number },
) {
  const data = JSON.stringify([series.length, series[0]?.length ?? 0, opts.min ?? '', opts.max ?? '']);
  return data;
}

export default function QuantBacktesterView() {
  const T = useTerminal();
  const [strategy, setStrategy] = useState<StrategyId>('ema-trend');
  const [days, setDays] = useState(750);
  const [seed, setSeed] = useState(11);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [showMC, setShowMC] = useState(true);
  const eqRef = useRef<HTMLCanvasElement>(null);
  const ddRef = useRef<HTMLCanvasElement>(null);
  const chartKey = useLineChart(eqRef, result ? [result.equityCurve] : [], { colors: ['#06B6D4'] });

  const stratMeta = useMemo(() => STRATEGIES.find((s) => s.id === strategy)!, [strategy]);

  const run = () => {
    setRunning(true);
    window.setTimeout(() => {
      const candles = generateOHLCV(seed, days, 18250, 0.155);
      setResult(runBacktest(candles, strategy, T.cash));
      setRunning(false);
    }, 60);
  };

  const exportCSV = () => {
    if (!result) return;
    const blob = new Blob([backtestToCsv(result, T.cash)], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `open-aladdin-backtest-${strategy}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const drawSeries = (
    ref: React.RefObject<HTMLCanvasElement>,
    lines: number[][],
    colors: string[],
    fillIdx: number | null,
    isDrawdown = false,
  ) => {
    const canvas = ref.current;
    const parent = canvas?.parentElement;
    if (!canvas || !parent || lines.length === 0) return;
    const dpr = window.devicePixelRatio || 1;
    const W = parent.clientWidth;
    const H = parent.clientHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const flat = lines.flat();
    let mn = Math.min(...flat);
    let mx = Math.max(...flat);
    if (isDrawdown) mn = Math.min(mn, -0.02);
    const X = (i: number) => (i / (lines[0].length - 1)) * (W - 8) + 4;
    const Y = (v: number) => H - 6 - ((v - mn) / Math.max(mx - mn, 1e-9)) * (H - 12);
    lines.forEach((s, li) => {
      ctx.beginPath();
      s.forEach((v, i) => (i === 0 ? ctx.moveTo(X(i), Y(v)) : ctx.lineTo(X(i), Y(v))));
      ctx.strokeStyle = colors[li % colors.length];
      ctx.lineWidth = li === fillIdx ? 2 : 1;
      ctx.stroke();
      if (li === fillIdx) {
        ctx.lineTo(X(s.length - 1), Y(isDrawdown ? 0 : mn));
        ctx.lineTo(X(0), Y(isDrawdown ? 0 : mn));
        ctx.closePath();
        ctx.fillStyle = colors[li % colors.length].replace('1)', '0.18)');
        ctx.fill();
      }
    });
    ctx.fillStyle = '#64748b';
    ctx.font = '10px JetBrains Mono';
    ctx.fillText(fmt(mx, mx > 1000 ? 0 : 2), 6, 12);
    ctx.fillText(fmt(mn, mx > 1000 ? 0 : 2), 6, H - 4);
  };

  useMemo(() => {
    if (!result) return;
    // paint after layout settles
    requestAnimationFrame(() => {
      drawSeries(
        eqRef,
        showMC
          ? [result.mc.p5, result.mc.p25, result.mc.p75, result.mc.p95, result.equityCurve]
          : [result.equityCurve],
        ['rgba(100,116,139,1)', 'rgba(6,182,212,1)', 'rgba(6,182,212,1)', 'rgba(100,116,139,1)', 'rgba(245,158,11,1)'],
        showMC ? 4 : 0,
      );
      drawSeries(ddRef, [result.drawdown], ['rgba(244,63,94,1)'], 0, true);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartKey, showMC, result]);

  const stat = (label: string, value: string, tone: string) => (
    <div className="bg-card border border-edge rounded px-3 py-2">
      <div className="text-[9px] tracking-widest text-gray-600">{label}</div>
      <div className={`text-[16px] font-extrabold ${tone}`}>{value}</div>
    </div>
  );

  const months = useMemo(() => {
    if (!result) return [];
    const years = [...new Set(result.monthlyReturns.map((m) => m.month.slice(0, 4)))].sort();
    return years.map((y) => ({
      year: y,
      cells: Array.from({ length: 12 }, (_, mi) => {
        const key = `${y}-${String(mi + 1).padStart(2, '0')}`;
        const m = result.monthlyReturns.find((x) => x.month === key);
        return m ? m.ret : null;
      }),
    }));
  }, [result]);

  return (
    <div className="h-full overflow-y-auto bg-base">
      <div className="flex items-center gap-3 px-4 py-3 border-b border-edge bg-panel flex-wrap sticky top-0 z-10">
        <span className="text-[10px] font-bold tracking-widest text-gray-400">STRATEGY</span>
        {STRATEGIES.map((s) => (
          <button key={s.id} onClick={() => setStrategy(s.id)} className={`px-2.5 py-1 text-[11px] font-bold rounded ${strategy === s.id ? 'bg-amber text-base' : 'bg-card border border-edge text-gray-400'}`}>
            {s.name}
          </button>
        ))}
        <span className="text-[10px] text-gray-600 ml-2">{stratMeta.desc}</span>
        <div className="ml-auto flex items-center gap-2">
          <label className="text-[10px] text-gray-500">DAYS</label>
          <input type="number" value={days} min={252} max={1500} onChange={(e) => setDays(parseInt(e.target.value || '750', 10))} className="w-20 bg-card border border-edge rounded px-2 py-1 text-[12px] text-white outline-none" />
          <label className="text-[10px] text-gray-500">SEED</label>
          <input type="number" value={seed} onChange={(e) => setSeed(parseInt(e.target.value || '11', 10))} className="w-16 bg-card border border-edge rounded px-2 py-1 text-[12px] text-white outline-none" />
          <button onClick={run} disabled={running} className="px-5 py-1.5 bg-qcyan text-base font-extrabold text-[12px] rounded hover:brightness-110 disabled:opacity-50">
            {running ? 'RUNNING…' : '▶ RUN BACKTEST'}
          </button>
          <button onClick={exportCSV} disabled={!result} className="px-3 py-1.5 bg-card border border-edge text-gray-300 font-bold text-[11px] rounded hover:border-qcyan disabled:opacity-40">
            ⤓ CSV EXPORT
          </button>
        </div>
      </div>

      {!result && (
        <div className="flex items-center justify-center h-[60%] text-[12px] text-gray-600 px-8 text-center">
          Pick a strategy and press RUN — 750 days of synthetic NIFTY-style OHLCV,
          <br />full tearsheet math + 1,000-run Monte Carlo bootstrap.
        </div>
      )}

      {result && (
        <div className="p-4 space-y-4">
          <div className="grid grid-cols-4 md:grid-cols-8 gap-2">
            {stat('TOTAL RETURN', fmtPct(result.totalReturn * 100), result.totalReturn >= 0 ? 'text-bull' : 'text-bear')}
            {stat('CAGR', fmtPct(result.cagr * 100), 'text-white')}
            {stat('SHARPE', fmt(result.sharpe), result.sharpe > 1 ? 'text-bull' : 'text-gray-300')}
            {stat('SORTINO', fmt(result.sortino), result.sortino > 1 ? 'text-bull' : 'text-gray-300')}
            {stat('CALMAR', fmt(result.calmar), 'text-qcyan')}
            {stat('MAX DRAWDOWN', `−${fmt(result.maxDrawdown * 100)}%`, 'text-bear')}
            {stat('WIN RATE', fmtPct(result.winRate * 100), 'text-white')}
            {stat('TRADES', String(result.trades), 'text-amber')}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-panel border border-edge rounded">
              <div className="flex items-center justify-between px-3 py-2 border-b border-edge">
                <span className="text-[10px] font-bold tracking-widest text-gray-400">EQUITY CURVE + MONTE CARLO FAN (1,000 RUNS)</span>
                <button onClick={() => setShowMC((v) => !v)} className={`text-[10px] font-bold px-2 py-0.5 rounded border ${showMC ? 'border-qcyan text-qcyan' : 'border-edge text-gray-600'}`}>
                  MC FAN {showMC ? 'ON' : 'OFF'}
                </button>
              </div>
              <div className="relative h-[260px]"><canvas ref={eqRef} className="absolute inset-0" /></div>
            </div>
            <div className="bg-panel border border-edge rounded">
              <div className="px-3 py-2 border-b border-edge text-[10px] font-bold tracking-widest text-gray-400">DRAWDOWN</div>
              <div className="relative h-[260px]"><canvas ref={ddRef} className="absolute inset-0" /></div>
            </div>
          </div>

          <div className="bg-panel border border-edge rounded">
            <div className="px-3 py-2 border-b border-edge text-[10px] font-bold tracking-widest text-gray-400">MONTHLY RETURNS HEATMAP</div>
            <div className="p-3 overflow-x-auto">
              <div className="inline-block min-w-full">
                <div className="grid grid-cols-[52px_repeat(12,1fr)] gap-1 text-[9.5px]">
                  <span />
                  {['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'].map((m, i) => (
                    <span key={i} className="text-center text-gray-600">{m}</span>
                  ))}
                  {months.map((row) => (
                    <React.Fragment key={row.year}>
                      <span className="text-gray-500 font-bold">{row.year}</span>
                      {row.cells.map((ret, i) => {
                        const bgc = ret == null ? '' : ret >= 0 ? 'bg-bull' : 'bg-bear';
                        const op = ret == null ? 1 : Math.min(0.25 + Math.abs(ret) * 9, 1);
                        return (
                          <div
                            key={i}
                            title={ret == null ? '—' : fmtPct(ret * 100)}
                            className={`h-7 rounded flex items-center justify-center text-[9px] font-bold ${ret == null ? 'bg-card text-gray-700' : `${bgc} text-base`}`}
                            style={{ opacity: op }}
                          >
                            {ret == null ? '' : fmtPct(ret * 100, 0)}
                          </div>
                        );
                      })}
                    </React.Fragment>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
