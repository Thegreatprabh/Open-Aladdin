"""
Kalman-filter pairs trading with a dynamic hedge ratio.

Two synthetic, cointegrated price series are generated from a shared random
walk factor plus stationary idiosyncratic noise:

    x_t = mu + cumsum(shocks)                      (the "leader")
    y_t = alpha_t + beta_t * x_t + eps_t           (the "follower")
    beta_t follows a slow random walk              (time-varying hedge ratio)

A Kalman filter tracks the latent state [alpha_t, beta_t] from observations
of (x_t, y_t).  The standardised residual (spread z-score) drives classic
mean-reversion rules:

    z > +entry  -> short the spread  (short y, long beta*x)
    z < -entry  -> long  the spread
    |z| < exit  -> flat
    |z| > stop  -> stop out

PnL is marked on both legs with per-trade transaction costs; the run prints
trade statistics plus annualised Sharpe / max-drawdown.

Only numpy / stdlib are used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = ["KalmanPairFilter", "PairsConfig", "run_pairs_backtest"]


# --------------------------------------------------------------------------- #
class KalmanPairFilter:
    """Kalman filter for the regression  y_t = [1, x_t] . state_t + e_t .

    The state [alpha, beta]^T follows a random walk.  Two ingredients make
    the tracker work on real-scale price data:

    1. *OLS formation*: the first ``init_bars`` observations initialise the
       state and its covariance via ordinary least squares (plus an estimate
       of the observation variance R).  This avoids the slow cold-start of a
       zero-initialised filter.
    2. *Forgetting factor*: the predicted covariance is inflated as
       ``P_pred = P / lam`` (lam < 1) instead of adding process noise Q.
       This keeps the Kalman gain bounded away from zero forever, so the
       filter keeps adapting to a drifting hedge ratio instead of locking
       onto a stale estimate.  Effective memory ~= 1 / (1 - lam) bars.
    """

    def __init__(self, forgetting: float = 0.985,
                 obs_var: float | None = None, init_bars: int = 50):
        self.lam = forgetting
        self.R = obs_var
        self.init_bars = init_bars
        self.state: np.ndarray | None = None
        self.P: np.ndarray | None = None
        self._xs: list = []
        self._ys: list = []
        self.initialised = False

    def _initialise_from_ols(self) -> None:
        X = np.column_stack([np.ones(len(self._xs)), self._xs])
        coef, res, *_ = np.linalg.lstsq(X, self._ys, rcond=None)
        self.state = coef
        dof = max(len(self._xs) - 2, 1)
        resid_var = float(res[0] / dof) if len(res) else 1.0
        if self.R is None:
            self.R = max(resid_var, 1e-6)
        XtX_inv = np.linalg.inv(X.T @ X)
        self.P = resid_var * XtX_inv * 4.0
        self.initialised = True

    def update(self, x: float, y: float) -> tuple:
        """One predict/update cycle.

        Returns (forecast_error, error_variance, beta) for z-scoring.
        """
        if not self.initialised:
            self._xs.append(x)
            self._ys.append(y)
            if len(self._xs) >= self.init_bars:
                self._initialise_from_ols()
            return 0.0, (self.R or 1.0), 0.0
        H = np.array([1.0, x])
        P_pred = self.P / self.lam
        err = y - H @ self.state
        S_var = float(H @ P_pred @ H + self.R)
        K_gain = (P_pred @ H) / S_var
        self.state = self.state + K_gain * err
        self.P = P_pred - np.outer(K_gain, H @ P_pred)
        return err, S_var, float(self.state[1])


# --------------------------------------------------------------------------- #
@dataclass
class PairsConfig:
    n_obs: int = 1500               # observations (~6y of daily bars, or intraday)
    beta0: float = 1.35             # initial true hedge ratio
    beta_drift_sd: float = 0.004    # random-walk sd of the true beta
    alpha0: float = 12.0
    spread_ar: float = 0.92         # AR(1) persistence of the stationary spread
    spread_sd: float = 1.6
    factor_vol: float = 0.9         # random-walk step stdev of x
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 4.0
    notional: float = 100_000.0     # Rs notional per leg (scaled by beta)
    cost_bp: float = 3.0            # per-leg, per-trade cost in bp
    seed: int = 11


@dataclass
class PairsResult:
    pnl_total: float
    pnl_curve: np.ndarray
    n_trades: int
    win_rate: float
    sharpe: float
    max_drawdown: float
    beta_final: float
    beta_true_final: float


# --------------------------------------------------------------------------- #
def generate_pair(cfg: PairsConfig, rng: np.random.Generator):
    """Generate cointegrated (x, y) with a drifting true beta."""
    n = cfg.n_obs
    x = 100.0 + np.cumsum(rng.normal(0.0, cfg.factor_vol, size=n))
    beta_true = cfg.beta0 + np.cumsum(rng.normal(0.0, cfg.beta_drift_sd, size=n))
    alpha_true = cfg.alpha0 + 0.35 * np.sin(np.linspace(0, 6, n))
    spread = np.empty(n)
    e = rng.normal(0.0, cfg.spread_sd, size=n)
    s = 0.0
    for t in range(n):
        s = cfg.spread_ar * s + e[t]
        spread[t] = s
    y = alpha_true + beta_true * x + spread
    return x, y, beta_true


def run_pairs_backtest(cfg: PairsConfig | None = None) -> PairsResult:
    cfg = cfg or PairsConfig()
    rng = np.random.default_rng(cfg.seed)
    x, y, beta_true = generate_pair(cfg, rng)

    kf = KalmanPairFilter(forgetting=0.985, init_bars=50)
    position = 0                    # +1 long spread, -1 short spread, 0 flat
    shares_y = 0.0                  # signed shares of y leg
    shares_x = 0.0                  # signed shares of x leg
    cash = 0.0
    trades = 0
    wins = 0
    trade_pnls: list[float] = []
    open_equity_at_entry = 0.0
    equity_curve = np.zeros(len(x))

    warmup = 80   # > init_bars: only trade once the filter is initialised
    for t in range(len(x)):
        err, err_var, beta = kf.update(x[t], y[t])
        z = err / np.sqrt(err_var) if err_var > 0 else 0.0

        equity_now = cash + shares_y * y[t] + shares_x * x[t]
        equity_curve[t] = equity_now

        if t < warmup:
            continue

        # ----- exit logic ------------------------------------------------ #
        if position != 0 and (abs(z) < cfg.exit_z or abs(z) > cfg.stop_z):
            # Close both legs
            cash += shares_y * y[t] + shares_x * x[t]
            cost = (abs(shares_y) * y[t] + abs(shares_x) * x[t]) * cfg.cost_bp / 1e4
            cash -= cost
            trades += 1
            trade_pnl = cash - open_equity_at_entry
            trade_pnls.append(trade_pnl)
            if trade_pnl > 0:
                wins += 1
            position, shares_y, shares_x = 0, 0.0, 0.0
            equity_now = cash
            equity_curve[t] = equity_now

        # ----- entry logic ----------------------------------------------- #
        if position == 0 and abs(z) > cfg.entry_z:
            direction = -1 if z > 0 else 1   # fade the spread
            units_y = cfg.notional / y[t]
            shares_y = direction * units_y
            shares_x = -direction * beta * units_y
            cost = (abs(shares_y) * y[t] + abs(shares_x) * x[t]) * cfg.cost_bp / 1e4
            cash -= (shares_y * y[t] + shares_x * x[t]) + cost
            open_equity_at_entry = cash + shares_y * y[t] + shares_x * x[t]
            position = direction

    # Liquidate any open position at the close
    if position != 0:
        cash += shares_y * y[-1] + shares_x * x[-1]
        equity_curve[-1] = cash
        trade_pnls.append(cash - open_equity_at_entry)
        trades += 1

    eq = equity_curve
    rets = np.diff(eq)
    # Annualise assuming 252 obs/year on the equity *changes*
    vol = np.std(rets) if np.std(rets) > 0 else np.nan
    sharpe = float(np.mean(rets) / vol * np.sqrt(252)) if vol and np.isfinite(vol) else 0.0
    peak = np.maximum.accumulate(eq)
    max_dd = float(np.max(peak - eq)) if len(eq) else 0.0
    win_rate = (wins / trades) if trades else 0.0

    return PairsResult(
        pnl_total=float(eq[-1]), pnl_curve=eq, n_trades=trades,
        win_rate=float(win_rate), sharpe=sharpe, max_drawdown=max_dd,
        beta_final=float(kf.state[1]), beta_true_final=float(beta_true[-1]),
    )


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    cfg = PairsConfig()
    res = run_pairs_backtest(cfg)
    print("=" * 64)
    print("  STAT-ARB PAIRS — Kalman dynamic hedge ratio + z-score rules")
    print("=" * 64)
    print(f"  Observations        : {cfg.n_obs}")
    print(f"  Entry / exit / stop : |z| > {cfg.entry_z} / < {cfg.exit_z} / > {cfg.stop_z}")
    print(f"  True beta (final)   : {res.beta_true_final:.3f}")
    print(f"  Kalman beta (final) : {res.beta_final:.3f}")
    print("-" * 64)
    print(f"  Round-trip trades   : {res.n_trades}")
    print(f"  Win rate            : {res.win_rate * 100:.1f}%")
    print(f"  Total PnL           : Rs {res.pnl_total:,.0f}  "
          f"(notional/leg Rs {cfg.notional:,.0f})")
    print(f"  Sharpe (ann.)       : {res.sharpe:.2f}")
    print(f"  Max drawdown        : Rs {res.max_drawdown:,.0f}")
    print("=" * 64)
