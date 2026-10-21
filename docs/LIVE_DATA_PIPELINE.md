# Live Market Data Pipeline — Design (AR-1)

Status: **P1 implemented (2026-10-21)** — ingestion + normalizer + bus +
WebSocket fan-out against a simulated/recorded upstream, 42 tests, Compose
service `market-data` (see `services/market-data/`). The terminal still
consumes only its in-browser simulator until P2 lands the frontend
`LiveProvider`; P3 (real KiteTicker) and P4 (Dhan parity, entitlements)
remain pending. This document specifies the backend streaming system that
replaces the simulator as the data source, what already exists to build on, and
the phased rollout. Nothing here is claimed as built until the corresponding
phase lands with tests.

## 1. What exists today (verified)

| Piece | State |
|---|---|
| In-browser simulator (`MarketEngine`) | The only data source the UI consumes |
| Provenance model (`DataSource = 'LIVE' \| 'SIMULATED'`) + `source` stamps on quotes/candles/ticks/book | Built and shown in the UI (SIMULATED / STALE badge) |
| Stale detection (`isStale`, 5 s at 1×) | Built, unit-tested |
| Python broker bridges (Zerodha/Dhan) | Real REST order paths (mock-tested); market-data side is a frame generator (`KiteTickerSim`), **not** a real socket |
| FIX gateway | Real order path over TCP; unrelated to market data |

## 2. Target architecture

```
 Zerodha KiteTicker (wss) ──┐
                            ├──► Ingestion service ──► Normalizer ──► Redis ──► WS fan-out ──► Browser
 Dhan feed (wss) ───────────┘    (services/market-     (canonical        (Streams/    (same svc)     LiveFeedClient
                                  data/, Python)        events)           pub-sub)                    │ mode: LIVE
                                                                                                      ▼
                                                                                          existing views, unchanged
```

Design choices, and why:

- **A dedicated ingestion service, not the browser.** Broker sockets require
  the deployment's API credentials and a long-lived connection. Credentials
  must never ship in a browser bundle — the same rule the broker bridges
  already follow. The service lives beside the quant engine in Compose.
- **Redis as the bus.** Compose already runs Redis. Streams give replay for
  reconnecting fan-out nodes; pub/sub is enough for v1 — the design uses
  pub/sub channels per instrument plus a snapshot key per instrument.
- **The browser speaks only to the fan-out WebSocket**, with the deployment's
  own session/edge auth (see §5). Broker protocol details (binary packets,
  instrument tokens) stop at the normalizer.
- **Views do not change.** The terminal consumes a provider interface; the
  simulator implements it today and `LiveProvider` implements it tomorrow.

## 3. Canonical event schema (JSON, versioned)

```jsonc
// tick — one trade/quotes update
{ "v": 1, "type": "tick", "symbol": "NIFTY", "instrumentToken": 256265,
  "ltp": 25796.51, "open": 25680.0, "high": 25810.2, "low": 25650.0,
  "close": 25666.3, "volume": 18234500, "oi": 0,
  "exchangeTs": "2026-10-20T09:15:03+05:30", "seq": 918273,
  "source": "LIVE" }

// depth — level-2 snapshot (5/20 levels as the feed provides)
{ "v": 1, "type": "depth", "symbol": "NIFTY", "bids": [[25796.45, 5079]],
  "asks": [[25797.50, 507]], "exchangeTs": "…", "seq": 918274, "source": "LIVE" }

// status — feed health, drives the existing LIVE/STALE badge logic
{ "v": 1, "type": "status", "state": "CONNECTED" | "RECONNECTING" | "STALE",
  "detail": "kite socket reconnected, attempt 2" }

// heartbeat every 5 s from the fan-out so the client can detect a dead
// socket even in a quiet market; the client's staleness rule is unchanged.
```

Rules: every event carries `source` and a monotonically increasing `seq` per
symbol; `exchangeTs` is the exchange timestamp, never the server's clock.
Candles are aggregated **client-side** from ticks for v1 (the views already
aggregate sim ticks the same way), keeping the server stateless per viewer.

## 4. Client: `LiveFeedClient` (terminal-ui)

- Connects to `VITE_MARKET_DATA_WS` (a real WebSocket URL this time — set by
  deployment, validated at boot; absent = LIVE mode is not offered in the UI).
- Subscribes to the instruments the current view needs; unsubscribes on view
  change. Reconnect: exponential backoff 1 s → 30 s with jitter, full
  re-subscribe, then a **snapshot resync** per symbol.
- Sequence gaps: if `seq` jumps, request a snapshot for that symbol instead of
  rendering a silently-incomplete book.
- Staleness: the existing `isStale` rule drives the header badge — LIVE when
  fresh, STALE when the feed ages, SIMULATED when in simulator mode. The mode
  switch is explicit and visible; the app never silently falls back from LIVE
  to simulated numbers without changing the badge.
- Provider interface: `LiveProvider` exposes the same surface the views use
  from `MarketEngine` today (`getQuotes`, tick stream, candles). Order entry
  stays on the paper ledger regardless of data mode — **live prices never
  imply live orders**; order routing is the broker bridges' separate concern.

## 5. Security boundaries

- Broker API key/secret and access tokens exist only in the ingestion
  service's environment (Compose secrets / `.env`, never committed).
- The fan-out requires the deployment's edge auth (same session mechanism the
  future account service in AR-2 provides). Until AR-2 exists, the pipeline is
  **single-operator**: one deployment, one broker account, read-only data —
  and the README/Register must keep saying so.
- No order path runs through the data pipeline. It is read-only by
  construction: the service holds no order-placement code.

## 6. Operational realities (the unglamorous parts)

- **Kite access tokens expire daily.** The service needs the daily
  request-token → access-token exchange; unattended renewal requires the
  account holder's login flow (and must respect the broker's API terms).
  The design treats "token expired" as a first-class status event.
- **Instrument mapping:** symbols ↔ broker instrument tokens come from the
  broker's daily instruments dump; the service caches it and resolves
  subscriptions through it.
- **Broker socket limits** (max instruments per connection, connections per
  API key) shape sharding: v1 targets the terminal's watchlist scale
  (tens of instruments), not full-market capture.
- **Clock discipline:** staleness uses exchange timestamps; server clock skew
  must not mark a healthy feed stale — heartbeat + server receipt time are
  carried alongside for diagnostics.

## 7. Testing strategy

- **Recorded fixtures:** capture real (or realistically-shaped) broker frames
  once, replay them in CI — ingestion, normalization, and fan-out are tested
  without live credentials.
- **Contract tests:** the JSON schema in §3 is the contract; the frontend's
  `LiveProvider` is tested against the same fixtures.
- **Chaos cases:** socket drop mid-stream, duplicate ticks, out-of-order
  seq, token expiry at open — each maps to a status/badge behavior, asserted
  in tests, not hoped for.
- **Load sanity:** fan-out to N idle clients must not multiply broker
  connections (one upstream socket, many downstream).

## 8. Phased rollout

| Phase | Deliverable | Needs |
|---|---|---|
| P1 | ✅ **Landed 2026-10-21** — ingestion + normalizer + fan-out, verified end-to-end against recorded/simulated upstream; Compose service `market-data`; 42 tests | Nothing external |
| P2 | Frontend `LiveProvider` + mode switch behind `VITE_MARKET_DATA_WS`; LIVE badge path live in the UI against the P1 service | P1 |
| P3 | Real KiteTicker upstream in the operator's deployment | Broker API subscription + credentials, daily token flow (account holder) |
| P4 | Dhan feed parity; per-user data entitlements | AR-2 account service |

## 9. What is deliberately out of scope

- Live order routing through the terminal UI (bridges stay a separate,
  server-side concern).
- Historical/tick storage (TimescaleDB is in Compose but candle persistence
  is a separate design).
- Multi-tenant credential handling — that is AR-2's design, which this
  pipeline consumes but does not replace.
