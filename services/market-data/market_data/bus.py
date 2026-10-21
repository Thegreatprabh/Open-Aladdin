"""Event bus: the seam between ingestion and fan-out (design section 2).

Two implementations behind one async interface:
  - RedisBus — production path. Pub/sub channel per symbol
    (md:events:<SYMBOL>), a status channel (md:status), and one snapshot
    key per symbol+kind (md:snap:<SYMBOL>:<tick|depth>).
  - InMemoryBus — dev/tests and single-process runs without Redis. Same
    semantics, no external dependency.

Snapshots are the latest tick and latest depth per symbol; a subscribing
client always receives the snapshot before live events (fanout.py), and
requests a fresh snapshot when it detects a sequence gap.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator


class Bus:
    """Interface. Events are canonical dicts (schema.py)."""

    async def publish(self, symbol: str, event: dict) -> None:
        raise NotImplementedError

    async def set_snapshot(self, symbol: str, event: dict) -> None:
        raise NotImplementedError

    async def get_snapshots(self, symbol: str) -> list[dict]:
        raise NotImplementedError

    async def broadcast_status(self, event: dict) -> None:
        raise NotImplementedError

    def subscribe(self, symbol: str) -> AsyncIterator[dict]:
        raise NotImplementedError

    def subscribe_status(self) -> AsyncIterator[dict]:
        raise NotImplementedError

    async def close(self) -> None:
        pass


class InMemoryBus(Bus):
    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], dict] = {}
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._status_subs: set[asyncio.Queue] = set()

    async def publish(self, symbol: str, event: dict) -> None:
        for q in list(self._subs.get(symbol, ())):
            q.put_nowait(event)

    async def set_snapshot(self, symbol: str, event: dict) -> None:
        self._snapshots[(symbol, event["type"])] = event

    async def get_snapshots(self, symbol: str) -> list[dict]:
        out = []
        for kind in ("tick", "depth"):
            ev = self._snapshots.get((symbol, kind))
            if ev is not None:
                out.append(ev)
        return out

    async def broadcast_status(self, event: dict) -> None:
        for q in list(self._status_subs):
            q.put_nowait(event)

    async def subscribe(self, symbol: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(symbol, set()).add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs.get(symbol, set()).discard(q)

    async def subscribe_status(self) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        self._status_subs.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._status_subs.discard(q)


class RedisBus(Bus):
    """Redis pub/sub + snapshot keys. `client` is a redis.asyncio.Redis
    (a fakeredis.aioredis.FakeRedis in tests — same wire semantics)."""

    def __init__(self, client) -> None:
        self._r = client

    @staticmethod
    def _chan(symbol: str) -> str:
        return f"md:events:{symbol}"

    @staticmethod
    def _snap_key(symbol: str, kind: str) -> str:
        return f"md:snap:{symbol}:{kind}"

    _STATUS_CHAN = "md:status"

    async def publish(self, symbol: str, event: dict) -> None:
        await self._r.publish(self._chan(symbol), json.dumps(event))

    async def set_snapshot(self, symbol: str, event: dict) -> None:
        await self._r.set(self._snap_key(symbol, event["type"]),
                          json.dumps(event))

    async def get_snapshots(self, symbol: str) -> list[dict]:
        out = []
        for kind in ("tick", "depth"):
            raw = await self._r.get(self._snap_key(symbol, kind))
            if raw:
                out.append(json.loads(raw))
        return out

    async def broadcast_status(self, event: dict) -> None:
        await self._r.publish(self._STATUS_CHAN, json.dumps(event))

    async def _listen(self, channel: str) -> AsyncIterator[dict]:
        pubsub = self._r.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") == "message":
                    yield json.loads(msg["data"])
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception:
                pass

    def subscribe(self, symbol: str) -> AsyncIterator[dict]:
        return self._listen(self._chan(symbol))

    def subscribe_status(self) -> AsyncIterator[dict]:
        return self._listen(self._STATUS_CHAN)

    async def close(self) -> None:
        await self._r.aclose()
