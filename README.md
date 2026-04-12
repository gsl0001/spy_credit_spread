<div align="center">

# SPY Options Backtesting Engine

### *Institutional-Grade Algorithmic Options Research & Trading Platform*

A full-stack platform for backtesting, optimizing, paper trading, and live trading options strategies on SPY (and any yfinance-supported ticker). Three modes in one dashboard: backtester with advanced analytics, Alpaca paper trading with real-time scanning, and Interactive Brokers live trading via TWS.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-19+-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://reactjs.org/)
[![lightweight-charts](https://img.shields.io/badge/lightweight--charts-v5-131722?style=for-the-badge)](https://tradingview.github.io/lightweight-charts/)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)]()

</div>

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Three Engine Modes](#three-engine-modes)
- [Backtester Features](#backtester-features)
- [Strategies](#strategies)
- [Option Topologies](#option-topologies)
- [Filters & Risk Controls](#filters--risk-controls)
- [Performance Analytics](#performance-analytics)
- [Paper Trading — Alpaca](#paper-trading--alpaca)
- [Live Trading — IBKR TWS](#live-trading--ibkr-tws)
- [Installation & Setup](#installation--setup)
- [API Reference](#api-reference)
- [Known Issues & Next Steps](#known-issues--next-steps)

---

## Architecture Overview

```
spy_credit_spread/
├── main.py                   # FastAPI backend — REST endpoints, backtest engine, analytics
├── paper_trading.py          # Alpaca integration — positions, orders, signal scanner
├── ibkr_trading.py           # IBKR TWS integration — combo orders, positions, heartbeat
├── start.py                  # One-command launcher (backend + frontend)
├── requirements.txt
├── strategies/
│   ├── base.py               # BaseStrategy ABC
│   ├── builder.py            # Black-Scholes pricer + OptionTopologyBuilder (6 topologies)
│   ├── consecutive_days.py   # Red/green day mean-reversion strategy
│   └── combo_spread.py       # SMA/EMA crossover + volume breakout strategy
└── frontend/
    └── src/
        ├── App.jsx            # React 19 SPA — all three modes, ~1,050 lines
        └── index.css          # Dark theme design system
```

**Backend**: FastAPI + Uvicorn · **Scheduling**: APScheduler BackgroundScheduler
**Frontend**: React 19 + Vite 8 · **Charts**: lightweight-charts v5 · **Plots**: Recharts
**Options Pricing**: Black-Scholes via SciPy · **Market Data**: yfinance

---

## Three Engine Modes

Switch between modes using the sidebar toggle. The left sidebar (strategy, filters, risk rules, presets) is shared — the same config drives backtesting, paper scanning, and live scanning consistently.

| Mode | Purpose |
|------|---------|
| **Backtest** | Simulate strategy over historical data, optimize parameters, review risk metrics |
| **Paper** | Connect Alpaca paper account, scan live signals, auto-execute, monitor positions & orders |
| **Live** | Connect IBKR TWS, scan live signals, place real combo orders, kill switch, heartbeat |

---

## Backtester Features

### Entry & Exit Engine

- Mark-to-market pricing on every bar using Black-Scholes (adjusts for theta decay day-by-day)
- Stop loss, take profit, and trailing stop — all configured as % of cost basis
- Expiry-based forced exit when DTE reaches zero
- Direction-aware logic: bull and bear paths both fully implemented and tested

### Position Sizing

| Method | Description |
|--------|-------------|
| Fixed contracts | N contracts per trade (default) |
| Dynamic sizing | Risk X% of equity per trade, optional dollar max cap |
| Targeted spread % | Size to X% of capital, bounded by max allocation cap |

### Preset System

- 5 built-in presets: Conservative, Aggressive, Post-Crash, Low-Vol Scalp, Bear Market
- Save unlimited custom presets under any name (persisted in `localStorage`)
- Delete custom presets from the UI
- All settings persist across browser sessions

---

## Strategies

### Consecutive Days (Mean Reversion)

Entry fires after N consecutive red candles (bull) or green candles (bear). Exit on M reversal candles or stop loss.

Parameters: `entry_red_days`, `exit_green_days`

### Combo Spread (Trend / Volume Breakout)

Uses SMA(3,8,10) crossdown and EMA(5,3) trend alignment. Two entry conditions: SMA crossdown + EMA confirmation, or low-volume EMA breakout with OHLC average confirmation. Time-based or profit-count based exit.

Parameters: `combo_sma1/2/3`, `combo_ema1/2`, `combo_max_bars`, `combo_max_profit_closes`

### Adding a New Strategy

1. Create `strategies/my_strategy.py` inheriting `BaseStrategy`
2. Implement: `name`, `compute_indicators(df, req)`, `check_entry(df, i, req)`, `check_exit(df, i, state, req)`, `get_schema()`
3. Register in `StrategyFactory.STRATEGIES` in `main.py`

---

## Option Topologies

All pricing uses **Black-Scholes** (European) with 21-day rolling historical volatility and live T-bill risk-free rate (^IRX).

| Topology | Description | Direction |
|----------|-------------|-----------|
| `vertical_spread` | Bull call spread or bear put spread | bull / bear |
| `long_call` | ATM long call (auto-swaps to long put on bear) | bull / bear |
| `long_put` | ATM long put | bear |
| `straddle` | ATM call + ATM put | neutral |
| `iron_condor` | OTM put spread + OTM call spread | neutral |
| `butterfly` | Long ATM, short 2× OTM, long far OTM | neutral |

**IV Realism Factor**: Multiplied onto historical vol before pricing to account for implied/realized spread. Default `1.15`.

---

## Filters & Risk Controls

### Entry Filters

| Filter | Keys | Description |
|--------|------|-------------|
| RSI | `use_rsi_filter`, `rsi_threshold` | Oversold/overbought — direction-aware |
| EMA | `use_ema_filter`, `ema_length` | Price above/below EMA confirmation |
| SMA200 | `use_sma200_filter` | Long-term trend filter |
| Volume | `use_volume_filter` | Volume must exceed 20-day MA |
| VIX | `use_vix_filter`, `vix_min/max` | Restrict trades to a VIX band |
| Regime | `use_regime_filter`, `regime_allowed` | Allow only bull/bear/sideways/all |

Regime is computed from 50 SMA vs 200 SMA vs price alignment.

### Exit Controls

| Control | Key | Notes |
|---------|-----|-------|
| Stop Loss | `stop_loss_pct` | % loss on cost basis triggers exit |
| Take Profit | `take_profit_pct` | % gain triggers exit (0 = off) |
| Trailing Stop | `trailing_stop_pct` | % drawdown from peak value triggers exit |
| DTE Expiry | `target_dte` | Force exit when days-to-expiry hits 0 |
| Strategy Exit | varies | Each strategy defines its own exit logic |

---

## Performance Analytics

14 metrics displayed after every backtest:

| Metric | Description |
|--------|-------------|
| Total P&L | Net profit after all commissions |
| Win Rate | % trades closed profitable |
| Sharpe Ratio | Annualized risk-adjusted return |
| Sortino Ratio | Sharpe using only downside deviation |
| Max Drawdown | Largest peak-to-trough equity decline |
| Profit Factor | Gross wins / gross losses |
| Kelly % | Optimal risk fraction (f* = W − (1−W)/R) |
| Recovery Factor | Total P&L / max dollar drawdown |
| Avg Win / Avg Loss | Average winning and losing trade |
| Max Consecutive Losses | Worst losing streak |
| Avg Hold Days | Average trade duration |
| Final Equity | Starting capital + total P&L |

### Analytics Tabs

- **Chart** — Candlestick price action with entry/exit markers + equity curve side-by-side
- **Trades** — Full trade log: entry/exit dates, SPY prices, spread cost, P&L, regime, exit reason
- **Analytics**:
  - Duration distribution histogram
  - Monte Carlo (1,000 simulations, P5/P50/P95, probability of profit, colored histogram)
  - Regime breakdown — trades/win rate/P&L per bull/bear/sideways market
  - Walk-forward analysis — rolling window stability test
- **Optimizer** — Grid search over two parameters, results sorted by P&L, best row highlighted

---

## Paper Trading — Alpaca

Connect a free [Alpaca paper account](https://alpaca.markets) (API key + secret).

### Features

- **Header HUD** — live equity, buying power, cash when connected
- **Market Scanner** — configurable interval (default 60s), runs strategy signal logic on live yfinance data
- **Signal widget** — SIGNAL FIRING / NO SIGNAL with price, RSI value, RSI filter pass/fail, EMA filter pass/fail
- **Auto-Execute** — when enabled, automatically places a market order when a signal fires
- **Kill Switch** — immediately stops scanner and submits close orders for all open positions
- **30s auto-refresh** of positions and orders while connected
- **Tabs**: Positions · Open Orders · All Orders (with fill prices) · Scan Log (last 30 entries)

### Setup

1. Create a paper account at [alpaca.markets](https://alpaca.markets)
2. Generate API Key + Secret in the dashboard
3. Enter credentials in the Paper mode panel — they are saved in `localStorage`

---

## Live Trading — IBKR TWS

Requires an Interactive Brokers account with **TWS** or **IB Gateway** running locally with API access enabled.

### Features

- **Connection panel** — host / port / client ID config with live status badge (ONLINE / DROPPED / OFFLINE)
- **Header HUD** — net liquidation, buying power, day P&L, unrealized P&L
- **15s heartbeat** — reconnects automatically, timestamp shown in header
- **Market Scanner** — same signal logic as paper mode; auto-scan every 60s option
- **Test Order** — non-filling SPY limit buy at $1.05 to verify TWS connectivity end-to-end
- **Combo Orders** — multi-leg `BAG` contracts built from the topology builder, submitted at midpoint
- **Kill Switch** — stops scanner and cancels all open orders
- **Per-order cancel** button in Open Orders tab
- **30s auto-refresh** of positions and orders while connected

### TWS Configuration

1. Open TWS → `Edit → Global Configuration → API → Settings`
2. Enable `Enable ActiveX and Socket Clients`
3. Set socket port to `7497` (paper TWS) or `7496` (live TWS) or `4001/4002` (IB Gateway)
4. Allow connections from localhost

### Order Types by Topology

| Topology | Legs | Order Structure |
|----------|------|----------------|
| Vertical Spread | 2 | BAG combo — buy/sell calls or puts |
| Long Call / Put | 1 | Single-leg market/limit |
| Straddle | 2 | BAG combo |
| Iron Condor | 4 | BAG combo |
| Butterfly | 3 | BAG combo |

Orders use `LimitOrder` at the midpoint price fetched from TWS. Expiry is approximated as nearest weekday from `today + target_dte`.

---

## Installation & Setup

### Requirements

- Python 3.11 or 3.12 (recommended; `ib_insync` has compatibility issues on Python 3.14)
- Node.js 18+

### One-Command Start

```bash
git clone https://github.com/gsl0001/spy_credit_spread.git
cd spy_credit_spread
pip install -r requirements.txt
python start.py
```

`start.py` installs npm dependencies, starts the uvicorn backend on port **8000**, starts the Vite dev server on port **5173**, and opens the browser.

### Manual Start

Terminal 1 — backend:
```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Terminal 2 — frontend:
```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`

### Python Dependencies

```
fastapi  uvicorn  yfinance  pandas  numpy  scipy  pydantic
apscheduler  ib_insync  alpaca-trade-api
```

### Node Dependencies

```
react@19  react-dom@19  vite@8  lightweight-charts@5  recharts  lucide-react
```

---

## API Reference

All endpoints at `http://127.0.0.1:8000`. Interactive docs at `/docs` (Swagger).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/strategies` | List registered strategy plugins |
| POST | `/api/backtest` | Run backtest → trades, metrics, equity curve, analytics |
| POST | `/api/optimize` | Grid search two parameters → ranked results |
| GET | `/api/live_chain` | Live options chain via yfinance |
| POST | `/api/paper/connect` | Alpaca connection test + account info |
| POST | `/api/paper/positions` | Open Alpaca positions |
| POST | `/api/paper/orders` | Recent Alpaca orders |
| POST | `/api/paper/execute` | Place equity order (buy/sell) |
| POST | `/api/paper/scan` | Run signal scan on live market data |
| POST | `/api/scanner/start` | Start background APScheduler scanner |
| POST | `/api/scanner/stop` | Stop background scanner |
| GET | `/api/scanner/status` | Scanner state + recent logs |
| POST | `/api/ibkr/connect` | Connect to TWS + return account summary |
| POST | `/api/ibkr/positions` | IBKR portfolio positions |
| POST | `/api/ibkr/execute` | Place multi-leg combo order |
| POST | `/api/ibkr/test_order` | Non-filling SPY test order at $1.05 |
| GET | `/api/ibkr/orders` | Open TWS orders |
| POST | `/api/ibkr/cancel` | Cancel order by ID |

---

## Known Issues & Next Steps

### Active Bugs (Not Yet Fixed)

1. **Silent skip on zero-cost legs** (`main.py:309`) — `if abs(one_cc) < 1e-3` silently discards credit topologies priced near-zero. Should warn or fall back to ATM sizing.

2. **Iron condor margin is wrong** — credit spreads return `net_cost < 0`, which adds to equity instead of reserving margin. Correct margin = `(strike_width × 100) − abs(net_credit)` per contract.

3. **Double `compute_indicators` call** (`main.py:574–580`) — engine runs indicators once inside `run_backtest_engine`, then again to build the regime timeline. Adds unnecessary latency.

4. **IBKR execute always BUYs** (`main.py:879`) — `side = 'BUY'` is hardcoded. Credit/bear positions should open with `SELL`.

5. **Expiry snap is approximate** — adds `target_dte` calendar days then snaps to nearest weekday. Real SPY expirations are Mon/Wed/Fri. Should pull actual expirations from the live chain.

6. **`live_chain` is slow** — calls `t.info` (makes a secondary HTTP request). Should use `t.fast_info` and cache with TTL.

7. **`scanner_state` not thread-safe** — global dict has no locking. Race condition with `--workers > 1`. Needs `asyncio.Lock` or an external store.

8. **Circular import in `paper_trading.py`** — `from main import BacktestRequest, StrategyFactory` creates a circular dependency. Shared types should move to `models.py`.

9. **Duplicate indicator code** — `consecutive_days` and `combo_spread` each compute RSI, EMA, SMA independently. Should share `_add_base_indicators(df, req)` in `base.py`.

---

### Next Steps — Priority Order

#### High Priority

- [ ] **Fix IBKR order direction** — derive buy-to-open vs sell-to-open from topology/direction (debit = BUY, credit = SELL)
- [ ] **Fix iron condor margin** — reserve spread width minus credit instead of crediting to equity
- [ ] **Real expiry resolution** — query `/api/live_chain` to find the nearest actual expiry at or beyond `target_dte`
- [ ] **Extract `models.py`** — move `BacktestRequest`, `StrategyFactory` to a shared module, break circular import

#### Medium Priority

- [ ] **Alpaca options orders** — Alpaca now supports real options; replace equity proxy with actual options leg orders
- [ ] **Live bid/ask pricing** — replace Black-Scholes synthetic pricing in live/paper mode with real bid/ask from yfinance or data feed
- [ ] **Multi-ticker scanning** — scan multiple symbols concurrently, aggregate and display all signals
- [ ] **Schema-driven sidebar** — each strategy already implements `get_schema()`; wire the sidebar to render fields dynamically instead of hardcoded JSX
- [ ] **Scanner thread safety** — wrap `scanner_state` mutations in `asyncio.Lock`
- [ ] **`live_chain` cache** — `t.fast_info` + LRU cache with 60s TTL

#### Low Priority / Enhancements

- [ ] **Export trades to CSV** — download button in the Trades tab
- [ ] **Win rate heatmap** — day-of-week × month heatmap chart in Analytics tab (data already computed server-side)
- [ ] **Signal alerts** — Web Notifications API browser notification when a signal fires during auto-scan
- [ ] **Walk-forward optimizer** — run parameter grid within each walk-forward window to prevent in-sample overfitting
- [ ] **Docker Compose** — containerize backend + frontend for single-command deployment
- [ ] **Rate limiting** — `slowapi` on `/api/backtest` and `/api/optimize` (CPU-intensive)
- [ ] **Test coverage** — pytest suite for `run_backtest_engine`, `compute_analytics`, `OptionTopologyBuilder`, and strategy `check_entry`/`check_exit` (current coverage ~0%)
- [ ] **Mobile layout** — current UI requires ~1200px width; add responsive breakpoints
- [ ] **Dark/light mode toggle**

---

<div align="center">
  <sub>Built for data-driven options traders. <strong>Not financial advice.</strong></sub>
</div>
