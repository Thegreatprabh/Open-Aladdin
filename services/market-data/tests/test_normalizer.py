from market_data.instruments import InstrumentRegistry
from market_data.normalizer import Normalizer, epoch_ms_to_iso

TS = 1792548903000  # fixed exchange time


def make_norm():
    return Normalizer(InstrumentRegistry(), "SIMULATED")


def tick(tk=256265, fid=1, ts=TS, **over):
    frame = {"tk": tk, "ltp": 100.0, "o": 99.0, "h": 101.0, "l": 98.0,
             "c": 97.0, "vol": 1000, "oi": 5, "ts": ts, "fid": fid}
    frame.update(over)
    return frame


def test_tick_maps_to_canonical():
    ev = make_norm().normalize(tick())[0]
    assert ev["type"] == "tick" and ev["symbol"] == "NIFTY"
    assert ev["instrumentToken"] == 256265
    assert ev["ltp"] == 100.0 and ev["volume"] == 1000 and ev["oi"] == 5
    assert ev["seq"] == 1 and ev["source"] == "SIMULATED" and ev["v"] == 1


def test_exchange_ts_is_ist_iso():
    assert epoch_ms_to_iso(TS).endswith("+05:30")
    ev = make_norm().normalize(tick())[0]
    assert ev["exchangeTs"] == epoch_ms_to_iso(TS)


def test_seq_is_per_symbol_and_monotonic():
    n = make_norm()
    a = n.normalize(tick(fid=1))[0]
    b = n.normalize(tick(tk=260105, fid=2))[0]   # BANKNIFTY
    c = n.normalize(tick(fid=3))[0]
    assert (a["seq"], b["seq"], c["seq"]) == (1, 1, 2)


def test_duplicate_frame_dropped_and_counted():
    n = make_norm()
    assert len(n.normalize(tick(fid=7))) == 1
    assert n.normalize(tick(fid=7)) == []
    assert n.stats["dropped_duplicate"] == 1


def test_stale_frame_dropped_and_counted():
    n = make_norm()
    n.normalize(tick(fid=10, ts=TS + 5000))
    assert n.normalize(tick(fid=11, ts=TS)) == []        # older exchange ts
    assert n.normalize(tick(fid=9, ts=TS + 9000)) == []  # lower frame id
    assert n.stats["dropped_stale"] == 2


def test_malformed_frames_dropped_and_counted():
    n = make_norm()
    assert n.normalize({"ltp": 1}) == []                 # no tk/ts/fid
    assert n.normalize({"tk": 424242, "ts": TS, "fid": 1, "ltp": 1.0,
                        "o": 1, "h": 1, "l": 1, "c": 1, "vol": 1}) == []
    assert n.normalize("not a dict") == []
    assert n.stats["dropped_malformed"] == 3


def test_depth_frame_maps():
    n = make_norm()
    frame = {"tk": 256265, "ts": TS, "fid": 1,
             "depth": {"buy": [[99.95, 250], [99.90, 500]],
                       "sell": [[100.05, 300]]}}
    ev = n.normalize(frame)[0]
    assert ev["type"] == "depth"
    assert ev["bids"] == [[99.95, 250], [99.90, 500]]
    assert ev["asks"] == [[100.05, 300]]
    assert ev["seq"] == 1


def test_status_frames_map():
    n = make_norm()
    ev = n.normalize({"ev": "token_expired", "msg": "expired"})[0]
    assert ev["type"] == "status" and ev["state"] == "TOKEN_EXPIRED"
    ev = n.normalize({"ev": "disconnect", "msg": "bye"})[0]
    assert ev["state"] == "RECONNECTING"
    ev = n.normalize({"ev": "reconnect", "msg": "back"})[0]
    assert ev["state"] == "CONNECTED"


def test_unknown_status_event_dropped():
    n = make_norm()
    assert n.normalize({"ev": "party", "msg": "?"}) == []
    assert n.stats["dropped_malformed"] == 1


def test_stats_accounting():
    n = make_norm()
    n.normalize(tick(fid=1))
    n.normalize(tick(fid=1))       # duplicate
    n.normalize({"junk": True})    # malformed
    s = n.stats
    assert s["received"] == 3 and s["emitted"] == 1
    assert s["dropped_duplicate"] == 1 and s["dropped_malformed"] == 1
