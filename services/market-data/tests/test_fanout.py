"""Fan-out server tests: real WebSocket server on an ephemeral port."""

import asyncio
import json

from websockets.asyncio.client import connect

from market_data.bus import InMemoryBus
from market_data.fanout import FanoutServer

TICK1 = {"v": 1, "type": "tick", "symbol": "NIFTY", "instrumentToken": 256265,
         "ltp": 100.0, "open": 99.0, "high": 101.0, "low": 98.0, "close": 97.0,
         "volume": 10, "oi": 0, "exchangeTs": "2026-10-20T09:15:03+05:30",
         "seq": 1, "source": "SIMULATED"}
TICK2 = dict(TICK1, ltp=100.5, seq=2)


async def recv_until(ws, pred, timeout=5.0):
    async def _search():
        while True:
            ev = json.loads(await ws.recv())
            if pred(ev):
                return ev
    return await asyncio.wait_for(_search(), timeout)


def run(coro):
    return asyncio.run(coro)


def test_subscribe_sends_snapshot_then_live():
    async def go():
        bus = InMemoryBus()
        await bus.set_snapshot("NIFTY", TICK1)
        server = FanoutServer(bus, source="SIMULATED", port=0, heartbeat_s=30)
        port = await server.start()
        try:
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps(
                    {"action": "subscribe", "symbols": ["NIFTY"]}))
                snap = await recv_until(ws, lambda e: e["type"] == "tick")
                assert snap["seq"] == 1  # snapshot first
                await bus.publish("NIFTY", TICK2)
                live = await recv_until(
                    ws, lambda e: e["type"] == "tick" and e["seq"] == 2)
                assert live["ltp"] == 100.5
        finally:
            await server.close()
    run(go())


def test_snapshot_action_resyncs():
    async def go():
        bus = InMemoryBus()
        server = FanoutServer(bus, source="SIMULATED", port=0, heartbeat_s=30)
        port = await server.start()
        try:
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps(
                    {"action": "subscribe", "symbols": ["NIFTY"]}))
                await asyncio.sleep(0.2)
                await bus.set_snapshot("NIFTY", TICK2)  # gap happened elsewhere
                await ws.send(json.dumps(
                    {"action": "snapshot", "symbol": "NIFTY"}))
                snap = await recv_until(
                    ws, lambda e: e["type"] == "tick" and e["seq"] == 2)
                assert snap["ltp"] == 100.5
        finally:
            await server.close()
    run(go())


def test_heartbeat_and_status_broadcast_reach_client():
    async def go():
        bus = InMemoryBus()
        server = FanoutServer(bus, source="SIMULATED", port=0, heartbeat_s=0.2)
        port = await server.start()
        try:
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                hb = await recv_until(ws, lambda e: e["type"] == "heartbeat")
                assert hb["source"] == "SIMULATED"
                await bus.broadcast_status(
                    {"v": 1, "type": "status", "state": "STALE",
                     "detail": "test", "source": "SIMULATED"})
                st = await recv_until(ws, lambda e: e["type"] == "status")
                assert st["state"] == "STALE"
        finally:
            await server.close()
    run(go())


def test_unsubscribe_stops_flow():
    async def go():
        bus = InMemoryBus()
        server = FanoutServer(bus, source="SIMULATED", port=0, heartbeat_s=30)
        port = await server.start()
        try:
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps(
                    {"action": "subscribe", "symbols": ["NIFTY"]}))
                await asyncio.sleep(0.3)
                await ws.send(json.dumps(
                    {"action": "unsubscribe", "symbols": ["NIFTY"]}))
                await asyncio.sleep(0.3)
                await bus.publish("NIFTY", TICK1)
                try:
                    ev = await asyncio.wait_for(ws.recv(), 0.6)
                    raise AssertionError(f"event after unsubscribe: {ev}")
                except asyncio.TimeoutError:
                    pass  # silence is the expected result
        finally:
            await server.close()
    run(go())
