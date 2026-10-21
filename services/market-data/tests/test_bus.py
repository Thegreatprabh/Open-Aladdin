"""Bus contract tests — both implementations must behave identically."""

import asyncio

import pytest

from market_data.bus import InMemoryBus, RedisBus

TICK = {"v": 1, "type": "tick", "symbol": "NIFTY", "instrumentToken": 256265,
        "ltp": 100.0, "open": 99.0, "high": 101.0, "low": 98.0, "close": 97.0,
        "volume": 10, "oi": 0, "exchangeTs": "2026-10-20T09:15:03+05:30",
        "seq": 1, "source": "SIMULATED"}
STATUS = {"v": 1, "type": "status", "state": "STALE", "detail": "test",
          "source": "SIMULATED"}


async def _first(aiter, timeout=3.0):
    return await asyncio.wait_for(anext(aiter), timeout)


def _exercise_bus(bus):
    async def go():
        # snapshot round-trip (empty first)
        assert await bus.get_snapshots("NIFTY") == []
        await bus.set_snapshot("NIFTY", TICK)
        snaps = await bus.get_snapshots("NIFTY")
        assert len(snaps) == 1 and snaps[0]["ltp"] == 100.0

        # publish/subscribe round-trip
        sub = bus.subscribe("NIFTY")
        pending = asyncio.create_task(_first(sub))
        await asyncio.sleep(0.3)  # let the subscription register
        await bus.publish("NIFTY", TICK)
        got = await pending
        assert got["symbol"] == "NIFTY" and got["seq"] == 1
        await sub.aclose()

        # status broadcast round-trip
        ssub = bus.subscribe_status()
        pending = asyncio.create_task(_first(ssub))
        await asyncio.sleep(0.3)
        await bus.broadcast_status(STATUS)
        assert (await pending)["state"] == "STALE"
        await ssub.aclose()
    asyncio.run(go())


def test_inmemory_bus_contract():
    _exercise_bus(InMemoryBus())


def test_redis_bus_contract_fakeredis():
    import fakeredis.aioredis as fakeredis
    client = fakeredis.FakeRedis(decode_responses=True)
    _exercise_bus(RedisBus(client))


def test_inmemory_subscribers_are_isolated_per_symbol():
    async def go():
        bus = InMemoryBus()
        nifty = bus.subscribe("NIFTY")
        bank = bus.subscribe("BANKNIFTY")
        got_bank = asyncio.create_task(_first(bank, timeout=0.4))
        await asyncio.sleep(0.2)
        await bus.publish("NIFTY", TICK)
        with pytest.raises(asyncio.TimeoutError):
            await got_bank  # BANKNIFTY subscriber must not see NIFTY events
        await nifty.aclose()
        await bank.aclose()
    asyncio.run(go())
