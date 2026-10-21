"""Service: wires upstream -> normalizer -> bus, and watches feed health.

  - pump: every raw frame is normalized and routed (tick/depth ->
    snapshot + per-symbol publish; status -> broadcast).
  - supervision: if the upstream stream raises, a RECONNECTING status is
    broadcast and the stream is restarted with backoff — the pipeline
    degrades loudly, never silently (design sections 4 and 7).
  - watchdog: no upstream frames for `stale_after_s` seconds -> one STALE
    broadcast; the first frame after a stale period -> CONNECTED.
"""

from __future__ import annotations

import asyncio
import time

from . import EVENT_VERSION
from .bus import Bus
from .normalizer import Normalizer
from .upstream import SimulatedUpstream, RecordedUpstream

Upstream = SimulatedUpstream | RecordedUpstream


class MarketDataService:
    def __init__(self, upstream: Upstream, normalizer: Normalizer, bus: Bus,
                 stale_after_s: float = 5.0, reconnect_backoff_s: float = 1.0):
        self.upstream = upstream
        self.normalizer = normalizer
        self.bus = bus
        self.stale_after_s = stale_after_s
        self.reconnect_backoff_s = reconnect_backoff_s
        self._last_frame_at = time.monotonic()
        self._stale = False

    def _status(self, state: str, detail: str) -> dict:
        return {"v": EVENT_VERSION, "type": "status", "state": state,
                "detail": detail, "source": self.normalizer.source}

    async def run(self) -> None:
        await asyncio.gather(self._pump(), self._watchdog())

    async def _pump(self) -> None:
        while True:
            try:
                await self.bus.broadcast_status(
                    self._status("CONNECTED", "upstream connected"))
                async for raw in self.upstream.stream():
                    self._last_frame_at = time.monotonic()
                    if self._stale:
                        self._stale = False
                        await self.bus.broadcast_status(
                            self._status("CONNECTED", "feed resumed"))
                    for event in self.normalizer.normalize(raw):
                        if event["type"] in ("tick", "depth"):
                            await self.bus.set_snapshot(event["symbol"], event)
                            await self.bus.publish(event["symbol"], event)
                        elif event["type"] == "status":
                            await self.bus.broadcast_status(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # upstream died — say so, then retry
                await self.bus.broadcast_status(self._status(
                    "RECONNECTING", f"upstream error: {exc!r:.120}"))
                await asyncio.sleep(self.reconnect_backoff_s)

    async def _watchdog(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            if self._stale:
                continue
            if time.monotonic() - self._last_frame_at > self.stale_after_s:
                self._stale = True
                await self.bus.broadcast_status(self._status(
                    "STALE",
                    f"no upstream frames for {self.stale_after_s:.0f}s"))
