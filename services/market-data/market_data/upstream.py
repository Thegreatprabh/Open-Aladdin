"""Upstream adapters: where raw (broker-shaped) frames come from.

P1 ships two adapters, both provenance "SIMULATED":
  - SimulatedUpstream: a deterministic, seeded random-walk feed emitting
    frames in a broker-like wire shape (compact keys, epoch-ms exchange
    timestamps, global frame ids) so the normalizer does real work.
  - RecordedUpstream: replays a JSONL fixture of raw frames — the
    recorded-fixture testing path from design section 7.

P3 adds the real KiteTicker adapter behind this same interface; its
provenance will be "LIVE". Adapters are read-only by construction: the
interface has no order methods.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import AsyncIterator

from .instruments import InstrumentRegistry


class SimulatedUpstream:
    source = "SIMULATED"

    def __init__(self, registry: InstrumentRegistry, symbols: list[str],
                 tick_ms: int = 250, seed: int = 42,
                 start_ts_ms: int | None = None, depth_every: int = 10):
        self.registry = registry
        self.symbols = list(symbols)
        self.tick_s = tick_ms / 1000
        self.depth_every = depth_every
        self._rng = random.Random(seed)
        self._ts = start_ts_ms if start_ts_ms is not None else int(time.time() * 1000)
        self._fid = 0
        self._inject: asyncio.Queue = asyncio.Queue()
        self._expired = False
        self._price: dict[str, float] = {}
        self._day: dict[str, dict] = {}
        for sym in self.symbols:
            base = registry.base_price(sym)
            self._price[sym] = base
            self._day[sym] = {"o": base, "h": base, "l": base,
                              "c": round(base * 0.994, 2), "vol": 1_000_000}

    # -- operator / test controls ------------------------------------------

    async def inject_raw(self, frame: dict) -> None:
        """Queue an arbitrary raw frame into the stream (chaos testing)."""
        await self._inject.put(frame)

    async def expire_token(self) -> None:
        """Simulate the daily Kite access-token expiry (design section 6)."""
        self._expired = True
        await self._inject.put({"ev": "token_expired",
                                "msg": "access token expired (simulated)"})

    def resume(self) -> None:
        if self._expired:
            self._expired = False
            self._inject.put_nowait({"ev": "reconnect",
                                     "msg": "token refreshed (simulated)"})

    # -- frame generation ----------------------------------------------------

    def _next_tick(self, sym: str) -> dict:
        self._fid += 1
        self._ts += int(self.tick_s * 1000)
        price = self._price[sym]
        price = round(price * (1 + self._rng.gauss(0, 0.0007)), 2)
        self._price[sym] = price
        day = self._day[sym]
        day["h"] = max(day["h"], price)
        day["l"] = min(day["l"], price)
        day["vol"] += self._rng.randint(500, 5000)
        return {"tk": self.registry.token_for(sym), "ltp": price,
                "o": day["o"], "h": day["h"], "l": day["l"], "c": day["c"],
                "vol": day["vol"], "oi": 0, "ts": self._ts, "fid": self._fid}

    def _next_depth(self, sym: str) -> dict:
        self._fid += 1
        self._ts += int(self.tick_s * 1000)
        mid = self._price[sym]
        buy = [[round(mid - 0.05 * (i + 1), 2), self._rng.randint(50, 5000)]
               for i in range(5)]
        sell = [[round(mid + 0.05 * (i + 1), 2), self._rng.randint(50, 5000)]
                for i in range(5)]
        return {"tk": self.registry.token_for(sym),
                "depth": {"buy": buy, "sell": sell},
                "ts": self._ts, "fid": self._fid}

    async def stream(self) -> AsyncIterator[dict]:
        counts = {sym: 0 for sym in self.symbols}
        while True:
            try:
                frame = await asyncio.wait_for(self._inject.get(),
                                               timeout=self.tick_s)
                yield frame
                continue
            except asyncio.TimeoutError:
                pass
            if self._expired:
                await asyncio.sleep(self.tick_s)
                continue
            for sym in self.symbols:
                counts[sym] += 1
                yield self._next_tick(sym)
                if counts[sym] % self.depth_every == 0:
                    yield self._next_depth(sym)


class RecordedUpstream:
    """Replay raw frames from a JSONL fixture, in file order."""

    source = "SIMULATED"

    def __init__(self, path: str, registry: InstrumentRegistry,
                 speed: float = 0.0):
        self.path = path
        self.registry = registry
        self.speed = speed  # 0 = no delay; >0 = ts-delta / speed

    async def stream(self) -> AsyncIterator[dict]:
        prev_ts: int | None = None
        with open(self.path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                frame = json.loads(line)
                if self.speed > 0 and prev_ts is not None and "ts" in frame:
                    delay = (int(frame["ts"]) - prev_ts) / 1000 / self.speed
                    if delay > 0:
                        await asyncio.sleep(delay)
                if "ts" in frame:
                    prev_ts = int(frame["ts"])
                yield frame
