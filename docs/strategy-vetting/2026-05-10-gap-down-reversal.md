# Gap-Down Reversal Vetting

Status: rejected

## Run 1

Date: 2026-05-10

## Intake

| Field | Value |
|---|---|
| Source | QuantifiedStrategies gap-down mean-reversion studies; Connors/Alvarez short-term ETF reversal work |
| Edge | Overnight gap-down dislocations in an uptrend may mean-revert over the next 1-3 sessions. |
| Timeframe | Daily |
| Topology | Bull-call debit vertical |
| Expected hold | 1-3 days |
| Invalidation | Recency turns negative or nearby threshold sweeps lose Sharpe/PF despite a good-looking baseline. |

## Implementation

| Field | Value |
|---|---|
| Strategy id | `gap_down_reversal` |
| File | `strategies/gap_down_reversal.py` |
| Bar size | `1 day` |
| History period | `5y` |
| Engine | `run_backtest_engine` vertical-spread debit support |
| Class verdict | `VETTING_RESULT = "rejected"` |

## Backtest Matrix

All runs used SPY, bull-call vertical, 7 DTE, $5 width, $250 target debit, 50% stop, 50% take profit, 1 contract, realistic commissions.

| Run | Years | Params | Trades | WR | PF | Sharpe | Max DD | Avg Hold | PnL |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 3 | `gap_down_pct=0.35, min_close_down_pct=0.25, entry_ibs=0.40, trend_sma=200` | 23 | 69.57% | 1.30 | 0.37 | -5.50% | 1.4 | 307.44 |
| Loose | 3 | `gap_down_pct=0.20, min_close_down_pct=0.00, entry_ibs=0.50, trend_sma=200` | 34 | 76.47% | 2.18 | 1.21 | -4.73% | 1.4 | 1263.08 |
| Loose recency | 1 | Same as loose | 3 | 66.67% | 0.82 | -0.29 | -1.78% | 1.7 | -32.28 |
| Gap 0.25 | 3 | `gap_down_pct=0.25, min_close_down_pct=0.00, entry_ibs=0.50, trend_sma=200` | 32 | 78.12% | 2.23 | 1.21 | -4.73% | 1.4 | 1240.30 |
| IBS 0.40 | 3 | `gap_down_pct=0.20, min_close_down_pct=0.00, entry_ibs=0.40, trend_sma=200` | 27 | 74.07% | 1.72 | 0.77 | -4.82% | 1.4 | 727.23 |
| No trend filter | 3 | `gap_down_pct=0.35, min_close_down_pct=0.25, entry_ibs=0.40, trend_filter=false` | 30 | 60.00% | 0.96 | -0.03 | -5.50% | 1.4 | -52.82 |

## Gate Decision

| Gate | Requirement | Result |
|---|---|---|
| Trades | >= 20 | Pass in most 3-year configs |
| Win rate | >= 55% mean reversion | Pass in most configs |
| Profit factor | >= 1.5 | Mixed; baseline fails, loose passes |
| Sharpe | >= 1.0 | Mixed; loose passes, IBS 0.40 and baseline fail |
| Max drawdown | no worse than -15% | Pass |
| Avg hold | matches 1-3 day thesis | Pass |
| Robustness | nearby params remain above gate | Fail: `entry_ibs=0.40` Sharpe 0.77 |
| Recency | not directionally opposite | Fail: 1-year loose config PnL -32.28, PF 0.82, Sharpe -0.29 |

Decision: reject. The loose 3-year config is tempting, but it does not survive the recency check and nearby IBS robustness. Do not create a preset.

## Preset

Not created.

## Notes For Next Run

- Retry only if the implementation changes materially, such as requiring a larger true gap with a same-day failed selloff reversal or adding an intraday confirmation harness.
- This overlaps with `panic_dip`, `rsi_ibs_confluence`, and `streak_ibs`, all of which currently provide cleaner evidence.

