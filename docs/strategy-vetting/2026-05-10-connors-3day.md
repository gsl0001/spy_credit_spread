# Connors 3-Day High/Low Vetting

Status: rejected

## Intake

| Field | Value |
|---|---|
| Source | Larry Connors/Cesar Alvarez 3-Day High/Low method, via [EdgeRater](https://www.edgerater.com/blog/connors-etf-three-day-highlow-method/) |
| Edge | Mean-reversion bounce after repeated lower highs in a long-term uptrend. |
| Timeframe | Daily |
| Topology | Bull-call debit vertical |
| Expected hold | 1-5 days |
| Invalidation | Fewer than 20 trades, PF below 1.5, Sharpe below 1.0, or negative 1-year recency. |

## Implementation

| Field | Value |
|---|---|
| Files touched | `strategies/connors_3day.py`, `core/scanner.py` |
| Strategy id | `connors_3day` |
| Bar size | `1 day` |
| History period | `5y` |
| Engine support | Supported by `/api/backtest` daily vertical-spread harness. |

## Backtest Matrix

All runs used SPY, 1 contract, 7 DTE, $5 width, $250 target debit, 50% stop, 50% take-profit, $0.65/contract commission, mark-to-market enabled.

| Run | Years | Params | Trades | WR | PF | Sharpe | Max DD | Avg Hold | Total PnL | Decision |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| Baseline | 3 | `lower_high_days=3,rsi_period=4,entry_rsi=20,exit_rsi=55,trend_sma=200,exit_sma=5,max_hold_days=5` | 6 | 50.00% | 1.00 | 0.01 | -3.74% | 2.0 | -$1.86 | Reject |
| Recency | 1 | same | 0 | 0.00% | 0.00 | 0.00 | 0.00% | 0.0 | $0.00 | Reject |
| Robust looser entry | 3 | `entry_rsi=25` | 8 | 62.50% | 1.52 | 0.48 | -4.31% | 1.9 | $308.55 | Reject |
| Robust stricter entry | 3 | `entry_rsi=15` | 2 | 50.00% | 0.95 | -0.03 | -1.68% | 2.0 | -$9.21 | Reject |
| Robust fewer lower highs | 3 | `lower_high_days=2` | 11 | 72.73% | 2.45 | 0.99 | -3.63% | 2.0 | $779.08 | Reject |

## Gate Decision

| Requirement | Debit bar | Result | Pass |
|---|---:|---:|---|
| Total trades | >= 20 | 6 baseline, 11 best nearby | No |
| Win rate | >= 55% mean-rev | 50.00% baseline | No |
| Profit factor | >= 1.5 | 1.00 baseline | No |
| Sharpe ratio | >= 1.0 | 0.01 baseline | No |
| Max drawdown | no worse than -15% | -3.74% | Yes |
| Avg hold | 1-5 days | 2.0 | Yes |
| Recency | not directionally opposite | 0 trades | No |
| Robustness | does not collapse | sparse and unstable | No |

## Preset

Not created.

## Notes For Next Run

The thesis may work better on a multi-ETF scanner or equity-underlying backtest, but SPY debit spreads over the last 3 years are too sparse. Do not retry without expanding the universe, using a longer historical study, or changing the option topology.
