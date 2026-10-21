"""Contract tests: the canonical schema from design section 3.

The example tick below mirrors the design document's own example values;
if the schema drifts from the document, these tests fail.
"""

import pytest

from market_data import EVENT_VERSION
from market_data.schema import SchemaError, validate_event

DESIGN_TICK = {
    "v": 1, "type": "tick", "symbol": "NIFTY", "instrumentToken": 256265,
    "ltp": 25796.51, "open": 25680.0, "high": 25810.2, "low": 25650.0,
    "close": 25666.3, "volume": 18234500, "oi": 0,
    "exchangeTs": "2026-10-20T09:15:03+05:30", "seq": 918273,
    "source": "LIVE",
}
DESIGN_DEPTH = {
    "v": 1, "type": "depth", "symbol": "NIFTY",
    "bids": [[25796.45, 5079]], "asks": [[25797.50, 507]],
    "exchangeTs": "2026-10-20T09:15:03+05:30", "seq": 918274,
    "source": "LIVE",
}


def test_design_tick_conforms():
    assert validate_event(dict(DESIGN_TICK))["symbol"] == "NIFTY"


def test_design_depth_conforms():
    assert validate_event(dict(DESIGN_DEPTH))["type"] == "depth"


@pytest.mark.parametrize("state", ["CONNECTED", "RECONNECTING", "STALE",
                                   "TOKEN_EXPIRED"])
def test_status_states_conform(state):
    ev = {"v": 1, "type": "status", "state": state, "detail": "x",
          "source": "SIMULATED"}
    assert validate_event(ev)["state"] == state


def test_heartbeat_conforms():
    ev = {"v": 1, "type": "heartbeat", "ts": "2026-10-20T09:15:03+05:30",
          "source": "SIMULATED"}
    assert validate_event(ev)["type"] == "heartbeat"


def test_version_is_one():
    assert EVENT_VERSION == 1


@pytest.mark.parametrize("mutate", [
    lambda e: e.pop("seq"),
    lambda e: e.pop("source"),
    lambda e: e.update(v=99),
    lambda e: e.update(source="FAKE"),
    lambda e: e.update(ltp="25796.51"),
    lambda e: e.update(seq=0),
])
def test_tick_rejects_nonconforming(mutate):
    ev = dict(DESIGN_TICK)
    mutate(ev)
    with pytest.raises(SchemaError):
        validate_event(ev)


def test_depth_rejects_bad_levels():
    ev = dict(DESIGN_DEPTH)
    ev["bids"] = [[25796.45]]  # missing qty
    with pytest.raises(SchemaError):
        validate_event(ev)


def test_status_rejects_unknown_state():
    ev = {"v": 1, "type": "status", "state": "SLEEPING", "detail": "x",
          "source": "LIVE"}
    with pytest.raises(SchemaError):
        validate_event(ev)


def test_unknown_type_rejected():
    with pytest.raises(SchemaError):
        validate_event({"v": 1, "type": "candle", "source": "LIVE"})
