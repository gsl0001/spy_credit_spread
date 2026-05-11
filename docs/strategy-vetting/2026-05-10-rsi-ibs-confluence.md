# RSI(2) + IBS Confluence Vetting

Status: promoted

## Run 1

Date: 2026-05-10

## Intake

| Field | Value |
|---|---|
| Source | Larry Connors, "Short-Term Trading Strategies That Work"; Quantpedia "Internal Bar Strength as Equity Market Predictor" |
| Edge | Requires both RSI(2) oversold and close-near-low IBS, reducing false positives versus either signal alone. |
| Timeframe | Daily |
| Topology | Bull-call debit vertical |
| Expected hold | 1-5 days |
| Invalidation | Fails debit gate on baseline or nearby IBS threshold sweeps; recency result is directionally opposite. |

## Implementation

| Field | Value |
|---|---|
| Strategy id | `rsi_ibs_confluence` |
| File | `strategies/rsi_ibs_confluence.py` |
| Bar size | `1 day` |
| History period | `5y` |
| Engine | `run_backtest_engine` vertical-spread debit support |
| Class verdict | `VETTING_RESULT = "shipped"` |

## Backtest Matrix

All runs used SPY, bull-call vertical, 7 DTE, $5 width, $250 target debit, 50% stop, 50% take profit, 1 contract, realistic commissions.

| Run | Years | Params | Trades | WR | PF | Sharpe | Max DD | Avg Hold | PnL |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 3 | `RSI(2)<15, IBS<0.25, trend_sma=200, exit_rsi=70, exit_sma=5, max_hold=5` | 26 | 80.77% | 3.10 | 1.40 | -2.97% | 1.4 | 1505.27 |
| Recency | 1 | Same as baseline | 1 | 100.00% | 999.99 | 0.78 | -0.75% | 2.0 | 51.35 |
| Robust strict IBS | 3 | `IBS<0.20`, other params baseline | 24 | 79.17% | 2.83 | 1.38 | -1.93% | 1.3 | 1306.34 |
| Robust loose IBS | 3 | `IBS<0.35`, other params baseline | 30 | 80.00% | 2.61 | 1.30 | -5.67% | 1.4 | 1426.94 |

## Gate Decision

| Gate | Requirement | Result |
|---|---|---|
| Trades | >= 20 | Pass: 26 baseline |
| Win rate | >= 55% mean reversion | Pass: 80.77% |
| Profit factor | >= 1.5 | Pass: 3.10 |
| Sharpe | >= 1.0 | Pass: 1.40 |
| Max drawdown | no worse than -15% | Pass: -2.97% |
| Avg hold | matches 1-5 day thesis | Pass: 1.4 |
| Robustness | nearby params remain profitable and above gate | Pass |
| Recency | not directionally opposite | Pass, but sample is only 1 trade |

Decision: promote. The strategy passes the debit-spread gate and robustness checks. Recency sample is sparse but positive, so this should enter paper-trial monitoring rather than live confidence.

## Preset

Preset: `rsi-ibs-confluence-moomoo`

Key fields:

- `strategy_name`: `rsi_ibs_confluence`
- `topology`: `vertical_spread`
- `direction`: `bull`
- `strategy_type`: `bull_call`
- `target_dte`: `7`
- `strike_width`: `5`
- `spread_cost_target`: `250`
- `fixed_contracts`: `1`
- `bypass_event_blackout`: `false`

## Notes For Next Run

- Start a paper trial before any real-money promotion.
- Track cadence carefully; 1-year recency had only one signal.
- If signal frequency is too low, compare against `streak_ibs` rather than loosening RSI/IBS thresholds further.

