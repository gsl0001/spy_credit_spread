# MFI Reversion Vetting

Status: rejected

## Intake

| Field | Value |
|---|---|
| Source | Gene Quong and Avrum Soudack, "The Money Flow Index," Technical Analysis of Stocks & Commodities (1997); StockCharts ChartSchool "Money Flow Index (MFI)" |
| Edge | Volume-confirmed oversold mean reversion; intended to catch capitulation dips that price-only `rsi2` and `ibs` may not distinguish. |
| Timeframe | Daily |
| Topology | Bull-call debit vertical |
| Expected hold | 1-5 days |
| Invalidation | Fails debit gate on baseline, produces too few trades, or nearby MFI threshold sweeps do not clear. |

## Implementation

| Field | Value |
|---|---|
| Strategy id | `mfi_reversion` |
| File | `strategies/mfi_reversion.py` |
| Bar size | `1 day` |
| History period | `5y` |
| Engine | `/api/backtest` using `run_backtest_engine` vertical-spread debit support |
| Class verdict | `VETTING_RESULT = "rejected"` |

## Backtest Matrix

All runs used SPY, bull-call vertical, 7 DTE, $5 width, $250 target debit, 50% stop, 50% take profit, 1 contract, realistic commissions, no shared RSI/EMA/VIX filters.

| Run | Years | Params | Trades | WR | PF | Sharpe | Max DD | Avg Hold | PnL |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 3 | `MFI(14)<20, exit_mfi=50, trend_sma=200, RSI(14)<40, max_hold=5` | 0 | 0.00% | 0.00 | 0.00 | 0.00% | 0.0 | 0.00 |
| Recency | 1 | Same as baseline | 0 | 0.00% | 0.00 | 0.00 | 0.00% | 0.0 | 0.00 |
| Strict MFI | 3 | `entry_mfi=15`, other params baseline | 0 | 0.00% | 0.00 | 0.00 | 0.00% | 0.0 | 0.00 |
| Loose MFI | 3 | `entry_mfi=25`, other params baseline | 1 | 0.00% | 0.00 | -0.68 | -1.30% | 1.0 | -130.49 |
| No RSI confirm | 3 | `use_rsi_confirm=false`, other params baseline | 0 | 0.00% | 0.00 | 0.00 | 0.00% | 0.0 | 0.00 |

## Gate Decision

| Gate | Requirement | Result |
|---|---|---|
| Trades | >= 20 | Fail: 0 baseline |
| Win rate | >= 55% mean reversion | Fail: no baseline trades |
| Profit factor | >= 1.5 | Fail: 0.00 baseline |
| Sharpe | >= 1.0 | Fail: 0.00 baseline |
| Max drawdown | no worse than -15% | Pass but meaningless with no trades |
| Avg hold | matches 1-5 day thesis | Fail: no baseline trades |
| Robustness | nearby params remain above gate | Fail: strict/no-RSI had 0 trades; loose had 1 losing trade |
| Recency | not directionally opposite | Fail: no trades |

Decision: reject. The strategy is too sparse as a standalone SPY debit-spread trigger. The only loosened sweep that fired produced one losing trade, which is nowhere near the sample-size gate.

## Preset

No preset created. The strategy did not clear Step 7 of the skill.

## Notes For Next Run

- Do not promote `mfi_reversion` to moomoo paper trading.
- MFI may still be useful as a secondary filter on an already shipped dip strategy, but not as a standalone trigger.
- A credit-spread version should wait until the engine has confirmed credit-spread pricing support.
