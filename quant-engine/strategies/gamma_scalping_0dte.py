"""
Delta-neutral gamma scalping on a synthetic NIFTY 0DTE (expiry-day) path.

Strategy
--------
Buy an at-the-money straddle at the open of expiry day, then keep the
portfolio delta-neutral by trading the underlying whenever the net delta
drifts outside a re-hedging band.  The economics are the classic
long-gamma trade:

    PnL  ~=  1/2 * Gamma * (dS)^2  accumulated over the day   (gamma gains)
             - Theta * time elapsed                          (theta decay)
             - transaction costs on the hedge trades

The simulator runs a seeded Merton jump-diffusion path at 1-minute
resolution over the 375-minute NSE session, re-prices the straddle with
Black-Scholes at every step, attributes PnL into (gamma, theta, hedge,
residual) buckets, and prints a summary tear-sheet.

Only numpy / stdlib are used.  Option maths is taken from
pricing/bsm_higher_order.py when importable, with a self-contained copy of
the BSM delta formula as fallback so this file also runs stand-alone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# --------------------------------------------------------------------------- #
try:  # package import (run as part of the quant engine)
    from pricing.bsm_higher_order import BSMOption  # type: ignore
except Exception:  # stand-alone execution fallback
    BSMOption = None  # type: ignore

_NORM_CDF = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bsm_delta(S: float, K: float, T: float, r: float, sigma: float,
               kind: str) -> float:
    """Stand-alone BSM delta (used only if the engine import is unavailable)."""
    if T <= 0 or sigma <= 0:
        return 1.0 if (kind == "call" and S > K) else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return _NORM_CDF(d1) if kind == "call" else _NORM_CDF(d1) - 1.0


def straddle_price_and_delta(S: float, K: float, T: float, r: float,
                             sigma: float) -> tuple[float, float, float, float]:
    """Return (price, delta, gamma, theta_per_day) of a long ATM straddle."""
    if BSMOption is not None and T > 0:
        call = BSMOption(S, K, T, r, sigma, 0.0, "call")
        put = BSMOption(S, K, T, r, sigma, 0.0, "put")
        gc, gp = call.greeks(), put.greeks()
        return (gc.price + gp.price, gc.delta + gp.delta,
                gc.gamma + gp.gamma, gc.theta + gp.theta)
    # Fallback: price from deltas is not available, approximate intrinsic-only.
    dc = _bsm_delta(S, K, T, r, sigma, "call")
    dp = _bsm_delta(S, K, T, r, sigma, "put")
    price = max(S - K, 0.0) + max(K - S, 0.0)
    return price, dc + dp, 0.0, 0.0


# --------------------------------------------------------------------------- #
@dataclass
class ScalpConfig:
    spot0: float = 24300.0
    strike: float = 24300.0
    iv: float = 0.14                # expiry-day implied vol
    rate: float = 0.065
    minutes: int = 375              # NSE session length (09:15 - 15:30)
    real_vol_annual: float = 0.19   # realised vol of the synthetic path (> IV -> edge)
    jump_intensity: float = 3.0     # expected jumps per day
    jump_mean: float = 0.0
    jump_sd: float = 0.0035         # 35 bp jump stdev
    lots: int = 20                  # straddle lots
    lot_size: int = 25              # NIFTY lot size
    hedge_band: float = 0.08        # re-hedge when |net delta| > band (per share)
    hedge_cost_bp: float = 1.5      # slippage+fees per hedge, bp of notional
    seed: int = 42


@dataclass
class ScalpResult:
    pnl_total: float
    pnl_gamma: float
    pnl_theta: float
    pnl_hedge_costs: float
    pnl_residual: float
    n_hedges: int
    premium_paid: float
    final_spot: float
    realised_vol: float
    equity_curve: np.ndarray = field(repr=False, default=None)
    path: np.ndarray = field(repr=False, default=None)


# --------------------------------------------------------------------------- #
def simulate_path(cfg: ScalpConfig, rng: np.random.Generator) -> np.ndarray:
    """Merton jump-diffusion at 1-minute steps, vol annualised."""
    n = cfg.minutes
    dt = 1.0 / (252.0 * cfg.minutes)          # fraction of a year per minute
    mu = -0.5 * cfg.real_vol_annual ** 2
    diffusion = cfg.real_vol_annual * np.sqrt(dt) * rng.standard_normal(n)
    # Compound-Poisson jumps
    lam_dt = cfg.jump_intensity / cfg.minutes
    n_jumps = rng.poisson(lam_dt, size=n)
    jump_sizes = (cfg.jump_mean + cfg.jump_sd * rng.standard_normal(n)) * (n_jumps > 0)
    jump_sizes *= n_jumps  # multiple jumps in one minute (rare) scale linearly
    log_rets = (mu * dt) + diffusion + jump_sizes
    path = cfg.spot0 * np.exp(np.cumsum(log_rets))
    return np.concatenate([[cfg.spot0], path])


def run_gamma_scalp(cfg: ScalpConfig | None = None) -> ScalpResult:
    cfg = cfg or ScalpConfig()
    rng = np.random.default_rng(cfg.seed)
    path = simulate_path(cfg, rng)
    qty_options = cfg.lots * cfg.lot_size       # straddle units (per leg)

    minutes_left0 = cfg.minutes
    T0 = minutes_left0 / (252.0 * cfg.minutes)
    px0, d0, g0, th0 = straddle_price_and_delta(path[0], cfg.strike, T0,
                                                cfg.rate, cfg.iv)
    premium_paid = px0 * qty_options

    hedge_shares = 0.0        # shares of underlying held as the delta hedge
    cash = -premium_paid      # paid premium at t=0
    pnl_gamma = pnl_theta = pnl_costs = 0.0
    n_hedges = 0
    equity = np.empty(len(path))

    prev_S = path[0]
    prev_straddle_px = px0
    prev_gamma = g0
    prev_theta_day = th0       # model theta (per calendar day) of the straddle

    for i in range(1, len(path)):
        S = path[i]
        minutes_left = cfg.minutes - i
        T = max(minutes_left, 0) / (252.0 * cfg.minutes)
        if minutes_left > 0:
            px, delta, gamma, theta_day = straddle_price_and_delta(
                S, cfg.strike, T, cfg.rate, cfg.iv)
        else:
            # At expiry the straddle is worth its intrinsic payoff.
            px = max(S - cfg.strike, 0.0) + max(cfg.strike - S, 0.0)
            delta, gamma, theta_day = 0.0, 0.0, 0.0

        dS = S - prev_S
        # --- PnL attribution for this step -------------------------------- #
        # Long-gamma step PnL ~= 0.5 * Gamma * dS^2  (rebalancing gains)
        # Theta step PnL   ~= model theta * fraction of the session elapsed.
        gamma_pnl = 0.5 * prev_gamma * dS * dS * qty_options
        theta_pnl = (prev_theta_day * (1.0 / cfg.minutes) * qty_options
                     if minutes_left > 0 else 0.0)
        pnl_gamma += gamma_pnl
        pnl_theta += theta_pnl

        # --- Delta hedge with band --------------------------------------- #
        net_delta = delta * qty_options + hedge_shares
        if abs(delta) > cfg.hedge_band and minutes_left > 0:
            trade = -net_delta
            if abs(trade) >= 1.0:
                cost = abs(trade) * S * cfg.hedge_cost_bp / 10_000.0
                cash -= trade * S + cost
                hedge_shares += trade
                pnl_costs -= cost
                n_hedges += 1

        # Mark-to-market equity
        equity[i] = cash + px * qty_options + hedge_shares * S
        prev_S, prev_straddle_px = S, px
        prev_gamma, prev_theta_day = gamma, theta_day

    equity[0] = 0.0
    # Expiry settlement: straddle pays intrinsic, hedge liquidated at close.
    S_T = path[-1]
    settle = (max(S_T - cfg.strike, 0.0) + max(cfg.strike - S_T, 0.0)) * qty_options
    final_equity = cash + settle + hedge_shares * S_T
    pnl_total = final_equity

    log_rets = np.diff(np.log(path))
    dt = 1.0 / (252.0 * cfg.minutes)
    realised = float(np.std(log_rets) / np.sqrt(dt))

    residual = pnl_total - (pnl_gamma + pnl_theta + pnl_costs)
    return ScalpResult(
        pnl_total=float(pnl_total), pnl_gamma=float(pnl_gamma),
        pnl_theta=float(pnl_theta), pnl_hedge_costs=float(pnl_costs),
        pnl_residual=float(residual), n_hedges=n_hedges,
        premium_paid=float(premium_paid), final_spot=float(S_T),
        realised_vol=realised, equity_curve=equity, path=path,
    )


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    cfg = ScalpConfig()
    res = run_gamma_scalp(cfg)
    print("=" * 64)
    print("  GAMMA SCALPING — NIFTY 0DTE  |  long ATM straddle, delta-hedged")
    print("=" * 64)
    print(f"  Spot open / close    : {cfg.spot0:,.1f} / {res.final_spot:,.1f}")
    print(f"  Premium paid         : Rs {res.premium_paid:,.0f} "
          f"({cfg.lots} lots x {cfg.lot_size})")
    print(f"  Implied vol (paid)   : {cfg.iv * 100:.1f}%")
    print(f"  Realised vol (path)  : {res.realised_vol * 100:.1f}%")
    print("-" * 64)
    print(f"  Gamma gains          : Rs {res.pnl_gamma:>+12,.0f}")
    print(f"  Theta decay          : Rs {res.pnl_theta:>+12,.0f}")
    print(f"  Hedge costs ({res.n_hedges:3d} trades) : Rs {res.pnl_hedge_costs:>+12,.0f}")
    print(f"  Residual (delta/vega): Rs {res.pnl_residual:>+12,.0f}")
    print("-" * 64)
    print(f"  TOTAL PnL            : Rs {res.pnl_total:>+12,.0f}")
    eq = res.equity_curve
    peak = np.maximum.accumulate(eq)
    max_dd = float(np.max(peak - eq))
    print(f"  Max intraday drawdown: Rs {max_dd:,.0f}")
    print(f"  Hedge band           : |delta| > {cfg.hedge_band}")
    print("=" * 64)
