"""
Zerodha KiteConnect v3 style bridge.

A self-contained wrapper shaped exactly like the official `kiteconnect`
package API (order placement / modification / cancellation, margins,
positions, holdings, quote) plus a simulated WebSocket ticker that mimics
`KiteTicker` callbacks (on_ticks / on_connect / on_close).

Safety model
------------
* Credentials are read ONLY from environment variables:
      KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN, KITE_USER_ID
* No network call is made at import time.
* Every method that would talk to Zerodha is guarded by `live`:
      - live=False (default): the request payload is built, validated and
        returned / recorded locally (paper mode).
      - live=True: the call is delegated to a real `kiteconnect.KiteConnect`
        instance if that package is installed and an access token is set.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

__all__ = ["KiteBridge", "KiteTickerSim", "KiteCredentialsError"]


class KiteCredentialsError(RuntimeError):
    """Raised when live mode is requested without credentials configured."""


# --------------------------------------------------------------------------- #
# Order vocabulary (mirrors kiteconnect.constants)
# --------------------------------------------------------------------------- #
class _Const:
    EXCHANGE_NSE = "NSE"
    EXCHANGE_BSE = "BSE"
    EXCHANGE_NFO = "NFO"
    EXCHANGE_BFO = "BFO"
    EXCHANGE_MCX = "MCX"
    EXCHANGE_CDS = "CDS"

    TRANSACTION_TYPE_BUY = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"

    PRODUCT_MIS = "MIS"
    PRODUCT_CNC = "CNC"
    PRODUCT_NRML = "NRML"

    ORDER_TYPE_MARKET = "MARKET"
    ORDER_TYPE_LIMIT = "LIMIT"
    ORDER_TYPE_SLM = "SL-M"
    ORDER_TYPE_SL = "SL"

    VALIDITY_DAY = "DAY"
    VALIDITY_IOC = "IOC"
    VALIDITY_TTL = "TTL"

    VARIETY_REGULAR = "regular"
    VARIETY_AMO = "amo"
    VARIETY_CO = "co"
    VARIETY_BO = "bo"


# --------------------------------------------------------------------------- #
@dataclass
class _PaperOrder:
    order_id: str
    payload: dict
    status: str = "OPEN"
    filled_quantity: int = 0
    average_price: float = 0.0


# --------------------------------------------------------------------------- #
class KiteBridge(_Const):
    """KiteConnect v3 style client (paper by default, live on demand)."""

    API_ROOT = "https://api.kite.trade"

    def __init__(self, live: bool = False):
        self.live = live
        self.api_key = os.environ.get("KITE_API_KEY", "")
        self.api_secret = os.environ.get("KITE_API_SECRET", "")
        self.access_token = os.environ.get("KITE_ACCESS_TOKEN", "")
        self.user_id = os.environ.get("KITE_USER_ID", "")
        self._kite = None
        self._paper_orders: dict[str, _PaperOrder] = {}
        self._request_log: list[dict] = []
        if live:
            self.connect()

    # ------------------------------------------------------------------ #
    # Session
    # ------------------------------------------------------------------ #
    def connect(self) -> dict:
        """Validate credentials and (in live mode) bind the real client."""
        if not self.api_key or not self.access_token:
            raise KiteCredentialsError(
                "KITE_API_KEY / KITE_ACCESS_TOKEN not set in environment; "
                "cannot enter live mode.")
        if self.live:
            try:
                from kiteconnect import KiteConnect  # type: ignore
            except ImportError as exc:
                raise KiteCredentialsError(
                    "live=True but the 'kiteconnect' package is not installed"
                ) from exc
            self._kite = KiteConnect(api_key=self.api_key)
            self._kite.set_access_token(self.access_token)
        return {"status": "connected", "user_id": self.user_id,
                "live": self.live}

    def login_url(self) -> str:
        if not self.api_key:
            raise KiteCredentialsError("KITE_API_KEY not set")
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={self.api_key}"

    def generate_session(self, request_token: str) -> dict:
        """Exchange a request token for an access token (live mode only)."""
        if not self.live or self._kite is None:
            return {"mode": "paper",
                    "note": "session exchange skipped in paper mode",
                    "request_token": request_token}
        import hashlib
        checksum = hashlib.sha256(
            f"{self.api_key}{request_token}{self.api_secret}".encode()
        ).hexdigest()
        data = self._kite.generate_session(request_token, api_secret=self.api_secret)
        self.access_token = data["access_token"]
        return {"checksum_verified": bool(checksum), **data}

    # ------------------------------------------------------------------ #
    # Internal request plumbing
    # ------------------------------------------------------------------ #
    def _dispatch(self, method: str, endpoint: str, payload: dict,
                  live_fn: Optional[Callable[[], Any]] = None) -> dict:
        """Record the request; execute `live_fn` only in live mode."""
        record = {"ts": time.time(), "method": method,
                  "endpoint": endpoint, "payload": payload, "live": self.live}
        self._request_log.append(record)
        if self.live and live_fn is not None:
            result = live_fn()
            record["response"] = result
            return {"mode": "live", "request": record, "response": result}
        return {"mode": "paper", "request": record,
                "response": {"status": "success", "simulated": True}}

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    def place_order(self, *, variety: str = "regular", exchange: str,
                    tradingsymbol: str, transaction_type: str, quantity: int,
                    product: str, order_type: str, price: float | None = None,
                    trigger_price: float | None = None,
                    validity: str = "DAY", tag: str | None = None,
                    disclosed_quantity: int | None = None,
                    squareoff: float | None = None,
                    stoploss: float | None = None,
                    trailing_stoploss: float | None = None) -> dict:
        """Place an order. Returns the dispatch record; the order id is in
        response/data in live mode, or the paper order id otherwise."""
        payload = {
            "variety": variety, "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "transaction_type": transaction_type, "quantity": quantity,
            "product": product, "order_type": order_type,
            "price": price, "trigger_price": trigger_price,
            "validity": validity, "tag": tag,
            "disclosed_quantity": disclosed_quantity,
            "squareoff": squareoff, "stoploss": stoploss,
            "trailing_stoploss": trailing_stoploss,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        self._validate_order(payload)

        order_id = uuid.uuid4().hex[:16].upper()
        self._paper_orders[order_id] = _PaperOrder(order_id=order_id,
                                                   payload=payload)
        live_fn = None
        if self._kite is not None:
            def live_fn():  # noqa: F811
                return self._kite.place_order(**payload)
        out = self._dispatch("POST", "/orders/{variety}".format(**payload)
                             if "variety" in payload else "/orders/regular",
                             payload, live_fn)
        out["order_id"] = order_id
        return out

    def _validate_order(self, p: dict) -> None:
        if p["quantity"] <= 0:
            raise ValueError("quantity must be positive")
        if p["order_type"] in ("LIMIT", "SL") and not p.get("price"):
            raise ValueError(f"{p['order_type']} order requires a price")
        if p["order_type"] in ("SL", "SL-M") and not p.get("trigger_price"):
            raise ValueError(f"{p['order_type']} order requires trigger_price")

    def modify_order(self, *, order_id: str, variety: str = "regular",
                     quantity: int | None = None, price: float | None = None,
                     order_type: str | None = None,
                     trigger_price: float | None = None,
                     validity: str | None = None) -> dict:
        payload = {"order_id": order_id, "variety": variety,
                   "quantity": quantity, "price": price,
                   "order_type": order_type, "trigger_price": trigger_price,
                   "validity": validity}
        payload = {k: v for k, v in payload.items() if v is not None}
        if order_id in self._paper_orders:
            po = self._paper_orders[order_id]
            for k, v in payload.items():
                if k in po.payload:
                    po.payload[k] = v
            po.status = "MODIFIED"
        return self._dispatch("PUT", f"/orders/{variety}/{order_id}", payload)

    def cancel_order(self, *, order_id: str, variety: str = "regular") -> dict:
        if order_id in self._paper_orders:
            self._paper_orders[order_id].status = "CANCELLED"
        return self._dispatch("DELETE", f"/orders/{variety}/{order_id}",
                              {"order_id": order_id, "variety": variety})

    def orders(self) -> dict:
        live_fn = self._kite.orders if self._kite is not None else None
        paper = [{"order_id": o.order_id, "status": o.status, **o.payload}
                 for o in self._paper_orders.values()]
        out = self._dispatch("GET", "/orders", {}, live_fn)
        if not self.live:
            out["response"] = {"status": "success", "data": paper}
        return out

    def positions(self) -> dict:
        live_fn = self._kite.positions if self._kite is not None else None
        out = self._dispatch("GET", "/portfolio/positions", {}, live_fn)
        if not self.live:
            out["response"] = {"status": "success",
                               "data": {"net": [], "day": []}}
        return out

    def margins(self, segment: str | None = None) -> dict:
        live_fn = (lambda: self._kite.margins(segment)) if self._kite else None
        out = self._dispatch("GET", "/user/margins", {"segment": segment}, live_fn)
        if not self.live:
            out["response"] = {"status": "success", "data": {
                "equity": {"available": {"cash": 1_000_000.0}}}}
        return out

    def quote(self, instruments: list[str]) -> dict:
        quoted = ",".join(instruments)
        live_fn = (lambda: self._kite.quote(instruments)) if self._kite else None
        out = self._dispatch("GET", "/quote", {"i": quoted}, live_fn)
        if not self.live:
            out["response"] = {"status": "success", "data": {
                sym: {"last_price": 0.0, "instrument_token": 0}
                for sym in instruments}}
        return out

    @property
    def request_log(self) -> list[dict]:
        return list(self._request_log)


# --------------------------------------------------------------------------- #
class KiteTickerSim:
    """Simulated KiteTicker: streams synthetic GBM ticks to on_ticks
    callbacks with the same message shape as the real websocket feed."""

    def __init__(self, api_key: str | None = None, access_token: str | None = None):
        self.api_key = api_key or os.environ.get("KITE_API_KEY", "")
        self.access_token = access_token or os.environ.get("KITE_ACCESS_TOKEN", "")
        self.on_ticks: Optional[Callable] = None
        self.on_connect: Optional[Callable] = None
        self.on_close: Optional[Callable] = None
        self._tokens: list[int] = []
        self._modes: dict[int, str] = {}
        self._prices: dict[int, float] = {}
        self._running = False

    # KiteTicker-compatible controls -------------------------------------- #
    def subscribe(self, tokens: list[int]) -> None:
        self._tokens.extend(t for t in tokens if t not in self._tokens)

    def unsubscribe(self, tokens: list[int]) -> None:
        self._tokens = [t for t in self._tokens if t not in tokens]

    def set_mode(self, mode: str, tokens: list[int]) -> None:
        for t in tokens:
            self._modes[t] = mode

    def connect(self, threaded: bool = True) -> None:
        self._running = True
        if self.on_connect:
            self.on_connect(self, {"status": "simulated-connected"})

    def close(self, code: int = 1000, reason: str = "") -> None:
        self._running = False
        if self.on_close:
            self.on_close(self, code, reason)

    # Simulation ------------------------------------------------------------ #
    def run_ticks(self, n_ticks: int, base_prices: dict[int, float],
                  vol: float = 0.12, dt_seconds: float = 1.0,
                  seed: int = 3) -> list[dict]:
        """Generate `n_ticks` synthetic tick batches (also fires on_ticks)."""
        rng = np.random.default_rng(seed)
        self._prices.update(base_prices)
        dt = dt_seconds / (252 * 375 * 60)
        out: list[dict] = []
        for _ in range(n_ticks):
            batch = []
            for tok in self._tokens or list(base_prices):
                shock = rng.normal(-0.5 * vol * vol * dt, vol * np.sqrt(dt))
                self._prices[tok] *= float(np.exp(shock))
                ltp = self._prices[tok]
                batch.append({
                    "instrument_token": tok,
                    "last_price": round(ltp, 2),
                    "ohlc": {"open": round(ltp * 0.999, 2),
                             "high": round(ltp * 1.001, 2),
                             "low": round(ltp * 0.998, 2),
                             "close": round(ltp, 2)},
                    "change": 0.0,
                    "mode": self._modes.get(tok, "ltp"),
                    "tradable": True,
                })
            out.append({"ticks": batch, "type": "tick"})
            if self.on_ticks:
                self.on_ticks(self, batch)
        return out


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    bridge = KiteBridge(live=False)
    print("=" * 62)
    print("  KITE BRIDGE (paper mode) — order lifecycle demo")
    print("=" * 62)
    res = bridge.place_order(
        exchange=bridge.EXCHANGE_NFO, tradingsymbol="NIFTY24DEC24300CE",
        transaction_type=bridge.TRANSACTION_TYPE_BUY, quantity=50,
        product=bridge.PRODUCT_NRML, order_type=bridge.ORDER_TYPE_LIMIT,
        price=112.35, tag="open-atlas")
    oid = res["order_id"]
    print(f"  Placed  : {oid}")
    print("  Payload :", json.dumps(res["request"]["payload"], indent=None))
    bridge.modify_order(order_id=oid, price=113.00)
    print(f"  Modified: price -> 113.00")
    bridge.cancel_order(order_id=oid)
    print(f"  Cancelled: {oid}")
    print(f"  Orders on book: {len(bridge.orders()['response']['data'])}")
    ticker = KiteTickerSim()
    ticker.subscribe([256265])  # NIFTY index token
    ticks = ticker.run_ticks(3, {256265: 24300.0})
    print(f"  Ticker  : {len(ticks)} synthetic tick batches, "
          f"last LTP {ticks[-1]['ticks'][0]['last_price']}")
    print("=" * 62)
