"""Correctness checks for pricing/bsm_higher_order.py (stdlib runner).

Verifies, against independent references and no-arbitrage identities:
  * the Hull textbook vector (S=42, K=40, T=0.5, r=0.10, sigma=0.20)
  * put-call parity across a strike grid
  * no-arbitrage price bounds for calls and puts
  * monotonicity of price in spot and in volatility
  * implied-vol round-trip (price -> IV -> sigma)
  * Greeks identities: call delta - put delta = e^{-qT}; gamma/vega equal
    for call and put at the same strike
  * invalid inputs raise ValueError (never NaN/crash)

Run: python3 quant-engine/tests/test_bsm_correctness.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pricing"))
from bsm_higher_order import BSMOption, implied_vol  # noqa: E402

FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


# 1. Hull textbook reference: c ~= 4.76, p ~= 0.81
c = BSMOption(42, 40, 0.5, 0.10, 0.20, kind="call").price()
p = BSMOption(42, 40, 0.5, 0.10, 0.20, kind="put").price()
check("Hull reference call", abs(c - 4.76) < 0.01, f"got {c}")
check("Hull reference put", abs(p - 0.81) < 0.01, f"got {p}")

# 2. Put-call parity: C - P = S e^{-qT} - K e^{-rT}
for S, K, T, r, q, sig in [(100, 100, 1.0, 0.05, 0.0, 0.2),
                           (25742, 25800, 0.08, 0.065, 0.012, 0.13),
                           (80, 120, 2.0, 0.03, 0.01, 0.35)]:
    cc = BSMOption(S, K, T, r, sig, q, "call").price()
    pp = BSMOption(S, K, T, r, sig, q, "put").price()
    rhs = S * math.exp(-q * T) - K * math.exp(-r * T)
    check(f"parity S={S} K={K}", abs((cc - pp) - rhs) < 1e-8, f"{cc-pp} vs {rhs}")

# 3. No-arbitrage bounds
for K in (60, 80, 100, 120, 150):
    S, T, r, q, sig = 100.0, 1.0, 0.05, 0.01, 0.25
    cc = BSMOption(S, K, T, r, sig, q, "call").price()
    pp = BSMOption(S, K, T, r, sig, q, "put").price()
    clo, chi = max(S * math.exp(-q * T) - K * math.exp(-r * T), 0.0), S * math.exp(-q * T)
    plo, phi = max(K * math.exp(-r * T) - S * math.exp(-q * T), 0.0), K * math.exp(-r * T)
    check(f"bounds K={K}", clo - 1e-9 <= cc <= chi + 1e-9 and plo - 1e-9 <= pp <= phi + 1e-9)

# 4. Monotonicity in spot and vol
prices_s = [BSMOption(S, 100, 1.0, 0.05, 0.2, kind="call").price() for S in (80, 90, 100, 110, 120)]
check("call increasing in S", all(a < b for a, b in zip(prices_s, prices_s[1:])))
prices_v = [BSMOption(100, 100, 1.0, 0.05, v, kind="call").price() for v in (0.05, 0.1, 0.2, 0.4)]
check("call increasing in sigma", all(a < b for a, b in zip(prices_v, prices_v[1:])))

# 5. Implied-vol round-trip
for sig in (0.08, 0.13, 0.25, 0.6):
    pr = BSMOption(100, 105, 0.75, 0.05, sig, 0.01, "call").price()
    iv = implied_vol(pr, 100, 105, 0.75, 0.05, 0.01, "call")
    check(f"IV round-trip sigma={sig}", abs(iv - sig) < 1e-4, f"got {iv}")

# 6. Greeks identities
gc = BSMOption(100, 100, 1.0, 0.05, 0.2, 0.01, "call").greeks()
gp = BSMOption(100, 100, 1.0, 0.05, 0.2, 0.01, "put").greeks()
check("delta parity", abs((gc.delta - gp.delta) - math.exp(-0.01)) < 1e-9)
check("gamma call==put", abs(gc.gamma - gp.gamma) < 1e-12)
check("vega call==put", abs(gc.vega - gp.vega) < 1e-9)

# 7. Invalid inputs raise
for kwargs in [dict(S=-1), dict(K=0), dict(T=0), dict(sigma=0), dict(S=float("nan"))]:
    base = dict(S=100, K=100, T=1.0, r=0.05, sigma=0.2)
    base.update(kwargs)
    try:
        BSMOption(**base)
        check(f"rejects {kwargs}", False, "no error raised")
    except ValueError:
        check(f"rejects {kwargs}", True)

# IV outside no-arbitrage bounds raises
try:
    implied_vol(999.0, 100, 100, 1.0, 0.05)
    check("IV out-of-bounds raises", False)
except ValueError:
    check("IV out-of-bounds raises", True)

print()
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}")
sys.exit(1 if FAIL else 0)
