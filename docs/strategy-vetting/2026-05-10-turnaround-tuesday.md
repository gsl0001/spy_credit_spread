# Turnaround Tuesday Vetting

Status: rejected

## Run 1

Date: 2026-05-10

## Intake

| Field | Value |
|---|---|
| Source | QuantifiedStrategies "Turnaround Tuesday Trading Strategy"; Connors/Alvarez weekday + short-term mean-reversion work |
| Edge | Monday weakness may reverse on Tuesday, especially when Monday closes weak in its daily range. |
| Timeframe | Daily |
| Topology | Bull-call debit vertical |
| Expected hold | 1-2 days |
| Invalidation | Fails debit gate on trade count, PF, or Sharpe after IBS/down-day sweeps. |

## Implementation

| Field | Value |
|---|---|
| Strategy id | `turnaround_tuesday` |
| File | `strategies/turnaround_tuesday.py` |
| Bar size | `1 day` |
| History period | `5y` |
| Engine | `run_backtest_engine` vertical-spread debit support |
| Class verdict | `VETTING_RESULT = "rejected"` |

## Backtest Matrix

All runs used SPY, bull-call vertical, 7 DTE, $5 width, $250 target debit, 50% stop, 50% take profit, 1 contract, realistic commissions.

| Run | Years | Params | Trades | WR | PF | Sharpe | Max DD | Avg Hold | PnL |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 3 | `entry_ibs=0.20, min_down_pct=0.00, trend_sma=200, max_hold=2` | 9 | 55.56% | 0.93 | -0.06 | -1.62% | 1.0 | -15.63 |
| IBS 0.30 | 3 | `entry_ibs=0.30, min_down_pct=0.00, trend_sma=200, max_hold=2` | 15 | 53.33% | 1.24 | 0.25 | -3.89% | 1.0 | 125.61 |
| Down 0.25 | 3 | `entry_ibs=0.30, min_down_pct=0.25, trend_sma=200, max_hold=2` | 10 | 50.00% | 1.03 | 0.04 | -2.83% | 1.0 | 11.64 |
| No trend filter | 3 | `entry_ibs=0.20, min_down_pct=0.00, trend_filter=false, max_hold=2` | 10 | 50.00% | 0.78 | -0.23 | -1.62% | 1.0 | -61.78 |
| IBS 0.50 | 3 | `entry_ibs=0.50, min_down_pct=0.00, trend_sma=200, max_hold=2` | 29 | 58.62% | 1.64 | 0.75 | -3.74% | 1.0 | 542.89 |
| IBS 0.30 recency | 1 | Same as IBS 0.30 | 0 | 0.00% | 0.00 | 0.00 | 0.00% | 0.0 | 0.00 |

## Gate Decision

| Gate | Requirement | Result |
|---|---|---|
| Trades | >= 20 | Fail except the loose `entry_ibs=0.50` sweep |
| Win rate | >= 55% mean reversion | Mixed; best loose sweep 58.62% |
| Profit factor | >= 1.5 | Only loose sweep passes |
| Sharpe | >= 1.0 | Fail; best 0.75 |
| Max drawdown | no worse than -15% | Pass |
| Avg hold | matches 1-2 day thesis | Pass |
| Robustness | nearby params remain above gate | Fail |
| Recency | not directionally opposite | Fail/no sample: 0 trades in 1-year recency for IBS 0.30 |

Decision: reject. The weekday effect may exist in underlying returns, but the signal is too sparse and too weak for 7-DTE $5-wide debit-spread mechanics.

## Preset

Not created.

## Notes For Next Run

- Do not retry as a debit spread without a materially stronger trigger.
- If credit vertical support is added, Turnaround Tuesday could be re-evaluated as a modest-drift premium-selling strategy.
- It may also work better as an equity/ETF overnight hold than as an options debit spread.

