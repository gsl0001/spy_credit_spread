# Williams %R Pullback Vetting

Status: rejected

## Intake

| Field | Value |
|---|---|
| Source | Williams %R oversold-bounce with 200-day trend filter via [Finwiz](https://finwiz.io/technical-indicators/williams-percent-r) and mean-reversion framing via [StockSharp](https://stocksharp.com/store/stocksharp.strategies.0242_williams_r_mean_reversion.py/) |
| Edge | Buy closes near the low of the recent high-low range while SPY remains above the 200-day SMA. |
| Timeframe | Daily |
| Topology | Bull-call debit vertical |
| Expected hold | 1-7 days |
| Invalidation | Fewer than 20 trades, PF below 1.5, Sharpe below 1.0, negative recency, or nearby lookback/threshold collapse. |

## Implementation

| Field | Value |
|---|---|
| Files touched | `strategies/williams_r.py`, `core/scanner.py` |
| Strategy id | `williams_r` |
| Bar size | `1 day` |
| History period | `5y` |
| Engine support | Supported by `/api/backtest` daily vertical-spread harness. |

## Backtest Matrix

All runs used SPY, 1 contract, 7 DTE, $5 width, $250 target debit, 50% stop, 50% take-profit, $0.65/contract commission, mark-to-market enabled.

| Run | Years | Params | Trades | WR | PF | Sharpe | Max DD | Avg Hold | Total PnL | Decision |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| Baseline 10-day | 3 | `lookback=10,entry_wr=-90,exit_wr=-50,trend_sma=200,exit_sma=5,max_hold_days=7` | 20 | 80.00% | 4.57 | 2.02 | -1.96% | 1.9 | $2,298.15 | Check recency |
| Baseline recency | 1 | same | 2 | 50.00% | 0.93 | -0.13 | -1.78% | 2.0 | -$13.34 | Reject |
| Strict entry | 3 | `entry_wr=-95` | 13 | 76.92% | 3.01 | 1.37 | -2.28% | 2.1 | $1,166.36 | Reject: sparse |
| Loose entry | 3 | `entry_wr=-85` | 25 | 72.00% | 2.79 | 1.55 | -2.82% | 1.7 | $1,963.32 | Pass 3y |
| 7-day candidate | 3 | `lookback=7,entry_wr=-90,exit_wr=-50,trend_sma=200,exit_sma=5,max_hold_days=7` | 22 | 81.82% | 4.95 | 2.06 | -2.64% | 2.0 | $2,542.66 | Check recency |
| 7-day recency | 1 | same | 2 | 50.00% | 0.93 | -0.13 | -1.78% | 2.0 | -$13.34 | Reject |
| 14-day lookback | 3 | `lookback=14` | 16 | 75.00% | 2.84 | 1.39 | -3.87% | 2.1 | $1,382.47 | Reject: sparse |
| 8-day neighbor | 3 | `lookback=8` | 20 | 80.00% | 4.47 | 1.93 | -1.96% | 1.9 | $2,231.34 | Pass 3y |
| 6-day neighbor | 3 | `lookback=6` | 25 | 80.00% | 4.44 | 2.07 | -2.65% | 1.9 | $2,663.51 | Pass 3y |
| Exit -60 neighbor | 3 | `exit_wr=-60` | 22 | 81.82% | 4.95 | 2.06 | -2.64% | 2.0 | $2,542.66 | Pass 3y |
| Exit -40 neighbor | 3 | `exit_wr=-40` | 22 | 81.82% | 4.95 | 2.06 | -2.64% | 2.0 | $2,542.66 | Pass 3y |

## Gate Decision

| Requirement | Debit bar | Result | Pass |
|---|---:|---:|---|
| Total trades | >= 20 | 22 for 7-day candidate | Yes |
| Win rate | >= 55% mean-rev | 81.82% | Yes |
| Profit factor | >= 1.5 | 4.95 | Yes |
| Sharpe ratio | >= 1.0 | 2.06 | Yes |
| Max drawdown | no worse than -15% | -2.64% | Yes |
| Avg hold | 1-7 days | 2.0 | Yes |
| Recency | not directionally opposite | 1-year PnL -$13.34, PF 0.93 | No |
| Robustness | does not collapse | 6/8-day lookbacks and exit variants passed 3y | Yes |

## Preset

Not created.

## Notes For Next Run

This is a strong 3-year candidate with unusually stable nearby parameters, but the most recent 1-year slice is directionally negative. Revisit after more recent Williams %R signals accumulate, or consider a paper-only watchlist tier for strong low-cadence candidates that fail live-preset recency.
