// Live flashing tape + simulator speed control.

import { useRef } from 'react';
import type { Quote } from '../types/market';
import { fmt, fmtPct, fmtTime } from '../lib/format';

interface Props {
  quotes: Quote[];
  simTime: number;
  speed: number;
  onSpeed: (n: number) => void;
  paused: boolean;
  onTogglePause: () => void;
  onOpenPalette: () => void;
}

export default function HeaderTicker({ quotes, simTime, speed, onSpeed, paused, onTogglePause, onOpenPalette }: Props) {
  const dirRef = useRef<Record<string, 1 | -1 | 0>>({});

  return (
    <header className="h-14 shrink-0 flex items-stretch bg-panel border-b border-edge">
      <div className="flex items-center gap-2 px-4 border-r border-edge shrink-0">
        <div className="w-2.5 h-2.5 rounded-full bg-amber animate-pulse" />
        <div className="leading-none">
          <div className="text-[13px] font-extrabold tracking-widest text-white">OPEN-ATLAS</div>
          <div className="text-[9px] tracking-[0.2em] text-qcyan">INSTITUTIONAL TERMINAL</div>
        </div>
      </div>

      <div className="flex-1 flex items-center gap-3 overflow-x-auto px-3 min-w-0">
        {quotes.map((q) => {
          const dir = q.price > q.prevPrice ? 1 : q.price < q.prevPrice ? -1 : (dirRef.current[q.symbol] ?? 0);
          dirRef.current[q.symbol] = dir;
          const up = dir >= 0;
          return (
            <div key={q.symbol} className="flex items-center gap-2 px-2.5 py-1 rounded bg-card border border-edge shrink-0">
              <span className="text-[10px] font-bold text-gray-400 tracking-wider">{q.label}</span>
              <span key={q.price} className={`tick-flash text-[13px] font-bold ${up ? 'text-bull' : 'text-bear'}`}>
                {fmt(q.price, q.symbol === 'USDINR' || q.symbol === 'INDIAVIX' ? 2 : 2)}
              </span>
              <span className={`text-[10px] ${q.changePct >= 0 ? 'text-bull' : 'text-bear'}`}>
                {fmtPct(q.changePct)}
              </span>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-2 px-3 border-l border-edge shrink-0">
        <span className="text-[11px] text-gray-500">{fmtTime(simTime)} IST</span>
        <div className="flex rounded overflow-hidden border border-edge">
          {[1, 5, 10].map((s) => (
            <button
              key={s}
              onClick={() => onSpeed(s)}
              className={`px-2 py-1 text-[10px] font-bold ${
                speed === s && !paused ? 'bg-qcyan text-base' : 'bg-card text-gray-400 hover:text-white'
              }`}
            >
              {s}x
            </button>
          ))}
          <button
            onClick={onTogglePause}
            className={`px-2.5 py-1 text-[10px] font-bold ${paused ? 'bg-amber text-base' : 'bg-card text-gray-400 hover:text-white'}`}
          >
            {paused ? '▶ RESUME' : '❚❚ PAUSE'}
          </button>
        </div>
        <button
          onClick={onOpenPalette}
          className="px-2.5 py-1 text-[10px] font-bold bg-card border border-edge rounded text-gray-300 hover:border-qcyan hover:text-qcyan"
          title="Command palette (Ctrl+K)"
        >
          ⌘K
        </button>
      </div>
    </header>
  );
}
