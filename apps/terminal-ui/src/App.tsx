// OPEN-ATLAS — master application shell.
// Layout: header ticker / sidebar / routed views. Owns the market engine
// loop, the ₹1,00,00,000 institutional paper account, order execution with
// Percentage-of-notional margin blocking, live MTM marking and net portfolio Greeks.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  NetGreeks,
  NewOrder,
  Order,
  Position,
} from './types/market';
import { LOT_SIZES, RISK_FREE_RATE } from './types/market';
import { MarketEngine } from './engine/tickSimulator';
import { bsGreeks, bsPrice } from './engine/blackScholes';
import {
  applyFill,
  closePosition,
  markPositions,
  planFill,
  totalMarginUsed,
  totalMtm,
} from './engine/paperLedger';
import { TerminalContext, type OrderResult, type TerminalCtx, type ViewId } from './state/TerminalContext';
import HeaderTicker from './components/HeaderTicker';
import SidebarNav from './components/SidebarNav';
import CommandPalette, { type PaletteCommand } from './components/CommandPalette';
import CommandDeckView from './components/views/CommandDeckView';
import Surface3DView from './components/views/Surface3DView';
import OptionsChainView from './components/views/OptionsChainView';
import DarkPoolRadarView from './components/views/DarkPoolRadarView';
import QuantBacktesterView from './components/views/QuantBacktesterView';
import BrokerBridgeView from './components/views/BrokerBridgeView';
import RepoExplorer50kView from './components/views/RepoExplorer50kView';

const INITIAL_CASH = 1_00_00_000; // ₹1 crore institutional paper account

let orderSeq = 10482;
let posSeq = 1;

interface AccountState {
  cash: number;
  positions: Position[];
  orders: Order[];
}

export default function App() {
  const engineRef = useRef<MarketEngine | null>(null);
  if (!engineRef.current) engineRef.current = new MarketEngine(42);
  const engine = engineRef.current;

  const [view, setView] = useState<ViewId>('deck');
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [speed, setSpeedState] = useState(1);
  const [paused, setPaused] = useState(false);
  const [, setTick] = useState(0);
  const [account, setAccount] = useState<AccountState>({
    cash: INITIAL_CASH,
    positions: [],
    orders: [],
  });
  const accountRef = useRef(account);
  accountRef.current = account;

  // ── Engine loop: advance sim, refresh quotes, mark positions ────────────
  useEffect(() => {
    const id = window.setInterval(() => {
      engine.advance(0.25);
      setTick((t) => t + 1);
      setAccount((prev) => ({
        ...prev,
        positions: markPositions(prev.positions, engine.getQuotes()),
      }));
    }, 250);
    return () => window.clearInterval(id);
  }, [engine]);

  const quotes = engine.getQuotes();
  const simTime = engine.time;

  const setSpeed = useCallback(
    (n: number) => {
      engine.setSpeed(n);
      setSpeedState(n);
    },
    [engine],
  );

  const togglePause = useCallback(() => {
    setPaused((p) => {
      engine.setPaused(!p);
      return !p;
    });
  }, [engine]);

  // ── Order execution ──────────────────────────────────────────────────────
  const placeOrder = useCallback(
    (o: NewOrder): OrderResult => {
      // Validate before any accounting: a zero/negative/NaN quantity coming
      // from the order form must never reach margin or position math.
      if (!Number.isFinite(o.qty) || !Number.isInteger(o.qty) || o.qty <= 0) {
        return { ok: false, reason: 'Invalid quantity: must be a positive whole number' };
      }
      if (o.price != null && (!Number.isFinite(o.price) || o.price <= 0)) {
        return { ok: false, reason: 'Invalid limit price' };
      }
      const acct = accountRef.current;
      const qmap = new Map(engine.getQuotes().map((q) => [q.symbol, q]));
      const lot = o.instrumentType === 'OPTION' ? LOT_SIZES[o.underlying] ?? 25 : 1;

      let fill = o.price;
      if (fill == null) {
        if (o.instrumentType === 'OPTION' && o.strike != null && o.optionType) {
          const spot = qmap.get(o.underlying)?.price ?? 0;
          const dte = Math.max(
            (new Date(o.expiry ?? '').getTime() - Date.now()) / 86400000,
            0.02,
          );
          fill = bsPrice({
            S: spot,
            K: o.strike,
            T: dte / 365,
            r: RISK_FREE_RATE,
            sigma: o.iv ?? 0.13,
            type: o.optionType,
          });
        } else {
          fill = qmap.get(o.symbol)?.price ?? qmap.get(o.underlying)?.price ?? 0;
        }
      }
      if (!fill || fill <= 0) return { ok: false, reason: 'No live price for instrument' };

      // Close/open split + margin pricing live in the pure ledger module
      // (src/engine/paperLedger.ts), where they are unit-tested.
      const planned = planFill(acct, o, fill, lot);
      if (!planned.ok) return { ok: false, reason: planned.reason };
      const plan = planned.plan;

      const order: Order = {
        id: `OA${++orderSeq}`,
        symbol: o.symbol,
        side: o.side,
        qty: o.qty,
        price: fill,
        type: o.orderType,
        status: 'FILLED',
        timestamp: engine.time,
        marginUsed: plan.openingMargin,
        instrumentType: o.instrumentType,
        strike: o.strike,
        expiry: o.expiry,
        optionType: o.optionType,
      };

      setAccount((prev) => {
        const applied = applyFill(prev, o, fill, lot, plan, () => `POS${posSeq++}`);
        return {
          cash: applied.cash,
          positions: applied.positions,
          orders: [order, ...prev.orders].slice(0, 120),
        };
      });
      return { ok: true };
    },
    [engine],
  );

  const squareOff = useCallback(
    (id: string): OrderResult => {
      const p = accountRef.current.positions.find((x) => x.id === id);
      if (!p) return { ok: false, reason: 'Position not found' };
      setAccount((prev) => {
        const pos = prev.positions.find((x) => x.id === id);
        if (!pos) return prev;
        const closeOrder: Order = {
          id: `OA${++orderSeq}`,
          symbol: pos.symbol,
          side: pos.side === 'LONG' ? 'SELL' : 'BUY',
          qty: pos.qty,
          price: pos.ltp,
          type: 'MARKET',
          status: 'FILLED',
          timestamp: engine.time,
          marginUsed: 0,
          instrumentType: pos.instrumentType,
          strike: pos.strike,
          expiry: pos.expiry,
          optionType: pos.optionType,
        };
        const closed = closePosition(prev, pos);
        return {
          cash: closed.cash,
          positions: closed.positions,
          orders: [closeOrder, ...prev.orders].slice(0, 120),
        };
      });
      return { ok: true };
    },
    [engine],
  );

  const flattenAll = useCallback(() => {
    const ids = accountRef.current.positions.map((p) => p.id);
    ids.forEach((id) => squareOff(id));
  }, [squareOff]);

  // ── Derived: margin, MTM, net Greeks ────────────────────────────────────
  const marginBlocked = totalMarginUsed(account.positions);
  const mtm = totalMtm(account.positions);

  const netGreeks: NetGreeks = useMemo(() => {
    const qmap = new Map(quotes.map((q) => [q.symbol, q]));
    const g: NetGreeks = { delta: 0, gamma: 0, theta: 0, vega: 0 };
    for (const p of account.positions) {
      const dir = p.side === 'LONG' ? 1 : -1;
      if (p.instrumentType === 'OPTION' && p.strike != null && p.optionType) {
        const spot = qmap.get(p.underlying)?.price ?? 0;
        const dte = Math.max((new Date(p.expiry ?? '').getTime() - Date.now()) / 86400000, 0.02);
        const gr = bsGreeks({
          S: spot,
          K: p.strike,
          T: dte / 365,
          r: RISK_FREE_RATE,
          sigma: p.iv ?? 0.13,
          type: p.optionType,
        });
        const m = dir * p.qty * p.lotSize;
        g.delta += gr.delta * m;
        g.gamma += gr.gamma * m;
        g.theta += gr.theta * m;
        g.vega += gr.vega * m;
      } else {
        g.delta += dir * p.qty * p.lotSize;
      }
    }
    return g;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.positions, quotes[0]?.price, quotes[1]?.price]);

  const hedgeDelta = useCallback((): OrderResult => {
    const d = netGreeks.delta;
    if (Math.abs(d) < 1) return { ok: false, reason: 'Net delta already ≈ 0' };
    return placeOrder({
      symbol: 'NIFTY',
      underlying: 'NIFTY',
      side: d > 0 ? 'SELL' : 'BUY',
      qty: Math.round(Math.abs(d)),
      orderType: 'MARKET',
      instrumentType: 'EQUITY',
    });
  }, [netGreeks.delta, placeOrder]);

  // ── Keyboard: Ctrl+K palette ─────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const paletteCommands: PaletteCommand[] = useMemo(
    () => [
      { id: 'go-deck', title: 'Go: Command Deck', hint: 'view', run: () => setView('deck') },
      { id: 'go-surface', title: 'Go: 3D GEX / IV Surface', hint: 'view', run: () => setView('surface') },
      { id: 'go-chain', title: 'Go: Options Chain & Payoff Builder', hint: 'view', run: () => setView('chain') },
      { id: 'go-darkpool', title: 'Go: Dark Pool Radar', hint: 'view', run: () => setView('darkpool') },
      { id: 'go-backtest', title: 'Go: Quant Backtester', hint: 'view', run: () => setView('backtest') },
      { id: 'go-broker', title: 'Go: Broker Bridge', hint: 'view', run: () => setView('broker') },
      { id: 'go-repo', title: 'Go: Monorepo & 50k Generator', hint: 'view', run: () => setView('repo') },
      {
        id: 'buy-nifty',
        title: 'BUY 25 NIFTY @ Market',
        hint: 'order',
        run: () =>
          placeOrder({ symbol: 'NIFTY', underlying: 'NIFTY', side: 'BUY', qty: 25, orderType: 'MARKET', instrumentType: 'EQUITY' }),
      },
      {
        id: 'sell-nifty',
        title: 'SELL 25 NIFTY @ Market',
        hint: 'order',
        run: () =>
          placeOrder({ symbol: 'NIFTY', underlying: 'NIFTY', side: 'SELL', qty: 25, orderType: 'MARKET', instrumentType: 'EQUITY' }),
      },
      {
        id: 'buy-bn',
        title: 'BUY 15 BANKNIFTY @ Market',
        hint: 'order',
        run: () =>
          placeOrder({ symbol: 'BANKNIFTY', underlying: 'BANKNIFTY', side: 'BUY', qty: 15, orderType: 'MARKET', instrumentType: 'EQUITY' }),
      },
      { id: 'hedge', title: 'Hedge Net Delta → 0', hint: 'risk', run: () => hedgeDelta() },
      { id: 'flatten', title: 'Flatten All Positions (Kill Switch)', hint: 'risk', run: () => flattenAll() },
      { id: 'pause', title: paused ? 'Resume Market Simulator' : 'Pause Market Simulator', hint: 'sim', run: () => togglePause() },
      { id: 'speed-10', title: 'Set Simulator Speed 10x', hint: 'sim', run: () => setSpeed(10) },
      { id: 'speed-1', title: 'Set Simulator Speed 1x', hint: 'sim', run: () => setSpeed(1) },
    ],
    [placeOrder, hedgeDelta, flattenAll, paused, togglePause, setSpeed],
  );

  const ctx: TerminalCtx = {
    engine,
    quotes,
    simTime,
    speed,
    setSpeed,
    paused,
    togglePause,
    cash: account.cash,
    positions: account.positions,
    orders: account.orders,
    marginBlocked,
    mtm,
    netGreeks,
    placeOrder,
    squareOff,
    hedgeDelta,
    flattenAll,
    view,
    setView,
    paletteOpen,
    setPaletteOpen,
  };

  return (
    <TerminalContext.Provider value={ctx}>
      <div className="h-screen w-screen overflow-hidden flex flex-col bg-base text-gray-200 font-mono">
        <HeaderTicker
          quotes={quotes}
          simTime={simTime}
          speed={speed}
          onSpeed={setSpeed}
          paused={paused}
          onTogglePause={togglePause}
          onOpenPalette={() => setPaletteOpen(true)}
        />
        <div className="flex-1 flex min-h-0">
          <SidebarNav view={view} onNavigate={setView} netMtm={mtm} />
          <main className="flex-1 min-w-0 overflow-hidden bg-base">
            {view === 'deck' && <CommandDeckView />}
            {view === 'surface' && <Surface3DView />}
            {view === 'chain' && <OptionsChainView />}
            {view === 'darkpool' && <DarkPoolRadarView />}
            {view === 'backtest' && <QuantBacktesterView />}
            {view === 'broker' && <BrokerBridgeView />}
            {view === 'repo' && <RepoExplorer50kView />}
          </main>
        </div>
        <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={paletteCommands} />
      </div>
    </TerminalContext.Provider>
  );
}
