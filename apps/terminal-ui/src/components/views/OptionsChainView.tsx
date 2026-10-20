// Options Chain — 21-strike NIFTY/BANKNIFTY chain with full Greeks, click-to-
// build multi-leg strategies, expiry payoff canvas, IV/DTE shock sliders,
// strategy presets and one-click execution into the paper book.

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTerminal } from '../../state/TerminalContext';
import { generateOptionChain } from '../../engine/gexCalculator';
import { bsGreeks, bsPrice } from '../../engine/blackScholes';
import { LOT_SIZES, RISK_FREE_RATE, type OptionContract, type OptionType } from '../../types/market';
import { fmt, fmt0, fmtINR } from '../../lib/format';

interface Leg {
  contract: OptionContract;
  qty: number; // signed lots: +long, −short
}

const EXPIRIES = [
  { label: '0 DTE', days: 0.5 },
  { label: '7 DTE', days: 7 },
  { label: '14 DTE', days: 14 },
  { label: '30 DTE', days: 30 },
];

const PRESETS: { name: string; legs: (c: OptionContract[], spot: number, step: number) => { strike: number; type: OptionType; dir: 1 | -1 }[] }[] = [
  {
    name: 'Long Straddle',
    legs: (c, spot, step) => {
      const atm = nearest(c, spot);
      return [
        { strike: atm, type: 'CE', dir: 1 },
        { strike: atm, type: 'PE', dir: 1 },
      ];
    },
  },
  {
    name: 'Long Strangle',
    legs: (c, spot, step) => {
      const k = strikesOf(c);
      const up = k.find((s) => s > spot + step) ?? k[k.length - 1];
      const dn = [...k].reverse().find((s) => s < spot - step) ?? k[0];
      return [
        { strike: up, type: 'CE', dir: 1 },
        { strike: dn, type: 'PE', dir: 1 },
      ];
    },
  },
  {
    name: 'Iron Condor',
    legs: (c, spot, step) => {
      const k = strikesOf(c);
      const up1 = k.find((s) => s > spot + step) ?? k[k.length - 1];
      const up2 = k.find((s) => s > up1) ?? k[k.length - 1];
      const dn1 = [...k].reverse().find((s) => s < spot - step) ?? k[0];
      const dn2 = [...k].reverse().find((s) => s < dn1) ?? k[0];
      return [
        { strike: up2, type: 'CE', dir: 1 },
        { strike: up1, type: 'CE', dir: -1 },
        { strike: dn1, type: 'PE', dir: -1 },
        { strike: dn2, type: 'PE', dir: 1 },
      ];
    },
  },
  {
    name: 'Bull Call Spread',
    legs: (c, spot, step) => {
      const k = strikesOf(c);
      const atm = nearest(c, spot);
      const up = k[k.indexOf(atm) + 2] ?? k[k.length - 1];
      return [
        { strike: atm, type: 'CE', dir: 1 },
        { strike: up, type: 'CE', dir: -1 },
      ];
    },
  },
];

function strikesOf(c: OptionContract[]): number[] {
  return [...new Set(c.map((x) => x.strike))].sort((a, b) => a - b);
}
function nearest(c: OptionContract[], spot: number): number {
  return strikesOf(c).reduce((a, b) => (Math.abs(b - spot) < Math.abs(a - spot) ? b : a));
}

export default function OptionsChainView() {
  const T = useTerminal();
  const [underlying, setUnderlying] = useState<'NIFTY' | 'BANKNIFTY'>('NIFTY');
  const [expIdx, setExpIdx] = useState(1);
  const [ivShock, setIvShock] = useState(0); // ±10 points
  const [dteShock, setDteShock] = useState(0); // ±days
  const [legs, setLegs] = useState<Leg[]>([]);
  const [execMsg, setExecMsg] = useState('');
  const payoffRef = useRef<HTMLCanvasElement>(null);

  const spot = T.quotes.find((q) => q.symbol === underlying)?.price ?? 0;
  const expiryDays = EXPIRIES[expIdx].days + dteShock;

  const chain = useMemo(() => {
    const base = generateOptionChain({ underlying, spot, expiryDays: Math.max(expiryDays, 0.5), seed: 7 });
    if (ivShock === 0) return base;
    return base.map((c) => {
      const iv = Math.max(c.iv + ivShock / 100, 0.02);
      const T2 = Math.max(c.dte, 0.25) / 365;
      const ltp = bsPrice({ S: spot, K: c.strike, T: T2, r: RISK_FREE_RATE, sigma: iv, type: c.optionType });
      return {
        ...c,
        iv,
        ltp,
        bid: Math.max(ltp * 0.98, 0.05),
        ask: ltp * 1.02,
        greeks: bsGreeks({ S: spot, K: c.strike, T: T2, r: RISK_FREE_RATE, sigma: iv, type: c.optionType }),
      };
    });
  }, [underlying, spot, expiryDays, ivShock]);

  const strikes = useMemo(() => strikesOf(chain), [chain]);
  const atm = nearest(chain, spot);
  const lot = LOT_SIZES[underlying] ?? 25;

  const addLeg = (contract: OptionContract, dir: 1 | -1) => {
    setLegs((prev) => {
      const i = prev.findIndex((l) => l.contract.symbol === contract.symbol);
      if (i >= 0) {
        const next = [...prev];
        const q = next[i].qty + dir;
        if (q === 0) next.splice(i, 1);
        else next[i] = { ...next[i], qty: q };
        return next;
      }
      return [...prev, { contract, qty: dir }];
    });
  };

  const applyPreset = (idx: number) => {
    const defs = PRESETS[idx].legs(chain, spot, underlying === 'BANKNIFTY' ? 500 : 50);
    const next: Leg[] = [];
    for (const d of defs) {
      const c = chain.find((x) => x.strike === d.strike && x.optionType === d.type);
      if (c) next.push({ contract: c, qty: d.dir });
    }
    setLegs(next);
  };

  const legNetPremium = useMemo(
    () => legs.reduce((a, l) => a + l.qty * l.contract.ltp * lot, 0),
    [legs, lot],
  );

  // ── Expiry payoff canvas ───────────────────────────────────────────────
  useEffect(() => {
    const canvas = payoffRef.current;
    const parent = canvas?.parentElement;
    if (!canvas || !parent || legs.length === 0) return;
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

    const lo = Math.min(...strikes) * 0.97;
    const hi = Math.max(...strikes) * 1.03;
    const payoffAt = (s: number) =>
      legs.reduce((a, l) => {
        const itm = l.contract.optionType === 'CE' ? Math.max(s - l.contract.strike, 0) : Math.max(l.contract.strike - s, 0);
        return a + l.qty * (itm - l.contract.ltp) * lot;
      }, 0);
    const xs = Array.from({ length: 220 }, (_, i) => lo + ((hi - lo) * i) / 219);
    const ys = xs.map(payoffAt);
    const yMin = Math.min(...ys, 0);
    const yMax = Math.max(...ys, 0);
    const X = (s: number) => ((s - lo) / (hi - lo)) * (W - 16) + 8;
    const Y = (v: number) => H - 14 - ((v - yMin) / Math.max(yMax - yMin, 1e-9)) * (H - 28);

    ctx.strokeStyle = '#1E293B';
    ctx.beginPath();
    ctx.moveTo(0, Y(0));
    ctx.lineTo(W, Y(0));
    ctx.stroke();

    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, 'rgba(6,182,212,0.35)');
    grad.addColorStop(1, 'rgba(244,63,94,0.35)');
    ctx.beginPath();
    xs.forEach((s, i) => (i === 0 ? ctx.moveTo(X(s), Y(ys[i])) : ctx.lineTo(X(s), Y(ys[i]))));
    ctx.strokeStyle = '#06B6D4';
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.lineTo(X(hi), Y(0));
    ctx.lineTo(X(lo), Y(0));
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // breakevens
    ctx.fillStyle = '#F59E0B';
    ctx.font = '10px JetBrains Mono';
    for (let i = 1; i < xs.length; i++) {
      if (ys[i - 1] <= 0 && ys[i] > 0) ctx.fillText(`BE ${fmt0(xs[i])}`, X(xs[i]) - 20, Y(0) - 6);
      if (ys[i - 1] >= 0 && ys[i] < 0) ctx.fillText(`BE ${fmt0(xs[i])}`, X(xs[i]) - 20, Y(0) + 12);
    }
    // legs markers
    legs.forEach((l) => {
      ctx.strokeStyle = l.qty > 0 ? '#10B981' : '#F43F5E';
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(X(l.contract.strike), 0);
      ctx.lineTo(X(l.contract.strike), H);
      ctx.stroke();
      ctx.setLineDash([]);
    });
    ctx.fillStyle = '#64748b';
    ctx.fillText(`MAX PROFIT ${fmtINR(Math.max(...ys))}   MAX LOSS ${fmtINR(Math.min(...ys))}`, 10, H - 4);
  }, [legs, strikes, lot]);

  const executeLegs = () => {
    if (legs.length === 0) {
      setExecMsg('No legs to execute.');
      return;
    }
    let okCount = 0;
    let lastErr = '';
    for (const l of legs) {
      const res = T.placeOrder({
        symbol: l.contract.symbol,
        underlying,
        side: l.qty > 0 ? 'BUY' : 'SELL',
        qty: Math.abs(l.qty),
        orderType: 'MARKET',
        instrumentType: 'OPTION',
        strike: l.contract.strike,
        expiry: l.contract.expiry,
        optionType: l.contract.optionType,
        iv: l.contract.iv,
      });
      if (res.ok) okCount++;
      else lastErr = res.reason ?? '';
    }
    setExecMsg(okCount === legs.length ? `✓ ${okCount} legs FILLED into paper book` : `⚠ ${okCount}/${legs.length} filled — ${lastErr}`);
  };

  const greekCell = (v: number, d = 3) => <span className="text-gray-400">{fmt(v, d)}</span>;

  return (
    <div className="h-full flex">
      {/* CHAIN TABLE */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-edge bg-panel flex-wrap shrink-0">
          {(['NIFTY', 'BANKNIFTY'] as const).map((u) => (
            <button key={u} onClick={() => setUnderlying(u)} className={`px-2.5 py-1 text-[11px] font-bold rounded ${underlying === u ? 'bg-amber text-base' : 'bg-card border border-edge text-gray-400'}`}>
              {u}
            </button>
          ))}
          <span className="w-px h-5 bg-edge mx-1" />
          {EXPIRIES.map((e, i) => (
            <button key={e.label} onClick={() => setExpIdx(i)} className={`px-2 py-1 text-[10px] font-bold rounded ${expIdx === i ? 'bg-qcyan text-base' : 'bg-card border border-edge text-gray-400'}`}>
              {e.label}
            </button>
          ))}
          <span className="text-[11px] text-gray-500 ml-2">Spot <span className="text-white font-bold">{fmt(spot)}</span> · ATM {fmt0(atm)} · Lot {lot}</span>
        </div>
        <div className="flex-1 overflow-y-auto min-h-0">
          <table className="w-full text-[10.5px] border-collapse">
            <thead className="sticky top-0 bg-panel z-10">
              <tr className="text-gray-500 border-b border-edge">
                <th colSpan={6} className="py-1.5 text-bull border-r border-edge">CALLS</th>
                <th className="py-1.5 text-amber">STRIKE</th>
                <th colSpan={6} className="py-1.5 text-bear border-l border-edge">PUTS</th>
              </tr>
              <tr className="text-gray-600 border-b border-edge">
                {['LTP', 'IV%', 'Δ', 'Γ', 'OI', ''].map((h, i) => (
                  <th key={`c${i}`} className="py-1 font-semibold border-r border-edge/40">{h}</th>
                ))}
                <th className="py-1 font-semibold">K</th>
                {['', 'OI', 'Γ', 'Δ', 'IV%', 'LTP'].map((h, i) => (
                  <th key={`p${i}`} className="py-1 font-semibold border-l border-edge/40">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {strikes.map((k) => {
                const ce = chain.find((c) => c.strike === k && c.optionType === 'CE')!;
                const pe = chain.find((c) => c.strike === k && c.optionType === 'PE')!;
                const isAtm = k === atm;
                return (
                  <tr key={k} className={`border-b border-edge/40 ${isAtm ? 'bg-amber/10' : 'hover:bg-card/50'}`}>
                    <td className="py-[5px] text-right text-gray-200">{fmt(ce.ltp)}</td>
                    <td className="py-[5px] text-right">{fmt(ce.iv * 100, 1)}</td>
                    <td className="py-[5px] text-right">{greekCell(ce.greeks.delta)}</td>
                    <td className="py-[5px] text-right">{greekCell(ce.greeks.gamma, 4)}</td>
                    <td className="py-[5px] text-right border-r border-edge/40 text-gray-300">{fmt0(ce.oi / 1000)}k</td>
                    <td className="py-[5px] text-center border-r border-edge/40">
                      <button onClick={() => addLeg(ce, 1)} className="px-1.5 py-0.5 bg-bull/15 border border-bull/50 text-bull rounded text-[9px] font-bold">+CE</button>
                    </td>
                    <td className={`py-[5px] text-center font-extrabold ${isAtm ? 'text-amber' : 'text-gray-300'}`}>{fmt0(k)}</td>
                    <td className="py-[5px] text-center border-l border-edge/40">
                      <button onClick={() => addLeg(pe, 1)} className="px-1.5 py-0.5 bg-bear/15 border border-bear/50 text-bear rounded text-[9px] font-bold">+PE</button>
                    </td>
                    <td className="py-[5px] text-right border-l border-edge/40 text-gray-300">{fmt0(pe.oi / 1000)}k</td>
                    <td className="py-[5px] text-right">{greekCell(pe.greeks.gamma, 4)}</td>
                    <td className="py-[5px] text-right">{greekCell(pe.greeks.delta)}</td>
                    <td className="py-[5px] text-right">{fmt(pe.iv * 100, 1)}</td>
                    <td className="py-[5px] text-right text-gray-200">{fmt(pe.ltp)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* PAYOFF BUILDER */}
      <div className="w-[380px] shrink-0 border-l border-edge bg-panel flex flex-col min-h-0">
        <div className="px-3 py-2 border-b border-edge flex items-center justify-between">
          <span className="text-[10px] font-bold tracking-widest text-gray-400">PAYOFF BUILDER</span>
          <button onClick={() => setLegs([])} className="text-[10px] text-gray-600 hover:text-bear font-bold">CLEAR</button>
        </div>
        <div className="px-3 py-2 border-b border-edge">
          <div className="text-[9px] tracking-widest text-gray-600 mb-1.5">STRATEGY PRESETS</div>
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((p, i) => (
              <button key={p.name} onClick={() => applyPreset(i)} className="px-2 py-1 bg-card border border-edge rounded text-[10px] font-bold text-gray-300 hover:border-qcyan hover:text-qcyan">
                {p.name}
              </button>
            ))}
          </div>
        </div>
        <div className="px-3 py-2 border-b border-edge space-y-2">
          <div>
            <div className="flex justify-between text-[10px] text-gray-500 mb-1">
              <span>IV SHOCK</span>
              <span className="text-amber font-bold">{ivShock >= 0 ? '+' : ''}{ivShock} pts</span>
            </div>
            <input type="range" min={-10} max={10} step={1} value={ivShock} onChange={(e) => setIvShock(parseInt(e.target.value, 10))} className="w-full accent-amber" />
          </div>
          <div>
            <div className="flex justify-between text-[10px] text-gray-500 mb-1">
              <span>DTE SHOCK</span>
              <span className="text-amber font-bold">{dteShock >= 0 ? '+' : ''}{dteShock}d</span>
            </div>
            <input type="range" min={-3} max={14} step={1} value={dteShock} onChange={(e) => setDteShock(parseInt(e.target.value, 10))} className="w-full accent-amber" />
          </div>
        </div>
        <div className="relative h-[190px] shrink-0 border-b border-edge">
          {legs.length === 0 ? (
            <div className="absolute inset-0 flex items-center justify-center text-[11px] text-gray-600 px-6 text-center">
              Click +CE / +PE on the chain, or load a preset, to build a multi-leg strategy.
            </div>
          ) : (
            <canvas ref={payoffRef} className="absolute inset-0" />
          )}
        </div>
        <div className="flex-1 overflow-y-auto min-h-0">
          {legs.map((l, i) => (
            <div key={i} className="flex items-center gap-2 px-3 py-1.5 border-b border-edge/40 text-[11px]">
              <span className={`font-extrabold ${l.qty > 0 ? 'text-bull' : 'text-bear'}`}>{l.qty > 0 ? 'LONG' : 'SHORT'}</span>
              <span className="text-white font-bold">{fmt0(l.contract.strike)}{l.contract.optionType}</span>
              <span className="text-gray-500">@ {fmt(l.contract.ltp)}</span>
              <span className="ml-auto flex gap-1">
                <button onClick={() => addLeg(l.contract, 1)} className="px-1.5 bg-card border border-edge rounded text-bull font-bold">+</button>
                <button onClick={() => addLeg(l.contract, -1)} className="px-1.5 bg-card border border-edge rounded text-bear font-bold">−</button>
              </span>
            </div>
          ))}
        </div>
        <div className="p-3 border-t border-edge shrink-0">
          <div className="flex justify-between text-[11px] mb-2">
            <span className="text-gray-500">NET PREMIUM</span>
            <span className={`font-bold ${legNetPremium >= 0 ? 'text-bear' : 'text-bull'}`}>{fmtINR(-legNetPremium)}</span>
          </div>
          <button onClick={executeLegs} className="w-full py-2.5 bg-qcyan text-base font-extrabold text-[12px] rounded hover:brightness-110">
            EXECUTE {legs.length} LEG{legs.length === 1 ? '' : 'S'} → PAPER BOOK
          </button>
          {execMsg && <div className="mt-1.5 text-[10.5px] text-gray-400">{execMsg}</div>}
        </div>
      </div>
    </div>
  );
}
