# OPEN-ATLAS Market-Data Service (AR-1 · Phase P1)

The backend streaming pipeline from
[`docs/LIVE_DATA_PIPELINE.md`](../../docs/LIVE_DATA_PIPELINE.md):
broker-shaped raw frames are ingested, normalized into canonical events,
published over Redis pub/sub with per-symbol snapshots, and fanned out to
browsers over a WebSocket. **Read-only by construction** — this service
contains no order-placement code and no order path runs through it.

```
 upstream ──► Normalizer ──► Bus ──► WS fan-out ──► Browser (P2: LiveProvider)
 (sim /       canonical      Redis     :9103
  recorded)   events         pub/sub
                             + snapshots
```

## Status — what P1 is, and is not

- **Implemented and tested (42 tests):** ingestion, normalizer, bus
  (Redis + in-memory implementations behind one interface), WebSocket
  fan-out, feed-health supervision (stale watchdog, reconnect, token
  expiry as a first-class status), and the Compose service `market-data`.
- **The P1 upstream is simulated/recorded.** Every event it produces is
  stamped `"source": "SIMULATED"` — provenance is assigned by the
  normalizer from the upstream adapter and is never upgraded downstream.
  Real broker frames (`"LIVE"`) arrive only in P3, which needs the
  account holder's Kite Connect subscription and credentials.
- The frontend does not consume this service yet — that is P2
  (`LiveProvider` behind `VITE_MARKET_DATA_WS`).

## Canonical events (the contract)

JSON, versioned (`"v": 1`). Full schema in the design doc, section 3;
`market_data/schema.py::validate_event` is its executable form and every
emitted event passes it.

- **tick** — symbol, instrumentToken, ltp/OHLC, volume, oi,
  `exchangeTs` (ISO 8601, +05:30 — the exchange clock, never the
  server's), `seq`, `source`.
- **depth** — 5-level bids/asks as `[price, qty]` pairs.
- **status** — `state`: `CONNECTED` · `RECONNECTING` · `STALE` ·
  `TOKEN_EXPIRED`, plus a human-readable `detail`.
- **heartbeat** — every 5 s from the fan-out, so a client can detect a
  dead socket in a quiet market.

`seq` is per-symbol and monotonically increasing **across tick and depth
together**. A gap means the client missed an event and should request a
snapshot (below) instead of rendering a silently incomplete book.

## Client protocol (for P2)

Connect to the fan-out WebSocket, then send JSON text frames:

```jsonc
{ "action": "subscribe",   "symbols": ["NIFTY", "BANKNIFTY"] }
{ "action": "unsubscribe", "symbols": ["NIFTY"] }
{ "action": "snapshot",    "symbol": "NIFTY" }   // sequence-gap resync
```

On subscribe you first receive the current snapshot (latest tick and
depth per symbol), then live events. Status events are broadcast to all
clients regardless of subscriptions.

## Run it

Local (no Docker, no Redis — in-memory bus, dev mode):

```bash
cd services/market-data
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
MD_TICK_MS=250 .venv/bin/python -m market_data     # ws://127.0.0.1:9103
```

Compose (Redis bus, the deployment shape):

```bash
docker compose up --build market-data              # ws://127.0.0.1:9103
```

Configuration is by environment: `MD_UPSTREAM` (`sim` | `recorded`),
`MD_FIXTURE`, `MD_SYMBOLS`, `MD_TICK_MS`, `MD_SEED`, `MD_REDIS_URL`,
`MD_WS_HOST`, `MD_WS_PORT`, `MD_STALE_S`. Without `MD_REDIS_URL` the
service runs on the in-memory bus and prints a loud DEV ONLY warning —
that mode is for development and tests, not deployment.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q     # 42 tests
```

Coverage: schema contract (the design doc's own example events),
instrument resolution, normalizer mapping/dedup/stale/malformed drops
with counters, both bus implementations against one contract (Redis via
fakeredis), fan-out protocol over a real socket (snapshot-then-live,
unsubscribe, heartbeat, resync), a two-client end-to-end run, recorded
fixture replay, and the chaos lifecycle — token expiry → `TOKEN_EXPIRED`
→ watchdog `STALE` → resume `CONNECTED`, plus injected garbage frames
never reaching clients.

Verified in this environment: full suite green; the service was also
smoke-run live (subscribe → ticks/depth streaming with canonical shape).
Not verified here: a real Redis server (no Redis in the dev environment
— the Redis bus is covered by fakeredis contract tests, which implement
the same wire semantics) and `docker compose up` (no Docker daemon in
the dev environment; the Compose file is structurally validated only).
