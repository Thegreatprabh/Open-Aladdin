"""Canonical event schema (design doc section 3) — the contract.

Every event is a plain JSON dict carrying:
  v          schema version (EVENT_VERSION)
  type       "tick" | "depth" | "status" | "heartbeat"
  source     "LIVE" | "SIMULATED" — provenance, stamped by the normalizer
             from the upstream adapter, never invented downstream
  seq        per-symbol monotonically increasing sequence (tick/depth)
  exchangeTs exchange timestamp (ISO 8601, +05:30) — never the server clock

validate_event() is the contract test surface: anything the pipeline emits
must pass it, and the frontend's LiveProvider (P2) is tested against the
same shapes.
"""

from __future__ import annotations

from . import EVENT_VERSION

SOURCES = ("LIVE", "SIMULATED")
STATUS_STATES = ("CONNECTED", "RECONNECTING", "STALE", "TOKEN_EXPIRED")

_TICK_FIELDS = {
    "v": int, "type": str, "symbol": str, "instrumentToken": int,
    "ltp": (int, float), "open": (int, float), "high": (int, float),
    "low": (int, float), "close": (int, float), "volume": int, "oi": int,
    "exchangeTs": str, "seq": int, "source": str,
}
_DEPTH_FIELDS = {
    "v": int, "type": str, "symbol": str, "bids": list, "asks": list,
    "exchangeTs": str, "seq": int, "source": str,
}
_STATUS_FIELDS = {
    "v": int, "type": str, "state": str, "detail": str, "source": str,
}


class SchemaError(ValueError):
    """Raised when an event does not conform to the canonical schema."""


def _check(event: dict, fields: dict, kind: str) -> None:
    if not isinstance(event, dict):
        raise SchemaError(f"{kind}: event is not a dict")
    for name, types in fields.items():
        if name not in event:
            raise SchemaError(f"{kind}: missing field {name!r}")
        if not isinstance(event[name], types):
            raise SchemaError(f"{kind}: field {name!r} has wrong type")
    if event["v"] != EVENT_VERSION:
        raise SchemaError(f"{kind}: unsupported version {event['v']!r}")
    if event["source"] not in SOURCES:
        raise SchemaError(f"{kind}: bad source {event['source']!r}")


def validate_event(event: dict) -> dict:
    """Return the event unchanged if it conforms; raise SchemaError else."""
    kind = event.get("type") if isinstance(event, dict) else None
    if kind == "tick":
        _check(event, _TICK_FIELDS, "tick")
        if event["seq"] < 1:
            raise SchemaError("tick: seq must start at 1")
    elif kind == "depth":
        _check(event, _DEPTH_FIELDS, "depth")
        for side in ("bids", "asks"):
            for level in event[side]:
                if (not isinstance(level, list) or len(level) != 2
                        or not all(isinstance(x, (int, float)) for x in level)):
                    raise SchemaError(f"depth: {side} levels must be [price, qty]")
        if event["seq"] < 1:
            raise SchemaError("depth: seq must start at 1")
    elif kind == "status":
        _check(event, _STATUS_FIELDS, "status")
        if event["state"] not in STATUS_STATES:
            raise SchemaError(f"status: bad state {event['state']!r}")
    elif kind == "heartbeat":
        _check(event, {"v": int, "type": str, "ts": str, "source": str}, "heartbeat")
    else:
        raise SchemaError(f"unknown event type {kind!r}")
    return event
