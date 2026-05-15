# Strategy-Vetting Skill — Reference

## Purpose

One-way valve. A strategy may not become a moomoo preset until it clears an explicit statistical bar measured on the correct topology.

---

## The 10-Step Process

| Step | What |
|---|---|
| 1 | **Choose bar size** — `"1 day"` (daily harness, works now) or `"5 mins"/"1 hour"` (requires a custom sibling engine first) |
| 2 | **Choose topology** — debit spread, credit spread, iron condor, straddle, butterfly, or calendar. Must be chosen BEFORE writing code because exits depend on it. |
| 3 | **Choose DTE** — 7-14 for short-hold debit; 7-21 for credit; 14-30 for condors |
| 4 | **Write the strategy class** — subclass `BaseStrategy`, implement `compute_indicators`, `check_entry`, `check_exit` |
| 5 | **Register** in `core/scanner.py:list_strategy_classes()` |
| 6 | **Backtest via `/api/backtest`** — minimum 3 years, use the same harness presets will use |
| 7 | **Pass or fail the bar** — topology-specific thresholds (see below) |
| 8 | **Reference benchmarks** — compare against shipped and rejected strategies |
| 9 | **Write the preset** — only if Step 7 cleared; edit `config/presets.json` |
| 10 | **Live paper validation** — one full trading week on moomoo paper |

---

## Topology → Bar Mapping

| Metric | Debit spread | Credit spread | Iron condor |
|---|---|---|---|
| `total_trades` | ≥ 20 | ≥ 20 | ≥ 20 |
| `win_rate` | ≥ 55% (mean-rev) | ≥ 65% | ≥ 70% |
| `profit_factor` | ≥ 1.5 | ≥ 1.3 | ≥ 1.2 |
| `sharpe_ratio` | ≥ 1.0 | ≥ 0.8 | ≥ 0.7 |
| `max_drawdown` | ≥ -15% | ≥ -10% | ≥ -8% |

---

## Engine Support Today

| Topology | Supported |
|---|---|
| Vertical debit (bull-call, bear-put) | ✅ full |
| Vertical credit (bull-put, bear-call) | ⚠️ partial — engine prices it as debit (results are indicative, not accurate) |
| Iron condor / calendar / butterfly | ❌ engine work required |
| Intraday | ❌ except ORB which has its own engine |

---

## Trigger → Topology Decision Tree

| Trigger profile | Topology |
|---|---|
| Big move expected in ≤3 days | Debit (bull-call / bear-put) |
| Small drift or stay-above-level | Credit (bull-put / bear-call) |
| Stay in a range | Iron condor |
| Big move, direction unknown | Straddle / strangle |
| Pin at specific price | Butterfly |

---

## Sweeping & Rejection Rules

- Up to **3 parameter sweeps** before rejecting
- After 3 failures: consider **topology change first**, then **widen strike width**, then **reject**
- Sharpe near-miss → switch topology (don't lower the bar)
- Document every rejection in the file docstring with: failing metrics + path to re-attempt

---

## Shipped vs Rejected (as of 2026-05-10)

### Shipped

| Strategy | Sharpe | Notes |
|---|---|---|
| `rsi2` | 1.52 | 24 trades/3y, WR 75%, PF 2.9 |
| `ibs` | 1.49 | 66 trades/3y, WR 74%, PF 2.0 |
| `orb` | per paper | Intraday harness, ~250 trades/y |
| `consecutive_days` | 1.0–1.3 | Varies by config |

### Rejected (with graduation path)

| Strategy | Why rejected | Path to graduation |
|---|---|---|
| `monday_spread` | PF 1.09, Sharpe 0.24 — Monday drift too small for 50% SL debit | Credit spread engine → re-test as bull-put |
| `fomc_pre` | WR 47%, Sharpe 0.29 — buys elevated pre-FOMC IV, gets crushed on announcement | Sell IV via credit/condor (opposite topology) |
| `spy_pcs` | Engine-blocked — debit proxy clears credit bar (PF 1.69, Sharpe 0.96) | Build credit-spread engine → immediate ship candidate |
| `turn_of_month` | WR 40-45% as debit | Credit spread engine |
| `dabd` | Sharpe stuck at 0.85 across 5 sweeps | Credit spread or wider strikes |
| `vix_spike` | 17 trades < 20 minimum | Extend to 5y or add QQQ |
| `donchian` | PF 1.28, Sharpe 0.64 | Vol + vol-contraction filter |
| `bollinger_b` | 14 trades, Sharpe 0.68 | Vol-regime filter |
| `eod_drift` | 0 trades on daily harness | Build intraday engine or reframe as daily |

---

## Key Anti-Patterns

- No 0DTE on the daily harness (build a sibling engine first)
- No tuning beyond 3 sweeps
- No "Sharpe is close enough" exceptions — the bar is the bar
- No look-ahead in `compute_indicators` (always `.shift(1)` future-facing data)
- No live network calls in `check_entry` / `check_exit`
- No "bull market only" strategies without an explicit trend filter

---

## The Single Biggest Unlock

Building the **credit-spread engine** (negative `entry_cost`, `max_loss = width×100 − credit`) unblocks 5 strategies in one lift: `spy_pcs`, `monday_spread`, `fomc_pre`, `turn_of_month`, `dabd`.
