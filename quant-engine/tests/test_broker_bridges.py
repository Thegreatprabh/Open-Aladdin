"""Regression tests for the broker bridges (stdlib runner, fully mocked).

Covers the P0 honesty defects fixed on branch fix/critical-trading-safety:
  * a live placement returns the BROKER's order id — never a local UUID
  * paper and live orders live in separate stores
  * no order id is invented when the broker errors or returns none
  * modify/cancel of an unknown paper order fails loudly (no fake success)
  * modify/cancel in live mode actually reach the broker SDK/API
  * session exchange reports no fake `checksum_verified` and never leaks
    the access token or echoes the request token
  * strict order validation (quantity type, NaN/negative prices, vocab,
    symbol shape, disclosed quantity)

No network is used: the Kite SDK is a fake module and Dhan's urlopen is
monkeypatched. Run: python3 quant-engine/tests/test_broker_bridges.py
"""
import io
import json
import os
import sys
import types
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from brokers.zerodha_kite_bridge import (  # noqa: E402
    KiteBridge, KiteCredentialsError, KiteOrderError)
from brokers.dhan_hq_bridge import (  # noqa: E402
    DhanBridge, DhanOrderError)

FAIL = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


def expect_raises(name, exc, fn):
    try:
        fn()
        check(name, False, "no exception raised")
    except exc:
        check(name, True)
    except Exception as e:  # noqa: BLE001
        check(name, False, f"wrong exception: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# Fake KiteConnect SDK
# --------------------------------------------------------------------------- #
class FakeKite:
    placed = []
    behavior = "ok"  # ok | raise | empty | dict
    modified = []
    cancelled = []

    def __init__(self, api_key):
        self.api_key = api_key

    def set_access_token(self, token):
        self.token = token

    def place_order(self, **payload):
        FakeKite.placed.append(payload)
        if FakeKite.behavior == "raise":
            raise RuntimeError("insufficient funds (broker)")
        if FakeKite.behavior == "empty":
            return ""
        if FakeKite.behavior == "dict":
            return {"order_id": "BRK-DICT-1"}
        return "BRK-LIVE-123"

    def modify_order(self, **kw):
        FakeKite.modified.append(kw)
        return kw["order_id"]

    def cancel_order(self, **kw):
        FakeKite.cancelled.append(kw)
        return kw["order_id"]

    orders_data = []

    def orders(self):
        return list(FakeKite.orders_data)

    def generate_session(self, request_token, api_secret=None):
        return {"access_token": "SECRET-TOKEN-XYZ", "user_id": "AB1234",
                "user_name": "Test User", "login_time": "2026-10-20 09:15"}


fake_mod = types.ModuleType("kiteconnect")
fake_mod.KiteConnect = FakeKite
sys.modules["kiteconnect"] = fake_mod

os.environ.update({"KITE_API_KEY": "key123", "KITE_API_SECRET": "sec",
                   "KITE_ACCESS_TOKEN": "tok", "KITE_USER_ID": "AB1234",
                   "DHAN_CLIENT_ID": "dhan-client", "DHAN_ACCESS_TOKEN": "dtok"})

VALID = dict(exchange="NFO", tradingsymbol="NIFTY24DEC24300CE",
             transaction_type="BUY", quantity=50, product="NRML",
             order_type="LIMIT", price=112.35)

# --- Zerodha: paper placement ---------------------------------------------- #
kb = KiteBridge(live=False)
out = kb.place_order(**VALID)
check("kite paper id is PAPER- prefixed", out["order_id"].startswith("PAPER-"))
check("kite paper stored in paper store", out["order_id"] in kb._paper_orders)
check("kite paper mode reported", out["mode"] == "paper")

# --- Zerodha: live placement returns the broker id -------------------------- #
FakeKite.behavior = "ok"
kl = KiteBridge(live=True)
out = kl.place_order(**VALID)
check("kite live id is the broker's", out["order_id"] == "BRK-LIVE-123",
      f"got {out['order_id']}")
check("kite live NOT in paper store", out["order_id"] not in kl._paper_orders
      and len(kl._paper_orders) == 0)
check("kite live tracked separately", "BRK-LIVE-123" in kl._live_orders)

FakeKite.behavior = "dict"
out = kl.place_order(**VALID)
check("kite live dict response id used", out["order_id"] == "BRK-DICT-1")

FakeKite.behavior = "raise"
expect_raises("kite live broker error propagates", KiteOrderError,
              lambda: kl.place_order(**VALID))
FakeKite.behavior = "empty"
expect_raises("kite live missing id fails loudly", KiteOrderError,
              lambda: kl.place_order(**VALID))
FakeKite.behavior = "ok"

# --- Zerodha: modify/cancel -------------------------------------------------- #
expect_raises("kite modify unknown paper order raises", KiteOrderError,
              lambda: kb.modify_order(order_id="PAPER-NOPE", price=1.0))
expect_raises("kite cancel unknown paper order raises", KiteOrderError,
              lambda: kb.cancel_order(order_id="PAPER-NOPE"))
pid = kb.place_order(**VALID)["order_id"]
kb.modify_order(order_id=pid, price=113.0)
check("kite paper modify applied",
      kb._paper_orders[pid].payload["price"] == 113.0)
kb.cancel_order(order_id=pid)
check("kite paper cancel applied", kb._paper_orders[pid].status == "CANCELLED")

FakeKite.modified.clear(); FakeKite.cancelled.clear()
kl.modify_order(order_id="BRK-LIVE-123", price=114.0)
check("kite live modify reached SDK", len(FakeKite.modified) == 1
      and FakeKite.modified[0]["order_id"] == "BRK-LIVE-123")
kl.cancel_order(order_id="BRK-LIVE-123")
check("kite live cancel reached SDK", len(FakeKite.cancelled) == 1)

# --- Zerodha: session honesty ------------------------------------------------- #
sess = kb.generate_session("REQTOKEN123")
check("kite paper session has no token echo",
      "REQTOKEN123" not in json.dumps(sess) and "checksum_verified" not in sess)
sess = kl.generate_session("REQTOKEN123")
blob = json.dumps(sess)
check("kite live session leaks no access token", "SECRET-TOKEN-XYZ" not in blob)
check("kite live session has no fake checksum claim",
      "checksum_verified" not in sess)
check("kite live session stores token on instance only",
      kl.access_token == "SECRET-TOKEN-XYZ")
expect_raises("kite empty request token rejected", KiteCredentialsError,
              lambda: kl.generate_session(""))

# --- Zerodha: strict validation ------------------------------------------------ #
for name, patch in [
    ("qty zero", {"quantity": 0}), ("qty negative", {"quantity": -5}),
    ("qty float", {"quantity": 2.5}), ("qty bool", {"quantity": True}),
    ("price NaN", {"price": float("nan")}), ("price negative", {"price": -1.0}),
    ("price inf", {"price": float("inf")}),
    ("bad exchange", {"exchange": "XXX"}), ("bad side", {"transaction_type": "HOLD"}),
    ("bad product", {"product": "XXX"}), ("bad order type", {"order_type": "ICEBERG"}),
    ("empty symbol", {"tradingsymbol": "  "}),
    ("dq > qty", {"disclosed_quantity": 51}),
    ("tag too long", {"tag": "x" * 21}),
]:
    bad = {**VALID, **patch}
    expect_raises(f"kite rejects {name}", ValueError,
                  lambda b=bad: kb.place_order(**b))
expect_raises("kite SL without trigger rejected", ValueError,
              lambda: kb.place_order(**{**VALID, "order_type": "SL-M", "price": None}))

# --------------------------------------------------------------------------- #
# Dhan (urlopen monkeypatched)
# --------------------------------------------------------------------------- #
import brokers.dhan_hq_bridge as dhan_mod  # noqa: E402


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_urlopen_factory(body=None, error=None):
    def _open(req, timeout=0):
        if error is not None:
            raise error
        return FakeResp(json.dumps(body or {}).encode())
    return _open


GOOD = dict(security_id="49081", exchange_segment="NSE_FNO",
            transaction_type="BUY", quantity=25, order_type="LIMIT",
            product_type="MARGIN", price=112.35,
            drv_expiry_date="2026-12-24", drv_option_type="CE",
            drv_strike_price=24300.0)

db = DhanBridge(live=False)
out = db.place_order(**GOOD)
check("dhan paper id is PAPER- prefixed", out["order_id"].startswith("PAPER-"))
check("dhan paper stored", out["order_id"] in db._orders)

dl = DhanBridge(live=True)
dhan_mod.urllib.request.urlopen = fake_urlopen_factory(
    {"orderId": "DHAN-LIVE-77", "orderStatus": "PENDING"})
out = dl.place_order(**GOOD)
check("dhan live id is the broker's", out["order_id"] == "DHAN-LIVE-77",
      f"got {out['order_id']}")
check("dhan live NOT in paper store", len(dl._orders) == 0)
check("dhan live tracked separately", "DHAN-LIVE-77" in dl._live_orders)

dhan_mod.urllib.request.urlopen = fake_urlopen_factory({"orderStatus": "PENDING"})
expect_raises("dhan live missing orderId fails loudly", DhanOrderError,
              lambda: dl.place_order(**GOOD))
dhan_mod.urllib.request.urlopen = fake_urlopen_factory(
    error=urllib.error.HTTPError("https://api.dhan.co", 400, "bad",
                                 {}, io.BytesIO(b'{"error": "bad order"}')))
expect_raises("dhan live HTTP error fails closed", DhanOrderError,
              lambda: dl.place_order(**GOOD))

expect_raises("dhan Paper modify unknown raises", DhanOrderError,
              lambda: db.modify_order("PAPER-NOPE", price=1.0))
expect_raises("dhan Paper cancel unknown raises", DhanOrderError,
              lambda: db.cancel_order("PAPER-NOPE"))
pid = db.place_order(**GOOD)["order_id"]
db.modify_order(pid, price=113.0)
check("dhan paper modify applied", db._orders[pid].payload["price"] == 113.0)
db.cancel_order(pid)
check("dhan paper cancel applied", db._orders[pid].status == "CANCELLED")

for name, patch in [
    ("qty float truncation", {"quantity": 25.7}),
    ("qty zero", {"quantity": 0}), ("qty bool", {"quantity": True}),
    ("NaN price", {"price": float("nan")}),
    ("bad segment", {"exchange_segment": "NYSE"}),
    ("bad product", {"product_type": "XXX"}),
    ("bad option type", {"drv_option_type": "XX"}),
    ("empty security", {"security_id": " "}),
    ("dq > qty", {"disclosed_quantity": 26}),
]:
    bad = {**GOOD, **patch}
    expect_raises(f"dhan rejects {name}", ValueError,
                  lambda b=bad: db.place_order(**b))

# --- Reconciliation + retry policy ------------------------------------------- #
FakeKite.behavior = "ok"
kl2 = KiteBridge(live=True)
placed_id = kl2.place_order(**VALID)["order_id"]
FakeKite.orders_data = [{"order_id": placed_id}, {"order_id": "FOREIGN-1"}]
rec = kl2.reconcile()
check("kite reconcile matched", rec["matched"] == [placed_id])
check("kite reconcile unknown_locally", rec["unknown_locally"] == ["FOREIGN-1"])
check("kite reconcile nothing missing", rec["missing_at_broker"] == [])
FakeKite.orders_data = []
rec = kl2.reconcile()
check("kite reconcile detects missing at broker",
      rec["missing_at_broker"] == [placed_id])
rec = kb.reconcile()
check("kite paper reconcile trivial", rec["mode"] == "paper"
      and rec["missing_at_broker"] == [])

calls_before = len(FakeKite.placed)
FakeKite.behavior = "raise"
try:
    kl2.place_order(**VALID)
except KiteOrderError:
    pass
FakeKite.behavior = "ok"
check("kite placement is single-shot (no blind retry)",
      len(FakeKite.placed) == calls_before + 1)

dhan_mod.urllib.request.urlopen = fake_urlopen_factory(
    [{"orderId": "DHAN-LIVE-77", "orderStatus": "PENDING"}])
rec = dl.reconcile()
check("dhan reconcile matched", rec["matched"] == ["DHAN-LIVE-77"])
rec = db.reconcile()
check("dhan paper reconcile trivial", rec["mode"] == "paper")

print()
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}")
sys.exit(1 if FAIL else 0)
