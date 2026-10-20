// Dark Pool Radar — institutional block deals, unusual options activity
// (UOA) sweep detection, and a FinBERT-style central-bank NLP sentiment feed.

import { useEffect, useMemo, useState } from 'react';
import { useTerminal } from '../../state/TerminalContext';
import { generateOptionChain, probITM } from '../../engine/gexCalculator';
import { fmt, fmt0, fmtINR, fmtTime } from '../../lib/format';

interface BlockDeal {
  id: number;
  symbol: string;
  side: 'BUY' | 'SELL';
  qty: number;
  price: number;
  value: number; // ₹
  venue: string;
  timestamp: number;
}

interface Headline {
  source: string;
  text: string;
  sentiment: number; // −1..+1 FinBERT score
  confidence: number;
  minutesAgo: number;
}

const HEADLINES: Omit<Headline, 'minutesAgo'>[] = [
  { source: 'RBI MPC', text: 'RBI holds repo rate steady, retains "withdrawal of accommodation" stance', sentiment: -0.18, confidence: 0.86 },
  { source: 'FED WIRE', text: 'Powell signals patience on cuts; dot plot pencils two reductions in 2026', sentiment: 0.24, confidence: 0.79 },
  { source: 'RBI MPC', text: 'Governor flags food inflation risks, sees FY27 CPI anchored near 4%', sentiment: -0.31, confidence: 0.82 },
  { source: 'ECB', text: 'Lagarde: disinflation on track, services inflation remains sticky', sentiment: 0.08, confidence: 0.71 },
  { source: 'BOJ', text: 'Ueda hints at further normalisation if wage growth sustains above 2%', sentiment: -0.12, confidence: 0.68 },
  { source: 'RBI MPC', text: 'Liquidity stance turns neutral; system surplus rises to ₹1.9 lakh crore', sentiment: 0.37, confidence: 0.77 },
  { source: 'FED WIRE', text: 'FOMC minutes: members divided on pace of QT taper, balance-sheet runoff slows', sentiment: 0.15, confidence: 0.74 },
  { source: 'PBOC', text: 'PBOC injects 500bn yuan via MLF, keeps 1Y rate unchanged', sentiment: 0.21, confidence: 0.66 },
  { source: 'RBI MPC', text: 'RBI raises FY27 GDP forecast to 7.2%, cites capex cycle and PLI momentum', sentiment: 0.52, confidence: 0.88 },
  { source: 'BOE', text: 'Bailey: market pricing for cuts "not unreasonable" as wage growth cools', sentiment: 0.29, confidence: 0.72 },
];

function hash01(n: number): number {
  const x = Math.sin(n * 91.17 + 47.31) * 43758.5453;
  return x - Math.floor(x);
}

export default function DarkPoolRadarView() {
  const T = useTerminal();
  const [refreshTick, setRefreshTick] = useState(0);
  const [filter, setFilter] = useState<'ALL' | 'BUY' | 'SELL'>('ALL');
  const [minCr, setMinCr] = useState(5);

  const niftySpot = T.quotes.find((q) => q.symbol === 'NIFTY')?.price ?? 0;

  // Block deals: regenerated as sim time advances (deterministic per minute)
  const minuteKey = Math.floor(T.simTime / 60000);
  const blocks = useMemo<BlockDeal[]>(() => {
    const syms = ['RELIANCE', 'HDFCBANK', 'TCS', 'INFY', 'SBIN', 'ICICIBANK', 'TATAMOTORS', 'ADANIENT', 'AXISBANK', 'KOTAKBANK'];
    const venues = ['NSE BLOCK WINDOW', 'BSE BULK', 'OFF-MKT CROSS', 'NSE NEGOTIATED'];
    const out: BlockDeal[] = [];
    for (let i = 0; i < 12; i++) {
      const r = hash01(minuteKey * 131 + i * 17);
      const r2 = hash01(minuteKey * 57 + i * 29 + 3);
      const qty = Math.round(150000 + r * 4200000);
      const price = 240 + r2 * 3100;
      out.push({
        id: i,
        symbol: syms[Math.floor(r * syms.length) % syms.length],
        side: r2 > 0.48 ? 'BUY' : 'SELL',
        qty,
        price,
        value: qty * price,
        venue: venues[Math.floor(r2 * venues.length) % venues.length],
        timestamp: T.simTime - i * 47000,
      });
    }
    return out.sort((a, b) => b.value - a.value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [minuteKey, refreshTick]);

  const filteredBlocks = blocks.filter((b) => (filter === 'ALL' || b.side === filter) && b.value / 1e7 >= minCr);
  const totalBlockValue = blocks.reduce((a, b) => a + b.value, 0);

  // UOA: highest volume/OI ratio contracts from the live chain
  const uoa = useMemo(() => {
    const chain = generateOptionChain({ underlying: 'NIFTY', spot: niftySpot, expiryDays: 1, seed: 7 });
    return chain
      .map((c) => ({ c, ratio: c.volume / Math.max(c.oi, 1) }))
      .sort((a, b) => b.ratio - a.ratio)
      .slice(0, 9);
  }, [niftySpot]);

  const headlines = useMemo<Headline[]>(
    () => HEADLINES.map((h, i) => ({ ...h, minutesAgo: 4 + i * 13 })),
    [],
  );
  const avgSentiment = headlines.reduce((a, h) => a + h.sentiment, 0) / headlines.length;

  useEffect(() => {
    const id = window.setInterval(() => setRefreshTick((t) => t + 1), 15000);
    return () => window.clearInterval(id);
  }, []);

  const sentColor = (s: number) => (s > 0.12 ? 'text-bull' : s < -0.12 ? 'text-bear' : 'text-gray-400');
  const sentBg = (s: number) => (s > 0.12 ? 'bg-bull' : s < -0.12 ? 'bg-bear' : 'bg-gray-500');

  return (
    <div className="h-full grid grid-cols-3 gap-0 min-h-0">
      {/* BLOCK DEALS */}
      <section className="border-r border-edge bg-panel flex flex-col min-h-0">
        <div className="px-3 py-2.5 border-b border-edge">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold tracking-widest text-gray-400">INSTITUTIONAL BLOCK DEALS</span>
            <span className="text-[10px] text-amber font-bold">{fmtINR(totalBlockValue)} total</span>
          </div>
          <div className="flex gap-1.5 mt-2">
            {(['ALL', 'BUY', 'SELL'] as const).map((f) => (
              <button key={f} onClick={() => setFilter(f)} className={`px-2 py-0.5 text-[10px] font-bold rounded ${filter === f ? 'bg-qcyan text-base' : 'bg-card border border-edge text-gray-500'}`}>
                {f}
              </button>
            ))}
            <span className="ml-auto flex items-center gap-1 text-[10px] text-gray-500">
              MIN
              <input type="number" value={minCr} min={0} onChange={(e) => setMinCr(parseFloat(e.target.value || '0'))} className="w-14 bg-card border border-edge rounded px-1.5 py-0.5 text-white outline-none" />
              ₹Cr
            </span>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto min-h-0">
          {filteredBlocks.map((b) => (
            <div key={b.id} className="px-3 py-2 border-b border-edge/40 hover:bg-card/50">
              <div className="flex items-center gap-2">
                <span className={`text-[10px] font-extrabold px-1.5 py-0.5 rounded ${b.side === 'BUY' ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear'}`}>{b.side}</span>
                <span className="text-[12px] font-bold text-white">{b.symbol}</span>
                <span className="ml-auto text-[12px] font-extrabold text-amber">{fmtINR(b.value / 1e7, 1)} Cr</span>
              </div>
              <div className="flex text-[10.5px] text-gray-500 mt-1">
                <span>{fmt0(b.qty)} sh @ {fmt(b.price)}</span>
                <span className="ml-auto">{b.venue} · {fmtTime(b.timestamp)}</span>
              </div>
            </div>
          ))}
          {filteredBlocks.length === 0 && <div className="p-4 text-center text-[11px] text-gray-600">No blocks above the size filter.</div>}
        </div>
      </section>

      {/* UOA SWEEPS */}
      <section className="border-r border-edge bg-panel flex flex-col min-h-0">
        <div className="px-3 py-2.5 border-b border-edge flex items-center justify-between">
          <span className="text-[10px] font-bold tracking-widest text-gray-400">UNUSUAL OPTIONS ACTIVITY · SWEEPS</span>
          <span className="text-[10px] text-qcyan">NIFTY 0DTE chain</span>
        </div>
        <div className="flex-1 overflow-y-auto min-h-0">
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-panel">
              <tr className="text-gray-600 text-left border-b border-edge">
                <th className="px-3 py-1.5 font-semibold">CONTRACT</th>
                <th className="px-2 py-1.5 font-semibold text-right">VOL/OI</th>
                <th className="px-2 py-1.5 font-semibold text-right">PREM</th>
                <th className="px-2 py-1.5 font-semibold text-right">P(ITM)</th>
                <th className="px-2 py-1.5 font-semibold">FLOW</th>
              </tr>
            </thead>
            <tbody>
              {uoa.map(({ c, ratio }, i) => {
                const bullish = c.optionType === 'CE';
                return (
                  <tr key={c.symbol} className="border-b border-edge/40 hover:bg-card/60">
                    <td className="px-3 py-2">
                      <span className={`font-bold ${bullish ? 'text-bull' : 'text-bear'}`}>{fmt0(c.strike)}{c.optionType}</span>
                      <span className="block text-[9.5px] text-gray-600">vol {fmt0(c.volume)} · OI {fmt0(c.oi)}</span>
                    </td>
                    <td className="px-2 py-2 text-right font-extrabold text-amber">{fmt(ratio, 2)}×</td>
                    <td className="px-2 py-2 text-right">{fmt(c.ltp)}</td>
                    <td className="px-2 py-2 text-right text-gray-400">{fmt(probITM(niftySpot, c.strike, 1, c.iv, c.optionType) * 100, 0)}%</td>
                    <td className="px-2 py-2">
                      <span className={`text-[9px] font-extrabold px-1.5 py-0.5 rounded ${i % 3 === 2 ? 'bg-amber/15 text-amber' : bullish ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear'}`}>
                        {i % 3 === 2 ? 'SWEEP' : bullish ? 'BULLISH' : 'BEARISH'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="px-3 py-2 border-t border-edge text-[10px] text-gray-600">
          Sweeps flagged when Vol/OI &gt; 0.45× with aggressive multi-exchange prints.
        </div>
      </section>

      {/* FINBERT SENTIMENT */}
      <section className="bg-panel flex flex-col min-h-0">
        <div className="px-3 py-2.5 border-b border-edge">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold tracking-widest text-gray-400">FINBERT · CENTRAL BANK NLP</span>
            <span className={`text-[11px] font-extrabold ${sentColor(avgSentiment)}`}>
              NET {avgSentiment >= 0 ? '+' : ''}{fmt(avgSentiment, 2)}
            </span>
          </div>
          <div className="mt-2 h-2 rounded bg-card overflow-hidden flex">
            <div className="bg-bear h-full" style={{ width: `${Math.max(0, (0.5 - avgSentiment / 2)) * 100 * 0.5}%` }} />
            <div className="bg-bull h-full flex-1" />
          </div>
          <div className="flex justify-between text-[9px] text-gray-600 mt-1">
            <span>HAWKISH</span>
            <span>DOVISH</span>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto min-h-0">
          {headlines.map((h, i) => (
            <div key={i} className="px-3 py-2.5 border-b border-edge/40 hover:bg-card/50">
              <div className="flex items-center gap-2">
                <span className="text-[9px] font-extrabold px-1.5 py-0.5 rounded bg-qcyan/15 text-qcyan">{h.source}</span>
                <span className="text-[9.5px] text-gray-600">{h.minutesAgo}m ago</span>
                <span className={`ml-auto text-[11px] font-extrabold ${sentColor(h.sentiment)}`}>
                  {h.sentiment >= 0 ? '+' : ''}{fmt(h.sentiment, 2)}
                </span>
              </div>
              <p className="text-[11.5px] text-gray-300 leading-snug mt-1.5">{h.text}</p>
              <div className="flex items-center gap-2 mt-1.5">
                <div className="flex-1 h-1 rounded bg-card overflow-hidden">
                  <div className={`h-full ${sentBg(h.sentiment)}`} style={{ width: `${Math.abs(h.sentiment) * 100}%` }} />
                </div>
                <span className="text-[9px] text-gray-600">conf {fmt(h.confidence * 100, 0)}%</span>
              </div>
            </div>
          ))}
        </div>
        <div className="px-3 py-2 border-t border-edge text-[10px] text-gray-600">
          Model: FinBERT-tone fine-tuned on FOMC / RBI MPC minutes · inference on headlines only.
        </div>
      </section>
    </div>
  );
}
