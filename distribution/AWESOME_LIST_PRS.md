# Awesome-List PR Kit — OPEN-ALADDIN

Ready-to-submit pull requests for curated awesome lists. Each block gives you: the target list, where the entry goes,
the exact markdown line to add, and the `gh` CLI commands to fork, patch and open the PR.

> Do this once per list, from your own GitHub account (`Thegreatprabh`). Run the commands in order. If `gh` asks,
> authenticate with `gh auth login` first. Keep PRs small — one entry, one list, no other changes.

---

## 1. wilsonfreitas/awesome-quant — best fit ⭐

**Section:** `Platforms & Tools` → open-source trading platforms / analytics

**Add this line** (alphabetical order, near other open-source platforms):

```markdown
- [OPEN-ALADDIN](https://github.com/Thegreatprabh/Open-Aladdin) - Open-source institutional quant terminal: React web terminal with live market simulator, 3D dealer gamma-exposure (GEX) surface, options chain with multi-leg payoff builder, Monte Carlo backtester, Rust matching engine and Python BSM/Heston analytics. (Python, Rust, TypeScript)
```

**Commands:**

```bash
gh repo fork wilsonfreitas/awesome-quant --clone
cd awesome-quant
# edit README.md, add the line above in the Platforms section
git checkout -b add-open-aladdin
git add README.md && git commit -m "Add OPEN-ALADDIN quant terminal"
git push origin add-open-aladdin
gh pr create --title "Add OPEN-ALADDIN" --body "Adds OPEN-ALADDIN — an open-source quant terminal monorepo (React terminal + Rust matching engine + Python quant engine). Includes a live market-microstructure simulator, 3D gamma-exposure analytics, options payoff builder and a Monte Carlo backtester. MIT licensed, all components run from a fresh clone."
```

---

## 2. rust-unofficial/awesome-rust — good fit

**Section:** `Applications` → `Finance`

**Add this line:**

```markdown
* [OPEN-ALADDIN](https://github.com/Thegreatprabh/Open-Aladdin) - Institutional quant terminal monorepo; its matching-engine core is a price-time-priority limit order book with iceberg orders, a FIX 4.4 parser and a SPAN-style risk gate in Rust (24 unit tests).
```

**Commands:**

```bash
gh repo fork rust-unofficial/awesome-rust --clone
cd awesome-rust
# edit README.md -> Applications -> Finance, add the line above
git checkout -b add-open-aladdin
git add README.md && git commit -m "Add OPEN-ALADDIN matching engine"
git push origin add-open-aladdin
gh pr create --title "Add OPEN-ALADDIN (Rust matching engine)" --body "OPEN-ALADDIN's core/matching-engine is a Rust price-time LOB with iceberg support, FIX 4.4 message parsing (with checksum validation) and a risk gate with margin checks and kill switch. cargo test: 24/24 passing."
```

> Note: awesome-rust has strict inclusion rules (needs crates.io publication or notable usage). If the PR is declined
> for maturity, publish `core/matching-engine` to crates.io first and resubmit — honest path, no shortcuts.

---

## 3. enaqx/awesome-react — good fit

**Section:** `React Apps` / open-source applications

**Add this line:**

```markdown
- [OPEN-ALADDIN Terminal](https://github.com/Thegreatprabh/Open-Aladdin/tree/main/apps/terminal-ui) - Bloomberg-style quant trading terminal in React + TypeScript: live GBM/jump-diffusion market simulator, canvas candlesticks, 3D vol/GEX surface, options chain payoff builder, Monte Carlo backtester and a Ctrl+K command palette. No backend.
```

**Commands:**

```bash
gh repo fork enaqx/awesome-react --clone
cd awesome-react
# edit README.md -> React Apps section, add the line above
git checkout -b add-open-aladdin
git add README.md && git commit -m "Add OPEN-ALADDIN terminal"
git push origin add-open-aladdin
gh pr create --title "Add OPEN-ALADDIN terminal" --body "A full trading terminal built with React + TypeScript + Tailwind — live tick simulator, canvas charts, software-rendered 3D surface, paper-trading account with margin checks, command palette. Builds clean with tsc && vite build."
```

---

## 4. igorbarinov/awesome-blockchain-rust — weakest fit (optional)

Honest note: OPEN-ALADDIN is a markets/quant project, not a blockchain project. The Rust matching engine is
general-purpose (any asset class), but this list is blockchain-focused, so a submission may be rejected as off-topic.
Only submit if you frame it strictly as the matching-engine component — and accept a decline gracefully.

**Section:** exchanges / matching engines, if the maintainers allow it

**Add this line:**

```markdown
* [OPEN-ALADDIN matching engine](https://github.com/Thegreatprabh/Open-Aladdin/tree/main/core/matching-engine) - Asset-agnostic Rust limit order book (price-time priority, iceberg orders) with FIX 4.4 parsing and a SPAN-style risk gate.
```

**Commands:**

```bash
gh repo fork igorbarinov/awesome-blockchain-rust --clone
cd awesome-blockchain-rust
git checkout -b add-open-aladdin-lob
# add the line above in the matching-engine/exchange section if one exists
git add README.md && git commit -m "Add OPEN-ALADDIN LOB"
git push origin add-open-aladdin-lob
gh pr create --title "Add OPEN-ALADDIN matching engine" --body "Asset-agnostic Rust LOB core with FIX 4.4 support. Happy to close if the maintainers consider it out of scope for a blockchain list."
```

---

## PR etiquette that actually gets merged

- One entry per PR. Never bulk-edit formatting or reorder other entries.
- Match the list's exact markdown style (some use `-`, some `*`, some require a license tag).
- Sort alphabetically where the list is alphabetical.
- If a maintainer asks for changes, make exactly those changes — don't argue, don't re-pitch.
- Wait at least a week before pinging. One polite ping max.
