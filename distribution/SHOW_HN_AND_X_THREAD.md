# Show HN + X Launch Kit — OPEN-ATLAS

Copy-paste-ready launch texts. Post from your own accounts. Timing: **Show HN first, X thread ~1 hour later** so HN
feedback can shape the thread. Reply to every comment in the first hour on both platforms.

---

## Hacker News — Show HN

**Title:** `Show HN: OPEN-ATLAS – open-source quant terminal with 3D gamma-exposure analytics`

**URL:** `https://github.com/Thegreatprabh/Open-Atlas`

**Text:**

I built an open-source, Bloomberg-style quant terminal that runs entirely in the browser against a built-in market
simulator — no backend, no API keys, no real money.

- Command deck: live candlesticks, Level-2 order book, time & sales, order dock with a ₹1 Cr paper account, SPAN-style
  margin checks, live MTM and net Greeks.
- 3D dealer-gamma-exposure surface: IV/GEX meshes with the zero-gamma flip and call/put walls computed live.
- Options chain with a multi-leg payoff builder (straddle/strangle/Iron Condor/bull call spread), IV/DTE shock sliders.
- Backtester with Sharpe/Sortino/Calmar and a 1,000-run Monte Carlo bootstrap over daily returns (I rewrote it after
  discovering naive order-shuffling makes the fan degenerate — worth knowing if you build one too).
- Rust matching engine: price–time-priority LOB with iceberg orders, FIX 4.4 parser with checksum validation, risk
  gate with kill switch (cargo test 24/24).
- Python quant stack: BSM Greeks through ultima, Heston COS pricer + smile calibration, gamma-scalping 0DTE sim,
  Kalman stat-arb.
- A generator script that deterministically expands the repo to ~59k lines of additional engine/strategy/FIX/tick
  data (self-verifying).

MIT licensed. Everything runs from a fresh clone — screenshots in the README are captured from the real built app.

I'd appreciate review especially on: the GEX sign convention, the Heston calibration, and the Rust order-book data
structures. Happy to answer anything.

---

## X (Twitter) — 7-post thread

Post these as a thread (reply chain, first post stands alone). Best time: ~8 PM IST. Pin the first post.

**1/7**
I built OPEN-ATLAS — an open-source, Bloomberg-style quant terminal for options traders. React front-end, Rust
matching engine, Python quant stack. Runs entirely in your browser against a live market simulator. MIT licensed 🧵

github.com/Thegreatprabh/Open-Atlas

**2/7**
The terminal: live candlesticks, Level-2 order book, time & sales tape, and an order dock wired to a ₹1 Cr paper
account — SPAN-style margin checks, live MTM, net Greeks, delta-hedge and a flatten-all kill switch. Ctrl+K does
everything.

**3/7**
Dealer gamma exposure, in 3D. IV and net-GEX meshes with the zero-gamma flip, call wall and put wall computed live —
drag to rotate, hover any node. The kind of view you'd normally pay a data terminal for.

**4/7**
The backtester is honest: 3 strategies, Sharpe/Sortino/Calmar, monthly heatmap, and a 1,000-run Monte Carlo
bootstrap over *daily returns*. I rewrote the MC after catching that order-shuffling is sum-invariant — the fan was
degenerate. Details in the repo.

**5/7**
Under the hood: a Rust matching engine (price–time-priority LOB, iceberg orders, FIX 4.4 parser with checksum, risk
gate — 24/24 tests) and Python quant modules (BSM Greeks through ultima, Heston calibration, gamma scalping,
Kalman stat-arb).

**6/7**
Plus a generator that deterministically expands the repo to ~59,000 lines — strategies, C++/Rust engine code, FIX
schemas, synthetic NIFTY/BANKNIFTY Level-2 tick data. Self-verifying: it fails if the count drops below 50k.

**7/7**
Everything is paper — fills are simulated, backtests are synthetic. Nothing here trades real money. Feedback and PRs
welcome, especially on the GEX math and the Rust book internals.

⭐ if it's useful, share it with someone building in fintech.

**Suggested tags** (verify handles before posting): @DhanHQ @Sensibull @zerodhaonline @rustlang — tag max 1–2 per
post, only the most relevant, and never in the first post.

---

## LinkedIn variant (single post)

I'm open-sourcing OPEN-ATLAS — an institutional-style quantitative trading terminal I built as a learning and
research project.

It combines a React web terminal (live market simulator, Level-2 depth, options chain with payoff builder, 3D
dealer-gamma analytics), a Rust limit-order-book matching engine, and a Python quant stack (Black–Scholes through
third-order Greeks, Heston calibration, Monte Carlo backtesting).

Everything is simulated/paper — no real money, no keys — which makes it a clean environment for learning options
microstructure, Greeks and backtest design. MIT licensed, runs from a fresh clone.

Repo: github.com/Thegreatprabh/Open-Atlas

#OpenSource #QuantFinance #FinTech #Rust #React #OptionsTrading #BuildInPublic

---

## Reply playbook (all platforms)

- **Technical corrections:** thank them, verify, fix in the repo, reply with the commit link. This is the fastest
  way to earn credibility.
- **"Is this real / can I trade with it?"** — Paper only. Be explicit: simulated fills, synthetic backtest data,
  broker bridges need your own keys and are your responsibility.
- **"How is this different from X?"** — don't trash competitors. One honest differentiator per reply.
- **Feature requests:** "Good idea — opened as an issue" (then actually open one).
- **Spam accusations:** never post the same text twice; tailor per platform (already done above).
