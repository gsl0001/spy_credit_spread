# Connors Cumulative RSI Vetting

Status: rejected

## Intake

| Field | Value |
|---|---|
| Source | Connors/Alvarez Cumulative RSI(2) via [Easycators](https://easycators.com/thinkscript/cumulative-rsi-2-trading-strategy/) and [Trade Loss Tracker](https://tradelosstracker.com/library/book/16-short-term-trading-strategies-that-work-alvarez/extended) |
| Edge | Require sustained RSI(2) selling pressure by summing multiple RSI values, rather than buying a single oversold print. |
| Timeframe | Daily |
| Topology | Bull-call debit vertical |
| Expected hold | 1-7 days |
| Invalidation | Fewer than 20 trades, PF below 1.5, Sharpe below 1.0, negative recency, or nearby threshold collapse. |

## Implementation

| Field | Value |
|---|---|
| Files touched | `strategies/cumulative_rsi.py`, `core/scanner.py` |
| Strategy id | `cumulative_rsi` |
| Bar size | `1 day` |
| History period | `5y` |
| Engine support | Supported by `/api/backtest` daily vertical-spread harness. |

## Backtest Matrix

All runs used SPY, 1 contract, $5 width, $250 target debit, 50% stop, 50% take-profit, $0.65/contract commission, mark-to-market enabled.

| Run | Years | DTE | Params | Trades | WR | PF | Sharpe | Max DD | Avg Hold | Total PnL | Decision |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| Baseline | 3 | 7 | `rsi_period=2,cum_days=2,entry_sum=35,exit_rsi=65,trend_sma=200,exit_sma=5,max_hold_days=7` | 22 | 68.18% | 1.74 | 0.87 | -4.49% | 1.8 | $916.73 | Reject |
| Recency | 1 | 7 | same | 1 | 100.00% | 999.99 | 2.81 | -0.01% | 2.0 | $166.30 | Too sparse |
| Strict threshold | 3 | 7 | `entry_sum=25` | 17 | 82.35% | 3.86 | 1.64 | -2.90% | 1.9 | $1,683.45 | Reject: sparse |
| Loose threshold | 3 | 7 | `entry_sum=45` | 34 | 61.76% | 1.31 | 0.52 | -6.74% | 1.9 | $681.10 | Reject |
| Higher exit | 3 | 7 | `exit_rsi=70` | 22 | 68.18% | 1.74 | 0.87 | -4.49% | 1.8 | $916.73 | Reject |
| 3-day sum | 3 | 7 | `cum_days=3,entry_sum=50` | 15 | 66.67% | 2.04 | 0.94 | -3.17% | 1.5 | $899.17 | Reject: sparse |
| Midpoint | 3 | 7 | `entry_sum=30` | 19 | 73.68% | 2.44 | 1.22 | -2.94% | 1.7 | $1,259.00 | Reject: sparse |
| Midpoint recency | 1 | 7 | `entry_sum=30` | 1 | 100.00% | 999.99 | 2.81 | -0.01% | 2.0 | $166.30 | Too sparse |
| Midpoint 14-DTE | 3 | 14 | `entry_sum=30` | 18 | 77.78% | 2.69 | 1.19 | -2.14% | 2.2 | $848.53 | Reject: sparse |
| Midpoint 14-DTE recency | 1 | 14 | `entry_sum=30` | 1 | 100.00% | 999.99 | 2.75 | -0.01% | 2.0 | $93.52 | Too sparse |
| Baseline 14-DTE | 3 | 14 | `entry_sum=35` | 21 | 76.19% | 2.09 | 0.95 | -3.53% | 2.2 | $720.13 | Reject |
| Baseline 14-DTE recency | 1 | 14 | `entry_sum=35` | 1 | 100.00% | 999.99 | 2.75 | -0.01% | 2.0 | $93.52 | Too sparse |

## Gate Decision

| Requirement | Debit bar | Result | Pass |
|---|---:|---:|---|
| Total trades | >= 20 | 22 baseline | Yes |
| Win rate | >= 55% mean-rev | 68.18% baseline | Yes |
| Profit factor | >= 1.5 | 1.74 baseline | Yes |
| Sharpe ratio | >= 1.0 | 0.87 baseline | No |
| Max drawdown | no worse than -15% | -4.49% baseline | Yes |
| Avg hold | 1-7 days | 1.8 baseline | Yes |
| Recency | not directionally opposite and enough cadence | positive but only 1 trade | No |
| Robustness | does not collapse | loose threshold PF 1.31, Sharpe 0.52 | No |

## Preset

Not created.

## Notes For Next Run

The stricter settings have attractive quality but not enough trades. The wider settings produce enough trades but fail Sharpe and robustness. Revisit only if the engine supports a portfolio of ETFs, because the original thesis was not meant to rely solely on a sparse SPY options sample.
