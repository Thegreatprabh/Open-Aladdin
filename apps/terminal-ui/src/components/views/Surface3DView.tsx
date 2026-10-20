// 3D Surface — interactive WebGL-grade canvas projection (custom software
// renderer: yaw/pitch drag rotation, wheel zoom, hover node inspection) for
// the IV smile surface, net dealer GEX surface and Vanna/Charm hedging flow.

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTerminal } from '../../state/TerminalContext';
import { buildGexProfile, generateOptionChain } from '../../engine/gexCalculator';
import { bsGreeks } from '../../engine/blackScholes';
import { RISK_FREE_RATE } from '../../types/market';
import { fmt, fmt0, fmtCr } from '../../lib/format';

type Mode = 'iv' | 'gex' | 'vanna';

const DTES = [0, 1, 2, 3, 7, 14, 21, 30];

function colormap(t: number): string {
  const stops: [number, number, number][] = [
    [30, 58, 138],
    [6, 182, 212],
    [16, 185, 129],
    [245, 158, 11],
    [244, 63, 94],
  ];
  const x = Math.max(0, Math.min(0.999, t)) * (stops.length - 1);
  const i = Math.floor(x);
  const f = x - i;
  const a = stops[i];
  const b = stops[i + 1];
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * f)},${Math.round(a[1] + (b[1] - a[1]) * f)},${Math.round(a[2] + (b[2] - a[2]) * f)})`;
}

export default function Surface3DView() {
  const T = useTerminal();
  const [underlying, setUnderlying] = useState<'NIFTY' | 'BANKNIFTY'>('NIFTY');
  const [mode, setMode] = useState<Mode>('gex');
  const [autoRotate, setAutoRotate] = useState(true);
  const [hover, setHover] = useState<{ strike: number; dte: number; value: number } | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef({ yaw: 0.7, pitch: 0.85, zoom: 1, dragging: false, lx: 0, ly: 0, mx: -1, my: -1 });

  const spot = T.quotes.find((q) => q.symbol === underlying)?.price ?? 0;

  const frontChain = useMemo(
    () => generateOptionChain({ underlying, spot, expiryDays: 1, seed: 7 }),
    [underlying, spot],
  );
  const gex = useMemo(() => buildGexProfile(frontChain, spot, underlying), [frontChain, spot, underlying]);
  const strikes = gex.strikes;

  // ── Surface dataset: strikes × DTEs → value ─────────────────────────────
  const grid = useMemo(() => {
    const rows: number[][] = [];
    for (let j = 0; j < DTES.length; j++) {
      const dte = DTES[j];
      const chain = generateOptionChain({ underlying, spot, expiryDays: Math.max(dte, 0.5), seed: 7 });
      const prof = buildGexProfile(chain, spot, underlying);
      const row: number[] = [];
      for (let i = 0; i < strikes.length; i++) {
        const k = strikes[i];
        if (mode === 'iv') {
          const c = chain.find((x) => x.strike === k);
          row.push((c?.iv ?? 0.13) * 100);
        } else if (mode === 'gex') {
          const idx = prof.strikes.indexOf(k);
          row.push(idx >= 0 ? prof.netGex[idx] : 0);
        } else {
          // Vanna hedging flow = vanna × OI (signed), decayed by expiry
          const ce = chain.find((x) => x.strike === k && x.optionType === 'CE');
          const pe = chain.find((x) => x.strike === k && x.optionType === 'PE');
          const gCe = bsGreeks({ S: spot, K: k, T: Math.max(dte, 0.5) / 365, r: RISK_FREE_RATE, sigma: ce?.iv ?? 0.13, type: 'CE' });
          const gPe = bsGreeks({ S: spot, K: k, T: Math.max(dte, 0.5) / 365, r: RISK_FREE_RATE, sigma: pe?.iv ?? 0.13, type: 'PE' });
          row.push(((gCe.vanna * (ce?.oi ?? 0) - gPe.vanna * (pe?.oi ?? 0)) / 1e5) * Math.exp(-dte / 45));
        }
      }
      rows.push(row);
    }
    return rows;
  }, [mode, underlying, spot, strikes]);

  const { vMin, vMax } = useMemo(() => {
    const flat = grid.flat();
    return { vMin: Math.min(...flat), vMax: Math.max(...flat) };
  }, [grid]);

  // ── Renderer ─────────────────────────────────────────────────────────────
  useEffect(() => {
    let raf = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const canvas = canvasRef.current;
      const parent = canvas?.parentElement;
      if (!canvas || !parent) return;
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

      const v = viewRef.current;
      if (autoRotate && !v.dragging) v.yaw += 0.0035;

      const nx = strikes.length;
      const ny = DTES.length;
      const zScale = mode === 'iv' ? 3.2 : 0.9;
      const project = (i: number, j: number, val: number) => {
        const xn = (i / (nx - 1) - 0.5) * 2;
        const yn = (j / (ny - 1) - 0.5) * 2;
        const zn = ((val - vMin) / Math.max(vMax - vMin, 1e-9)) * zScale - zScale * 0.35;
        // yaw around Z, pitch around X
        const cy = Math.cos(v.yaw);
        const sy = Math.sin(v.yaw);
        const cp = Math.cos(v.pitch);
        const sp = Math.sin(v.pitch);
        const x1 = xn * cy - yn * sy;
        const y1 = xn * sy + yn * cy;
        const y2 = y1 * cp - zn * sp;
        const z2 = y1 * sp + zn * cp;
        const scale = (Math.min(W, H) / 3.4) * v.zoom;
        const persp = 1 / (1 + z2 * 0.16);
        return {
          x: W / 2 + x1 * scale * persp,
          y: H / 2 + 30 - y2 * scale * persp,
          depth: z2,
        };
      };

      interface Quad {
        pts: { x: number; y: number }[];
        depth: number;
        color: string;
        value: number;
      }
      const quads: Quad[] = [];
      for (let j = 0; j < ny - 1; j++) {
        for (let i = 0; i < nx - 1; i++) {
          const corners = [
            project(i, j, grid[j][i]),
            project(i + 1, j, grid[j][i + 1]),
            project(i + 1, j + 1, grid[j + 1][i + 1]),
            project(i, j + 1, grid[j + 1][i]),
          ];
          const avgVal = (grid[j][i] + grid[j][i + 1] + grid[j + 1][i] + grid[j + 1][i + 1]) / 4;
          const tPos = (avgVal - vMin) / Math.max(vMax - vMin, 1e-9);
          quads.push({
            pts: corners,
            depth: corners.reduce((a, c) => a + c.depth, 0) / 4,
            color: colormap(tPos),
            value: avgVal,
          });
        }
      }
      quads.sort((a, b) => a.depth - b.depth);
      for (const q of quads) {
        ctx.beginPath();
        q.pts.forEach((p, k) => (k === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
        ctx.closePath();
        ctx.fillStyle = q.color;
        ctx.globalAlpha = 0.88;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = 'rgba(6,8,13,0.55)';
        ctx.lineWidth = 0.5;
        ctx.stroke();
      }

      // hover inspection: find nearest grid node to mouse
      if (v.mx >= 0) {
        let best: { d: number; i: number; j: number } | null = null;
        for (let j = 0; j < ny; j++) {
          for (let i = 0; i < nx; i++) {
            const p = project(i, j, grid[j][i]);
            const d = (p.x - v.mx) ** 2 + (p.y - v.my) ** 2;
            if (!best || d < best.d) best = { d, i, j };
          }
        }
        if (best && best.d < 2600) {
          const p = project(best.i, best.j, grid[best.j][best.i]);
          ctx.strokeStyle = '#ffffff';
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
          ctx.stroke();
          setHover((prev) => {
            const nv = { strike: strikes[best!.i], dte: DTES[best!.j], value: grid[best!.j][best!.i] };
            return prev && prev.strike === nv.strike && prev.dte === nv.dte && prev.value === nv.value ? prev : nv;
          });
        }
      }

      // axis labels
      ctx.fillStyle = '#64748b';
      ctx.font = '10px JetBrains Mono';
      ctx.fillText(`STRIKE →`, 14, H - 12);
      ctx.fillText(`← DTE`, 14, 18);
      ctx.fillText(mode === 'iv' ? 'IV %' : mode === 'gex' ? 'NET GEX ₹Cr' : 'VANNA FLOW', W - 110, 18);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [grid, strikes, vMin, vMax, mode, autoRotate]);

  const badge = (label: string, value: string, accent: string) => (
    <div className="bg-card border border-edge rounded px-3 py-2 min-w-[130px]">
      <div className="text-[9px] tracking-widest text-gray-500">{label}</div>
      <div className={`text-[15px] font-extrabold ${accent}`}>{value}</div>
    </div>
  );

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-edge bg-panel flex-wrap shrink-0">
        {(['NIFTY', 'BANKNIFTY'] as const).map((u) => (
          <button key={u} onClick={() => setUnderlying(u)} className={`px-2.5 py-1 text-[11px] font-bold rounded ${underlying === u ? 'bg-amber text-base' : 'bg-card border border-edge text-gray-400'}`}>
            {u}
          </button>
        ))}
        <span className="w-px h-5 bg-edge mx-1" />
        {(
          [
            ['iv', 'IV SURFACE'],
            ['gex', 'NET GEX'],
            ['vanna', 'VANNA / CHARM FLOW'],
          ] as [Mode, string][]
        ).map(([m, label]) => (
          <button key={m} onClick={() => setMode(m)} className={`px-2.5 py-1 text-[11px] font-bold rounded ${mode === m ? 'bg-qcyan text-base' : 'bg-card border border-edge text-gray-400'}`}>
            {label}
          </button>
        ))}
        <button onClick={() => setAutoRotate((v) => !v)} className={`px-2.5 py-1 text-[11px] font-bold rounded border ${autoRotate ? 'border-qcyan text-qcyan bg-qcyan/10' : 'border-edge text-gray-500 bg-card'}`}>
          AUTO-ROTATE {autoRotate ? 'ON' : 'OFF'}
        </button>
        <div className="ml-auto flex gap-2 flex-wrap">
          {badge('SPOT', fmt(spot), 'text-white')}
          {badge('ZERO-GAMMA FLIP', fmt0(gex.zeroGammaLevel), 'text-amber')}
          {badge('CALL WALL', fmt0(gex.callWall), 'text-bull')}
          {badge('PUT WALL', fmt0(gex.putWall), 'text-bear')}
          {badge('NET GEX', fmtCr(gex.netGexTotal), gex.netGexTotal >= 0 ? 'text-bull' : 'text-bear')}
        </div>
      </div>

      <div className="relative flex-1 min-h-0 bg-base">
        <canvas
          ref={canvasRef}
          className="absolute inset-0 cursor-grab active:cursor-grabbing"
          onMouseDown={(e) => {
            viewRef.current.dragging = true;
            viewRef.current.lx = e.clientX;
            viewRef.current.ly = e.clientY;
          }}
          onMouseUp={() => (viewRef.current.dragging = false)}
          onMouseLeave={() => {
            viewRef.current.dragging = false;
            viewRef.current.mx = -1;
          }}
          onMouseMove={(e) => {
            const v = viewRef.current;
            const r = e.currentTarget.getBoundingClientRect();
            v.mx = e.clientX - r.left;
            v.my = e.clientY - r.top;
            if (v.dragging) {
              v.yaw += (e.clientX - v.lx) * 0.008;
              v.pitch = Math.max(0.15, Math.min(1.45, v.pitch + (e.clientY - v.ly) * 0.006));
              v.lx = e.clientX;
              v.ly = e.clientY;
            }
          }}
          onWheel={(e) => {
            viewRef.current.zoom = Math.max(0.45, Math.min(2.6, viewRef.current.zoom * (e.deltaY < 0 ? 1.08 : 0.93)));
          }}
        />
        <div className="absolute bottom-3 left-3 bg-panel/90 border border-edge rounded px-3 py-2 text-[11px] pointer-events-none">
          {hover ? (
            <span>
              <span className="text-gray-500">NODE</span> K <span className="text-white font-bold">{fmt0(hover.strike)}</span>
              {' · '}DTE <span className="text-white font-bold">{hover.dte}d</span>
              {' · '}
              {mode === 'iv' ? 'IV' : mode === 'gex' ? 'Net GEX' : 'Vanna flow'}{' '}
              <span className="text-qcyan font-bold">
                {mode === 'gex' ? fmtCr(hover.value) : fmt(hover.value, mode === 'iv' ? 2 : 1)}
              </span>
            </span>
          ) : (
            <span className="text-gray-500">Drag to rotate · scroll to zoom · hover a node to inspect</span>
          )}
        </div>
        <div className="absolute top-3 right-3 bg-panel/90 border border-edge rounded px-3 py-2 text-[10px] text-gray-500 pointer-events-none">
          Range: {fmt(vMin, 1)} → {fmt(vMax, 1)} · {strikes.length} strikes × {DTES.length} tenors
        </div>
      </div>
    </div>
  );
}
