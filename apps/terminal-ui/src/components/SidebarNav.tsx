// Zero-glitch sidebar navigation across the 7 institutional modules.

import type { ViewId } from '../state/TerminalContext';
import { fmtINR } from '../lib/format';

interface Props {
  view: ViewId;
  onNavigate: (v: ViewId) => void;
  netMtm: number;
}

const ITEMS: { id: ViewId; label: string; icon: string; hotkey: string }[] = [
  { id: 'deck', label: 'Command Deck', icon: '▦', hotkey: '1' },
  { id: 'surface', label: '3D GEX Surface', icon: '◈', hotkey: '2' },
  { id: 'chain', label: 'Options Chain', icon: '☰', hotkey: '3' },
  { id: 'darkpool', label: 'Dark Pool Radar', icon: '◉', hotkey: '4' },
  { id: 'backtest', label: 'Quant Backtester', icon: '∑', hotkey: '5' },
  { id: 'broker', label: 'Broker Bridge', icon: '⇄', hotkey: '6' },
  { id: 'repo', label: 'Monorepo · 50k', icon: '▣', hotkey: '7' },
];

export default function SidebarNav({ view, onNavigate, netMtm }: Props) {
  return (
    <nav className="w-[148px] shrink-0 bg-panel border-r border-edge flex flex-col py-2">
      {ITEMS.map((it) => {
        const active = view === it.id;
        return (
          <button
            key={it.id}
            onClick={() => onNavigate(it.id)}
            className={`flex items-center gap-2.5 px-3.5 py-2.5 text-left border-l-2 transition-colors ${
              active
                ? 'border-qcyan bg-card text-white'
                : 'border-transparent text-gray-500 hover:text-gray-200 hover:bg-card/50'
            }`}
          >
            <span className={`text-[15px] ${active ? 'text-qcyan' : ''}`}>{it.icon}</span>
            <span className="flex-1">
              <span className="block text-[10.5px] font-bold leading-tight">{it.label}</span>
              <span className="block text-[9px] text-gray-600">Alt+{it.hotkey}</span>
            </span>
          </button>
        );
      })}
      <div className="mt-auto px-3.5 py-3 border-t border-edge">
        <div className="text-[9px] tracking-widest text-gray-600 mb-1">NET MTM</div>
        <div className={`text-[14px] font-extrabold ${netMtm >= 0 ? 'text-bull' : 'text-bear'}`}>
          {fmtINR(netMtm)}
        </div>
        <div className="text-[9px] text-gray-600 mt-1">PAPER · ₹1.00 Cr</div>
      </div>
    </nav>
  );
}
