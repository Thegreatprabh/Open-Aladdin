// Broker Bridge — broker API configuration + payload inspector.
//
// HONESTY CONTRACT (read before editing):
// * This view NEVER opens a real broker connection. No network call is made
//   from this file, and no credential entered here leaves the browser tab.
// * The only reachable "up" state is SIMULATED: a local, clearly-labelled
//   demo stream that mimics broker payload shapes for UI development.
// * The connection lifecycle is an explicit state machine:
//   DISCONNECTED -> CONNECTING -> AUTHENTICATING -> SIMULATED
//   (-> DISCONNECTING -> DISCONNECTED), with ERROR reachable from CONNECTING.
//   There is deliberately no CONNECTED state: a real authenticated session
//   only exists in the backend Python bridges (official broker login flow).
// * Simulated acknowledgements are generated locally and always marked
//   SIMULATED; their fields and their captions agree with each other.

import { useEffect, useRef, useState } from 'react';
import {
  BROKER_META,
  type BrokerConfig,
  type BrokerConnState,
  type BrokerId,
} from '../../types/market';
import { fmtTime } from '../../lib/format';

interface PayloadLine {
  id: number;
  ts: number;
  dir: 'IN' | 'OUT';
  body: string;
}

const INITIAL: Record<BrokerId, BrokerConfig> = {
  zerodha: { broker: 'zerodha', name: BROKER_META.zerodha.name, apiKey: '', apiSecret: '', accessToken: '', wsUrl: BROKER_META.zerodha.wsUrl, state: 'DISCONNECTED' },
  dhan: { broker: 'dhan', name: BROKER_META.dhan.name, apiKey: '', apiSecret: '', accessToken: '', wsUrl: BROKER_META.dhan.wsUrl, state: 'DISCONNECTED' },
  angelone: { broker: 'angelone', name: BROKER_META.angelone.name, apiKey: '', apiSecret: '', accessToken: '', wsUrl: BROKER_META.angelone.wsUrl, state: 'DISCONNECTED' },
  neostox: { broker: 'neostox', name: BROKER_META.neostox.name, apiKey: '', apiSecret: '', accessToken: '', wsUrl: BROKER_META.neostox.wsUrl, state: 'DISCONNECTED' },
};

function rnd(n: number): number {
  return Math.floor(Math.abs(Math.sin(n * 127.1) * 43758.5453) % 100000);
}

const STATE_LABEL: Record<BrokerConnState, string> = {
  DISCONNECTED: '○ OFFLINE',
  CONNECTING: '◌ CONNECTING',
  AUTHENTICATING: '◌ AUTHENTICATING',
  SIMULATED: '● SIMULATED',
  ERROR: '✗ ERROR',
  DISCONNECTING: '◌ CLOSING',
};

export default function BrokerBridgeView() {
  const [active, setActive] = useState<BrokerId>('zerodha');
  const [configs, setConfigs] = useState(INITIAL);
  const [feed, setFeed] = useState<PayloadLine[]>([]);
  const [feedPaused, setFeedPaused] = useState(false);
  const [handshake, setHandshake] = useState('');
  const feedId = useRef(0);
  const feedEndRef = useRef<HTMLDivElement>(null);
  const timers = useRef<number[]>([]);
  const cfg = configs[active];
  const simulated = cfg.state === 'SIMULATED';

  const push = (dir: 'IN' | 'OUT', body: string) => {
    setFeed((prev) => [...prev.slice(-160), { id: ++feedId.current, ts: Date.now(), dir, body }]);
  };

  // Simulated payload stream — local demo data only, runs solely in the
  // SIMULATED state and every line is rendered with a SIM tag.
  useEffect(() => {
    if (!simulated || feedPaused) return;
    const id = window.setInterval(() => {
      const n = feedId.current;
      const base = active === 'zerodha' ? 256265 : active === 'dhan' ? 1333 : active === 'angelone' ? 99926009 : 26000;
      const kind = n % 4;
      if (kind === 0) {
        push('IN', JSON.stringify({ t: 'ck', s: 'ok', v: '3.0.0', client_id: `WS-${rnd(n)}` }));
      } else if (kind === 1) {
        push('IN', JSON.stringify({ t: 'tf', tk: String(base), ltpc: (25700 + rnd(n) % 900).toFixed(2), fts: Date.now() }));
      } else if (kind === 2) {
        push('IN', JSON.stringify({ type: 'order_update', order_id: `SIM-${10482 + (n % 40)}`, status: 'COMPLETE', filled_qty: 25, avg_price: (240 + rnd(n) % 300).toFixed(1) }));
      } else {
        push('IN', JSON.stringify({ t: 'depth', tk: String(base), bids: [[(25690 + rnd(n) % 40).toFixed(2), 180], [(25685 + rnd(n) % 30).toFixed(2), 240]], asks: [[(25710 + rnd(n) % 40).toFixed(2), 160]] }));
      }
    }, 900);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simulated, feedPaused, active]);

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [feed]);

  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);

  const update = (patch: Partial<BrokerConfig>, broker: BrokerId = active) => {
    setConfigs((prev) => ({ ...prev, [broker]: { ...prev[broker], ...patch } }));
  };

  const later = (fn: () => void, ms: number) => {
    timers.current.push(window.setTimeout(fn, ms));
  };

  // Starts the *simulated* session. The stages mirror what a real handshake
  // would do, but no socket is opened and no credential is transmitted.
  const connect = () => {
    const broker = active;
    if (!cfg.apiKey.trim()) {
      update({ state: 'ERROR' }, broker);
      setHandshake('✗ Simulation needs a placeholder API key (any value). Nothing is sent anywhere — a real key belongs in the backend bridge, not the browser.');
      return;
    }
    update({ state: 'CONNECTING' }, broker);
    setHandshake(`Simulating handshake with ${cfg.wsUrl} … (local only — no network connection is made)`);
    push('OUT', JSON.stringify({ t: 'connect', url: cfg.wsUrl, api_key: `${cfg.apiKey.slice(0, 4)}••••`, mode: 'full', simulated: true }));
    later(() => {
      update({ state: 'AUTHENTICATING' }, broker);
      setHandshake('Simulating authentication … (no credentials leave this tab)');
      later(() => {
        update({ state: 'SIMULATED' }, broker);
        setHandshake(`◉ SIMULATED session — ${cfg.name}. Payloads below are generated locally for UI development. This is NOT a live broker connection.`);
        push('IN', JSON.stringify({ t: 'ck', s: 'ok', simulated: true }));
      }, 450);
    }, 450);
  };

  const disconnect = () => {
    const broker = active;
    update({ state: 'DISCONNECTING' }, broker);
    push('OUT', JSON.stringify({ t: 'disconnect', simulated: true }));
    later(() => {
      update({ state: 'DISCONNECTED' }, broker);
      setHandshake('Simulated session closed.');
    }, 250);
  };

  const switchBroker = (b: BrokerId) => {
    setActive(b);
    // Each broker has its own simulated stream: never carry one broker's
    // payloads into another broker's inspector.
    setFeed([]);
    setHandshake('');
  };

  const sendTestOrder = () => {
    if (!simulated) {
      setHandshake('✗ Start the simulated session first — test order not sent.');
      return;
    }
    const fixBody = '8=FIX.4.4|9=178|35=D|49=CLIENT|56=BROKER|34=2|52=' + new Date().toISOString() + '|11=OA-TEST|55=NIFTY|54=1|38=25|40=2|44=25740.00|59=0|10=128|';
    push('OUT', fixBody);
    later(() => {
      // Locally fabricated demo acknowledgement — labelled as such, and the
      // FIX fields agree with the caption (150=2 / 39=2 = Fill).
      push('IN', '8=FIX.4.4|35=8|39=2|150=2|14=25|151=0|32=25|31=25740.00|6=25740.00|37=SIM-ORD-88412|11=OA-TEST|58=SIMULATED|10=091|');
      setHandshake('◉ Simulated acknowledgement — generated locally, no broker received this order. (ExecReport 35=8, OrdStatus 39=2 Fill, marked SIMULATED in tag 58.)');
    }, 420);
  };

  const field = (label: string, value: string, onChange: (v: string) => void, secret = false) => (
    <div>
      <label className="block text-[9px] tracking-widest text-gray-600 mb-1">{label}</label>
      <input
        type={secret ? 'password' : 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
        className="w-full bg-card border border-edge rounded px-2.5 py-2 text-[12px] text-white outline-none focus:border-qcyan"
        placeholder={secret ? '••••••••••••' : ''}
      />
    </div>
  );

  return (
    <div className="h-full flex min-h-0">
      {/* CONFIG */}
      <div className="w-[400px] shrink-0 border-r border-edge bg-panel flex flex-col min-h-0 overflow-y-auto">
        <div className="grid grid-cols-4 border-b border-edge">
          {(Object.keys(BROKER_META) as BrokerId[]).map((b) => (
            <button
              key={b}
              onClick={() => switchBroker(b)}
              className={`py-2.5 text-[10.5px] font-bold border-b-2 ${
                active === b ? 'border-amber text-white bg-card' : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              {b === 'zerodha' ? 'ZERODHA' : b === 'dhan' ? 'DHAN' : b === 'angelone' ? 'ANGEL' : 'NEOSTOX'}
              <span className={`block text-[8px] mt-0.5 ${configs[b].state === 'SIMULATED' ? 'text-amber' : 'text-gray-700'}`}>
                {STATE_LABEL[configs[b].state]}
              </span>
            </button>
          ))}
        </div>
        <div className="p-4 space-y-3">
          <div className="text-[12px] font-bold text-white">{cfg.name}</div>
          <div className="text-[10.5px] text-gray-500">REST: {BROKER_META[active].restUrl}</div>
          <div className="text-[10.5px] leading-relaxed border border-amber/40 text-amber bg-amber/5 rounded px-3 py-2">
            This panel is a <b>simulation sandbox</b>. Nothing typed here is transmitted or stored.
            Real broker sessions run only through the backend Python bridges
            (quant-engine/brokers/) using each broker's official login flow.
          </div>
          {field('API KEY (placeholder — never transmitted)', cfg.apiKey, (v) => update({ apiKey: v }))}
          {field('API SECRET (unused in simulation)', cfg.apiSecret, (v) => update({ apiSecret: v }), true)}
          {field('ACCESS TOKEN (unused in simulation)', cfg.accessToken, (v) => update({ accessToken: v }), true)}
          {field('WEBSOCKET URL (display only)', cfg.wsUrl, (v) => update({ wsUrl: v }))}
          <div className="flex gap-2 pt-1">
            {!simulated ? (
              <button onClick={connect} className="flex-1 py-2.5 bg-amber text-base font-extrabold text-[12px] rounded hover:brightness-110">
                START SIMULATED SESSION
              </button>
            ) : (
              <button onClick={disconnect} className="flex-1 py-2.5 bg-bear text-white font-extrabold text-[12px] rounded hover:brightness-110">
                STOP SIMULATION
              </button>
            )}
            <button onClick={sendTestOrder} className="px-4 py-2.5 bg-card border border-amber text-amber font-bold text-[11px] rounded">
              SIM FIX TEST ORDER
            </button>
          </div>
          {handshake && (
            <div className={`text-[11px] leading-relaxed border rounded px-3 py-2 ${handshake.startsWith('◉') ? 'border-amber/40 text-amber bg-amber/5' : handshake.startsWith('✗') ? 'border-bear/40 text-bear bg-bear/5' : 'border-edge text-gray-400'}`}>
              {handshake}
            </div>
          )}
          <div className="text-[10.5px] text-gray-600 leading-relaxed border-t border-edge pt-3">
            Credentials stay in this tab's memory only — never written to localStorage, never
            sent over the network by this UI, and masked in every payload line. Payload
            shapes mimic Kite Connect v3, DhanHQ v2, SmartAPI and NeoStox so the inspector
            can be developed against realistic examples.
          </div>
          <div className="grid grid-cols-3 gap-2 text-center">
            {[
              ['STATE', STATE_LABEL[cfg.state].replace(/^[●○◌✗] /, '')],
              ['MODE', 'SIMULATION'],
              ['NETWORK', 'NONE'],
            ].map(([l, v]) => (
              <div key={l} className="bg-card border border-edge rounded py-2">
                <div className="text-[8.5px] tracking-widest text-gray-600">{l}</div>
                <div className="text-[10.5px] font-bold text-white mt-0.5">{v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* PAYLOAD INSPECTOR */}
      <div className="flex-1 flex flex-col min-w-0 bg-base">
        <div className="px-4 py-2.5 border-b border-edge bg-panel flex items-center gap-3 shrink-0">
          <span className="text-[10px] font-bold tracking-widest text-gray-400">WEBSOCKET PAYLOAD INSPECTOR — SIMULATED FEED</span>
          <span className={`text-[10px] font-bold ${simulated ? 'text-amber' : 'text-gray-600'}`}>
            {simulated ? `● SIMULATING — ${BROKER_META[active].name} (local demo data)` : '○ idle'}
          </span>
          <span className="ml-auto flex gap-2">
            <button onClick={() => setFeedPaused((v) => !v)} className={`px-2 py-1 text-[10px] font-bold rounded border ${feedPaused ? 'border-amber text-amber' : 'border-edge text-gray-400'}`}>
              {feedPaused ? 'RESUME FEED' : 'PAUSE FEED'}
            </button>
            <button onClick={() => setFeed([])} className="px-2 py-1 text-[10px] font-bold rounded border border-edge text-gray-400 hover:text-bear">
              CLEAR
            </button>
          </span>
        </div>
        <div className="flex-1 overflow-y-auto min-h-0 p-3 space-y-1.5">
          {feed.length === 0 && (
            <div className="text-[11.5px] text-gray-600 text-center pt-16">
              No payloads yet. Enter any placeholder API key and START SIMULATED SESSION to run the
              local demo stream, or fire a SIM FIX TEST ORDER.
            </div>
          )}
          {feed.map((l) => (
            <div key={l.id} className="flex gap-2 items-start text-[11px]">
              <span className="text-gray-700 shrink-0">{fmtTime(l.ts)}</span>
              <span className={`shrink-0 font-extrabold ${l.dir === 'IN' ? 'text-qcyan' : 'text-amber'}`}>{l.dir === 'IN' ? '◀ IN ' : '▶ OUT'}</span>
              <span className="shrink-0 mt-0.5 px-1 rounded bg-amber/20 text-amber text-[8px] font-extrabold">SIM</span>
              <code className="break-all text-gray-300 bg-panel border border-edge/60 rounded px-2 py-1 flex-1">{l.body}</code>
            </div>
          ))}
          <div ref={feedEndRef} />
        </div>
        <div className="px-4 py-2 border-t border-edge text-[10px] text-gray-600 shrink-0">
          Every payload on this page is generated locally by the simulator — no broker traffic exists here.
        </div>
      </div>
    </div>
  );
}
