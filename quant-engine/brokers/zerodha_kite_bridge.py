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
        returned / recorded locally (paper mode). Paper order ids are
        prefixed `PAPER-` and live only in the paper store.
      - live=True: the call is delegated to a real `kiteconnect.KiteConnect`
        instance if that package is installed and an access token is set.
        The broker-assigned order id from the SDK response is the only id
        ever returned for a live order — a local id is never substituted.
      - Paper and live orders are kept in separate stores; a live order
        never appears in the paper book and vice versa.
      - Session responses never contain access tokens or secrets.

Order lifecycle & retry policy
------------------------------
* Placement is **single-shot**: it is never retried automatically. A
  timeout after submission may mean the broker accepted the order, and a
  blind retry can create a duplicate. On an uncertain outcome, call
  `reconcile()` / `orders()` to learn the broker's actual state before
  deciding anything.
* An order id is an **acknowledgement, not a fill**. Status lifecycle
  (OPEN -> partial -> COMPLETE / REJECTED / CANCELLED), filled quantity
  and average price must be read back from the broker (`orders()`), never
  inferred from the placement response.
* These bridges are **single-account** (environment credentials). They
  enforce no per-user isolation themselves: ownership is enforced by the
  broker against the authenticated account. Do not expose a bridge
  instance to multiple end users; a multi-user deployment needs a backend
  account layer (per-user encrypted tokens, authorization checks) that
  does not exist in this codebase yet.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

__all__ = ["KiteBridge", "KiteTickerSim", "KiteCredentialsError"]


class KiteCredentialsError(RuntimeError):
    """Raised when live mode is requested without credentials configured."""


class KiteOrderError(RuntimeError):
    """Raised when an order operation cannot be completed truthfully:
    unknown paper order, broker rejection, or a broker response that does
    not contain the order id it was required to return."""


_SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .&'_\-]{0,63}$")


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
        self._live_orders: dict[str, dict] = {}
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
        return {"status": "live-ready" if self.live else "paper-ready",
                "user_id": self.user_id, "live": self.live}

    def login_url(self) -> str:
        if not self.api_key:
            raise KiteCredentialsError("KITE_API_KEY not set")
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={self.api_key}"

    def generate_session(self, request_token: str) -> dict:
        """Exchange a request token for an access token (live mode only).

        The checksum is computed and verified by Zerodha's servers as part
        of the SDK's session exchange — this wrapper performs no checksum
        verification of its own and therefore reports none. The returned
        dict is sanitized: the access token is stored on the instance only
        and is never included in the response (or in any log record).
        """
        if not request_token or not isinstance(request_token, str):
            raise KiteCredentialsError("request_token is required")
        if not self.live or self._kite is None:
            return {"mode": "paper",
                    "note": "session exchange skipped in paper mode"}
        try:
            data = self._kite.generate_session(
                request_token, api_secret=self.api_secret)
        except Exception as exc:  # SDK/network rejection: fail closed.
            raise KiteCredentialsError(
                f"broker rejected the session exchange: {exc}") from exc
        token = data.get("access_token")
        if not token:
            raise KiteCredentialsError(
                "broker session response contained no access_token")
        self.access_token = token
        # Sanitized view only — never return tokens/secrets to callers.
        return {"mode": "live",
                "user_id": data.get("user_id", self.user_id),
                "user_name": data.get("user_name"),
                "login_time": str(data.get("login_time", ""))}

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
        """Place an order.

        Paper mode: returns a `PAPER-…` id and records the order in the
        paper store only. Live mode: delegates to the SDK and returns the
        **broker-assigned** order id from its response; if the SDK raises
        or returns no id, KiteOrderError is raised — an id is never
        invented locally for a live order.
        """
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

        if not (self.live and self._kite is not None):
            order_id = "PAPER-" + uuid.uuid4().hex[:16].upper()
            self._paper_orders[order_id] = _PaperOrder(order_id=order_id,
                                                       payload=payload)
            out = self._dispatch(
                "POST", f"/orders/{payload['variety']}", payload)
            out["order_id"] = order_id
            return out

        try:
            result = self._kite.place_order(**payload)
        except Exception as exc:
            raise KiteOrderError(
                f"broker rejected the order: {exc}") from exc
        broker_id = result if isinstance(result, str) else (
            result.get("order_id") if isinstance(result, dict) else None)
        if not broker_id or not isinstance(broker_id, str):
            raise KiteOrderError(
                "broker response did not contain an order id; refusing to "
                "substitute a local one")
        self._live_orders[broker_id] = payload
        record = {"ts": time.time(), "method": "POST",
                  "endpoint": f"/orders/{payload['variety']}",
                  "payload": payload, "live": True, "response": result}
        self._request_log.append(record)
        return {"mode": "live", "request": record, "response": result,
                "order_id": broker_id}

    _EXCHANGES = {"NSE", "BSE", "NFO", "BFO", "MCX", "CDS"}
    _PRODUCTS = {"MIS", "CNC", "NRML"}
    _ORDER_TYPES = {"MARKET", "LIMIT", "SL-M", "SL"}
    _VARIETIES = {"regular", "amo", "co", "bo"}
    _VALIDITIES = {"DAY", "IOC", "TTL"}

    @staticmethod
    def _check_price(name: str, v: Any) -> None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{name} must be a number")
        if not math.isfinite(v) or v <= 0:
            raise ValueError(f"{name} must be positive and finite")

    def _validate_order(self, p: dict) -> None:
        q = p.get("quantity")
        if isinstance(q, bool) or not isinstance(q, int):
            raise ValueError("quantity must be an integer")
        if q <= 0:
            raise ValueError("quantity must be positive")
        if p.get("exchange") not in self._EXCHANGES:
            raise ValueError(f"unsupported exchange: {p.get('exchange')!r}")
        if p.get("transaction_type") not in ("BUY", "SELL"):
            raise ValueError("transaction_type must be BUY or SELL")
        if p.get("product") not in self._PRODUCTS:
            raise ValueError(f"unsupported product: {p.get('product')!r}")
        if p.get("order_type") not in self._ORDER_TYPES:
            raise ValueError(f"unsupported order_type: {p.get('order_type')!r}")
        if p.get("variety", "regular") not in self._VARIETIES:
            raise ValueError(f"unsupported variety: {p.get('variety')!r}")
        if p.get("validity", "DAY") not in self._VALIDITIES:
            raise ValueError(f"unsupported validity: {p.get('validity')!r}")
        sym = p.get("tradingsymbol")
        if not isinstance(sym, str) or not _SYMBOL_RE.match(sym):
            raise ValueError(f"invalid tradingsymbol: {sym!r}")
        ot = p["order_type"]
        if ot in ("LIMIT", "SL"):
            if p.get("price") is None:
                raise ValueError(f"{ot} order requires a price")
            self._check_price("price", p["price"])
        elif p.get("price") is not None:
            self._check_price("price", p["price"])
        if ot in ("SL", "SL-M"):
            if p.get("trigger_price") is None:
                raise ValueError(f"{ot} order requires trigger_price")
            self._check_price("trigger_price", p["trigger_price"])
        elif p.get("trigger_price") is not None:
            self._check_price("trigger_price", p["trigger_price"])
        dq = p.get("disclosed_quantity")
        if dq is not None:
            if isinstance(dq, bool) or not isinstance(dq, int) \
                    or dq < 0 or dq > q:
                raise ValueError(
                    "disclosed_quantity must be an integer in [0, quantity]")
        tag = p.get("tag")
        if tag is not None and (not isinstance(tag, str) or len(tag) > 20):
            raise ValueError("tag must be a string of at most 20 characters")
        for opt in ("squareoff", "stoploss", "trailing_stoploss"):
            v = p.get(opt)
            if v is not None:
                if isinstance(v, bool) or not isinstance(v, (int, float)) \
                        or not math.isfinite(v) or v < 0:
                    raise ValueError(f"{opt} must be a finite number >= 0")

    def modify_order(self, *, order_id: str, variety: str = "regular",
                     quantity: int | None = None, price: float | None = None,
                     order_type: str | None = None,
                     trigger_price: float | None = None,
                     validity: str | None = None) -> dict:
        """Modify an order. Paper: the id must exist in the paper store.
        Live: delegated to the SDK; the broker's response is returned."""
        changes = {"quantity": quantity, "price": price,
                   "order_type": order_type, "trigger_price": trigger_price,
                   "validity": validity}
        changes = {k: v for k, v in changes.items() if v is not None}

        if self.live and self._kite is not None:
            try:
                result = self._kite.modify_order(
                    variety=variety, order_id=order_id, **changes)
            except Exception as exc:
                raise KiteOrderError(
                    f"broker rejected the modification: {exc}") from exc
            if order_id in self._live_orders:
                self._live_orders[order_id].update(changes)
            record = {"ts": time.time(), "method": "PUT",
                      "endpoint": f"/orders/{variety}/{order_id}",
                      "payload": {"order_id": order_id, **changes},
                      "live": True, "response": result}
            self._request_log.append(record)
            return {"mode": "live", "request": record, "response": result,
                    "order_id": result if isinstance(result, str)
                    else order_id}

        po = self._paper_orders.get(order_id)
        if po is None:
            raise KiteOrderError(
                f"unknown paper order {order_id!r}: nothing was modified")
        merged = {**po.payload, **changes}
        self._validate_order(merged)
        po.payload.update(changes)
        po.status = "MODIFIED"
        payload = {"order_id": order_id, "variety": variety, **changes}
        return self._dispatch("PUT", f"/orders/{variety}/{order_id}", payload)

    def cancel_order(self, *, order_id: str, variety: str = "regular") -> dict:
        """Cancel an order. Paper: the id must exist in the paper store.
        Live: delegated to the SDK; the broker's response is returned."""
        if self.live and self._kite is not None:
            try:
                result = self._kite.cancel_order(variety=variety,
                                                 order_id=order_id)
            except Exception as exc:
                raise KiteOrderError(
                    f"broker rejected the cancellation: {exc}") from exc
            self._live_orders.pop(order_id, None)
            record = {"ts": time.time(), "method": "DELETE",
                      "endpoint": f"/orders/{variety}/{order_id}",
                      "payload": {"order_id": order_id},
                      "live": True, "response": result}
            self._request_log.append(record)
            return {"mode": "live", "request": record, "response": result,
                    "order_id": result if isinstance(result, str)
                    else order_id}

        po = self._paper_orders.get(order_id)
        if po is None:
            raise KiteOrderError(
                f"unknown paper order {order_id!r}: nothing was cancelled")
        po.status = "CANCELLED"
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

    def reconcile(self) -> dict:
        """Compare the local order caches against the broker's order book.

        Returns order ids in three buckets: `matched` (known locally and
        at the broker), `missing_at_broker` (placed locally, absent at the
        broker — investigate before re-submitting anything), and
        `unknown_locally` (at the broker but not placed through this
        instance). Paper mode reconciles the paper store trivially.
        """
        if not (self.live and self._kite is not None):
            return {"mode": "paper",
                    "matched": sorted(self._paper_orders),
                    "missing_at_broker": [], "unknown_locally": []}
        try:
            broker_orders = self._kite.orders()
        except Exception as exc:
            raise KiteOrderError(
                f"reconciliation failed: broker order book unreadable: {exc}"
            ) from exc
        broker_ids = {str(o["order_id"]) for o in broker_orders
                      if isinstance(o, dict) and o.get("order_id")}
        local = set(self._live_orders)
        return {"mode": "live",
                "matched": sorted(local & broker_ids),
                "missing_at_broker": sorted(local - broker_ids),
                "unknown_locally": sorted(broker_ids - local)}

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
