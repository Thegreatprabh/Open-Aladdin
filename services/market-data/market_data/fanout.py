"""WebSocket fan-out: the only thing browsers talk to (design section 4).

Client protocol (JSON text frames):
  -> {"action": "subscribe",   "symbols": ["NIFTY", "BANKNIFTY"]}
  -> {"action": "unsubscribe", "symbols": ["NIFTY"]}
  -> {"action": "snapshot",    "symbol": "NIFTY"}     (sequence-gap resync)
  <- canonical events (tick / depth / status / heartbeat)

On subscribe the client first receives the current snapshot (latest tick
and depth) for each symbol, then live events. Status events are broadcast
to every connected client regardless of subscriptions. A heartbeat event
is sent every `heartbeat_s` seconds (default 5, per design) so clients
can detect a dead socket in a quiet market.

One upstream connection feeds every client: fan-out subscribes to the
bus per (client, symbol); it never creates upstream connections.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from websockets.asyncio.server import ServerConnection, serve

from . import EVENT_VERSION
from .bus import Bus
from .normalizer import IST
from .schema import validate_event


class FanoutServer:
    def __init__(self, bus: Bus, source: str, host: str = "127.0.0.1",
                 port: int = 9103, heartbeat_s: float = 5.0):
        self.bus = bus
        self.source = source
        self.host = host
        self.port = port
        self.heartbeat_s = heartbeat_s
        self.client_count = 0
        self._server = None

    async def start(self) -> int:
        self._server = await serve(self._handle, self.host, self.port)
        return self._server.sockets[0].getsockname()[1]

    async def serve_forever(self) -> None:
        await self.start()
        await self._server.wait_closed()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    # -- per-connection ------------------------------------------------------

    async def _handle(self, ws: ServerConnection) -> None:
        self.client_count += 1
        out: asyncio.Queue = asyncio.Queue()
        forwarders: dict[str, asyncio.Task] = {}
        tasks = [
            asyncio.create_task(self._sender(ws, out)),
            asyncio.create_task(self._heartbeat(out)),
            asyncio.create_task(self._pump(self.bus.subscribe_status(), out)),
        ]
        try:
            async for raw in ws:
                await self._control(raw, out, forwarders)
        except Exception:
            pass  # a dead client must never take the server down
        finally:
            self.client_count -= 1
            for task in list(forwarders.values()) + tasks:
                task.cancel()

    async def _control(self, raw: str, out: asyncio.Queue,
                       forwarders: dict[str, asyncio.Task]) -> None:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        action = msg.get("action")
        if action == "subscribe":
            for symbol in msg.get("symbols", []):
                if symbol in forwarders:
                    continue
                for snap in await self.bus.get_snapshots(symbol):
                    out.put_nowait(snap)
                forwarders[symbol] = asyncio.create_task(
                    self._pump(self.bus.subscribe(symbol), out))
        elif action == "unsubscribe":
            for symbol in msg.get("symbols", []):
                task = forwarders.pop(symbol, None)
                if task is not None:
                    task.cancel()
        elif action == "snapshot":
            symbol = msg.get("symbol")
            if symbol:
                for snap in await self.bus.get_snapshots(symbol):
                    out.put_nowait(snap)

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    async def _pump(source_aiter, out: asyncio.Queue) -> None:
        try:
            async for event in source_aiter:
                out.put_nowait(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _sender(self, ws: ServerConnection, out: asyncio.Queue) -> None:
        try:
            while True:
                event = await out.get()
                validate_event(event)
                await ws.send(json.dumps(event))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _heartbeat(self, out: asyncio.Queue) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            out.put_nowait({
                "v": EVENT_VERSION, "type": "heartbeat",
                "ts": datetime.now(IST).isoformat(), "source": self.source,
            })
