// Ctrl+K instant command palette: fuzzy filter, keyboard navigation, execution.

import { useEffect, useMemo, useRef, useState } from 'react';

export interface PaletteCommand {
  id: string;
  title: string;
  hint: string;
  run: () => void;
}

interface Props {
  open: boolean;
  onClose: () => void;
  commands: PaletteCommand[];
}

export default function CommandPalette({ open, onClose, commands }: Props) {
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter(
      (c) => c.title.toLowerCase().includes(q) || c.hint.toLowerCase().includes(q) || c.id.includes(q),
    );
  }, [commands, query]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setIndex(0);
      setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [open ]);

  useEffect(() => setIndex(0), [query]);

  if (!open) return null;

  const exec = (c: PaletteCommand) => {
    onClose();
    c.run();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[14vh] bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-[560px] max-w-[92vw] bg-panel border border-edge rounded-lg shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setIndex((i) => Math.min(i + 1, filtered.length - 1));
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setIndex((i) => Math.max(i - 1, 0));
          } else if (e.key === 'Enter' && filtered[index]) {
            exec(filtered[index]);
          } else if (e.key === 'Escape') {
            onClose();
          }
        }}
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-edge">
          <span className="text-qcyan font-bold">›</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type a command — views, orders, risk…"
            className="flex-1 bg-transparent outline-none text-[13px] text-white placeholder-gray-600"
          />
          <kbd className="text-[9px] text-gray-600 border border-edge rounded px-1.5 py-0.5">ESC</kbd>
        </div>
        <div className="max-h-[320px] overflow-y-auto py-1">
          {filtered.length === 0 && (
            <div className="px-4 py-6 text-center text-[12px] text-gray-600">No matching command.</div>
          )}
          {filtered.map((c, i) => (
            <button
              key={c.id}
              onClick={() => exec(c)}
              onMouseEnter={() => setIndex(i)}
              className={`w-full flex items-center gap-3 px-4 py-2 text-left ${
                i === index ? 'bg-card text-white' : 'text-gray-400'
              }`}
            >
              <span className={`text-[9px] px-1.5 py-0.5 rounded border ${i === index ? 'border-qcyan text-qcyan' : 'border-edge text-gray-600'}`}>
                {c.hint.toUpperCase()}
              </span>
              <span className="text-[12.5px] font-medium flex-1">{c.title}</span>
              {i === index && <span className="text-qcyan text-[12px]">⏎</span>}
            </button>
          ))}
        </div>
        <div className="px-4 py-2 border-t border-edge text-[10px] text-gray-600 flex gap-4">
          <span>↑↓ navigate</span>
          <span>⏎ execute</span>
          <span className="ml-auto">OPEN-ATLAS command mode</span>
        </div>
      </div>
    </div>
  );
}
