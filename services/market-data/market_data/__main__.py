"""Entrypoint: python -m market_data

Configuration (environment):
  MD_UPSTREAM   sim (default) | recorded
  MD_FIXTURE    JSONL path when MD_UPSTREAM=recorded
  MD_SYMBOLS    comma list (default: full watchlist)
  MD_TICK_MS    simulated tick interval (default 250)
  MD_SEED       simulated feed seed (default 42)
  MD_REDIS_URL  e.g. redis://redis:6379/0 — when unset, an in-memory bus
                is used and a warning is printed (dev mode only)
  MD_WS_HOST    fan-out bind host (default 127.0.0.1)
  MD_WS_PORT    fan-out bind port (default 9103)
  MD_STALE_S    stale watchdog threshold seconds (default 5)

The pipeline is read-only: it holds no order-placement code.
"""

from __future__ import annotations

import asyncio
import os

from .bus import InMemoryBus, RedisBus
from .fanout import FanoutServer
from .instruments import InstrumentRegistry
from .normalizer import Normalizer
from .service import MarketDataService
from .upstream import RecordedUpstream, SimulatedUpstream


async def amain() -> None:
    registry = InstrumentRegistry()
    symbols_env = os.environ.get("MD_SYMBOLS", "")
    symbols = ([s.strip() for s in symbols_env.split(",") if s.strip()]
               or registry.symbols())

    if os.environ.get("MD_UPSTREAM", "sim") == "recorded":
        upstream = RecordedUpstream(os.environ["MD_FIXTURE"], registry,
                                     speed=float(os.environ.get("MD_SPEED", "0")))
    else:
        upstream = SimulatedUpstream(
            registry, symbols,
            tick_ms=int(os.environ.get("MD_TICK_MS", "250")),
            seed=int(os.environ.get("MD_SEED", "42")))

    redis_url = os.environ.get("MD_REDIS_URL", "")
    if redis_url:
        import redis.asyncio as aioredis
        bus = RedisBus(aioredis.from_url(redis_url, decode_responses=True))
        bus_kind = f"redis ({redis_url})"
    else:
        bus = InMemoryBus()
        bus_kind = "in-memory (DEV ONLY — set MD_REDIS_URL in deployment)"

    normalizer = Normalizer(registry, source=upstream.source)
    service = MarketDataService(
        upstream, normalizer, bus,
        stale_after_s=float(os.environ.get("MD_STALE_S", "5")))
    fanout = FanoutServer(bus, source=upstream.source,
                          host=os.environ.get("MD_WS_HOST", "127.0.0.1"),
                          port=int(os.environ.get("MD_WS_PORT", "9103")))
    port = await fanout.start()
    print(f"[market-data] source={upstream.source} bus={bus_kind} "
          f"symbols={symbols} ws=:{port}", flush=True)
    try:
        await service.run()
    finally:
        await fanout.close()
        await bus.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
