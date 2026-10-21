"""End-to-end: simulated upstream -> service -> bus -> fan-out -> client.

This is the P1 acceptance test (design section 8): the full pipeline
verified against a simulated upstream with no external anything.
"""

import asyncio
import json
import os

from websockets.asyncio.client import connect

from market_data.bus import InMemoryBus
from market_data.fanout import FanoutServer
from market_data.instruments import InstrumentRegistry
from market_data.normalizer import Normalizer
from market_data.schema import validate_event
from market_data.service import MarketDataService
from market_data.upstream import RecordedUpstream, SimulatedUpstream

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures",
                       "frames_sample.jsonl")
START_TS = 1792548900000


def build_service(tick_ms=50, stale_after_s=60.0):
    registry = InstrumentRegistry()
    upstream = SimulatedUpstream(registry, ["NIFTY"], tick_ms=tick_ms,
                                 seed=11, start_ts_ms=START_TS)
    bus = InMemoryBus()
    service = MarketDataService(upstream, Normalizer(registry, "SIMULATED"),
                                bus, stale_after_s=stale_after_s)
    return upstream, bus, service


async def collect_events(ws, seconds):
    """Collect every tick/depth event. seq is per-symbol across BOTH types
    (design section 3: tick 918273, depth 918274), so gaplessness is a
    property of the combined stream, not of ticks alone."""
    events = []

    async def _collect():
        while True:
            ev = json.loads(await ws.recv())
            if ev["type"] in ("tick", "depth"):
                events.append(ev)
    try:
        await asyncio.wait_for(_collect(), seconds)
    except asyncio.TimeoutError:
        pass
    return events


def test_pipeline_end_to_end_two_clients():
    async def go():
        upstream, bus, service = build_service()
        fanout = FanoutServer(bus, source="SIMULATED", port=0, heartbeat_s=30)
        port = await fanout.start()
        svc = asyncio.create_task(service.run())
        try:
            async with connect(f"ws://127.0.0.1:{port}") as ws1, \
                    connect(f"ws://127.0.0.1:{port}") as ws2:
                for ws in (ws1, ws2):
                    await ws.send(json.dumps(
                        {"action": "subscribe", "symbols": ["NIFTY"]}))
                t1, t2 = await asyncio.gather(collect_events(ws1, 1.5),
                                              collect_events(ws2, 1.5))
            # one upstream, many downstream: both clients got the feed
            ticks1 = [e for e in t1 if e["type"] == "tick"]
            assert len(ticks1) >= 5 and len(t2) >= 5
            seqs = [e["seq"] for e in t1]
            assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))  # gapless
            for t in ticks1:
                validate_event(t)
                assert t["source"] == "SIMULATED"  # honest provenance
                assert t["symbol"] == "NIFTY"
        finally:
            svc.cancel()
            await fanout.close()
    asyncio.run(go())


def test_recorded_fixture_replays_through_normalizer():
    async def go():
        registry = InstrumentRegistry()
        upstream = RecordedUpstream(FIXTURE, registry)
        normalizer = Normalizer(registry, upstream.source)
        per_symbol: dict[str, int] = {}
        total = 0
        async for raw in upstream.stream():
            for ev in normalizer.normalize(raw):
                if ev["type"] in ("tick", "depth"):
                    total += 1
                    per_symbol[ev["symbol"]] = ev["seq"]
        assert total == 40  # every fixture frame normalizes
        assert per_symbol["NIFTY"] >= 15 and per_symbol["BANKNIFTY"] >= 15
        assert normalizer.stats["dropped_malformed"] == 0
    asyncio.run(go())
