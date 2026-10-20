"""
Heston stochastic-volatility model: COS pricer + smile calibration.

Model (risk-neutral):
    dS = (r - q) S dt + sqrt(v) S dW1
    dv = kappa (theta - v) dt + sigma sqrt(v) dW2 ,  corr(dW1, dW2) = rho

Pricing uses the Fourier-COS method of Fang & Oosterlee (2008): the density
of log(S_T/K) is recovered from the Heston characteristic function via a
cosine series expansion on a truncated domain, giving fast, accurate
European prices without Monte Carlo noise.

Calibration fits (kappa, theta, sigma, rho, v0) to an implied-volatility
smile with scipy.optimize.least_squares, pricing each strike through the
COS pricer and converting to Black-Scholes implied vols.

Only numpy / scipy are used.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

from bsm_higher_order import implied_vol, BSMOption

__all__ = ["HestonParams", "heston_cf", "heston_price_cos", "calibrate"]


# --------------------------------------------------------------------------- #
@dataclass
class HestonParams:
    kappa: float   # mean-reversion speed of variance
    theta: float   # long-run variance
    sigma: float   # vol-of-variance
    rho: float     # spot/vol correlation
    v0: float      # initial variance

    def as_array(self) -> np.ndarray:
        return np.array([self.kappa, self.theta, self.sigma, self.rho, self.v0])

    @staticmethod
    def from_array(x: np.ndarray) -> "HestonParams":
        return HestonParams(*[float(v) for v in x])


# --------------------------------------------------------------------------- #
def heston_cf(u: np.ndarray, S: float, T: float, r: float, q: float,
              p: HestonParams) -> np.ndarray:
    """Heston characteristic function of log(S_T), 'little trap' formulation
    (Albrecher et al. 2007) which is numerically stable for all maturities."""
    kappa, theta, sigma, rho, v0 = p.kappa, p.theta, p.sigma, p.rho, p.v0
    u = np.asarray(u, dtype=complex)
    i = 1j
    d = np.sqrt((rho * sigma * i * u - kappa) ** 2
                + sigma ** 2 * (i * u + u ** 2))
    g = (kappa - rho * sigma * i * u - d) / (kappa - rho * sigma * i * u + d)
    exp_dT = np.exp(-d * T)
    C = (r - q) * i * u * T + (kappa * theta / sigma ** 2) * (
        (kappa - rho * sigma * i * u - d) * T
        - 2.0 * np.log((1.0 - g * exp_dT) / (1.0 - g))
    )
    D = ((kappa - rho * sigma * i * u - d) / sigma ** 2) * (
        (1.0 - exp_dT) / (1.0 - g * exp_dT)
    )
    return np.exp(C + D * v0 + i * u * np.log(S))


# --------------------------------------------------------------------------- #
def heston_price_cos(S: float, K: float, T: float, r: float, q: float,
                     p: HestonParams, kind: str = "call",
                     n_terms: int = 128) -> float:
    """European price via the Fang-Oosterlee COS method.

    Truncation range [a, b] for y = log(S_T/K) is set from the first two
    cumulants of the moneyness distribution (Fang & Oosterlee eq. 60-61).
    """
    kappa, theta, sigma, rho, v0 = p.kappa, p.theta, p.sigma, p.rho, p.v0

    # Cumulants of y = log(S_T / K). The ln(S/K) shift moves the log-return
    # cumulants onto the moneyness grid (Fang & Oosterlee, eq. 60-61).
    c1 = (np.log(S / K) + (r - q) * T
          + (1.0 - np.exp(-kappa * T)) * (theta - v0) / (2.0 * kappa)
          - 0.5 * theta * T)
    c2 = (1.0 / (8.0 * kappa ** 3)) * (
        sigma * T * kappa * np.exp(-kappa * T) * (v0 - theta)
        * (8.0 * kappa * rho - 4.0 * sigma)
        + kappa * rho * sigma * (1.0 - np.exp(-kappa * T)) * (16.0 * theta - 8.0 * v0)
        + 2.0 * theta * kappa * T * (-8.0 * kappa * rho * sigma
                                     + sigma ** 2 + 4.0 * kappa ** 2)
        + sigma ** 2 * ((theta - 2.0 * v0) * np.exp(-2.0 * kappa * T)
                        + theta * (6.0 * np.exp(-kappa * T) - 7.0) + 2.0 * v0)
        + 8.0 * kappa ** 2 * (v0 - theta) * (1.0 - np.exp(-kappa * T))
    )
    L = 10.0
    a = c1 - L * np.sqrt(abs(c2))
    b = c1 + L * np.sqrt(abs(c2))

    k = np.arange(n_terms)
    omega = k * np.pi / (b - a)

    # Characteristic function of y = log(S_T / K) at the cosine frequencies,
    # shifted onto the truncated domain: phi_y(u) * e^{-i u a}.
    phi_y = (heston_cf(omega, S, T, r, q, p)
             * np.exp(-1j * omega * np.log(K))
             * np.exp(-1j * omega * a))

    # Payoff coefficients for the call K*(e^y - 1)^+ on [a, b]
    # (Fang & Oosterlee, eq. 22-23).
    def chi(kk: np.ndarray, c_: float, d_: float) -> np.ndarray:
        return (1.0 / (1.0 + (kk * np.pi / (b - a)) ** 2)) * (
            np.cos(kk * np.pi * (d_ - a) / (b - a)) * np.exp(d_)
            - np.cos(kk * np.pi * (c_ - a) / (b - a)) * np.exp(c_)
            + (kk * np.pi / (b - a)) * np.sin(kk * np.pi * (d_ - a) / (b - a)) * np.exp(d_)
            - (kk * np.pi / (b - a)) * np.sin(kk * np.pi * (c_ - a) / (b - a)) * np.exp(c_)
        )

    def psi(kk: np.ndarray, c_: float, d_: float) -> np.ndarray:
        out = np.empty_like(kk, dtype=float)
        nz = kk > 0
        out[nz] = (1.0 / (kk[nz] * np.pi / (b - a))) * (
            np.sin(kk[nz] * np.pi * (d_ - a) / (b - a))
            - np.sin(kk[nz] * np.pi * (c_ - a) / (b - a))
        )
        out[~nz] = d_ - c_
        return out

    vk = 2.0 / (b - a) * K * (chi(k, 0.0, b) - psi(k, 0.0, b))

    # Primed cosine sum: halve the k = 0 term.
    coeffs = np.real(phi_y) * vk
    coeffs[0] *= 0.5
    call = float(np.exp(-r * T) * coeffs.sum())

    if kind == "call":
        return call
    # Put via put-call parity.
    return call - S * np.exp(-q * T) + K * np.exp(-r * T)


# --------------------------------------------------------------------------- #
def calibrate(strikes: np.ndarray, market_ivs: np.ndarray, S: float,
              T: float, r: float, q: float = 0.0,
              x0: HestonParams | None = None,
              verbose: bool = True) -> HestonParams:
    """Fit Heston parameters to an implied-vol smile (least squares on IV)."""
    if x0 is None:
        x0 = HestonParams(kappa=1.5, theta=0.04, sigma=0.35, rho=-0.6, v0=0.03)

    bounds_lo = np.array([0.05, 1e-4, 0.01, -0.999, 1e-4])
    bounds_hi = np.array([15.0, 1.00, 2.50, 0.999, 1.00])

    def residuals(x: np.ndarray) -> np.ndarray:
        p = HestonParams.from_array(x)
        res = np.empty_like(strikes, dtype=float)
        for i, K in enumerate(strikes):
            try:
                px = heston_price_cos(S, float(K), T, r, q, p, kind="call")
                iv = implied_vol(px, S, float(K), T, r, q, kind="call")
                res[i] = iv - market_ivs[i]
            except Exception:
                res[i] = 1.0  # penalise unpriceable parameter sets
        return res

    sol = least_squares(residuals, x0.as_array(),
                        bounds=(bounds_lo, bounds_hi),
                        method="trf", xtol=1e-10, ftol=1e-10)
    p = HestonParams.from_array(sol.x)
    if verbose:
        rmse = float(np.sqrt(np.mean(sol.fun ** 2)))
        print(f"  Calibration converged: cost={sol.cost:.3e}  IV RMSE={rmse:.5f} "
              f"({rmse * 100:.2f} vol pts)")
    return p


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print("=" * 66)
    print("  HESTON CALIBRATION — synthetic NIFTY smile (1M, Spot 24,300)")
    print("=" * 66)

    S, T, r = 24300.0, 30 / 365, 0.065
    true_params = HestonParams(kappa=2.10, theta=0.032, sigma=0.42,
                               rho=-0.68, v0=0.021)
    print(f"  True params      : kappa={true_params.kappa:.2f}  "
          f"theta={true_params.theta:.3f}  sigma={true_params.sigma:.2f}  "
          f"rho={true_params.rho:.2f}  v0={true_params.v0:.3f}")

    strikes = np.array([22800, 23400, 23850, 24300, 24750, 25200, 25800], dtype=float)
    # Generate the 'market' smile from the true model (+ tiny deterministic wiggle)
    market_ivs = np.empty_like(strikes)
    for i, K in enumerate(strikes):
        px = heston_price_cos(S, float(K), T, r, 0.0, true_params, kind="call")
        market_ivs[i] = implied_vol(px, S, float(K), T, r, kind="call")
    rng = np.random.default_rng(7)
    market_ivs += rng.normal(0.0, 0.0004, size=strikes.size)  # ~0.04 vol pt noise

    fitted = calibrate(strikes, market_ivs, S, T, r)
    print(f"  Fitted params    : kappa={fitted.kappa:.3f}  "
          f"theta={fitted.theta:.4f}  sigma={fitted.sigma:.3f}  "
          f"rho={fitted.rho:.3f}  v0={fitted.v0:.4f}")
    feller = 2 * fitted.kappa * fitted.theta - fitted.sigma ** 2
    print(f"  Feller condition : 2*kappa*theta - sigma^2 = {feller:+.4f} "
          f"({'satisfied' if feller > 0 else 'violated'})")
    print("-" * 66)
    print(f"  {'Strike':>8} {'Market IV':>10} {'Fitted IV':>10} {'Diff (bp)':>10}")
    for i, K in enumerate(strikes):
        px = heston_price_cos(S, float(K), T, r, 0.0, fitted, kind="call")
        iv_fit = implied_vol(px, S, float(K), T, r, kind="call")
        print(f"  {K:8.0f} {market_ivs[i] * 100:9.3f}% {iv_fit * 100:9.3f}% "
              f"{(iv_fit - market_ivs[i]) * 10000:+10.1f}")
    print("=" * 66)
