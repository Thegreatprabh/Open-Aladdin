"""Normalizer: broker-shaped raw frames -> canonical events (schema.py).

Responsibilities (design sections 3 and 7):
  - resolve instrument token -> symbol (unknown tokens are dropped)
  - stamp provenance `source` from the upstream adapter — never downstream
  - assign the per-symbol monotonically increasing `seq`
  - convert exchange epoch-ms to ISO 8601 (+05:30) `exchangeTs`
  - drop duplicate frames (same frame id), stale frames (older exchange
    timestamp / lower frame id), and malformed frames — every drop is
    counted in `stats`, never silent
  - map upstream status frames to canonical status events; token expiry
    is a first-class state (design section 6)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import EVENT_VERSION
from .instruments import InstrumentRegistry, UnknownInstrument
from .schema import validate_event

IST = timezone(timedelta(hours=5, minutes=30))

_STATUS_MAP = {
    "token_expired": "TOKEN_EXPIRED",
    "disconnect": "RECONNECTING",
    "reconnect": "CONNECTED",
}


def epoch_ms_to_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=IST).isoformat()


class _SymbolState:
    __slots__ = ("seq", "last_fid", "last_ts")

    def __init__(self) -> None:
        self.seq = 0
        self.last_fid = -1
        self.last_ts = -1


class Normalizer:
    def __init__(self, registry: InstrumentRegistry, source: str):
        if source not in ("LIVE", "SIMULATED"):
            raise ValueError(f"bad source {source!r}")
        self.registry = registry
        self.source = source
        self._state: dict[str, _SymbolState] = {}
        self.stats = {
            "received": 0, "emitted": 0, "dropped_duplicate": 0,
            "dropped_stale": 0, "dropped_malformed": 0,
        }

    # -- public API ---------------------------------------------------------

    def normalize(self, raw: dict) -> list[dict]:
        """Normalize one raw frame. Returns [] when the frame is dropped."""
        self.stats["received"] += 1
        if not isinstance(raw, dict):
            self.stats["dropped_malformed"] += 1
            return []
        if "ev" in raw:  # upstream status frame
            return self._status(raw)
        try:
            symbol = self.registry.symbol_for(raw["tk"])
            ts = int(raw["ts"])
            fid = int(raw["fid"])
        except (KeyError, TypeError, ValueError, UnknownInstrument):
            self.stats["dropped_malformed"] += 1
            return []

        st = self._state.setdefault(symbol, _SymbolState())
        if fid == st.last_fid:
            self.stats["dropped_duplicate"] += 1
            return []
        if fid < st.last_fid or ts < st.last_ts:
            self.stats["dropped_stale"] += 1
            return []
        st.last_fid, st.last_ts = fid, ts
        st.seq += 1

        try:
            if "depth" in raw:
                event = self._depth(raw, symbol, ts, st.seq)
            else:
                event = self._tick(raw, symbol, ts, st.seq)
        except (KeyError, TypeError, ValueError):
            self.stats["dropped_malformed"] += 1
            return []
        validate_event(event)  # the normalizer never emits off-contract data
        self.stats["emitted"] += 1
        return [event]

    # -- frame kinds --------------------------------------------------------

    def _tick(self, raw: dict, symbol: str, ts: int, seq: int) -> dict:
        return {
            "v": EVENT_VERSION, "type": "tick", "symbol": symbol,
            "instrumentToken": self.registry.token_for(symbol),
            "ltp": float(raw["ltp"]), "open": float(raw["o"]),
            "high": float(raw["h"]), "low": float(raw["l"]),
            "close": float(raw["c"]), "volume": int(raw["vol"]),
            "oi": int(raw.get("oi", 0)),
            "exchangeTs": epoch_ms_to_iso(ts), "seq": seq,
            "source": self.source,
        }

    def _depth(self, raw: dict, symbol: str, ts: int, seq: int) -> dict:
        depth = raw["depth"]
        bids = [[float(p), int(q)] for p, q in depth["buy"]]
        asks = [[float(p), int(q)] for p, q in depth["sell"]]
        return {
            "v": EVENT_VERSION, "type": "depth", "symbol": symbol,
            "bids": bids, "asks": asks,
            "exchangeTs": epoch_ms_to_iso(ts), "seq": seq,
            "source": self.source,
        }

    def _status(self, raw: dict) -> list[dict]:
        state = _STATUS_MAP.get(str(raw.get("ev", "")))
        if state is None:
            self.stats["dropped_malformed"] += 1
            return []
        event = {
            "v": EVENT_VERSION, "type": "status", "state": state,
            "detail": str(raw.get("msg", "")), "source": self.source,
        }
        validate_event(event)
        self.stats["emitted"] += 1
        return [event]
