# Reddit Launch Posts — OPEN-ATLAS

Four posts, each written for that subreddit's actual culture. Rules that apply everywhere: no brigading, no
cross-posting the same day (space them out), reply to every comment in the first few hours, and **do not** claim
things that aren't true — Reddit will check, and the comment section is where credibility is won or lost.

Post these from your own Reddit account. Best window for US + India overlap: **7–9 PM IST**.

---

## r/IndianStreetBets

**Title:** Built an open-source "Bloomberg terminal" for NIFTY options — runs in the browser, no API keys needed

**Body:**

Hey folks — I built [OPEN-ATLAS](https://github.com/Thegreatprabh/Open-Atlas), an open-source quant terminal for
Indian derivatives, and wanted to share it here first since it's made with NIFTY/BANKNIFTY in mind.

What it does:

- **Live market simulator** — candlesticks, Level-2 order book, time & sales tape, all ticking in your browser. No
  broker login, no API keys, no real money.
- **₹1 Cr paper account** with SPAN-style margin checks — place orders from the dock, watch MTM and Greeks update live,
  hit "flatten all" when the sim runs against you.
- **3D gamma exposure surface** — dealer GEX mesh with the zero-gamma flip, call/put walls marked.
- **Options chain + payoff builder** — 21 strikes, full Greeks, Iron Condor/straddle/strangle presets, IV and DTE shock
  sliders.
- **Backtester** with a real 1,000-run Monte Carlo bootstrap and CSV export.

Under the hood: React + TypeScript terminal, a Rust limit-order-book engine, and Python quant modules (BSM Greeks to
ultima, Heston calibration, gamma scalping, Kalman stat-arb). MIT licensed.

Run it locally in 2 minutes:

```
git clone https://github.com/Thegreatprabh/Open-Atlas.git
cd Open-Atlas/apps/terminal-ui
npm install && npm run dev
```

Caveats: it's a simulator, fills aren't real fills, and the backtest data is synthetic. If you're a zerodha/kite API
user, the repo has broker bridge stubs you can wire up. Feedback and PRs genuinely welcome — especially from people
who actually trade 0DTE.

---

## r/algotrading

**Title:** [Project] Open-source quant terminal: React front-end, Rust matching engine, Python quant stack

**Body:**

I've open-sourced [OPEN-ATLAS](https://github.com/Thegreatprabh/Open-Atlas) — a monorepo with a full trading
terminal that runs entirely in the browser against a built-in market simulator (GBM + Merton jump-diffusion, 240
pre-seeded 1m candles, Level-2 book, tape).

What's in the repo:

- **Terminal UI** (React/TS/Tailwind): command deck with canvas OHLCV + VWAP/EMA20/Bollinger, DOM ladder with
  click-to-limit, order dock with SPAN-style margin blocking, live MTM + net Greeks, Ctrl+K palette.
- **Backtester**: 3 strategies, Sharpe/Sortino/Calmar/maxDD, monthly heatmap, and a Monte Carlo bootstrap over
  daily returns (proper resampling — I rewrote it after catching that naive order-shuffling produces a degenerate
  distribution).
- **Rust core** (`core/matching-engine`): price–time LOB with iceberg orders, FIX 4.4 parser with checksum validation,
  risk gate (margin checks, fat-finger collars, kill switch). `cargo test` — 24/24.
- **Python quant** (`quant-engine`): full BSM Greek ladder through ultima, Newton+bisection IV solver, Heston COS
  pricer + smile calibration, gamma-scalping 0DTE sim, Kalman stat-arb. All modules execute clean.
- **Generator**: `scripts/generate_50k_monorepo.py` deterministically expands the repo to ~59k lines of additional
  strategy/engine/FIX/tick-data code (self-verifying, exits non-zero below 50k lines).

MIT licensed. Everything is paper — the terminal trades against the internal sim, broker bridges need your own keys.
Happy to get code review; the backtest engine and the Rust risk gate are where I'd appreciate the most eyes.

---

## r/quant

**Title:** [OC] OPEN-ATLAS — open-source terminal with 3D dealer-gamma analytics, BSM/Heston engine, Rust LOB

**Body:**

[OPEN-ATLAS](https://github.com/Thegreatprabh/Open-Atlas) — an open-source quant terminal and research monorepo
I'm sharing for review.

The quantitative core:

- **Black–Scholes–Merton** closed form with the full Greek ladder through third order (Vanna, Charm, Vomma,
  Speed, Zomma, Ultima) and an IV solver (Newton–Raphson with bisection fallback; round-trip error ~1e-15).
- **Dealer gamma exposure**: per-strike GEX aggregation, zero-gamma flip via bisection, call/put wall solver,
  visualized on a 3D surface over the option chain grid (21 strikes × 8 expiries, IV smile + term structure).
- **Heston** stochastic vol via the COS method with smile calibration (IV RMSE reported in vol points).
- **Strategies**: 0DTE gamma-scalping simulator with PnL attribution, Kalman-filter pairs with a forgetting factor
  (I had to fix the covariance update — naive implementations drift; documented in code).
- **Backtest rigor**: equity/drawdown/monthly stats plus a 1,000-run MC bootstrap over daily returns. I explicitly
  avoided the common trap of order-shuffling (sum-invariant → degenerate fan) and validated the fan has real
  dispersion (P5/median/P95 distinct).

The repo also has a Rust matching engine (price–time LOB, iceberg, FIX 4.4 with checksum, risk gate) and a React
terminal that makes all of this interactive. MIT, ~11.4k hand-written lines, plus a verified 59k-line generator.

I'd genuinely appreciate scrutiny of the GEX sign convention and the Heston calibration — those are the two spots
where a fresh pair of quant eyes would help most.

---

## r/rust

**Title:** Rust limit order book with iceberg orders, FIX 4.4 parser and risk gate — part of an open-source quant terminal

**Body:**

I wrote a small Rust matching engine as part of [OPEN-ATLAS](https://github.com/Thegreatprabh/Open-Atlas) and
thought r/rust might enjoy tearing it apart.

`core/matching-engine` contains:

- **Price–time-priority LOB** (`lob.rs`, ~640 lines) with hidden/iceberg order support, order amendment and cancel,
  aggressive/crossed-book handling.
- **FIX 4.4 parser** (`fix_parser.rs`, ~470 lines) with tag parsing, BodyLength/Checksum validation (I caught and
  fixed an off-by-one in my own BodyLength calc while testing — the test suite caught it).
- **Risk gate** (`risk_gate.rs`, ~270 lines): SPAN-style margin checks, fat-finger collars, kill switch.
- 24 unit tests, `cargo test` green, zero warnings on `cargo build --release`.

The engine is consumed by a React terminal (paper trading against a simulator) and a Python quant stack. The Rust is
intentionally dependency-light so it's readable — happy to get feedback on the order-book data structures (BTreeMap
vs intrusive, where the real latency wins are) and on whether the FIX parser should use a zero-copy approach.

Code: [core/matching-engine](https://github.com/Thegreatprabh/Open-Atlas/tree/main/core/matching-engine) — MIT.
