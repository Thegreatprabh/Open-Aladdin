// OPEN-ALADDIN — 60FPS market microstructure simulator.
// Geometric Brownian Motion + Merton jump-diffusion price paths, Level-2
// order book generation around spot, and a time & sales tape. Deterministic
// (seeded) so the terminal is reproducible.

import type { Candle, Level2Book, Quote, Tick } from '../types/market';

interface SymbolSpec {
  symbol: string;
  label: string;
  spot: number;
  sigma: number; // annualised vol
  tick: number; // minimum price increment
  jumpLambda: number; // jumps per year
  jumpMean: number;
  jumpSigma: number;
  drift: number; // annualised drift
  seedVol: number; // baseline tape volume
}

export const SYMBOL_SPECS: SymbolSpec[] = [
  { symbol: 'NIFTY', label: 'NIFTY 50', spot: 25742.35, sigma: 0.13, tick: 0.05, jumpLambda: 22, jumpMean: -0.001, jumpSigma: 0.006, drift: 0.04, seedVol: 4200 },
  { symbol: 'BANKNIFTY', label: 'BANKNIFTY', spot: 58216.8, sigma: 0.17, tick: 0.05, jumpLambda: 30, jumpMean: -0.0012, jumpSigma: 0.008, drift: 0.03, seedVol: 3100 },
  { symbol: 'INDIAVIX', label: 'INDIA VIX', spot: 13.86, sigma: 0.55, tick: 0.01, jumpLambda: 60, jumpMean: 0.01, jumpSigma: 0.05, drift: -0.05, seedVol: 900 },
  { symbol: 'SPX', label: 'S&P 500', spot: 6612.44, sigma: 0.14, tick: 0.25, jumpLambda: 18, jumpMean: -0.001, jumpSigma: 0.005, drift: 0.05, seedVol: 2800 },
  { symbol: 'NDX', label: 'NASDAQ 100', spot: 24318.9, sigma: 0.19, tick: 0.25, jumpLambda: 24, jumpMean: -0.001, jumpSigma: 0.007, drift: 0.06, seedVol: 2600 },
  { symbol: 'USDINR', label: 'USD/INR', spot: 88.42, sigma: 0.045, tick: 0.0025, jumpLambda: 8, jumpMean: 0, jumpSigma: 0.002, drift: 0.02, seedVol: 1500 },
  { symbol: 'GOLD', label: 'GOLD', spot: 7824.5, sigma: 0.16, tick: 0.1, jumpLambda: 20, jumpMean: 0.0005, jumpSigma: 0.006, drift: 0.05, seedVol: 1200 },
];

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface SimState {
  spec: SymbolSpec;
  price: number;
  open: number;
  high: number;
  low: number;
  prevPrice: number;
  volume: number;
  candles: Candle[];
}

export class MarketEngine {
  private states = new Map<string, SimState>();
  private tape: Tick[] = [];
  private rng: () => number;
  private speedMult = 1;
  private pausedFlag = false;
  private simTime: number;
  private tickCount = 0;

  constructor(seed = 42) {
    this.rng = mulberry32(seed);
    const start = new Date();
    start.setHours(9, 15, 0, 0);
    this.simTime = start.getTime();

    for (const spec of SYMBOL_SPECS) {
      const st: SimState = {
        spec,
        price: spec.spot,
        open: spec.spot,
        high: spec.spot,
        low: spec.spot,
        prevPrice: spec.spot,
        volume: 0,
        candles: [],
      };
      // Pre-seed 240 one-minute candles of history so charts render full.
      let p = spec.spot * (1 - 0.004 * this.rng());
      const t0 = this.simTime - 240 * 60000;
      for (let i = 0; i < 240; i++) {
        const dt = 1 / (252 * 375);
        const z = this.gauss();
        const open = p;
        p = Math.max(p * Math.exp((spec.drift - 0.5 * spec.sigma ** 2) * dt + spec.sigma * Math.sqrt(dt) * z), spec.tick);
        const v = spec.seedVol * (0.5 + this.rng());
        st.candles.push({
          t: t0 + i * 60000,
          o: open,
          h: Math.max(open, p) * (1 + 0.0006 * this.rng()),
          l: Math.min(open, p) * (1 - 0.0006 * this.rng()),
          c: p,
          v: Math.round(v),
        });
      }
      st.price = p;
      st.open = st.candles[0].o;
      st.high = Math.max(...st.candles.map((c) => c.h));
      st.low = Math.min(...st.candles.map((c) => c.l));
      this.states.set(spec.symbol, st);
    }
  }

  private gauss(): number {
    // Box-Muller
    let u = 0;
    let v = 0;
    while (u === 0) u = this.rng();
    while (v === 0) v = this.rng();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  setSpeed(mult: number): void {
    this.speedMult = mult;
  }
  get speed(): number {
    return this.speedMult;
  }
  setPaused(p: boolean): void {
    this.pausedFlag = p;
  }
  get paused(): boolean {
    return this.pausedFlag;
  }
  get time(): number {
    return this.simTime;
  }

  /** Advance simulation time by dtRealSeconds of wall-clock (scaled by speed). */
  advance(dtRealSeconds: number): void {
    if (this.pausedFlag) return;
    const dtSim = dtRealSeconds * this.speedMult;
    // 0.1-second sim substeps
    const steps = Math.max(1, Math.round(dtSim / 0.1));
    const stepDt = dtSim / steps;
    const yearFrac = stepDt / (252 * 6.25 * 3600); // trading-year fraction

    for (let s = 0; s < steps; s++) {
      this.simTime += stepDt * 1000;
      this.tickCount++;
      for (const st of this.states.values()) {
        const { spec } = st;
        st.prevPrice = st.price;
        const z = this.gauss();
        let ret = (spec.drift - 0.5 * spec.sigma ** 2) * yearFrac + spec.sigma * Math.sqrt(yearFrac) * z;
        // Merton jump component
        if (this.rng() < spec.jumpLambda * yearFrac) {
          ret += spec.jumpMean + spec.jumpSigma * this.gauss();
        }
        st.price = Math.max(st.price * Math.exp(ret), spec.tick);
        st.high = Math.max(st.high, st.price);
        st.low = Math.min(st.low, st.price);

        // Candle maintenance (1-minute buckets)
        const bucket = Math.floor(this.simTime / 60000) * 60000;
        const last = st.candles[st.candles.length - 1];
        const vol = Math.round(spec.seedVol * (0.4 + this.rng()) * stepDt);
        st.volume += vol;
        if (last && last.t === bucket) {
          last.c = st.price;
          last.h = Math.max(last.h, st.price);
          last.l = Math.min(last.l, st.price);
          last.v += vol;
        } else {
          st.candles.push({ t: bucket, o: st.price, h: st.price, l: st.price, c: st.price, v: vol });
          if (st.candles.length > 720) st.candles.splice(0, st.candles.length - 720);
        }

        // Tape: record trades for the two headline indices + random others
        if ((spec.symbol === 'NIFTY' || spec.symbol === 'BANKNIFTY' || this.rng() < 0.25) && this.rng() < 0.8) {
          this.tape.unshift({
            symbol: spec.symbol,
            price: st.price,
            qty: Math.max(1, Math.round(spec.seedVol * this.rng() * 0.4)),
            side: ret >= 0 ? 'BUY' : 'SELL',
            timestamp: this.simTime,
          });
          if (this.tape.length > 220) this.tape.length = 220;
        }
      }
    }
  }

  getQuotes(): Quote[] {
    return SYMBOL_SPECS.map((spec) => {
      const st = this.states.get(spec.symbol)!;
      const change = st.price - st.open;
      return {
        symbol: spec.symbol,
        label: spec.label,
        price: st.price,
        prevPrice: st.prevPrice,
        open: st.open,
        high: st.high,
        low: st.low,
        change,
        changePct: (change / st.open) * 100,
        volume: st.volume,
        timestamp: this.simTime,
      };
    });
  }

  getQuote(symbol: string): Quote | undefined {
    return this.getQuotes().find((q) => q.symbol === symbol);
  }

  getCandles(symbol: string, timeframe: string): Candle[] {
    const st = this.states.get(symbol);
    if (!st) return [];
    const base = st.candles;
    if (timeframe === '1m') return base.slice(-180);
    const multMap: Record<string, number> = { '5m': 5, '15m': 15, '1H': 60, '1D': 375 };
    const mult = multMap[timeframe] ?? 1;
    const out: Candle[] = [];
    for (let i = 0; i < base.length; i += mult) {
      const chunk = base.slice(i, i + mult);
      if (chunk.length === 0) continue;
      out.push({
        t: chunk[0].t,
        o: chunk[0].o,
        h: Math.max(...chunk.map((c) => c.h)),
        l: Math.min(...chunk.map((c) => c.l)),
        c: chunk[chunk.length - 1].c,
        v: chunk.reduce((a, c) => a + c.v, 0),
      });
    }
    return out.slice(-180);
  }

  /** Level-2 book: 10 levels each side, sizes decaying away from touch. */
  getBook(symbol: string, depth = 10): Level2Book {
    const st = this.states.get(symbol);
    if (!st) return { symbol, bids: [], asks: [], spread: 0, timestamp: this.simTime };
    const { spec } = st;
    const mid = st.price;
    const spreadTicks = spec.tick * (1 + Math.floor(this.rng() * 3));
    const bids = [];
    const asks = [];
    for (let i = 0; i < depth; i++) {
      const decay = 1 / (1 + i * 0.55);
      bids.push({
        price: Math.round((mid - spreadTicks / 2 - i * spec.tick * 2) / spec.tick) * spec.tick,
        qty: Math.round(spec.seedVol * decay * (0.6 + this.rng() * 0.9)),
        orders: 1 + Math.floor(this.rng() * 14 * decay) + 1,
      });
      asks.push({
        price: Math.round((mid + spreadTicks / 2 + i * spec.tick * 2) / spec.tick) * spec.tick,
        qty: Math.round(spec.seedVol * decay * (0.6 + this.rng() * 0.9)),
        orders: 1 + Math.floor(this.rng() * 14 * decay) + 1,
      });
    }
    return {
      symbol,
      bids,
      asks,
      spread: asks[0].price - bids[0].price,
      timestamp: this.simTime,
    };
  }

  getTape(symbol: string | null, n = 60): Tick[] {
    const src = symbol ? this.tape.filter((t) => t.symbol === symbol) : this.tape;
    return src.slice(0, n);
  }
}
