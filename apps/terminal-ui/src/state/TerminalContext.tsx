import { createContext, useContext } from 'react';
import type { NetGreeks, NewOrder, Order, Position, Quote } from '../types/market';
import type { MarketEngine } from '../engine/tickSimulator';

export type ViewId = 'deck' | 'surface' | 'chain' | 'darkpool' | 'backtest' | 'broker' | 'repo';

export interface OrderResult {
  ok: boolean;
  reason?: string;
}

export interface TerminalCtx {
  engine: MarketEngine;
  quotes: Quote[];
  simTime: number;
  speed: number;
  setSpeed: (n: number) => void;
  paused: boolean;
  togglePause: () => void;
  cash: number;
  positions: Position[];
  orders: Order[];
  marginBlocked: number;
  mtm: number;
  netGreeks: NetGreeks;
  placeOrder: (o: NewOrder) => OrderResult;
  squareOff: (id: string) => OrderResult;
  hedgeDelta: () => OrderResult;
  flattenAll: () => void;
  view: ViewId;
  setView: (v: ViewId) => void;
  paletteOpen: boolean;
  setPaletteOpen: (b: boolean) => void;
}

export const TerminalContext = createContext<TerminalCtx | null>(null);

export function useTerminal(): TerminalCtx {
  const ctx = useContext(TerminalContext);
  if (!ctx) throw new Error('useTerminal must be used inside <App>');
  return ctx;
}
