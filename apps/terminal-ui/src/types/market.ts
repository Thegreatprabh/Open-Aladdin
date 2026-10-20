// OPEN-ATLAS — strict market data & trading type definitions

export type Side = 'BUY' | 'SELL';

/**
 * Provenance of every market-data object. The terminal currently ships with
 * exactly one producer — the seeded simulator — so everything is stamped
 * SIMULATED. HISTORICAL and LIVE exist so a future authorized feed can be
 * integrated without ever letting its data masquerade as another mode.
 */
export type DataSource = 'SIMULATED' | 'HISTORICAL' | 'LIVE';
export type PositionSide = 'LONG' | 'SHORT';
export type OptionType = 'CE' | 'PE';
export type OrderType = 'MARKET' | 'LIMIT';
export type OrderStatus = 'FILLED' | 'PENDING' | 'CANCELLED' | 'REJECTED';
export type InstrumentType = 'EQUITY' | 'OPTION';

export interface Order {
  id: string;
  symbol: string;
  side: Side;
  qty: number;
  price: number;
  type: OrderType;
  status: OrderStatus;
  timestamp: number;
  marginUsed: number;
  instrumentType: InstrumentType;
  strike?: number;
  expiry?: string;
  optionType?: OptionType;
}

export interface BookLevel {
  price: number;
  qty: number;
  orders: number;
}

export interface Level2Book {
  symbol: string;
  source: DataSource;
  bids: BookLevel[];
  asks: BookLevel[];
  spread: number;
  timestamp: number;
}

export interface Tick {
  symbol: string;
  source: DataSource;
  price: number;
  qty: number;
  side: Side;
  timestamp: number;
}

export interface Quote {
  symbol: string;
  source: DataSource;
  label: string;
  price: number;
  prevPrice: number;
  open: number;
  high: number;
  low: number;
  change: number;
  changePct: number;
  volume: number;
  timestamp: number;
}

export interface Candle {
  source: DataSource;
  t: number; // epoch ms
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface Greeks {
  delta: number;
  gamma: number;
  theta: number; // per day
  vega: number; // per 1% vol
  rho: number; // per 1% rate
  vanna: number; // dDelta per 1% vol
  charm: number; // -dDelta per day
  vomma: number; // dVega per 1% vol
}

export interface OptionContract {
  symbol: string;
  underlying: string;
  strike: number;
  expiry: string; // ISO date
  optionType: OptionType;
  dte: number; // days to expiry
  iv: number; // decimal, e.g. 0.14
  bid: number;
  ask: number;
  ltp: number;
  oi: number;
  volume: number;
  greeks: Greeks;
}

export interface GexProfile {
  strikes: number[];
  callGex: number[]; // ₹ Cr per strike
  putGex: number[];
  netGex: number[];
  totalCallGex: number;
  totalPutGex: number;
  netGexTotal: number;
  zeroGammaLevel: number;
  callWall: number;
  putWall: number;
  spot: number;
}

export interface Position {
  id: string;
  symbol: string;
  underlying: string;
  side: PositionSide;
  qty: number; // contracts for options, shares for equity
  lotSize: number;
  avgPrice: number;
  ltp: number;
  instrumentType: InstrumentType;
  strike?: number;
  expiry?: string;
  optionType?: OptionType;
  iv?: number;
  mtm: number;
  pnl: number;
  marginUsed: number;
}

export interface NetGreeks {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
}

export interface NewOrder {
  symbol: string;
  underlying: string;
  side: Side;
  qty: number;
  price?: number;
  orderType: OrderType;
  instrumentType: InstrumentType;
  strike?: number;
  expiry?: string;
  optionType?: OptionType;
  iv?: number;
}

export interface BacktestResult {
  equityCurve: number[];
  drawdown: number[]; // negative fractions (0..-1)
  dates: string[];
  monthlyReturns: { month: string; ret: number }[];
  sharpe: number;
  sortino: number;
  calmar: number;
  maxDrawdown: number; // positive fraction
  totalReturn: number; // fraction
  cagr: number;
  winRate: number;
  trades: number;
  mc: {
    p5: number[];
    p25: number[];
    p50: number[];
    p75: number[];
    p95: number[];
  };
}

export type BrokerId = 'zerodha' | 'dhan' | 'angelone' | 'neostox';

/**
 * Broker connection lifecycle. CONNECTED is deliberately absent: the
 * browser bridge never completes a real broker handshake, so the only
 * reachable "up" state is SIMULATED. Real sessions belong to the backend
 * Python bridges (official login flow), never to this UI.
 */
export type BrokerConnState =
  | 'DISCONNECTED'
  | 'CONNECTING'
  | 'AUTHENTICATING'
  | 'SIMULATED'
  | 'ERROR'
  | 'DISCONNECTING';

export interface BrokerConfig {
  broker: BrokerId;
  name: string;
  apiKey: string;
  apiSecret: string;
  accessToken: string;
  wsUrl: string;
  state: BrokerConnState;
}

export const BROKER_META: Record<BrokerId, { name: string; wsUrl: string; restUrl: string }> = {
  zerodha: { name: 'Zerodha Kite Connect v3', wsUrl: 'wss://ws.kite.trade', restUrl: 'https://api.kite.trade' },
  dhan: { name: 'DhanHQ v2', wsUrl: 'wss://api-feed.dhan.co', restUrl: 'https://api.dhan.co/v2' },
  angelone: { name: 'Angel One SmartAPI', wsUrl: 'wss://smartapisocket.angelone.in', restUrl: 'https://apiconnect.angelbroking.com' },
  neostox: { name: 'NeoStox API', wsUrl: 'wss://ws.neostox.com', restUrl: 'https://api.neostox.com' },
};

export const LOT_SIZES: Record<string, number> = {
  NIFTY: 25,
  BANKNIFTY: 15,
};

export const RISK_FREE_RATE = 0.065;
