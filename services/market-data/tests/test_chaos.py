"""Chaos cases from design section 7, each asserted — not hoped for."""

import asyncio
import json

from websockets.asyncio.client import connect

from market_data.bus import InMemoryBus
from market_data.fanout import FanoutServer
from market_data.instruments import InstrumentRegistry
from market_data.normalizer import Normalizer
from market_data.service import MarketDataService
from market_data.upstream import SimulatedUpstream

START_TS = 1792548900000


async def recv_until(ws, pred, timeout=6.0):
    async def _search():
        while True:
            ev = json.loads(await ws.recv())
            if pred(ev):
                return ev
    return await asyncio.wait_for(_search(), timeout)


def test_feed_health_lifecycle_expiry_stale_resume():
    """token expiry -> TOKEN_EXPIRED, silence -> STALE, resume -> CONNECTED."""
    async def go():
        registry = InstrumentRegistry()
        upstream = SimulatedUpstream(registry, ["NIFTY"], tick_ms=50,
                                     seed=3, start_ts_ms=START_TS)
        bus = InMemoryBus()
        service = MarketDataService(upstream, Normalizer(registry, "SIMULATED"),
                                    bus, stale_after_s=0.6)
        fanout = FanoutServer(bus, source="SIMULATED", port=0, heartbeat_s=30)
        port = await fanout.start()
        svc = asyncio.create_task(service.run())
        try:
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps(
                    {"action": "subscribe", "symbols": ["NIFTY"]}))
                tick = await recv_until(ws, lambda e: e["type"] == "tick")
                assert tick["seq"] >= 1
                await upstream.expire_token()
                ev = await recv_until(
                    ws, lambda e: e["type"] == "status"
                    and e["state"] == "TOKEN_EXPIRED")
                assert "expired" in ev["detail"]
                ev = await recv_until(
                    ws, lambda e: e["type"] == "status"
                    and e["state"] == "STALE")
                assert ev["state"] == "STALE"  # watchdog noticed the silence
                upstream.resume()
                ev = await recv_until(
                    ws, lambda e: e["type"] == "status"
                    and e["state"] == "CONNECTED")
                assert ev["state"] == "CONNECTED"
                tick = await recv_until(ws, lambda e: e["type"] == "tick")
                assert tick["seq"] > 1  # stream resumed, seq kept counting
        finally:
            svc.cancel()
            await fanout.close()
    asyncio.run(go())


def test_injected_garbage_never_reaches_clients():
    """Duplicate/stale/malformed frames are dropped at the normalizer;
    client-visible seq stays gapless (design section 7 chaos cases)."""
    async def go():
        registry = InstrumentRegistry()
        upstream = SimulatedUpstream(registry, ["NIFTY"], tick_ms=50,
                                     seed=5, start_ts_ms=START_TS)
        bus = InMemoryBus()
        normalizer = Normalizer(registry, "SIMULATED")
        service = MarketDataService(upstream, normalizer, bus,
                                    stale_after_s=60.0)
        fanout = FanoutServer(bus, source="SIMULATED", port=0, heartbeat_s=30)
        port = await fanout.start()
        svc = asyncio.create_task(service.run())
        try:
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps(
                    {"action": "subscribe", "symbols": ["NIFTY"]}))
                first = await recv_until(ws, lambda e: e["type"] == "tick")
                token = registry.token_for("NIFTY")
                # duplicate of an ancient frame, a stale frame, and junk
                await upstream.inject_raw(
                    {"tk": token, "ltp": 1.0, "o": 1, "h": 1, "l": 1, "c": 1,
                     "vol": 1, "ts": START_TS, "fid": 1})
                await upstream.inject_raw({"nonsense": True})
                events = [first]
                try:
                    while len(events) < 6:
                        ev = json.loads(await asyncio.wait_for(ws.recv(), 4))
                        if ev["type"] in ("tick", "depth"):
                            events.append(ev)
                except asyncio.TimeoutError:
                    pass
                seqs = [t["seq"] for t in events]  # seq spans tick+depth
                assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
                assert normalizer.stats["dropped_malformed"] >= 1
                assert (normalizer.stats["dropped_duplicate"]
                        + normalizer.stats["dropped_stale"]) >= 1
        finally:
            svc.cancel()
            await fanout.close()
    asyncio.run(go())
