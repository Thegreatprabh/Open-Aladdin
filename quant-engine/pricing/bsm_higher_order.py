"""
Black-Scholes-Merton pricing with full higher-order Greeks.

Implements closed-form European option pricing under the BSM model and the
complete Greek ladder:

    1st order : delta, theta, vega, rho, lambda (elasticity)
    2nd order : gamma, vanna, charm, vomma (volga), vera, veta
    3rd order : speed, zomma, color, ultima

plus an implied-volatility solver (Newton-Raphson with a bisection fallback).

References
----------
Hull, J. C. (2018). Options, Futures, and Other Derivatives (10th ed.).
Hague, R. (2009). "Higher Order Greeks" — practitioner notes on 3rd-order
sensitivities (ultima, zomma, speed, color).

Only numpy / scipy / stdlib are used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

__all__ = ["BSMOption", "Greeks", "implied_vol"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _npdf(x: float) -> float:
    """Standard normal probability density function n(x)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _ncdf(x: float) -> float:
    """Standard normal CDF N(x) via math.erf (no scipy dependency for scalars)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# --------------------------------------------------------------------------- #
# Greeks container
# --------------------------------------------------------------------------- #
@dataclass
class Greeks:
    """All sensitivities of an option's value.  Theta/veta/color are per
    calendar day; charm is delta decay per day."""

    price: float
    # 1st order
    delta: float
    theta: float          # per day
    vega: float           # per 1.00 vol (multiply by 0.01 for per-1%)
    rho: float            # per 1.00 rate
    lambda_: float        # elasticity = delta * S / price
    # 2nd order
    gamma: float
    vanna: float          # dDelta/dSigma
    charm: float          # -dDelta/dTau, per day
    vomma: float          # dVega/dSigma (volga)
    vera: float           # dRho/dSigma
    veta: float           # dVega/dTau, per day
    # 3rd order
    speed: float          # dGamma/dS
    zomma: float          # dGamma/dSigma
    color: float          # dGamma/dTau, per day
    ultima: float         # dVomma/dSigma


# --------------------------------------------------------------------------- #
# Core BSM pricer
# --------------------------------------------------------------------------- #
class BSMOption:
    """European option under Black-Scholes-Merton with continuous yield.

    Parameters
    ----------
    S : float  Spot price.
    K : float  Strike price.
    T : float  Time to expiry in years.
    r : float  Risk-free rate (continuously compounded).
    sigma : float  Volatility (annualised).
    q : float  Continuous dividend / convenience yield.
    kind : str  "call" or "put".
    """

    def __init__(self, S: float, K: float, T: float, r: float, sigma: float,
                 q: float = 0.0, kind: str = "call"):
        if kind not in ("call", "put"):
            raise ValueError("kind must be 'call' or 'put'")
        if T <= 0:
            raise ValueError("T must be positive")
        if sigma <= 0:
            raise ValueError("sigma must be positive")
        if not all(math.isfinite(x) for x in (S, K, T, r, sigma, q)):
            raise ValueError("all inputs must be finite")
        if S <= 0:
            raise ValueError("S must be positive")
        if K <= 0:
            raise ValueError("K must be positive")
        self.S, self.K, self.T = float(S), float(K), float(T)
        self.r, self.sigma, self.q = float(r), float(sigma), float(q)
        self.kind = kind
        self._d1, self._d2 = self._d_values()

    # ------------------------------------------------------------------ #
    def _d_values(self) -> tuple[float, float]:
        S, K, T, r, q, s = self.S, self.K, self.T, self.r, self.q, self.sigma
        d1 = (math.log(S / K) + (r - q + 0.5 * s * s) * T) / (s * math.sqrt(T))
        d2 = d1 - s * math.sqrt(T)
        return d1, d2

    # ------------------------------------------------------------------ #
    def price(self) -> float:
        S, K, T, r, q = self.S, self.K, self.T, self.r, self.q
        d1, d2 = self._d1, self._d2
        if self.kind == "call":
            return S * math.exp(-q * T) * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
        return K * math.exp(-r * T) * _ncdf(-d2) - S * math.exp(-q * T) * _ncdf(-d1)

    # ------------------------------------------------------------------ #
    def greeks(self) -> Greeks:
        S, K, T, r, q, s = self.S, self.K, self.T, self.r, self.q, self.sigma
        d1, d2 = self._d1, self._d2
        sqrtT = math.sqrt(T)
        nd1 = _npdf(d1)
        disc_q = math.exp(-q * T)
        disc_r = math.exp(-r * T)
        price = self.price()
        is_call = self.kind == "call"

        # ------------------------- 1st order ------------------------- #
        if is_call:
            delta = disc_q * _ncdf(d1)
        else:
            delta = -disc_q * _ncdf(-d1)

        gamma = disc_q * nd1 / (S * s * sqrtT)

        theta_annual: float
        if is_call:
            theta_annual = (
                -(S * disc_q * nd1 * s) / (2.0 * sqrtT)
                + q * S * disc_q * _ncdf(d1)
                - r * K * disc_r * _ncdf(d2)
            )
        else:
            theta_annual = (
                -(S * disc_q * nd1 * s) / (2.0 * sqrtT)
                - q * S * disc_q * _ncdf(-d1)
                + r * K * disc_r * _ncdf(-d2)
            )
        theta = theta_annual / 365.0

        vega = S * disc_q * sqrtT * nd1

        if is_call:
            rho = K * T * disc_r * _ncdf(d2)
        else:
            rho = -K * T * disc_r * _ncdf(-d2)

        lambda_ = delta * S / price if price > 0 else float("nan")

        # ------------------------- 2nd order ------------------------- #
        vanna = -disc_q * nd1 * d2 / s
        # Charm = -dDelta/dTau (Haug closed form).
        drift_term = (2.0 * (r - q) * T - d2 * s * sqrtT) / (2.0 * T * s * sqrtT)
        if is_call:
            charm_annual = -disc_q * nd1 * drift_term + q * disc_q * _ncdf(d1)
        else:
            charm_annual = -disc_q * nd1 * drift_term - q * disc_q * _ncdf(-d1)
        charm = charm_annual / 365.0

        vomma = vega * d1 * d2 / s
        # vera = dRho/dSigma = ∓K T e^{-rT} n(d2) d1 / sigma
        nd2 = _npdf(d2)
        vera = (-K * T * disc_r * nd2 * d1 / s) if is_call else \
               (K * T * disc_r * nd2 * d1 / s)
        veta_annual = -S * disc_q * nd1 * sqrtT * (
            q + ((r - q) * d1) / (s * sqrtT) - (1.0 + d1 * d2) / (2.0 * T)
        )
        veta = veta_annual / 365.0

        # ------------------------- 3rd order ------------------------- #
        speed = -gamma / S * (d1 / (s * sqrtT) + 1.0)
        zomma = gamma * (d1 * d2 - 1.0) / s
        color_annual = -disc_q * nd1 / (2.0 * S * T * s * sqrtT) * (
            2.0 * q * T + 1.0 + (2.0 * (r - q) * T - d2 * s * sqrtT) / (s * sqrtT) * d1
        )
        color = color_annual / 365.0
        ultima = -vega / (s * s) * (d1 * d2 * (1.0 - d1 * d2) + d1 * d1 + d2 * d2)

        return Greeks(
            price=price, delta=delta, theta=theta, vega=vega, rho=rho,
            lambda_=lambda_, gamma=gamma, vanna=vanna, charm=charm,
            vomma=vomma, vera=vera, veta=veta, speed=speed, zomma=zomma,
            color=color, ultima=ultima,
        )


# --------------------------------------------------------------------------- #
# Implied volatility solver
# --------------------------------------------------------------------------- #
def implied_vol(market_price: float, S: float, K: float, T: float, r: float,
                q: float = 0.0, kind: str = "call",
                tol: float = 1e-8, max_iter: int = 100) -> float:
    """Solve for implied volatility.

    Newton-Raphson from a Manaster-Koehler style seed, with a bisection
    fallback on [1e-4, 5.0] if Newton diverges.
    """
    # Intrinsic / arbitrage bounds check
    disc_r, disc_q = math.exp(-r * T), math.exp(-q * T)
    if kind == "call":
        lower = max(S * disc_q - K * disc_r, 0.0)
        upper = S * disc_q
    else:
        lower = max(K * disc_r - S * disc_q, 0.0)
        upper = K * disc_r
    if market_price < lower - 1e-10 or market_price > upper + 1e-10:
        raise ValueError(f"market_price {market_price} outside no-arbitrage bounds "
                         f"[{lower}, {upper}]")

    def price_at(sig: float) -> float:
        return BSMOption(S, K, T, r, sig, q, kind).price()

    # --- Newton ------------------------------------------------------- #
    sigma = max(0.05, math.sqrt(2.0 * math.pi / T) * market_price / (S * disc_q))
    sigma = min(max(sigma, 1e-3), 3.0)
    for _ in range(max_iter):
        opt = BSMOption(S, K, T, r, sigma, q, kind)
        diff = opt.price() - market_price
        if abs(diff) < tol:
            return sigma
        vega = opt.greeks().vega
        if vega < 1e-12:
            break
        sigma -= diff / vega
        if sigma <= 0 or sigma > 5.0 or not math.isfinite(sigma):
            break

    # --- Bisection fallback ------------------------------------------- #
    lo, hi = 1e-4, 5.0
    flo = price_at(lo) - market_price
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        fmid = price_at(mid) - market_price
        if abs(fmid) < tol:
            return mid
        if (fmid < 0) == (flo < 0):
            lo, flo = mid, fmid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------- #
# Vectorised helper (numpy) — price a whole chain at once
# --------------------------------------------------------------------------- #
def price_chain(S: float, strikes: np.ndarray, T: float, r: float,
                sigmas: np.ndarray, q: float = 0.0,
                kind: str = "call") -> np.ndarray:
    """Vectorised BSM prices for an array of strikes / vols."""
    strikes = np.asarray(strikes, dtype=float)
    sigmas = np.asarray(sigmas, dtype=float)
    d1 = (np.log(S / strikes) + (r - q + 0.5 * sigmas ** 2) * T) / (sigmas * np.sqrt(T))
    d2 = d1 - sigmas * np.sqrt(T)
    if kind == "call":
        return S * np.exp(-q * T) * norm.cdf(d1) - strikes * np.exp(-r * T) * norm.cdf(d2)
    return strikes * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    opt = BSMOption(S=24300.0, K=24300.0, T=7 / 365, r=0.065,
                    sigma=0.115, q=0.0, kind="call")
    g = opt.greeks()
    print("=" * 58)
    print("  NIFTY 24300 CE  |  Spot 24,300  |  7 DTE  |  IV 11.5%")
    print("=" * 58)
    rows = [
        ("Price", f"{g.price:12.4f}"),
        ("-- 1st order --", ""),
        ("Delta", f"{g.delta:12.6f}"),
        ("Theta (per day)", f"{g.theta:12.4f}"),
        ("Vega (per 1.00 vol)", f"{g.vega:12.4f}"),
        ("Rho (per 1.00 rate)", f"{g.rho:12.4f}"),
        ("Lambda (elasticity)", f"{g.lambda_:12.4f}"),
        ("-- 2nd order --", ""),
        ("Gamma", f"{g.gamma:12.8f}"),
        ("Vanna", f"{g.vanna:12.6f}"),
        ("Charm (per day)", f"{g.charm:12.8f}"),
        ("Vomma / Volga", f"{g.vomma:12.4f}"),
        ("Vera", f"{g.vera:12.4f}"),
        ("Veta (per day)", f"{g.veta:12.4f}"),
        ("-- 3rd order --", ""),
        ("Speed", f"{g.speed:12.10f}"),
        ("Zomma", f"{g.zomma:12.8f}"),
        ("Color (per day)", f"{g.color:12.10f}"),
        ("Ultima", f"{g.ultima:12.4f}"),
    ]
    for name, val in rows:
        print(f"  {name:<26}{val}")

    iv = implied_vol(g.price, 24300.0, 24300.0, 7 / 365, 0.065, kind="call")
    print("-" * 58)
    print(f"  Implied-vol round trip : {iv:.6f}  (input 0.115000)")
    print("=" * 58)
