// Command Deck — multi-panel OHLCV canvas chart (VWAP / EMA20 / Bollinger /
// Order Blocks), L2 DOM ladder, time & sales tape, quick order dock,
// positions with live MTM + net Greeks, and risk actions.

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTerminal } from '../../state/TerminalContext';
import { ema } from '../../engine/backtestEngine';
import { fmt, fmt0, fmtINR, fmtSigned, fmtTime } from '../../lib/format';
import type { Candle } from '../../types/market';

const TIMEFRAMES = ['1m', '5m', '15m', '1H', '1D'];
const SYMBOLS = ['NIFTY', 'BANKNIFTY'];

function vwapSeries(candles: Candle[]): number[] {
  let cumPV = 0;
  let cumV = 0;
  return candles.map((c) => {
    const tp = (c.h + c.l + c.c) / 3;
    cumPV += tp * c.v;
    cumV += c.v;
    return cumV > 0 ? cumPV / cumV : c.c;
  });
}

function bollinger(closes: number[], period = 20, mult = 2): { mid: number[]; up: number[]; dn: number[] } {
  const mid: number[] = [];
  const up: number[] = [];
  const dn: number[] = [];
  closes.forEach((_, i) => {
    if (i < period - 1) {
      mid.push(NaN);
      up.push(NaN);
      dn.push(NaN);
      return;
    }
    const win = closes.slice(i - period + 1, i + 1);
    const m = win.reduce((a, b) => a + b, 0) / period;
    const sd = Math.sqrt(win.reduce((a, v) => a + (v - m) ** 2, 0) / period);
    mid.push(m);
    up.push(m + mult * sd);
    dn.push(m - mult * sd);
  });
  return { mid, up, dn };
}

export default function CommandDeckView() {
  const T = useTerminal();
  const [symbol, setSymbol] = useState('NIFTY');
  const [tf, setTf] = useState('5m');
  const [showVwap, setShowVwap] = useState(true);
  const [showEma, setShowEma] = useState(true);
  const [showBoll, setShowBoll] = useState(false);
  const [showOB, setShowOB] = useState(true);
  const [qty, setQty] = useState(25);
  const [orderType, setOrderType] = useState<'MARKET' | 'LIMIT'>('MARKET');
  const [limitPrice, setLimitPrice] = useState('');
  const [feedback, setFeedback] = useState('');
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mouseRef = useRef<{ x: number; y: number } | null>(null);

  const quote = T.quotes.find((q) => q.symbol === symbol);
  const book = T.engine.getBook(symbol, 10);
  const tape = T.engine.getTape(symbol, 46);

  // ── Canvas chart render loop ────────────────────────────────────────────
  useEffect(() => {
    let raf = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const canvas = canvasRef.current;
      if (!canvas) return;
      const parent = canvas.parentElement;
      if (!parent) return;
      const dpr = window.devicePixelRatio || 1;
      const W = parent.clientWidth;
      const H = parent.clientHeight;
      if (canvas.width !== W * dpr || canvas.height !== H * dpr) {
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        canvas.style.width = `${W}px`;
        canvas.style.height = `${H}px`;
      }
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      const candles = T.engine.getCandles(symbol, tf);
      if (candles.length < 2) return;
      const closes = candles.map((c) => c.c);
      const vw = vwapSeries(candles);
      const e20 = ema(closes, 20);
      const bb = bollinger(closes);

      let min = Math.min(...candles.map((c) => c.l));
      let max = Math.max(...candles.map((c) => c.h));
      const pad = (max - min) * 0.08 || 1;
      min -= pad;
      max += pad;
      const volMax = Math.max(...candles.map((c) => c.v));
      const priceH = H * 0.78;
      const x = (i: number) => (i / (candles.length - 1)) * (W - 70) + 8;
      const y = (p: number) => priceH - ((p - min) / (max - min)) * priceH;

      // grid + axis
      ctx.strokeStyle = '#1E293B';
      ctx.lineWidth = 0.5;
      ctx.fillStyle = '#64748b';
      ctx.font = '10px JetBrains Mono';
      for (let i = 0; i <= 5; i++) {
        const p = min + ((max - min) * i) / 5;
        const yy = y(p);
        ctx.beginPath();
        ctx.moveTo(0, yy);
        ctx.lineTo(W - 62, yy);
        ctx.stroke();
        ctx.fillText(fmt(p, 1), W - 58, yy + 3);
      }

      // order blocks: shade zone of the max-volume candle's range
      if (showOB) {
        let mi = 0;
        candles.forEach((c, i) => {
          if (c.v > candles[mi].v) mi = i;
        });
        const ob = candles[mi];
        ctx.fillStyle = 'rgba(245,158,11,0.10)';
        ctx.fillRect(0, y(ob.h), W - 62, Math.max(y(ob.l) - y(ob.h), 3));
        ctx.fillStyle = '#F59E0B';
        ctx.fillText('ORDER BLOCK', 10, y(ob.h) + 12);
      }

      // volume bars
      candles.forEach((c, i) => {
        const vh = (c.v / volMax) * (H - priceH - 8);
        ctx.fillStyle = c.c >= c.o ? 'rgba(16,185,129,0.5)' : 'rgba(244,63,94,0.5)';
        ctx.fillRect(x(i) - 2, H - vh, 4, vh);
      });

      // candles
      const bw = Math.max(2, (W - 70) / candles.length - 2);
      candles.forEach((c, i) => {
        const up = c.c >= c.o;
        ctx.strokeStyle = up ? '#10B981' : '#F43F5E';
        ctx.fillStyle = up ? '#10B981' : '#F43F5E';
        ctx.beginPath();
        ctx.moveTo(x(i), y(c.h));
        ctx.lineTo(x(i), y(c.l));
        ctx.stroke();
        const top = y(Math.max(c.o, c.c));
        const hgt = Math.max(Math.abs(y(c.o) - y(c.c)), 1);
        ctx.fillRect(x(i) - bw / 2, top, bw, hgt);
      });

      const series = (vals: number[], color: string, dash: number[] = []) => {
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.2;
        ctx.setLineDash(dash);
        ctx.beginPath();
        let started = false;
        vals.forEach((v, i) => {
          if (Number.isNaN(v)) return;
          if (!started) {
            ctx.moveTo(x(i), y(v));
            started = true;
          } else ctx.lineTo(x(i), y(v));
        });
        ctx.stroke();
        ctx.setLineDash([]);
      };
      if (showVwap) series(vw, '#F59E0B');
      if (showEma) series(e20, '#06B6D4');
      if (showBoll) {
        series(bb.up, '#7c3aed', [4, 4]);
        series(bb.mid, '#7c3aed', [2, 3]);
        series(bb.dn, '#7c3aed', [4, 4]);
      }

      // crosshair
      const m = mouseRef.current;
      if (m) {
        const idx = Math.max(0, Math.min(candles.length - 1, Math.round(((m.x - 8) / (W - 70)) * (candles.length - 1))));
        ctx.strokeStyle = 'rgba(148,163,184,0.5)';
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(x(idx), 0);
        ctx.lineTo(x(idx), H);
        ctx.moveTo(0, m.y);
        ctx.lineTo(W, m.y);
        ctx.stroke();
        ctx.setLineDash([]);
        const c = candles[idx];
        ctx.fillStyle = 'rgba(11,15,23,0.92)';
        ctx.fillRect(8, 8, 300, 18);
        ctx.fillStyle = '#e5e7eb';
        ctx.font = '11px JetBrains Mono';
        ctx.fillText(
          `O ${fmt(c.o)}  H ${fmt(c.h)}  L ${fmt(c.l)}  C ${fmt(c.c)}  V ${fmt0(c.v)}`,
          14,
          21,
        );
      }
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [T.engine, symbol, tf, showVwap, showEma, showBoll, showOB]);

  const doOrder = (side: 'BUY' | 'SELL') => {
    const price = orderType === 'LIMIT' && limitPrice ? parseFloat(limitPrice) : undefined;
    const res = T.placeOrder({
      symbol,
      underlying: symbol,
      side,
      qty: Math.max(1, qty),
      price,
      orderType,
      instrumentType: 'EQUITY',
    });
    setFeedback(res.ok ? `✓ ${side} ${qty} ${symbol} FILLED @ market/limit` : `✗ ${res.reason}`);
  };

  const toggleBtn = (active: boolean) =>
    `px-2 py-1 text-[10px] font-bold rounded border ${
      active ? 'bg-qcyan/20 border-qcyan text-qcyan' : 'bg-card border-edge text-gray-500 hover:text-gray-300'
    }`;

  const positionsForSymbol = useMemo(() => T.positions, [T.positions]);

  return (
    <div className="h-full flex">
      {/* LEFT: chart + dock + positions */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-edge bg-panel shrink-0 flex-wrap">
          {SYMBOLS.map((s) => (
            <button key={s} onClick={() => setSymbol(s)} className={`px-2.5 py-1 text-[11px] font-bold rounded ${symbol === s ? 'bg-amber text-base' : 'bg-card border border-edge text-gray-400'}`}>
              {s}
            </button>
          ))}
          <span className="w-px h-5 bg-edge mx-1" />
          {TIMEFRAMES.map((t) => (
            <button key={t} onClick={() => setTf(t)} className={`px-2 py-1 text-[10px] font-bold rounded ${tf === t ? 'bg-qcyan text-base' : 'bg-card border border-edge text-gray-400'}`}>
              {t}
            </button>
          ))}
          <span className="w-px h-5 bg-edge mx-1" />
          <button className={toggleBtn(showVwap)} onClick={() => setShowVwap((v) => !v)}>VWAP</button>
          <button className={toggleBtn(showEma)} onClick={() => setShowEma((v) => !v)}>EMA20</button>
          <button className={toggleBtn(showBoll)} onClick={() => setShowBoll((v) => !v)}>BOLLINGER</button>
          <button className={toggleBtn(showOB)} onClick={() => setShowOB((v) => !v)}>ORDER BLOCKS</button>
          <span className="ml-auto text-[12px]">
            <span className="text-gray-500">{symbol} </span>
            <span className={`font-extrabold ${(quote?.changePct ?? 0) >= 0 ? 'text-bull' : 'text-bear'}`}>{fmt(quote?.price ?? 0)}</span>
          </span>
        </div>

        <div className="relative flex-1 min-h-[220px] bg-base">
          <canvas
            ref={canvasRef}
            className="absolute inset-0"
            onMouseMove={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              mouseRef.current = { x: e.clientX - r.left, y: e.clientY - r.top };
            }}
            onMouseLeave={() => (mouseRef.current = null)}
          />
        </div>

        {/* Order dock */}
        <div className="shrink-0 border-t border-edge bg-panel px-3 py-2.5 flex items-center gap-3 flex-wrap">
          <div>
            <label className="block text-[9px] text-gray-600 tracking-widest">QTY</label>
            <input type="number" value={qty} min={1} onChange={(e) => setQty(parseInt(e.target.value || '0', 10))} className="w-20 bg-card border border-edge rounded px-2 py-1.5 text-[12px] text-white outline-none focus:border-qcyan" />
          </div>
          <div>
            <label className="block text-[9px] text-gray-600 tracking-widest">TYPE</label>
            <div className="flex rounded overflow-hidden border border-edge">
              {(['MARKET', 'LIMIT'] as const).map((t) => (
                <button key={t} onClick={() => setOrderType(t)} className={`px-2 py-1.5 text-[10px] font-bold ${orderType === t ? 'bg-qcyan text-base' : 'bg-card text-gray-400'}`}>{t}</button>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-[9px] text-gray-600 tracking-widest">LIMIT PRICE</label>
            <input value={limitPrice} onChange={(e) => setLimitPrice(e.target.value)} disabled={orderType === 'MARKET'} placeholder={fmt(quote?.price ?? 0)} className="w-24 bg-card border border-edge rounded px-2 py-1.5 text-[12px] text-white outline-none focus:border-qcyan disabled:opacity-40" />
          </div>
          <button onClick={() => doOrder('BUY')} className="px-6 py-2.5 bg-bull text-base font-extrabold text-[13px] rounded hover:brightness-110">BUY</button>
          <button onClick={() => doOrder('SELL')} className="px-6 py-2.5 bg-bear text-white font-extrabold text-[13px] rounded hover:brightness-110">SELL</button>
          {feedback && <span className={`text-[11px] ${feedback.startsWith('✓') ? 'text-bull' : 'text-bear'}`}>{feedback}</span>}
          <div className="ml-auto flex items-center gap-4 text-[11px]">
            <span className="text-gray-500">FREE CASH <span className="text-white font-bold">{fmtINR(T.cash)}</span></span>
            <span className="text-gray-500">MARGIN <span className="text-amber font-bold">{fmtINR(T.marginBlocked)}</span></span>
            <span className="text-gray-500">MTM <span className={`font-bold ${T.mtm >= 0 ? 'text-bull' : 'text-bear'}`}>{fmtINR(T.mtm)}</span></span>
            <button onClick={() => setFeedback(T.hedgeDelta().ok ? '✓ Delta hedged to ≈ 0' : 'Δ already ≈ 0')} className="px-2.5 py-1.5 bg-card border border-qcyan text-qcyan rounded text-[10px] font-bold">HEDGE Δ→0</button>
            <button onClick={() => { T.flattenAll(); setFeedback('✓ All positions flattened'); }} className="px-2.5 py-1.5 bg-bear/20 border border-bear text-bear rounded text-[10px] font-bold">FLATTEN ALL</button>
          </div>
        </div>

        {/* Positions */}
        <div className="shrink-0 h-[168px] overflow-y-auto border-t border-edge bg-panel">
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-panel">
              <tr className="text-gray-600 text-left border-b border-edge">
                <th className="px-3 py-1.5 font-semibold">POSITION</th>
                <th className="px-2 py-1.5 font-semibold">SIDE</th>
                <th className="px-2 py-1.5 font-semibold text-right">QTY</th>
                <th className="px-2 py-1.5 font-semibold text-right">AVG</th>
                <th className="px-2 py-1.5 font-semibold text-right">LTP</th>
                <th className="px-2 py-1.5 font-semibold text-right">P&amp;L</th>
                <th className="px-2 py-1.5 font-semibold text-right">NET GREEKS (ΣΔ / ΣΓ / ΣΘ / ΣV)</th>
                <th className="px-2 py-1.5" />
              </tr>
            </thead>
            <tbody>
              {positionsForSymbol.length === 0 && (
                <tr><td colSpan={8} className="px-3 py-4 text-center text-gray-600">No open positions — execute from the dock or the Options Chain builder.</td></tr>
              )}
              {positionsForSymbol.map((p) => (
                <tr key={p.id} className="border-b border-edge/50 hover:bg-card/60">
                  <td className="px-3 py-1.5 font-bold text-white">{p.symbol}</td>
                  <td className={`px-2 py-1.5 font-bold ${p.side === 'LONG' ? 'text-bull' : 'text-bear'}`}>{p.side}</td>
                  <td className="px-2 py-1.5 text-right">{fmt0(p.qty * (p.instrumentType === 'OPTION' ? p.lotSize : 1))}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(p.avgPrice)}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(p.ltp)}</td>
                  <td className={`px-2 py-1.5 text-right font-bold ${p.pnl >= 0 ? 'text-bull' : 'text-bear'}`}>{fmtINR(p.pnl)}</td>
                  <td className="px-2 py-1.5 text-right text-gray-500">
                    {p.id === positionsForSymbol[0].id
                      ? `${fmtSigned(T.netGreeks.delta, 0)} / ${fmt(T.netGreeks.gamma, 1)} / ${fmtINR(T.netGreeks.theta)} / ${fmtINR(T.netGreeks.vega)}`
                      : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <button onClick={() => T.squareOff(p.id)} className="px-2 py-1 bg-card border border-amber text-amber rounded text-[10px] font-bold">SQUARE OFF</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* RIGHT: DOM + tape */}
      <div className="w-[290px] shrink-0 border-l border-edge bg-panel flex flex-col min-h-0">
        <div className="px-3 py-2 border-b border-edge flex justify-between items-center">
          <span className="text-[10px] font-bold tracking-widest text-gray-400">LEVEL-2 DOM</span>
          <span className="text-[10px] text-gray-600">spread {fmt(book.spread)}</span>
        </div>
        <div className="shrink-0">
          {[...book.asks].reverse().map((l, i) => {
            const maxQ = Math.max(...book.asks.map((a) => a.qty));
            return (
              <button key={`a${i}`} onClick={() => { setOrderType('LIMIT'); setLimitPrice(String(l.price)); }} className="w-full flex items-center px-3 py-[3px] relative hover:bg-card">
                <span className="absolute right-0 top-0 bottom-0 bg-bear/10" style={{ width: `${(l.qty / maxQ) * 55}%` }} />
                <span className="text-bear text-[11px] font-bold w-24 text-left relative">{fmt(l.price)}</span>
                <span className="text-gray-300 text-[11px] flex-1 text-right relative">{fmt0(l.qty)}</span>
                <span className="text-gray-600 text-[10px] w-8 text-right relative">{l.orders}</span>
              </button>
            );
          })}
          <div className="px-3 py-1 border-y border-edge text-center text-[11px] font-extrabold text-white bg-card">
            {fmt(quote?.price ?? 0)} <span className="text-gray-600 font-normal">LTP</span>
          </div>
          {book.bids.map((l, i) => {
            const maxQ = Math.max(...book.bids.map((b) => b.qty));
            return (
              <button key={`b${i}`} onClick={() => { setOrderType('LIMIT'); setLimitPrice(String(l.price)); }} className="w-full flex items-center px-3 py-[3px] relative hover:bg-card">
                <span className="absolute right-0 top-0 bottom-0 bg-bull/10" style={{ width: `${(l.qty / maxQ) * 55}%` }} />
                <span className="text-bull text-[11px] font-bold w-24 text-left relative">{fmt(l.price)}</span>
                <span className="text-gray-300 text-[11px] flex-1 text-right relative">{fmt0(l.qty)}</span>
                <span className="text-gray-600 text-[10px] w-8 text-right relative">{l.orders}</span>
              </button>
            );
          })}
        </div>
        <div className="px-3 py-2 border-y border-edge">
          <span className="text-[10px] font-bold tracking-widest text-gray-400">TIME &amp; SALES</span>
        </div>
        <div className="flex-1 overflow-y-auto min-h-0">
          {tape.map((t, i) => (
            <div key={i} className="flex items-center px-3 py-[2.5px] border-b border-edge/40 text-[11px]">
              <span className="text-gray-600 w-[70px]">{fmtTime(t.timestamp)}</span>
              <span className={`font-bold flex-1 ${t.side === 'BUY' ? 'text-bull' : 'text-bear'}`}>{fmt(t.price)}</span>
              <span className="text-gray-400">{fmt0(t.qty)}</span>
              <span className={`ml-2 text-[9px] font-bold ${t.side === 'BUY' ? 'text-bull' : 'text-bear'}`}>{t.side === 'BUY' ? '▲' : '▼'}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
