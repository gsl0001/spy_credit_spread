---
name: strategy-vetting
description: Use when adding a new SPY options trading strategy. Walks through choosing the right time frame and option topology (debit vs credit vs condor vs calendar) for the trigger profile, writing the BaseStrategy subclass, registering it, running 3-year backtests with topology-appropriate thresholds, and only then creating a moomoo preset. Prevents shipping unvalidated strategies and forces topology selection before coding.
---

# Strategy vetting — from paper claim to moomoo preset

The project has a one-way valve: a strategy may not become a moomoo preset
until its backtest stats clear an explicit bar **measured on the topology
that fits the trigger profile**. This skill is the procedure.

## Inputs you need before starting

- A reference for the strategy (paper, blog post, internal note). Cite it
  in the docstring of the new file.
- A clear answer to: "what does this catch that the existing strategies
  (`consecutive_days`, `combo_spread`, `orb`, `rsi2`, `ibs`) don't?"
- A bar-size declaration (see Step 1).
- A topology selection (see Step 2). **Do this before writing code.**

If you can't write each of those in one sentence, stop. The strategy isn't
ready.

---

## Step 1 — Choose the time frame

`BaseStrategy.BAR_SIZE` declares the bar resolution as a class invariant.
Each level has different harness implications:

| BAR_SIZE | Use cases | Harness support |
|---|---|---|
| `"1 day"` | Mean-reversion, breakouts on closes, calendar effects, multi-day positions | **Default daily harness in main.py** — works out of the box |
| `"1 min"` | Scalp-grain triggers (tick imbalance, VWAP reversion, micro-momentum). yfinance caps 1m data at **7 days**, so backtest sample is thin — prefer 5m unless the edge truly lives on 1m bars. | **Generic intraday harness** (`core/backtest_intraday.py`) when `INTRADAY_ENGINE = "generic"`. ORB harness still available for breakout-touch strategies. |
| `"5 mins"` | Intraday breakout (ORB), mean-revert intraday, VWAP touch, 0DTE same-session entry/exit. yfinance caps 5m data at **60 days**. | Two engines available — see "Intraday engine selection" below. |
| `"1 hour"` | Macro intraday — drift over half-session windows. | Build a sibling harness or re-frame as daily. |

**Rule:** if you pick anything other than `"1 day"`, set `INTRADAY_ENGINE`
on the class to pick which intraday harness scores the strategy. Don't try
to score intraday strategies on the daily harness — you will get 0 trades
and waste hours debugging. (See `strategies/eod_drift.py` for a documented
case of this failure mode.)

### Intraday engine selection

Set `INTRADAY_ENGINE` as a class attribute on the strategy. Default is
`"orb"` for back-compat with the existing intraday strategies (`orb`,
`ldm_0dte`, `ldm_fade_0dte`, `order_flow_0dte`).

| `INTRADAY_ENGINE` | When to use | Engine file |
|---|---|---|
| `"orb"` | Strategy is an opening-range-style breakout: needs OR window, breakout-touch detection, direction inferred from breakout side. Knobs: `or_minutes`, `or_start_hhmm`, `offset`, `min_range_pct`. | `core/backtest_orb.py` |
| `"generic"` | Any other 1m/5m strategy. Engine calls `strategy.check_entry(df, i, req)` / `strategy.check_exit(df, i, trade_state, req)` per bar exactly like the daily harness, and force-flats at `session_close` (default 15:55 ET). No OR-window assumptions. | `core/backtest_intraday.py` |

**Pricing** in both engines uses the same linear delta-vs-entry model
(spread mid ≈ entry_debit + 0.40 × Δunderlying × sign, clamped to
`[0, width]`). Adequate for screening; not a substitute for Black-Scholes
when you need vega precision.

**Force-flat at session close** is enforced by both engines — intraday
strategies cannot carry overnight by contract. If you need multi-day
intraday, that's a different engine (not in scope here).

### History period

`BaseStrategy.HISTORY_PERIOD` (yfinance period notation) sets the warmup.
Pick the smallest window that still produces ≥20 trades:

| Strategy fires | HISTORY_PERIOD |
|---|---|
| Multiple times per week | `"3y"` produces ~50-200 trades, plenty |
| ~weekly | `"5y"` for a 30-trade-ish sample |
| ~monthly (calendar effects) | `"5y"` minimum, more if available |

---

## Step 2 — Choose the option topology

This is the step the project's first ten strategies all skipped. **The
trigger profile dictates the topology**, not project default. Pick BEFORE
writing the strategy class because the strategy's exit rules depend on
topology.

### Trigger → topology mapping

| Trigger profile | Best topology | Why |
|---|---|---|
| **Big directional move expected, short hold (≤3 days)** | Bull-call / bear-put **debit** spread | Asymmetric reward, captures the move. Theta acceptable because hold is short. Examples: `rsi2`, `ibs`, `consecutive_days`, `orb`. |
| **Small directional drift over multi-day window** | Bull-put / bear-call **credit** spread | Theta tailwind. The drift is too small for a debit spread's loss to recover; the credit collects regardless of small drift, just needs SPY to stay above (or below) the short strike. Examples: TOM, FOMC, calendar effects. |
| **Range-bound (low vol expected)** | **Iron condor** (short both wings) | Wins if SPY stays in range. Profits double-theta. Use for vol-contraction triggers. |
| **Big move expected, direction unknown** | **Long straddle / strangle** | Pays for direction-agnostic move. Use sparingly — pays a lot of theta. |
| **Pin near specific price** | **Long butterfly** | Asymmetric high-reward narrow-zone bet. Use when the trigger predicts a specific level. |
| **Volatility expansion expected** | **Long calendar** | Wins on IV expansion + theta differential between near/far expiry. Pre-event or vol-contraction triggers. |

### How to tell which trigger profile you have

- "I expect a big move within N days" → directional debit.
- "I expect SPY to drift modestly OR stay above a level" → credit spread.
- "I expect SPY to stay in a range" → iron condor.
- "I expect a big move, but I don't know which direction" → straddle/strangle.

The two near-misses in this codebase (`vix_spike`, `dabd`) both **rejected
on Sharpe** specifically because they used debit spreads when their thesis
was "modest reversion over 1-3 days" — credit spreads would have harvested
the theta and probably cleared the bar. **Reject and document** rather than
re-tune; topology change is the right escalation.

### Engine support

| Topology | Backtest engine support | Notes |
|---|---|---|
| Vertical debit (bull-call, bear-put) | ✅ `run_backtest_engine` in `main.py` | Default, well-tested |
| Vertical credit (bull-put, bear-call) | ⚠️ partial — verify | Engine treats `entry_cost` as paid premium; for credit you need the engine to handle negative entry_cost (premium received) and width × 100 − credit as max loss. Confirm before relying. |
| Iron condor | ❌ not supported | Engine work required first |
| Calendar / butterfly / straddle | ❌ not supported | Engine work required first |
| Intraday (1m / 5m debit vertical) | ✅ via `INTRADAY_ENGINE = "orb"` for breakout-touch or `INTRADAY_ENGINE = "generic"` for check_entry/check_exit-driven strategies | Pricing is linear-delta approximation; force-flat at session close |
| Intraday credit / condor / calendar | ❌ neither engine prices these | Engine work required first |

If your chosen topology isn't supported, **the strategy can't ship until
the engine learns to price it**. That's a much larger lift than writing
the strategy itself; flag it explicitly in the next-agent notes.

---

## Step 3 — Choose the DTE

DTE selection follows topology and expected hold:

| Topology | Hold time | Recommended DTE |
|---|---|---|
| Debit spread, 1-3 day hold | ≤3 days | 7-14 (room for spread to move; theta survivable) |
| Debit spread, 5-10 day hold | 5-10 days | 14-21 |
| Credit spread | any | 7-21 (more theta = more credit collected) |
| Iron condor | any | 14-30 (max theta, but long enough to survive small moves) |
| Calendar | any | front-leg matches expected move horizon, back-leg 30+ DTE |
| Long straddle | event-driven | match the event window (FOMC ≈ 0DTE, earnings ≈ next-week) |

**0DTE warning:** unless you've built a custom intraday harness, do NOT use
`target_dte: 0` for new strategies. The daily harness models one bar/day
and 0DTE means the position expires same day — engine math gets unstable.

---

## Step 4 — Write the strategy class

File: `strategies/<id>.py`. Subclass `strategies.base.BaseStrategy`.

Required:
```python
class <Name>Strategy(BaseStrategy):
    BAR_SIZE = "1 day"                # or "1 min" / "5 mins" for intraday
    HISTORY_PERIOD = "5y"             # capped at 7d for "1 min", 60d for "5 mins"
    INTRADAY_ENGINE = "generic"       # only consulted if BAR_SIZE != "1 day"

    @property
    def name(self) -> str: ...
    @classmethod
    def id(cls) -> str: return "<id>"
    @classmethod
    def get_schema(cls) -> dict: ...
    def compute_indicators(self, df, req) -> pd.DataFrame: ...
    def check_entry(self, df, i, req) -> bool: ...
    def check_exit(self, df, i, trade_state, req) -> tuple[bool, str]: ...
```

Conventions to follow:

- **Read params from `req.strategy_params` first, then `req` flat.**
  The `BacktestRequest` model only accepts strategy-specific params via
  the nested `strategy_params` dict; flat keys are silently dropped.
  Use a `_get(self, req, key, default)` helper:
  ```python
  def _get(self, req, key, default):
      params = getattr(req, "strategy_params", {}) or {}
      v = params.get(key)
      return v if v is not None else getattr(req, key, default)
  ```

- **`compute_indicators` always also computes** `SMA_200`, `SMA_50`, `RSI`,
  `Volume_MA`, `HV_21`, and `EMA_<req.ema_length>`. The shared filter
  pipeline (`use_rsi_filter`, `use_ema_filter`, etc.) reads those columns —
  if you skip them, every cross-cutting filter silently no-ops.

- **`compute_indicators` must run shared indicators BEFORE any early
  return.** If your strategy has a "no DatetimeIndex → bail" branch, the
  shared indicators must already be on the df before that branch.

- **`check_entry` honors `req.strategy_type`** so a `bull_call` preset
  doesn't fire bear signals. Define a private `_is_bear(req)` method.

- **`check_exit` always exits eventually**. Include `(True, "expired")`
  when `(entry_dte - days_held) <= 0`, otherwise the backtester holds to
  infinity.

- **No live network calls** in `check_entry` / `check_exit`. Cache anything
  you need in `compute_indicators` or in module scope (see ORB's VIX
  cache for a pattern; `vix_spike` does the cache + merge inside
  `compute_indicators`).

### Topology-aware exit rules

A debit spread exit:
```python
# revert-to-mean → take profit
# trend-break → cut loss
# expired → mandatory exit
```

A credit spread exit:
```python
# short strike still safe + theta accretion → hold to <max_hold> for max theta capture
# short strike challenged (Close near short_K) → cut, don't ride into max loss
# expired worthless → mandatory exit at "expired" branch
```

An iron condor exit:
```python
# either short strike challenged → close affected wing
# both wings safe at <max_hold> → close to lock theta gain
```

---

## Step 5 — Register the strategy

Edit `core/scanner.py:list_strategy_classes()` — single source of truth.
Add the import inside the existing `try:` block and the `id → cls` entry.

---

## Step 6 — Backtest via API

Always use `/api/backtest`. Don't write a one-off script — the endpoint
runs the same realism harness presets will use, that's the only meaningful
test.

### Topology-appropriate request

For a **debit** strategy:
```json
{
  "strategy_type":"bull_call","direction":"bull","topology":"vertical_spread",
  "target_dte":7, "strike_width":5, "spread_cost_target":250,
  "stop_loss_pct":50,"take_profit_pct":50,
  ...
}
```

For a **credit** strategy: confirm engine support first; if confirmed, use
`strategy_type:"bull_put"` (or `bear_call`) with appropriate sizing.

For **iron condor / calendar / butterfly**: engine work required before
this step is meaningful.

```bash
curl -sS -X POST http://127.0.0.1:8000/api/backtest \
  -H 'Content-Type: application/json' \
  -d '{
    "ticker":"SPY","strategy_id":"<id>",
    "strategy_type":"bull_call","direction":"bull",
    "topology":"vertical_spread","target_dte":7,"strike_width":5,
    "years_history":3,"capital_allocation":10000,"contracts_per_trade":1,
    "stop_loss_pct":50,"take_profit_pct":50,"commission_per_contract":0.65,
    "use_rsi_filter":false,"use_ema_filter":false,"use_vix_filter":false,
    "enable_mc_histogram":false,"enable_walk_forward":false,
    "strategy_params":{<your params here>}
  }'
```

Run for at least 3 years. If the strategy claims regime-specific edge, also
run a 1-year window for tighter recency.

---

## Step 7 — Decide if it ships (topology-aware bar)

The bar varies by topology. **Use the column for the topology you actually
backtested.**

| Metric | Debit spread (daily) | Credit spread | Iron condor | Intraday debit (1m/5m) | Notes |
|---|---|---|---|---|---|
| `total_trades` | ≥ 20 | ≥ 20 | ≥ 20 | ≥ 50 | Intraday must clear higher count — more bars, more opportunities, so a low count signals an over-restrictive trigger. |
| `win_rate` | ≥ 55% (mean-rev) / ≥ 45% (trend) | ≥ 65% | ≥ 70% | ≥ 45% | Intraday breakouts pay asymmetric R:R; lower WR acceptable if PF holds. |
| `profit_factor` | ≥ 1.5 | ≥ 1.3 | ≥ 1.2 | ≥ 1.4 | Intraday gets less theta drag (same-session) but more whipsaw. |
| `sharpe_ratio` | ≥ 1.0 | ≥ 0.8 | ≥ 0.7 | ≥ 0.9 | Intraday samples annualize aggressively; treat as a relative signal. |
| `max_drawdown` | ≥ -15% | ≥ -10% | ≥ -8% | ≥ -12% | Intraday DD bounded by same-day force-flat. |
| `avg_hold_days` | matches strategy thesis | matches | matches | < 0.5 (≈ < 3h of a 6.5h session) | Reported as fraction of session by the intraday adapter. |

**If any threshold fails for the topology you used, do not ship.** Iterate
parameters via `strategy_params` and re-run.

After 3 parameter sweeps that don't clear:

1. **First, consider topology change.** If you used a debit spread and
   Sharpe is the only failing metric (typical for short-hold mean-rev
   strategies — see `vix_spike` and `dabd` rejection notes), switching to
   a credit spread often graduates the strategy because theta tailwind
   improves consistency. Document the planned topology change as a TODO
   even if the engine doesn't yet support it.
2. **Second, consider widening the strike width** ($10 instead of $5) so
   the per-trade move is larger relative to noise — improves PF and Sharpe
   simultaneously.
3. **Third, reject.** Document the rejection in the file's docstring with
   the failing metrics + the path to re-attempt. Don't keep tuning beyond
   3 sweeps.

---

## Step 8 — Reference benchmarks

Validated as of 2026-05-10. Use these to calibrate your expectations:

### Shipped (cleared the bar)
| Strategy | Topology | Trades/3y | WR | PF | Sharpe | DD |
|---|---|---|---|---|---|---|
| `rsi2` | debit | 24 | 75% | 2.9 | 1.52 | -3.9% |
| `ibs` | debit | 66 | 74% | 2.0 | 1.49 | -4.0% |
| `orb` (intraday harness) | debit 0DTE | ~250 | ~65% | ~1.6 | per SSRN paper | — |
| `consecutive_days` | debit | varies | 60-65% | 1.6-2.0 | 1.0-1.3 | -8% |

### Rejected (and why) — don't re-attempt without addressing the recorded reason
| Strategy | Topology | Why rejected | Path to graduation |
|---|---|---|---|
| `donchian` | debit | PF 1.28, Sharpe 0.64 — SPY's per-day trend-continuation drift too small for spread mechanics | Volume + vol-contraction filter, expect Sharpe still <1 on SPY |
| `eod_drift` | intraday-only | 0 trades on daily harness | Build sibling intraday engine OR reframe as daily next-day-open trade |
| `turn_of_month` | debit | 40-45% WR, PF <1.3 — calendar drift smaller than 50% SL noise floor | **Switch to credit spread** (sell put spread expiring in TOM window) — engine work required |
| `vix_spike` | debit | 17 trades < 20 minimum (other metrics excellent: PF 3.2, Sharpe 1.26) | Extend data window to 5y OR pair with QQQ for 2× sample density |
| `bollinger_b` | debit | PF 1.80 / Sharpe 0.68 / 14 trades — bands "breathe" with vol so true extremes rare | Combine with vol-regime filter — but at that point it's RSI(2) in disguise |
| `dabd` | debit | Sharpe stuck at 0.85 across 5 sweeps — small per-trade EV (~$30) drowns in variance | **Switch to credit spread** (theta tailwind) OR widen strikes to $10 |

Two of six rejections (`turn_of_month`, `dabd`) explicitly block on
**topology mismatch**. That's the strongest signal that this skill needed
the topology dimension to be load-bearing.

---

## Step 9 — Write the preset (only if Step 7 cleared)

Edit `config/presets.json` directly. Mirror an existing moomoo preset's
shape; `rsi2-moomoo` is a good debit template, no credit/condor templates
exist yet.

Required fields:
- `name`: `<id>-moomoo`
- `broker`: `moomoo`
- `strategy_name`: `<id>`
- `strategy_params`: the parameters that produced the winning backtest,
  PLUS `chain_max_bid_ask_pct: 0.25` (moomoo NBBO is wider than IBKR's)
- `notes`: cite the backtest stats verbatim, including the topology used
- `auto_execute`: `true`
- `bypass_event_blackout`: **`false`** (smoke test only)
- `position_size_method`: `fixed` with `fixed_contracts: 1` for first-time
  presets; switch to `dynamic_risk` only after the live paper run confirms
- `target_dte`, `strike_width`, `spread_cost_target`: from the winning
  backtest
- `topology`, `direction`, `strategy_type`: must match the topology you
  validated

Verify the preset loads:
```bash
curl -sS http://127.0.0.1:8000/api/presets/<id>-moomoo
```

---

## Step 10 — Live paper validation

Before declaring victory, run the preset on moomoo paper for **one full
trading week** while the system is otherwise idle. Compare live-paper
stats to the backtest:

- Live trade count should be roughly proportional to backtest cadence.
- Live WR should be within 10 percentage points of backtest WR.
- A single losing trade > backtest's max drawdown is a stop-and-investigate.

Tag the result in `AGENT_COORDINATION_README.md`.

---

## Anti-patterns to refuse

- **No data-snooping.** Don't tune to match a specific year. If you tune
  on 2024, test on 2022-2023 separately.
- **No "bull market only" strategies** without an explicit trend filter.
  SPY 2009-2024 makes everything look great; the trend filter proves you
  thought about regime.
- **No look-ahead in `compute_indicators`.** Use `.shift(1)` on anything
  that references the future.
- **No PnL-tuned stops.** The 50% SL / 50% TP is the project default for
  debit; credit and condors have different defaults (see Step 2). Override
  only with a comment explaining the strategy-specific reason.
- **No "Sharpe is close enough" exceptions.** The bar is the bar. The way
  to clear a near-miss is to change topology or expand sample, not to
  redefine the threshold.
- **No 0DTE on the daily harness.** Build a sibling engine first.

---

## Files this skill touches

- `strategies/<id>.py` — new
- `core/scanner.py:list_strategy_classes` — register
- `config/presets.json` — preset (only after Step 7 clears)
- `AGENT_COORDINATION_README.md` — log results in next-pass section
- `core/backtest_<id>.py` — only if you need a per-strategy engine
  beyond what `core/backtest_orb.py` (ORB-style) or
  `core/backtest_intraday.py` (generic) provide; for typical 1m/5m
  debit-vertical strategies, set `INTRADAY_ENGINE = "generic"` and reuse
  the shared harness.

## Companion files

- `.claude/moomoo_api_reference.md` — what the SDK supports for execution
- `.claude/moomoo_gap_analysis.md` — features we *could* add but haven't
- `docs/MOOMOO_REVIEW.md` — known issues with the execution path

## Engine work that would unlock more topologies

These are tracked but not in this skill's scope. Adding any of them
expands the set of strategies that can ship:

1. **Credit spread support** in `run_backtest_engine` — handles negative
   entry_cost, max_loss = width × 100 − credit. Would unlock the
   `turn_of_month` and `dabd` near-misses and any future calendar /
   small-drift strategy.
2. **Iron condor support** — opens range-bound strategies (vol-contraction
   triggers, neutral RSI bands).
3. ✅ **Generic intraday harness** — landed via
   `core/backtest_intraday.py` + `INTRADAY_ENGINE = "generic"`. Opens
   the `eod_drift` class of strategies (mean-revert intraday, VWAP
   touch, micro-momentum) and any non-breakout 1m/5m 0DTE play. ORB
   engine retained for breakout-touch strategies.
4. **Calendar / butterfly support** — opens vol-expansion strategies and
   pin-prediction strategies.
