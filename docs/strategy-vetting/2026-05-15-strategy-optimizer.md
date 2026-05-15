# Strategy Optimizer Sweep

Status: presets created, paper-ready but not auto-executing

## Scope

| Field | Value |
|---|---|
| Date | 2026-05-15 |
| Strategies screened | Eligible daily registered strategies with `VETTING_RESULT` not `rejected` or `engine_blocked` |
| First-pass instrument | SPY |
| Instrument checks | QQQ, IWM for top candidates |
| Topology | `vertical_spread` |
| Direction / type | `bull` / `bull_call` |
| Base option settings | 7 DTE, $5 width, $250 target debit, 50% stop, 50% take profit, 1 contract, $0.65 commission |
| Engine | `/api/backtest` through FastAPI TestClient |

## First-Pass Ranking

| Strategy | Ticker | Trades | WR | PF | Sharpe | Max DD | Avg Hold | PnL | Decision |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `rsi2` | SPY | 24 | 75.00% | 2.90 | 1.52 | -3.89% | 2.0 | 1860.57 | Pass |
| `ibs` | SPY | 65 | 73.85% | 1.99 | 1.48 | -3.98% | 1.3 | 2281.36 | Pass |
| `rsi_ibs_confluence` | SPY | 26 | 80.77% | 3.10 | 1.40 | -2.97% | 1.4 | 1505.40 | Watchlist, no new preset |
| `streak_ibs` | SPY | 30 | 70.00% | 2.01 | 1.05 | -3.06% | 1.5 | 1050.12 | Pass |
| `combo_spread` | SPY | 43 | 60.47% | 1.53 | 0.91 | -5.85% | 2.6 | 1650.08 | Reject: Sharpe below 1.0 |
| `panic_dip` | SPY | 23 | 73.91% | 1.84 | 0.81 | -2.90% | 1.4 | 686.89 | Reject: Sharpe below 1.0 |
| `consecutive_days` | SPY | 62 | 56.45% | 1.32 | 0.71 | -12.08% | 2.3 | 1462.62 | Reject: PF and Sharpe below gate |

## Robustness Notes

| Candidate | Nearby checks | Result |
|---|---|---|
| `rsi2` | DTE 14/21 and width 5/10 remained above gate; entry RSI 5 and 15 behaved same as baseline in current engine path. QQQ and IWM checks also cleared. | Robust enough for SPY preset |
| `ibs` | DTE 14/21 and width 5/10 remained above gate; nearby IBS thresholds behaved same as baseline in current engine path. QQQ/IWM were weaker and did not clear. | SPY-only preset |
| `rsi_ibs_confluence` | DTE/width remained above gate, but strict params fell to 19 trades and loose params missed Sharpe. IWM also missed PF/Sharpe. | No new preset; existing preset already covers it |
| `streak_ibs` | Strict IBS sweep: 23 trades, 82.61% WR, PF 3.78, Sharpe 1.59. Loose sweep also cleared. | Strict SPY preset |
| `combo_spread` | DTE 21 improved PF but Sharpe stayed below 1.0; QQQ/IWM failed badly. | No preset |

## Instrument Checks

| Strategy | Ticker | Trades | WR | PF | Sharpe | Max DD | Decision |
|---|---|---:|---:|---:|---:|---:|---|
| `rsi2` | QQQ | 22 | 68.18% | 2.09 | 1.07 | -3.24% | Pass |
| `rsi2` | IWM | 23 | 65.22% | 2.10 | 1.00 | -4.88% | Pass, borderline Sharpe |
| `ibs` | QQQ | 60 | 63.33% | 1.20 | 0.41 | -4.56% | Reject |
| `ibs` | IWM | 75 | 54.67% | 1.01 | 0.06 | -9.61% | Reject |
| `streak_ibs` | QQQ | 32 | 71.88% | 2.75 | 1.47 | -1.95% | Pass |
| `streak_ibs` | IWM | 38 | 65.79% | 1.75 | 0.81 | -7.15% | Reject |

## Presets Created

These presets are paper-ready but `auto_execute=false` so they do not disturb the current `canary-moomoo` unattended paper run.

| Preset | Strategy | Ticker | WR | Trades | PF | Sharpe | Notes |
|---|---|---|---:|---:|---:|---:|---|
| `rsi2-spy-75wr-moomoo` | `rsi2` | SPY | 75.00% | 24 | 2.90 | 1.52 | Most robust cross-instrument candidate |
| `ibs-spy-74wr-moomoo` | `ibs` | SPY | 73.85% | 65 | 1.99 | 1.48 | Strong SPY-only candidate |
| `streak-ibs-spy-83wr-moomoo` | `streak_ibs` | SPY | 82.61% | 23 | 3.78 | 1.59 | Strict IBS variant from robustness sweep |

## Notes For Next Run

- If one of these should replace `canary-moomoo`, enable only one preset at a time and rerun `python scripts\premarket_check.py`.
- Do not treat QQQ/IWM one-off instrument checks as preset approval until they receive their own DTE/parameter robustness passes.
- The current engine appears to ignore some nested strategy params for older strategies; before broad automated optimization, confirm every target strategy reads from `req.strategy_params`.
