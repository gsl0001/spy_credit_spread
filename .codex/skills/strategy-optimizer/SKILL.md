---
name: strategy-optimizer
description: Use when the user wants to search existing system strategies, backtest them across instruments, timeframes, option settings, and strategy parameters, find working candidates, and create named presets that include win rate only after validation. Works with the local SPY/moomoo trading repo and should be used for strategy sweeps, optimization runs, candidate ranking, preset generation, and paper-trial promotion decisions.
---

# Strategy Optimizer

Use this skill to run disciplined strategy sweeps in `C:\spy\master`. It optimizes existing strategies already registered in the system, tests nearby parameter/timeframe/instrument variants, rejects weak or overfit results, and creates presets only for candidates that clear validation.

This skill complements `strategy-vetting`: `strategy-vetting` is for adding or deeply validating one strategy; `strategy-optimizer` is for searching the existing strategy universe for robust presets.

## Operating Rules

- Use existing strategy classes from `core/scanner.py:list_strategy_classes()`.
- Use `/api/backtest` or `main.backtest()` so results match the app path.
- Do not create a preset from a single lucky run.
- Do not optimize on one window and declare victory. Always run baseline, recency, and at least two nearby robustness sweeps.
- Do not switch to real trading.
- Do not overwrite existing presets unless the user explicitly asks.
- Preserve user changes and untracked files.
- Do not print secrets from `.env`, `.env.live`, or `config/.env`.

## Search Inputs

If the user does not specify scope, use conservative defaults:

- Strategies: all registered strategies whose `VETTING_RESULT` is not `rejected` or `engine_blocked`
- Instruments: `SPY` first; optionally test `QQQ` and `IWM` if the user asked for multiple instruments
- Timeframe: use each strategy class `BAR_SIZE`; do not force daily strategies onto intraday bars
- Topology: `vertical_spread`
- Direction: `bull`
- Strategy type: `bull_call`
- Target DTE: `7`
- Strike width: `5`
- Spread cost target: `250`
- Contracts: `1`
- Stop loss: `50`
- Take profit: `50`
- Commission: `0.65`
- Capital: `10000`

If the user asks for "different parameters, timeframe and instrument", treat that as permission to sweep:

- Instrument: `SPY`, `QQQ`, `IWM`
- DTE: `7`, `14`, `21` for daily strategies
- Strike width: `5`, `10`
- Strategy parameters: use the strategy schema defaults plus nearby values inside schema min/max
- Timeframe: only if the strategy supports it through `BAR_SIZE` / `INTRADAY_ENGINE`; otherwise leave unchanged and state why

## Workflow

1. Check repo status:

```powershell
git status --short --branch
```

2. Discover strategies:

```python
from core.scanner import list_strategy_classes
registry = list_strategy_classes()
```

Skip by default:

- `VETTING_RESULT = "rejected"`
- `VETTING_RESULT = "engine_blocked"`
- strategies requiring unsupported topology
- strategies whose data horizon is too short for the requested validation

3. Build a sweep matrix.

Keep the first pass small. Prefer breadth first, then deepen only winners:

- One baseline run per candidate
- Top 3-5 candidates get nearby parameter sweeps
- Top 1-3 candidates get recency and instrument checks

4. Run backtests through `/api/backtest` or `main.backtest()`.

Required metrics:

- `total_trades`
- `win_rate`
- `profit_factor`
- `sharpe_ratio`
- `max_drawdown`
- `avg_hold_days`
- `total_pnl`

5. Apply gates.

Default daily debit-spread gate:

| Metric | Minimum |
|---|---:|
| Trades | 20 |
| Win rate | 55% for mean reversion, 45% for trend |
| Profit factor | 1.5 |
| Sharpe | 1.0 |
| Max drawdown | no worse than -15% |
| Robustness | nearby settings remain above gate or degrade mildly |
| Recency | not directionally opposite |

Intraday gate:

| Metric | Minimum |
|---|---:|
| Trades | 50 |
| Win rate | 45% |
| Profit factor | 1.4 |
| Sharpe | 0.9 |
| Max drawdown | no worse than -12% |

6. Reject overfit candidates.

Reject or mark paper-only if:

- Most profit comes from one trade
- One instrument passes but similar liquid instruments collapse
- Baseline passes but nearby parameters collapse
- Recency is strongly negative
- Trade count is below gate
- Metrics depend on an unsupported topology assumption

7. Write a memo.

Create or update:

`docs/strategy-vetting/YYYY-MM-DD-strategy-optimizer.md`

Include:

- Sweep scope
- Candidate ranking
- Backtest matrix
- Gate decisions
- Presets created
- Candidates rejected and why
- Next run notes

8. Create presets only for validated winners.

Edit `config/presets.json`. Mirror existing moomoo preset shape.

Preset naming rule:

`<strategy>-<instrument>-<winrate>-moomoo`

Examples:

- `rsi2-spy-75wr-moomoo`
- `ibs-qqq-68wr-moomoo`

Use integer-rounded win rate. If two presets collide, add the main distinguishing parameter:

- `rsi2-spy-75wr-dte7-moomoo`
- `ibs-spy-68wr-w10-moomoo`

Required preset fields:

- `name`
- `ticker`
- `strategy_name`
- `strategy_params`
- `broker: "moomoo"`
- `auto_execute: true`
- `fetch_only_live: true`
- `bypass_event_blackout: false`
- `position_size_method: "fixed"`
- `sizing_params.fixed_contracts: 1`
- `topology`
- `direction`
- `strategy_type`
- `target_dte`
- `strike_width`
- `spread_cost_target`
- `stop_loss_pct`
- `take_profit_pct`
- `commission_per_contract`
- `use_mark_to_market: true`
- `notes` with exact backtest stats and validation date

After editing presets, verify:

```powershell
python -m pytest -q tests\test_presets.py
```

If the backend is running, also verify:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/presets/<preset-name>
```

## Sweep Implementation Pattern

Prefer a temporary inline Python runner instead of adding permanent scripts unless the user asks for a reusable optimizer tool.

Use this shape:

```python
from fastapi.testclient import TestClient
import main

client = TestClient(main.app)
resp = client.post("/api/backtest", json=request, timeout=120)
data = resp.json()
metrics = data.get("metrics") or data.get("performance_metrics") or {}
```

Keep output compact: save full results to the memo or a clearly named artifact only if the user asks.

## Final Answer

Report:

- Strategies tested
- Instruments tested
- Best candidates
- Presets created, with names and win rates
- Rejections worth knowing
- Tests run

If no candidate passes, say that clearly and create no preset.
