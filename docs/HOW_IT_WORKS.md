# SPY Credit Spread Platform — How It Works

A full reference for operators and developers: what the system does, how each piece fits together, and how to use every feature.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Getting Started](#3-getting-started)
4. [The Dashboard (React UI)](#4-the-dashboard-react-ui)
5. [Strategies](#5-strategies)
6. [Scanner & Presets](#6-scanner--presets)
7. [Live Trading — IBKR](#7-live-trading--ibkr)
8. [Live Trading — Moomoo](#8-live-trading--moomoo)
9. [NautilusTrader + Moomoo](#9-nautilustrader--moomoo)
10. [Paper Trading — Alpaca](#10-paper-trading--alpaca)
11. [Backtest Engine](#11-backtest-engine)
12. [Risk & Guardrails](#12-risk--guardrails)
13. [Journal & Fill Watcher](#13-journal--fill-watcher)
14. [Console Control Board](#14-console-control-board)
15. [Notifications — Telegram Bot](#15-notifications--telegram-bot)
16. [API Reference](#16-api-reference)
17. [Configuration Reference](#17-configuration-reference)

---

## 1. System Overview

The platform has **two independent systems** that share config and strategy logic:

| System | Entry point | Broker | Use case |
|---|---|---|---|
| **FastAPI platform** | `python main.py` | IBKR TWS **or** moomoo OpenD | Full dashboard, backtest, scanner, journal |
| **NautilusTrader engine** | `python nautilus/run_live.py` | moomoo OpenD only | Production-grade event-driven live trading |

Most users start with the FastAPI platform. The NautilusTrader engine is for operators who want a hardened standalone process with built-in OMS, reconciliation, and a Rust core.

---

## 2. Architecture

```
spy_credit_spread/
│
├── main.py                 FastAPI backend (40+ REST endpoints)
├── ibkr_trading.py         IBKR TWS adapter (ib_async)
├── moomoo_trading.py       Moomoo OpenD adapter (moomoo-api)
├── console_live.py         Terminal control board (Rich live display)
│
├── core/
│   ├── broker.py           BrokerProtocol + registry (get_broker / register_broker)
│   ├── scanner.py          APScheduler-driven bar fetcher + signal engine
│   ├── presets.py          ScannerPreset dataclass + JSON persistence
│   ├── monitor.py          Position monitor (P&L exits, time exits, HWM)
│   ├── fill_watcher.py     Order fill FSM (submitted → filled / cancelled)
│   ├── risk.py             Pre-trade gates + position sizing
│   ├── journal.py          SQLite persistence (positions, orders, fills, events)
│   ├── chain.py            Options chain resolver + strike picker
│   ├── calendar.py         Market hours + holiday calendar
│   ├── filters.py          Entry filters (RSI, EMA, VIX, SMA200, volume)
│   ├── notifier.py         Telegram push notifications
│   ├── telegram_bot.py     Telegram command bot (poll-based)
│   └── settings.py         .env loader + runtime config
│
├── strategies/
│   ├── base.py             BaseStrategy ABC (compute_indicators / check_entry / check_exit)
│   ├── consecutive_days.py ConsecutiveDays — N red days in, M green days out
│   ├── orb.py              ORB 5-min 0DTE (SSRN 6355218)
│   ├── combo_spread.py     Combo/spread skeleton
│   └── dryrun.py           Always-signals strategy for wiring tests
│
├── config/
│   ├── .env.example        All environment variables with comments
│   ├── presets.json        Saved scanner presets
│   └── events_2026.json    FOMC / CPI / NFP blackout calendar
│
├── frontend/src/
│   ├── views/
│   │   ├── LiveView.jsx    IBKR live trading UI
│   │   ├── MoomooView.jsx  Moomoo live trading UI (orange theme)
│   │   ├── PaperView.jsx   Alpaca paper trading UI
│   │   ├── BacktestView.jsx Backtest engine UI
│   │   ├── JournalView.jsx Trade journal UI
│   │   ├── ScannerView.jsx Signal scanner UI
│   │   └── RiskView.jsx    Risk guardrails UI
│   └── api.js              All API calls in one place
│
└── nautilus/               Standalone NautilusTrader sub-project
    ├── run_live.py         Entry point (TradingNode)
    ├── run_backtest.py     Entry point (BacktestEngine)
    ├── adapters/futu_options/  Custom moomoo options adapter
    └── strategies/orb_spread.py  ORB strategy for NautilusTrader
```

### Request flow (FastAPI live trade)

```
Preset tick fires (APScheduler)
    │
    ▼
Strategy.check_entry(bars)  → signal?
    │ yes
    ▼
Idempotency check (journal)  → already traded today?
    │ no
    ▼
Pre-trade risk gate (risk.py)
    │ approved
    ▼
Chain resolution (chain.py / broker.get_option_chain)
    │
    ▼
Position sizing (risk.size_position)
    │
    ▼
Order placement
    ├─ broker == "ibkr"   → IBKRTrader.place_combo_order (atomic)
    └─ broker == "moomoo" → MoomooTrader.place_spread (legged, with safety net)
    │
    ▼
Journal entry (position + orders)
    │
    ▼
Telegram notification
```

---

## 3. Getting Started

### Prerequisites

| Component | Required for |
|---|---|
| Python 3.11+ | FastAPI platform |
| Node 18+ | Frontend build |
| IBKR TWS or Gateway | IBKR live trading |
| moomoo OpenD v8.3+ | Moomoo live trading |
| Python 3.12+ (separate venv) | NautilusTrader engine |

### Installation

```bash
# 1. Clone / enter project
cd spy_credit_spread

# 2. Backend dependencies
pip install -r requirements.txt

# 3. Frontend build
cd frontend && npm install && npm run build && cd ..

# 4. Copy environment template
cp config/.env.example config/.env
# Edit config/.env — at minimum set IBKR_HOST, IBKR_PORT or FUTU_* vars

# 5. Start backend
python main.py           # runs on http://127.0.0.1:8000

# 6. Open dashboard
open http://127.0.0.1:8000
```

### One-command start

```bash
python start.py          # starts backend + serves frontend on :8000
```

---

## 4. The Dashboard (React UI)

Seven views accessible from the left sidebar:

| View | Icon | What it does |
|---|---|---|
| **Live Trading** | Dashboard | IBKR connection, manual order ticket, positions, order log |
| **Moomoo** | Zap (orange) | Moomoo OpenD connection, legged spread ticket, positions |
| **Paper (Alpaca)** | Radar | Alpaca paper account, equity surrogate positions |
| **Backtest** | Activity | Run historical simulations, view P&L chart + analytics |
| **Journal** | Book | All positions, orders, fills, daily P&L history |
| **Risk & Guardrails** | Shield | Pre-trade gates, risk limits, event calendar |
| **Scanner** | Target | Signal scanner with live preset management |

The topbar shows: live market status, SPY last price, IBKR connection pill, alert count, and the Flatten All panic button.

The statusbar (bottom) shows: API connectivity, IBKR connection, market open/closed, open positions count, and daily realized P&L.

---

## 5. Strategies

All strategies implement the same three-method interface:

```python
class BaseStrategy(ABC):
    def compute_indicators(self, df, req) -> pd.DataFrame   # add columns to bar data
    def check_entry(self, df, i, req) -> bool               # should we open a position?
    def check_exit(self, df, i, trade_state, req) -> (bool, str)  # should we close?
```

### ConsecutiveDays (`consecutive_days`)

Opens a bull call spread after `entry_red_days` consecutive red bars (close < open). Exits after `exit_green_days` consecutive green bars.

**When to use:** Mean-reversion plays on short-term SPY weakness.

**Key parameters:**

| Parameter | Default | Description |
|---|---|---|
| `entry_red_days` | 1 | Number of consecutive down-closes before entry |
| `exit_green_days` | 1 | Number of consecutive up-closes before exit |

### ORB 5-min 0DTE (`orb`)

Opening Range Breakout based on **SSRN paper 6355218** — "Regime-Conditional Alpha in SPY 0DTE Opening Range Breakout Strategies."

Trades the **first 5-minute bar breakout** after the 9:30–9:35 ET opening range closes. Bull breakout → buy bull call debit spread. Bear breakout → buy bear put debit spread.

**Entry filters** (all must pass — these are the paper's critical alpha drivers):

| Filter | Value | Why |
|---|---|---|
| Day of week | Mon / Wed / Fri only | Paper found Tue/Thu have negative edge |
| VIX regime | 15 ≤ VIX ≤ 25 | Lifts win rate from ~47% to ~65% per paper |
| No news day | FOMC / CPI / NFP | Gap risk overwhelms spread edge |
| OR range size | ≥ 0.05% of price | Filters out low-volatility, non-directional opens |
| First breakout only | 9:35 bar | One trade per day (idempotency key) |

**Strike selection (paper's best EV zone):**

| Parameter | Default | Paper range |
|---|---|---|
| `offset` | 1.50 pts | 0.96 – 2.00 pts from breakout price |
| `width` | 5 pts | Spread width |

**Exits:**
- Profit target: +50% of net debit paid
- Stop loss: −50% of net debit paid
- Time exit: 15:30 ET (avoid theta/gamma risk into close)

### DryRun (`dryrun`)

Always returns `check_entry = True`. Used to test the full order pipeline without a real signal.

### Adding your own strategy

1. Create `strategies/my_strategy.py` inheriting `BaseStrategy`
2. Implement `compute_indicators`, `check_entry`, `check_exit`
3. Register it in `core/scanner.py → list_strategy_classes()`
4. It appears automatically in the scanner UI and `/api/strategies`

---

## 6. Scanner & Presets

The scanner runs on APScheduler. Every `timing_value` seconds (or on a cron schedule) it:

1. Fetches `HISTORY_PERIOD` of bars for the preset's ticker
2. Runs `strategy.compute_indicators()` on the bars
3. Calls `strategy.check_entry()` on the latest bar
4. If `auto_execute: true` and signal fired → routes to IBKR or moomoo

### Preset fields

```json
{
  "name": "orb-5m",
  "broker": "ibkr",               // "ibkr" | "moomoo"
  "ticker": "SPY",
  "strategy_name": "orb",
  "strategy_params": {
    "offset": 1.5,                // OTM offset in pts
    "vix_min": 15,
    "vix_max": 25,
    "skip_news_days": true
  },
  "target_dte": 0,                // days to expiry (0 = 0DTE)
  "spread_cost_target": 250.0,   // target net debit in dollars
  "stop_loss_pct": 50.0,
  "take_profit_pct": 50.0,
  "auto_execute": true,
  "timing_mode": "interval",      // "interval" | "cron"
  "timing_value": 60              // seconds between ticks
}
```

### Included presets

| Name | Strategy | Broker | Auto-execute | Notes |
|---|---|---|---|---|
| `s1` | consecutive_days | IBKR | Yes | 1 red day entry, 1 green day exit |
| `dry-run` | dryrun | IBKR | Yes | Pipeline smoke test |
| `orb-5m` | ORB | IBKR | Yes | SSRN 6355218, offset 1.50, 50/50 PT/SL |
| `orb-5m-moomoo` | ORB | Moomoo | Yes | Same strategy, routes to moomoo OpenD |

### Per-day idempotency

Each signal generates a key `scan:{date}:{symbol}:{preset_name}`. If that key is already in the journal (order placed earlier today), the scanner silently skips. This prevents double-trading if the scanner restarts mid-day.

---

## 7. Live Trading — IBKR

### Connection

1. Open IBKR TWS or Gateway (paper port `7497`, live port `7496`)
2. Enable API connections in TWS: `Edit → Global Configuration → API → Enable ActiveX and Socket Clients`
3. Set `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID` in `config/.env`
4. In the dashboard → **Live Trading** → connect

### Order execution

IBKR spreads are placed as **atomic combo orders** via `IBKRTrader.place_combo_order()`. Both legs fill or neither does — no partial-fill risk.

The chain is resolved live against the real IBKR option chain to get actual bid/ask quotes for the strikes chosen by `pick_bull_call_strikes()`.

### Key IBKR API endpoints

| Endpoint | Description |
|---|---|
| `POST /api/ibkr/connect` | Connect to TWS |
| `POST /api/ibkr/execute` | Place a spread order |
| `POST /api/ibkr/exit` | Close a specific position |
| `POST /api/ibkr/flatten_all` | Market-close all open positions (panic) |
| `POST /api/ibkr/cancel` | Cancel a pending order |
| `GET /api/ibkr/orders` | List recent orders |
| `POST /api/ibkr/heartbeat` | Check TWS connection + fetch account data |
| `POST /api/ibkr/chain_debug` | Inspect live option chain for a date/strike |

---

## 8. Live Trading — Moomoo

### Connection

1. Install and open **moomoo OpenD** v8.3+ (download from moomoo developer portal)
2. OpenD must be running at `FUTU_HOST:FUTU_PORT` (default `127.0.0.1:11111`)
3. In the dashboard → **Moomoo** (orange tab) → enter host, port, trade password → Connect

### Legged execution

moomoo has no atomic combo order API. Every spread is placed as **two sequential single-leg limit orders** with a built-in safety net:

```
Step 1: Place long leg (BUY limit)
  → Poll for fill every 0.5s, timeout = 30s

  Long fills within 30s:
    Step 2: Place short leg (SELL limit)
      → Poll for fill every 0.5s, timeout = 30s

      Short fills → SUCCESS (both legs journaled)
      Short times out → CANCEL short order
                      → MARKET SELL long leg (flatten)
                      → Return "short_leg_timeout_flattened"

  Long times out → CANCEL long order
                 → Return "long_leg_timeout"
```

All outcomes (success, partial, flatten) are journaled and visible in the order log.

The **orange warning banner** in the Moomoo UI is permanent — it reminds operators of this risk every session.

### Moomoo option code format

`US.SPY260428C580000`
= `US.{symbol}{YYMMDD}{C|P}{strike × 1000, zero-padded to 8 digits}`

### Broker routing in presets

Set `"broker": "moomoo"` on any preset. The auto-execute path in `_run_preset_tick` detects this and routes to `_moomoo_execute_impl` instead of `_ibkr_execute_impl`. Both paths share the same risk gate, position sizer, and journal.

### Key Moomoo API endpoints

| Endpoint | Description |
|---|---|
| `POST /api/moomoo/connect` | Connect to OpenD, unlock trade |
| `POST /api/moomoo/disconnect` | Disconnect |
| `GET /api/moomoo/account` | Account KPIs (equity, buying power, P&L) |
| `GET /api/moomoo/positions` | Open positions from OpenD |
| `GET /api/moomoo/chain?symbol=SPY&date=2026-04-28` | Option chain for a date |
| `POST /api/moomoo/execute` | Place a spread (legged) |
| `POST /api/moomoo/exit` | Close a position |
| `POST /api/moomoo/cancel` | Cancel a pending order |

---

## 9. NautilusTrader + Moomoo

A **completely separate process** from the FastAPI platform. Uses NautilusTrader's Rust-core event-driven engine. Same ORB strategy logic, same moomoo OpenD broker, but with:

- Built-in OMS and portfolio tracker
- Startup reconciliation (queries existing orders/positions from OpenD)
- Identical legged execution safety net inside the exec client
- Backtest mode runs the same strategy code on yfinance historical data

### Setup

```bash
cd nautilus/
pip install -e ".[dev]"        # installs nautilus_trader, nautilus-futu, etc.
```

Requires **Python 3.12+** in an isolated venv. The parent project can stay on 3.11.

Set `FUTU_*` vars in `config/.env`:

```bash
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_TRD_ENV=0            # 0=simulate, 1=real
FUTU_UNLOCK_PWD_MD5=      # echo -n "your_pin" | md5sum
FUTU_ACC_ID=0             # 0 = auto-discover first US margin account
```

### Running live

```bash
cd nautilus/
python run_live.py        # blocks until Ctrl+C
                          # on_stop() market-closes any open position
```

### Running a backtest

```bash
cd nautilus/
python run_backtest.py --start 2026-01-01 --end 2026-04-25
```

Loads SPY 5-min bars from yfinance. Entry/exit filters run exactly as in live mode. Spread submissions log "no instruments found" (no live option chain in backtest) but all filter logic is validated.

### How the adapter works

```
NautilusTrader message bus
    │
    ├── FutuOptionsDataClient
    │   ├── Subscribes SPY 5-min bars (SubType.K_5M via OpenD push)
    │   ├── Subscribes option quote ticks for subscribed legs
    │   └── Refreshes ^VIX snapshot every 5 min (fail-closed)
    │
    └── FutuOptionsExecClient
        ├── _submit_order()       → single leg via place_order()
        ├── _submit_order_list()  → legged spread with safety net
        └── generate_*_reports()  → startup reconciliation
```

### Running tests (no live connection needed)

```bash
cd nautilus/
pytest tests/ -v           # 25 tests, all stubs, ~0.1s
```

---

## 10. Paper Trading — Alpaca

Alpaca doesn't support options spreads, so the paper trading mode trades **100 shares of SPY as an equity surrogate** to validate strategy signal timing.

### Connection

1. Create an Alpaca paper account at alpaca.markets
2. Generate paper API keys
3. In the dashboard → **Paper (Alpaca)** → enter API key + secret → Connect
4. Keys are saved to `localStorage` — no server-side storage

### What it tests

- Signal timing (does the strategy fire at the right bar?)
- Auto-execute pipeline (idempotency, risk gate, journal)
- P&L tracking and fill reconciliation

For real options execution, use IBKR Live or Moomoo Live.

---

## 11. Backtest Engine

Runs historical simulations of any strategy + topology combination. Uses **yfinance** for price data and **Black-Scholes** for option pricing (21-day realized volatility proxy).

### Running a backtest

1. Dashboard → **Backtest** tab
2. Set ticker, date range, strategy, entry filters
3. Choose topology (bull call, bear put, iron condor, etc.)
4. Set spread cost target, stop loss %, take profit %
5. Click **Run Backtest**

Results include:
- Equity curve chart (lightweight-charts)
- Win rate, average P&L, max drawdown
- Per-trade log with entry/exit prices

### Option topologies supported

| Topology | Description |
|---|---|
| `bull_call` | Long lower strike call + short higher strike call (debit) |
| `bear_put` | Long higher strike put + short lower strike put (debit) |
| `bull_put` | Short higher strike put + long lower strike put (credit) |
| `bear_call` | Short lower strike call + long higher strike call (credit) |
| `iron_condor` | Bull put spread + bear call spread (credit) |
| `iron_butterfly` | ATM short straddle + OTM long strangle |

### Backtest realism adjustments

The `realism_factor` (default 1.15) widens the Black-Scholes mid-price to approximate real bid/ask slippage. Commission (`commission_per_contract`, default $0.65) is subtracted per leg.

---

## 12. Risk & Guardrails

### Pre-trade gate (every live order passes through this)

| Check | Default | Configurable |
|---|---|---|
| Market hours (RTH only) | 9:30–16:00 ET | No |
| Daily loss limit | 2% of equity | `DAILY_LOSS_LIMIT_PCT` |
| Max concurrent positions | 2 | `MAX_CONCURRENT_POSITIONS` |
| Min buying power | 10% of equity | In `risk.py` |

If any check fails, the order is rejected and journaled with a reason code. No order is ever placed.

### Position monitor

Runs every `MONITOR_INTERVAL_SECONDS` (default 15s). For each open position:

1. Fetches the current spread midpoint from the broker
2. Rolls the high-water mark forward (for trailing stop)
3. Evaluates: stop loss, take profit, trailing stop, DTE-based exit, time-based exit, strategy's own `check_exit()`
4. If an exit fires → submits an opposite-side limit order with a 5% haircut (aggressive-to-fill)
5. Falls back to market order if no quote is available

The monitor is broker-aware: IBKR positions use `get_combo_midpoint()` + `place_combo_order()`; moomoo positions use `get_spread_mid()` (individual leg snapshots) + `close_position()`.

### Trailing stop

When `trailing_stop_pct > 0`:
- High-water mark tracks the best mark-to-market value seen since entry
- Stop fires when current value drops more than `trailing_stop_pct`% from the HWM
- Example: entry $200, HWM $280, trailing 20% → stop at $224

### Event blackout calendar

`config/events_2026.json` lists FOMC, CPI, and NFP dates with severity `high` or `medium`. The ORB strategy's news-day filter reads this file. A warning is logged when fewer than 60 days remain in the calendar — update `events_2027.json` before end of year.

---

## 13. Journal & Fill Watcher

All trades are persisted in SQLite (`data/trades.db`).

### Data model

```
Position          Order              Fill
─────────         ─────────          ─────────
id                id                 id
symbol            position_id        order_id
direction         broker             qty
contracts         broker_order_id    price
entry_cost        side               time
expiry            limit_price        commission
state             status
broker            submitted_at
broker_order_id   filled_at
stop_loss_pct     fill_price
take_profit_pct   commission
realized_pnl      kind (entry/exit)
legs              idempotency_key
meta
```

### Fill watcher FSM

Every `MONITOR_INTERVAL_SECONDS` the fill watcher checks all `submitted` or `partial` orders against the broker:

```
submitted
    │
    ├─ broker confirms fill → finalize_filled() → position: open
    ├─ broker shows partial → update qty, wait
    ├─ timeout exceeded    → cancel_order() → finalize_cancelled()
    └─ broker rejects      → finalize_rejected()
```

For moomoo orders, `_resolve_trader_for_order()` automatically routes to the registered `MoomooTrader` instead of the IBKR trader.

### Journal API

| Endpoint | Description |
|---|---|
| `GET /api/journal/positions?state=open` | Open positions |
| `GET /api/journal/positions?state=all` | All positions (all time) |
| `GET /api/journal/daily_pnl?days=30` | Daily realized P&L history |
| `GET /api/journal/events?limit=50` | Recent journal events |
| `GET /api/journal/reconciliation` | EOD commission/slippage report |

---

## 14. Console Control Board

An alternative to the React UI for server/headless operation.

```bash
python console_live.py [--preset orb-5m] [--interval 15]
```

Displays a live Rich dashboard with: system status, active preset, leader/follower mode, SPY price, broker indicator, open positions, and a scrolling event log.

### Commands

| Command | What it does |
|---|---|
| `ls` | List all saved presets |
| `use <name>` | Load and activate a preset |
| `pos` | Show all open positions with P&L |
| `orders` | Show recent orders |
| `flatten` | Panic-close all open positions (notifies Telegram) |
| `scan on / off` | Start or stop the scanner job |
| `broker` | Show current active broker + connection status |
| `broker ibkr` | Switch to IBKR |
| `broker moomoo` | Switch to moomoo (prompts for OpenD credentials) |
| `tg status` | Show Telegram bot status + registered commands |
| `tg test` | Send a test message to Telegram |
| `clear` | Clear the log panel |
| `exit` | Graceful shutdown |

### Broker indicator

The header panel shows either `● IBKR` (green) or `● Moomoo` (orange), reflecting the currently active broker. Switching brokers with `broker moomoo` prompts for host, port, and trade password if not already connected.

---

## 15. Notifications — Telegram Bot

Optional but recommended for live trading. The bot push-notifies on every entry, exit, risk rejection, and panic event. It also accepts slash commands so you can manage the system from your phone.

### Setup

1. Talk to `@BotFather` on Telegram → `/newbot` → copy the token
2. Send any message to your new bot, then talk to `@userinfobot` for your chat ID
3. Set in `config/.env`:
   ```
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_CHAT_ID=<chat_id>
   TELEGRAM_POLL_INTERVAL_SECONDS=3
   ```
4. Restart the server — the bot is active immediately

### Available slash commands (sent to your bot)

| Command | Description |
|---|---|
| `/status` | System status, uptime, active preset |
| `/positions` | All open positions |
| `/pnl` | Today's realized P&L |
| `/flatten` | Panic-close all positions |
| `/help` | List all commands |

---

## 16. API Reference

All endpoints are at `http://127.0.0.1:8000`. Interactive docs at `/docs` (Swagger) or `/redoc`.

### Backtest & data

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/backtest` | Run backtest (up to 120s timeout) |
| `POST` | `/api/optimize` | Run parameter sweep (up to 300s) |
| `GET` | `/api/strategies` | List available strategies |
| `GET` | `/api/strategies/{id}/schema` | Strategy parameter schema |
| `GET` | `/api/spy/intraday` | SPY intraday bar data |
| `GET` | `/api/live_chain` | Live option chain snapshot |

### Scanner & presets

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/scanner/start` | Start ad-hoc scanner |
| `POST` | `/api/scanner/stop` | Stop scanner |
| `GET` | `/api/scanner/status` | Scanner state + recent signals |
| `GET` | `/api/presets` | List all presets |
| `GET` | `/api/presets/{name}` | Get single preset |
| `POST` | `/api/presets` | Create or update preset |
| `DELETE` | `/api/presets/{name}` | Delete preset |
| `POST` | `/api/scanner/preset/start` | Start preset-driven scanner |
| `POST` | `/api/scanner/preset/stop` | Stop preset scanner |
| `GET` | `/api/scanner/preset/status` | Preset scanner state |

### IBKR live trading

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/ibkr/connect` | Connect to TWS |
| `POST` | `/api/ibkr/positions` | Get positions from TWS |
| `POST` | `/api/ibkr/execute` | Place spread order |
| `POST` | `/api/ibkr/exit` | Close position |
| `POST` | `/api/ibkr/flatten_all` | Close all positions (panic) |
| `POST` | `/api/ibkr/cancel` | Cancel order |
| `GET` | `/api/ibkr/orders` | Recent orders |
| `POST` | `/api/ibkr/heartbeat` | Ping TWS, update account data |
| `POST` | `/api/ibkr/reconnect` | Force reconnect |
| `POST` | `/api/ibkr/chain_debug` | Inspect live chain |

### Moomoo live trading

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/moomoo/connect` | Connect to OpenD |
| `POST` | `/api/moomoo/disconnect` | Disconnect |
| `GET` | `/api/moomoo/account` | Account summary |
| `GET` | `/api/moomoo/positions` | Open positions |
| `GET` | `/api/moomoo/chain` | Option chain for date |
| `POST` | `/api/moomoo/execute` | Place spread (legged) |
| `POST` | `/api/moomoo/exit` | Close position |
| `POST` | `/api/moomoo/cancel` | Cancel order |

### Journal

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/journal/positions` | Positions (state=open or all) |
| `GET` | `/api/journal/daily_pnl` | Daily P&L history |
| `GET` | `/api/journal/events` | Recent journal events |
| `GET` | `/api/journal/reconciliation` | EOD commission report |

---

## 17. Configuration Reference

All configuration lives in `config/.env`. Copy from `config/.env.example`.

### IBKR

| Variable | Default | Description |
|---|---|---|
| `IBKR_HOST` | `127.0.0.1` | TWS or Gateway host |
| `IBKR_PORT` | `7497` | 7497 = paper, 7496 = live |
| `IBKR_CLIENT_ID` | `1` | Must be unique per connection |

### Moomoo / Futu (FastAPI platform)

These are also read by the NautilusTrader sub-project.

| Variable | Default | Description |
|---|---|---|
| `FUTU_HOST` | `127.0.0.1` | OpenD host |
| `FUTU_PORT` | `11111` | OpenD port |
| `FUTU_TRD_ENV` | `0` | 0 = simulate, 1 = real |
| `FUTU_UNLOCK_PWD_MD5` | _(empty)_ | `echo -n "pin" \| md5sum` |
| `FUTU_ACC_ID` | `0` | 0 = auto-discover first US margin account |
| `FUTU_LEG_FILL_TIMEOUT_S` | `30` | Seconds to wait for each leg fill |

### Risk

| Variable | Default | Description |
|---|---|---|
| `MAX_CONCURRENT_POSITIONS` | `2` | Hard cap on open positions |
| `DAILY_LOSS_LIMIT_PCT` | `2.0` | Block new trades after this % loss |
| `DEFAULT_STOP_LOSS_PCT` | `50.0` | Default stop (% of entry debit) |
| `DEFAULT_TAKE_PROFIT_PCT` | `50.0` | Default take-profit |
| `DEFAULT_TRAILING_STOP_PCT` | `0.0` | 0 = disabled |
| `FILL_TIMEOUT_SECONDS` | `30` | Cancel pending orders after this |
| `MONITOR_INTERVAL_SECONDS` | `15` | Position monitor tick frequency |
| `LIMIT_PRICE_HAIRCUT` | `0.05` | Haircut on exit limit orders (5%) |

### Telegram

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | _(empty)_ | From @BotFather |
| `TELEGRAM_CHAT_ID` | _(empty)_ | Your personal or group chat ID |
| `TELEGRAM_POLL_INTERVAL_SECONDS` | `3` | How often to poll for commands |

### Paths

| Variable | Default | Description |
|---|---|---|
| `JOURNAL_DB_PATH` | `data/trades.db` | SQLite journal location |
| `LOG_DIR` | `logs` | Structured JSON log directory |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `EVENT_CALENDAR_FILE` | `config/events_2026.json` | News blackout calendar |
