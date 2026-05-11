# Down-Streak + IBS Vetting

Status: promoted

## Run 1

Date: 2026-05-10

## Intake

| Field | Value |
|---|---|
| Source | Larry Connors / Cesar Alvarez short-term ETF mean-reversion; Quantpedia "Internal Bar Strength as Equity Market Predictor" |
| Edge | Combines consecutive down closes with weak daily close location, targeting clustered weakness that mean-reverts quickly. |
| Timeframe | Daily |
| Topology | Bull-call debit vertical |
| Expected hold | 1-5 days |
| Invalidation | Fails debit gate for strict and nearby balanced IBS thresholds; recency result is directionally opposite. |

## Implementation

| Field | Value |
|---|---|
| Strategy id | `streak_ibs` |
| File | `strategies/streak_ibs.py` |
| Bar size | `1 day` |
| History period | `5y` |
| Engine | `run_backtest_engine` vertical-spread debit support |
| Class verdict | `VETTING_RESULT = "shipped"` |

## Backtest Matrix

All runs used SPY, bull-call vertical, 7 DTE, $5 width, $250 target debit, 50% stop, 50% take profit, 1 contract, realistic commissions.

| Run | Years | Params | Trades | WR | PF | Sharpe | Max DD | Avg Hold | PnL |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| Strict baseline | 3 | `down_days=2, IBS<0.20, exit_ibs=0.70, trend_sma=200, max_hold=5` | 23 | 82.61% | 3.78 | 1.59 | -3.15% | 1.3 | 1406.11 |
| Strict recency | 1 | Same as strict baseline | 2 | 100.00% | 999.99 | 1.79 | -0.75% | 1.5 | 136.51 |
| Strict nearby | 3 | `down_days=2, IBS<0.30`, other params strict baseline | 30 | 70.00% | 2.01 | 1.05 | -3.06% | 1.5 | 1050.00 |
| Balanced baseline | 3 | `down_days=2, IBS<0.40, exit_ibs=0.70, trend_sma=200, max_hold=5` | 39 | 69.23% | 2.07 | 1.29 | -2.74% | 1.4 | 1477.47 |
| Balanced recency | 1 | Same as balanced baseline | 2 | 100.00% | 999.99 | 1.79 | -0.75% | 1.5 | 136.51 |
| Balanced strict-nearby | 3 | `IBS<0.30`, other params balanced baseline | 30 | 70.00% | 2.01 | 1.05 | -3.06% | 1.5 | 1050.00 |
| Balanced loose-nearby | 3 | `IBS<0.50`, other params balanced baseline | 44 | 68.18% | 1.83 | 1.21 | -4.30% | 1.3 | 1411.66 |

## Gate Decision

| Gate | Requirement | Strict Result | Balanced Result |
|---|---|---|---|
| Trades | >= 20 | Pass: 23 | Pass: 39 |
| Win rate | >= 55% mean reversion | Pass: 82.61% | Pass: 69.23% |
| Profit factor | >= 1.5 | Pass: 3.78 | Pass: 2.07 |
| Sharpe | >= 1.0 | Pass: 1.59 | Pass: 1.29 |
| Max drawdown | no worse than -15% | Pass: -3.15% | Pass: -2.74% |
| Avg hold | matches 1-5 day thesis | Pass: 1.3 | Pass: 1.4 |
| Robustness | nearby params remain profitable and above gate | Pass | Pass |
| Recency | not directionally opposite | Pass | Pass |

Decision: promote both preset variants. The strict variant has higher quality but lower cadence. The balanced variant gives more sample density while preserving PF and Sharpe above the debit-spread bar.

## Preset

Presets:

- `streak-ibs-strict-moomoo`
- `streak-ibs-balanced-moomoo`

Shared key fields:

- `strategy_name`: `streak_ibs`
- `topology`: `vertical_spread`
- `direction`: `bull`
- `strategy_type`: `bull_call`
- `target_dte`: `7`
- `strike_width`: `5`
- `spread_cost_target`: `250`
- `fixed_contracts`: `1`
- `bypass_event_blackout`: `false`

## Notes For Next Run

- Run both variants through paper trials and compare live cadence before choosing one as the primary production candidate.
- Strict and balanced overlap; avoid firing both simultaneously in the same account unless the paper gate enforces per-trial overlap limits.
- If live slippage is materially worse than backtest, prefer strict over balanced.

