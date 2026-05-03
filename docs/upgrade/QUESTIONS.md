# Upgrade Clarification Questions & Decisions

> Decision log captured during planning. All questions answered before plan was written.

## Q1 — Which option strategies should the upgrade support in v1?
**Answer:** All four families
- ✅ Long Call / Long Put
- ✅ Vertical Spreads (Bull/Bear Call/Put)
- ✅ Straddles / Strangles
- ✅ Iron Condors / Butterflies

**Implication:** Need an abstract `OptionStructure` base class and 6+ concrete implementations. Strike-selection algorithms must generalize beyond the current ATM+offset scan.

---

## Q2 — How should directional bias (Long/Short/Neutral) map to option structures?
**Answer:** Auto — signal chooses structure

**Implication:** Signal engine outputs `DirectionalBias` (enum: LONG / SHORT / NEUTRAL) + confidence score. A `StructureFactory` maps `(bias, strategy_config)` → concrete `OptionStructure` instance. Presets declare which structures are allowed for each bias.

---

## Q3 — How should presets be organized and stored?
**Answer:** Per-strategy JSON files in repo

**Implication:**
- Folder layout: `presets/<strategy_id>/<preset_name>.json`
- Preset loader scans at startup, exposes via API: `GET /api/presets`
- Each preset JSON declares: `strategy_id`, `direction_policy`, `structure_preferences`, `filter_config`, `risk_config`
- Future user-custom presets can go in a different folder (`~/.spy_credit_spread/presets/`) — **out of scope for v1**

---

## Q4 — What does "Go Live" mode execute against?
**Answer:** Interactive Brokers (via ib_insync + TWS/Gateway)

**Implication:**
- New `IBKRBroker` adapter using `ib_insync` async library
- User must run IB Gateway or TWS locally (paper or live account)
- IBKR **does** support multi-leg options natively (unlike Alpaca paper) → can execute real spreads
- Alpaca paper stays as secondary mode for users without IBKR

---

## Q5 — Which filters should move into the universal filter layer?
**Answer:** Existing + VIX regime bands + FOMC/CPI/NFP event blackouts

**Implication:**
- Filters become a chain (`FilterChain` class) applied by engine, independent of strategy plugin
- Strategy plugins only output raw signals; filters decide if the signal is tradeable
- VIX regime adds labels: `LOW` (<15), `MID` (15-25), `HIGH` (25-35), `EXTREME` (>35)
- Event calendar needs a data source — use a static JSON seed file for v1, refresh manually

---

## Q6 — Shared execution engine or separate code paths per mode?
**Answer:** Unified engine + broker adapter pattern

**Implication:**
- One `TradingEngine` class runs the bar-by-bar loop
- Engine delegates order placement to a `BrokerAdapter` Protocol
- Implementations: `SimBroker` (backtest), `AlpacaBroker` (paper), `IBKRBroker` (live + paper)
- Backtest uses synthetic fills via Black-Scholes; Paper/Live use real fills from broker

---

## Q7 — Safety features required for live mode
**Answer:**
- ✅ Max concurrent positions (hard cap)
- ✅ Emergency "close all" button

**Implication:**
- `SafetyGuard` class evaluated before every order submission
- `POST /api/safety/flatten` endpoint triggers `broker.close_all()` on whatever adapter is active
- UI adds a bright red "PANIC" button visible in live mode only

---

## Q8 — Which IBKR API variant?
**Answer:** `ib_insync` + TWS/Gateway (recommended choice)

**Implication:**
- Async-first API → wrap in sync helpers for FastAPI endpoints, or switch endpoints to async
- User runs Gateway on `127.0.0.1:7497` (paper) or `7496` (live)
- Connection failure must be handled gracefully (UI shows "not connected")

---

## Q9 — Scope boundary
**Answer:** Full upgrade as specified (single large plan)

**Implication:** Plan will be phased into ~9 sequential task groups. Can be paused at any phase boundary and still leave a working codebase.

---

## Q10 — Frontend scope
**Answer:** New "Strategy Hub" top-level tab

**Implication:**
- Add React Router (currently single-page)
- Routes: `/hub`, `/backtest`, `/paper`, `/live`, `/history`
- Existing `App.jsx` split into `pages/`

---

## Q11 — Workspace location
**Answer:** `/upgrade` folder in current repo

**Implication:** All planning artefacts live here. Implementation lands in the main `src/` / `strategies/` / new `core/`, `structures/`, `brokers/` folders during execution. `upgrade/` stays as the design reference.
