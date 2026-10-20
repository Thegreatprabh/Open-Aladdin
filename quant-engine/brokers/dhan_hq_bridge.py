"""
DhanHQ v2 REST order-router bridge.

Wraps the DhanHQ v2 HTTP API (https://api.dhan.co/v2) in a small typed
client: order placement with fully-built payloads, super orders
(entry + target + stop-loss + trailing jump in a single request),
bracket-style helpers emulated on top of super orders, order
modify/cancel, and order-book / positions / holdings fetchers.

Safety model
------------
* Credentials are read ONLY from the environment:
      DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN
* No network at import.  Every call is guarded by `live`:
      - live=False (default): payloads are built + validated and recorded
        in a local paper ledger.
      - live=True: calls go through `urllib` (stdlib) to api.dhan.co with
        the access-token / client-id headers Dhan requires.
      - Paper order ids are prefixed `PAPER-` and live only in the paper
        ledger. In live mode the order id returned is the one assigned by
        Dhan in its response body; if the response has none, the call
        fails loudly instead of substituting a local id.
      - Paper and live orders are kept in separate stores.

Order lifecycle & retry policy: identical to the Kite bridge — placement
is single-shot (never auto-retried; reconcile after uncertain outcomes),
an order id is an acknowledgement not a fill (read status/fills back via
`get_orders()`), and the bridge is single-account: the broker enforces
ownership against the authenticated account; there is no per-user
isolation layer in this codebase yet.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Optional

__all__ = ["DhanBridge", "DhanCredentialsError"]


class DhanCredentialsError(RuntimeError):
    """Raised when live mode lacks DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN."""


class DhanOrderError(RuntimeError):
    """Raised when an order operation cannot be completed truthfully:
    unknown paper order, broker HTTP error, or a live response without
    the broker-assigned order id."""


# --------------------------------------------------------------------------- #
class _Seg:
    IDX_I = "IDX_I"
    NSE_EQ = "NSE_EQ"
    NSE_FNO = "NSE_FNO"
    BSE_EQ = "BSE_EQ"
    BSE_FNO = "BSE_FNO"
    MCX_COMM = "MCX_COMM"


@dataclass
class _PaperDhanOrder:
    order_id: str
    payload: dict
    status: str = "PENDING"


# --------------------------------------------------------------------------- #
class DhanBridge(_Seg):
    """DhanHQ v2 order router (paper by default)."""

    BASE_URL = "https://api.dhan.co/v2"

    TXN_BUY = "BUY"
    TXN_SELL = "SELL"
    PRODUCT_INTRADAY = "INTRADAY"
    PRODUCT_CNC = "CNC"
    PRODUCT_MARGIN = "MARGIN"
    PRODUCT_MTF = "MTF"
    PRODUCT_BO = "BO"
    PRODUCT_CO = "CO"
    TYPE_LIMIT = "LIMIT"
    TYPE_MARKET = "MARKET"
    TYPE_SL = "STOP_LOSS"
    TYPE_SLM = "STOP_LOSS_MARKET"
    VALIDITY_DAY = "DAY"
    VALIDITY_IOC = "IOC"

    def __init__(self, live: bool = False):
        self.live = live
        self.client_id = os.environ.get("DHAN_CLIENT_ID", "")
        self.access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")
        self._orders: dict[str, _PaperDhanOrder] = {}
        self._live_orders: dict[str, dict] = {}
        self._request_log: list[dict] = []
        if live and (not self.client_id or not self.access_token):
            raise DhanCredentialsError(
                "DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN not set in environment")

    # ------------------------------------------------------------------ #
    # HTTP plumbing
    # ------------------------------------------------------------------ #
    def _headers(self) -> dict:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": self.access_token,
            "client-id": self.client_id,
        }

    def _request(self, method: str, path: str,
                 payload: Optional[dict] = None) -> dict:
        record = {"ts": time.time(), "method": method, "path": path,
                  "payload": payload or {}, "live": self.live}
        self._request_log.append(record)
        if not self.live:
            return {"mode": "paper", "request": record,
                    "response": {"status": "success", "simulated": True}}
        url = f"{self.BASE_URL}{path}"
        data = json.dumps(payload).encode() if payload else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                body = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            # Broker error bodies carry an error code/message, never our
            # credentials; surface them and fail closed (no fake success).
            detail = exc.read().decode(errors="replace")[:300]
            raise DhanOrderError(
                f"Dhan API error {exc.code} on {method} {path}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise DhanOrderError(
                f"Dhan API unreachable on {method} {path}: {exc.reason}"
            ) from exc
        record["response"] = body
        return {"mode": "live", "request": record, "response": body}

    # ------------------------------------------------------------------ #
    # Payload builder
    # ------------------------------------------------------------------ #
    def build_order_payload(self, *, security_id: str, exchange_segment: str,
                            transaction_type: str, quantity: int,
                            order_type: str, product_type: str,
                            price: float = 0.0, trigger_price: float = 0.0,
                            validity: str = "DAY",
                            disclosed_quantity: int = 0,
                            drv_expiry_date: str | None = None,
                            drv_option_type: str | None = None,
                            drv_strike_price: float | None = None,
                            correlation_id: str | None = None) -> dict:
        """Build a DhanHQ v2 /orders payload (derivative fields included
        only for F&O segments, as the API requires)."""
        payload: dict[str, Any] = {
            "dhanClientId": self.client_id or "PAPER-CLIENT",
            "correlationId": correlation_id or uuid.uuid4().hex[:24],
            "transactionType": transaction_type,
            "exchangeSegment": exchange_segment,
            "productType": product_type,
            "orderType": order_type,
            "validity": validity,
            "securityId": str(security_id),
            "quantity": quantity,
            "disclosedQuantity": disclosed_quantity,
            "price": price,
            "triggerPrice": trigger_price,
            "afterMarketOrder": False,
            "amoTime": "",
            "boProfitValue": 0,
            "boStopLossValue": 0,
        }
        if exchange_segment.endswith("FNO"):
            payload.update({
                "drvExpiryDate": drv_expiry_date,
                "drvOptionType": drv_option_type,
                "drvStrikePrice": float(drv_strike_price or 0.0),
            })
        self._validate(payload)
        return payload

    _SEGMENTS = {"IDX_I", "NSE_EQ", "NSE_FNO", "BSE_EQ", "BSE_FNO",
                 "MCX_COMM"}
    _PRODUCTS = {"INTRADAY", "CNC", "MARGIN", "MTF", "BO", "CO"}
    _ORDER_TYPES = {"LIMIT", "MARKET", "STOP_LOSS", "STOP_LOSS_MARKET"}
    _VALIDITIES = {"DAY", "IOC"}

    @staticmethod
    def _finite(name: str, v, *, positive: bool = False) -> None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{name} must be a number")
        if not math.isfinite(v):
            raise ValueError(f"{name} must be finite")
        if positive and v <= 0:
            raise ValueError(f"{name} must be > 0")

    def _validate(self, p: dict) -> None:
        q = p["quantity"]
        if isinstance(q, bool) or not isinstance(q, int):
            raise ValueError(
                "quantity must be an integer (no silent truncation)")
        if q <= 0:
            raise ValueError("quantity must be positive")
        dq = p["disclosedQuantity"]
        if isinstance(dq, bool) or not isinstance(dq, int) \
                or dq < 0 or dq > q:
            raise ValueError("disclosedQuantity must be an integer in "
                             "[0, quantity]")
        if p["exchangeSegment"] not in self._SEGMENTS:
            raise ValueError(
                f"unsupported exchangeSegment: {p['exchangeSegment']!r}")
        if p["transactionType"] not in ("BUY", "SELL"):
            raise ValueError("transactionType must be BUY or SELL")
        if p["productType"] not in self._PRODUCTS:
            raise ValueError(f"unsupported productType: {p['productType']!r}")
        if p["orderType"] not in self._ORDER_TYPES:
            raise ValueError(f"unsupported orderType: {p['orderType']!r}")
        if p["validity"] not in self._VALIDITIES:
            raise ValueError(f"unsupported validity: {p['validity']!r}")
        if not str(p["securityId"]).strip():
            raise ValueError("securityId must be a non-empty string")
        self._finite("price", p["price"])
        self._finite("triggerPrice", p["triggerPrice"])
        if p["orderType"] in ("LIMIT", "STOP_LOSS"):
            self._finite("price", p["price"], positive=True)
        if p["orderType"] in ("STOP_LOSS", "STOP_LOSS_MARKET"):
            self._finite("triggerPrice", p["triggerPrice"], positive=True)
        if p["exchangeSegment"].endswith("FNO"):
            if not p.get("drvExpiryDate") or not p.get("drvOptionType"):
                raise ValueError("F&O orders need drvExpiryDate + drvOptionType")
            if p["drvOptionType"] not in ("CE", "PE"):
                raise ValueError("drvOptionType must be CE or PE")
            self._finite("drvStrikePrice", p.get("drvStrikePrice") or 0.0,
                         positive=True)

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    def _place(self, path: str, payload: dict) -> dict:
        """Shared placement path. Paper: local PAPER- id + paper ledger.
        Live: the id comes from Dhan's response body — never invented."""
        out = self._request("POST", path, payload)
        if not self.live:
            order_id = "PAPER-" + uuid.uuid4().hex[:12].upper()
            self._orders[order_id] = _PaperDhanOrder(order_id=order_id,
                                                     payload=payload)
            out["order_id"] = order_id
            return out
        body = out.get("response") or {}
        broker_id = body.get("orderId") or body.get("order_id")
        if not broker_id or not isinstance(broker_id, str):
            raise DhanOrderError(
                "Dhan response contained no orderId; refusing to "
                "substitute a local one")
        self._live_orders[broker_id] = payload
        out["order_id"] = broker_id
        return out

    def place_order(self, **kwargs) -> dict:
        payload = self.build_order_payload(**kwargs)
        return self._place("/orders", payload)

    def modify_order(self, order_id: str, **changes) -> dict:
        if not self.live:
            po = self._orders.get(order_id)
            if po is None:
                raise DhanOrderError(
                    f"unknown paper order {order_id!r}: nothing modified")
            merged = {**po.payload, **changes}
            self._validate({**merged,
                            "drvExpiryDate": merged.get("drvExpiryDate"),
                            "drvOptionType": merged.get("drvOptionType")})
            po.payload.update(changes)
            po.status = "MODIFIED"
        payload = {"dhanClientId": self.client_id or "PAPER-CLIENT",
                   "orderId": order_id, **changes}
        out = self._request("PUT", f"/orders/{order_id}", payload)
        if self.live and order_id in self._live_orders:
            self._live_orders[order_id].update(changes)
        return out

    def cancel_order(self, order_id: str) -> dict:
        if not self.live:
            po = self._orders.get(order_id)
            if po is None:
                raise DhanOrderError(
                    f"unknown paper order {order_id!r}: nothing cancelled")
            po.status = "CANCELLED"
        out = self._request("DELETE", f"/orders/{order_id}",
                            {"orderId": order_id})
        if self.live:
            self._live_orders.pop(order_id, None)
        return out

    def place_super_order(self, *, security_id: str, exchange_segment: str,
                          transaction_type: str, quantity: int,
                          price: float, product_type: str = "INTRADAY",
                          target_price: float, stop_loss_price: float,
                          trailing_jump: float = 0.0,
                          validity: str = "DAY", **drv_kwargs) -> dict:
        """Dhan Super Order: entry + target + SL (+ optional trailing jump)
        in one request -> POST /super/orders."""
        for name, v, pos in (("target_price", target_price, True),
                             ("stop_loss_price", stop_loss_price, True),
                             ("trailing_jump", trailing_jump, False)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not math.isfinite(v) or (v <= 0 if pos else v < 0):
                raise ValueError(
                    f"{name} must be {'positive' if pos else '>= 0'} "
                    "and finite")
        base = self.build_order_payload(
            security_id=security_id, exchange_segment=exchange_segment,
            transaction_type=transaction_type, quantity=quantity,
            order_type="LIMIT", product_type=product_type, price=price,
            validity=validity, **drv_kwargs)
        payload = {**base,
                   "targetPrice": float(target_price),
                   "stopLossPrice": float(stop_loss_price),
                   "trailingJump": float(trailing_jump)}
        return self._place("/super/orders", payload)

    def place_bracket_order(self, *, security_id: str, exchange_segment: str,
                            transaction_type: str, quantity: int,
                            price: float, target_points: float,
                            stoploss_points: float,
                            product_type: str = "BO",
                            trailing_jump: float = 0.0, **kw) -> dict:
        """Bracket-style helper: converts point offsets to absolute
        target / stop-loss and routes through the Super Order API."""
        sign = 1 if transaction_type == self.TXN_BUY else -1
        target = price + sign * target_points
        stop = price - sign * stoploss_points
        return self.place_super_order(
            security_id=security_id, exchange_segment=exchange_segment,
            transaction_type=transaction_type, quantity=quantity,
            price=price, product_type=product_type, target_price=target,
            stop_loss_price=stop, trailing_jump=trailing_jump, **kw)

    # ------------------------------------------------------------------ #
    # Fetchers
    # ------------------------------------------------------------------ #
    def get_orders(self) -> dict:
        out = self._request("GET", "/orders")
        if not self.live:
            data = [{"orderId": o.order_id, "orderStatus": o.status,
                     **o.payload} for o in self._orders.values()]
            out["response"] = {"status": "success", "data": data}
        return out

    def get_order_by_id(self, order_id: str) -> dict:
        out = self._request("GET", f"/orders/{order_id}")
        if not self.live:
            o = self._orders.get(order_id)
            out["response"] = {"status": "success",
                               "data": ({"orderId": o.order_id,
                                         "orderStatus": o.status,
                                         **o.payload} if o else None)}
        return out

    def reconcile(self) -> dict:
        """Compare local order stores against Dhan's order book.
        Buckets: matched / missing_at_broker / unknown_locally (see the
        Kite bridge for semantics). Paper mode is trivially consistent."""
        if not self.live:
            return {"mode": "paper", "matched": sorted(self._orders),
                    "missing_at_broker": [], "unknown_locally": []}
        out = self.get_orders()
        body = out.get("response")
        rows = body if isinstance(body, list) else (
            body.get("data", []) if isinstance(body, dict) else [])
        broker_ids = {str(r["orderId"]) for r in rows
                      if isinstance(r, dict) and r.get("orderId")}
        local = set(self._live_orders)
        return {"mode": "live",
                "matched": sorted(local & broker_ids),
                "missing_at_broker": sorted(local - broker_ids),
                "unknown_locally": sorted(broker_ids - local)}

    def get_positions(self) -> dict:
        out = self._request("GET", "/positions")
        if not self.live:
            out["response"] = {"status": "success", "data": []}
        return out

    def get_holdings(self) -> dict:
        out = self._request("GET", "/holdings")
        if not self.live:
            out["response"] = {"status": "success", "data": []}
        return out

    def get_fund_limits(self) -> dict:
        out = self._request("GET", "/fundlimit")
        if not self.live:
            out["response"] = {"status": "success", "data": {
                "availabelBalance": 1_000_000.0, "utilizedAmount": 0.0}}
        return out

    @property
    def request_log(self) -> list[dict]:
        return list(self._request_log)


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    dhan = DhanBridge(live=False)
    print("=" * 62)
    print("  DHANHQ BRIDGE (paper mode) — order routing demo")
    print("=" * 62)
    res = dhan.place_order(
        security_id="49081", exchange_segment=dhan.NSE_FNO,
        transaction_type=dhan.TXN_BUY, quantity=25,
        order_type=dhan.TYPE_LIMIT, product_type=dhan.PRODUCT_MARGIN,
        price=112.35, drv_expiry_date="2024-12-26",
        drv_option_type="CE", drv_strike_price=24300.0)
    print("  Regular order :", res["order_id"])
    print("  Payload keys  :", ", ".join(sorted(res["request"]["payload"])[:8]),
          "...")
    sup = dhan.place_super_order(
        security_id="49081", exchange_segment=dhan.NSE_FNO,
        transaction_type=dhan.TXN_BUY, quantity=25, price=112.35,
        target_price=132.0, stop_loss_price=102.0, trailing_jump=1.5,
        drv_expiry_date="2024-12-26", drv_option_type="CE",
        drv_strike_price=24300.0)
    print("  Super order   :", sup["order_id"],
          "| target 132.0 | SL 102.0 | trail 1.5")
    bo = dhan.place_bracket_order(
        security_id="1333", exchange_segment=dhan.NSE_EQ,
        transaction_type=dhan.TXN_BUY, quantity=10, price=1650.0,
        target_points=30.0, stoploss_points=15.0)
    print("  Bracket helper:", bo["order_id"], "| target 1680.0 | SL 1635.0")
    print("  Orders on book:", len(dhan.get_orders()["response"]["data"]))
    print("=" * 62)
