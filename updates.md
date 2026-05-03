# Project Updates - Saturday, May 3, 2026

## 🚀 Moomoo 0DTE Order Flow Engine (No Nautilus)

### 1. Core Order Flow Pipeline
- **core/order_flow.py**: Implemented a standalone, high-performance tick processor that avoids the heavyweight Nautilus Trader dependency.
- **Tick Classifier**: Classifies raw ticks into Bid/Ask aggressors using the tick test.
- **Bar Builder**: Aggregates ticks into 1-minute `OrderFlowBar` objects enriched with Net Delta, Volume, and Absorption Factor ($\alpha = |\Delta| / \text{Price Range}$).

### 2. Strategy Logic & Signal Detection
- **strategies/order_flow_0dte.py**: Built the `ZeroDTEOrderFlowBot` which subscribes to Moomoo ticks via `MoomooTrader`.
- **Signals**: Implemented detectors for:
  - **Absorption Bull**: Price at 5-min low + negative delta spike + tight range + price recovery.
  - **Absorption Bear**: Price at 5-min high + positive delta spike + tight range + price rejection.
  - **Delta Divergence**: Price makes a lower low but 1-min cumulative delta is rising (divergence).
- **Execution & Option Selection**: Dynamically rounds the SPY spot price to the nearest integer and calculates the ATM 0DTE option code (e.g., `US.SPY260502C510000`).

### 3. Integrated Safety & OCO
- **Risk Management**: Plumbs into the existing `MoomooTrader` execution path.
- **Built-in OCO**: Submits Market entry, then immediately monitors live snapshots for `-50%` stop-loss and `+100%` profit target triggers.
- **API Endpoints**: Exposed `/api/moomoo/0dte/start`, `stop`, and `status` in `main.py` for UI control.
- **Tests**: Verified math and state transitions via 22 passing tests in `test_order_flow.py`.

## 🚀 Moomoo Live Trading — Phase 1 Safety Features

### 1. Auto-Reconnect with Exponential Backoff
- **moomoo_trading.py**: Added `_reconnect_loop()` background coroutine with exponential backoff (2s → 4s → 8s → … → 60s max).
- **Telegram Alerts**: Sends ⚠️ on disconnect, ✅ on reconnect success, 🔴 every 5 failed attempts.
- **Health Integration**: `main.py` health endpoint now triggers `schedule_reconnect()` when `is_alive() == False`, surfacing `moomoo_reconnecting` and `moomoo_reconnect_attempt` in the heartbeat response.
- **Intentional Disconnect Guard**: `disconnect()` sets `_intentional_disconnect = True` to prevent the loop from fighting a deliberate user action.

### 2. Daily Risk Limits — Absolute $ Cap
- **core/settings.py**: Added `DAILY_LOSS_LIMIT_ABS` (default 0 = disabled) and `MAX_ORDERS_PER_DAY` (default 0 = unlimited) as env-backed fields in `RiskSettings`.
- **core/risk.py**: `RiskLimits.from_settings()` now wires `daily_loss_limit_abs` from settings so the pre-trade gate actually checks both the percentage and absolute dollar loss limits.

### 3. Backtest UI Overhaul
- **chart.jsx**: Migrated from v4 API (`addCandlestickSeries`, `CrosshairMode`) to **v5 API** (`chart.addSeries(CandlestickSeries, ...)`, `createSeriesMarkers()`). This was the root cause of the blank chart.
- **BacktestView.jsx**: Complete layout rewrite — independent-scroll sidebar (320px), chart + Execution Stats side-by-side, KPI strip + Trade Log + Equity/MC/WF in right panel.

### Safety Audit
- **Idempotency**: No order paths changed — auto-reconnect only retries `connect()`, never replays orders.
- **Resilience**: Reconnect loop has exponential backoff capped at 60s, `_intentional_disconnect` guard, and `_cancel_reconnect()` cleanup.
- **Testing**: 437 passed, 0 failed, 7 skipped.

### 🏁 Hard Gate Verification (May 3, 2026)
- **Backend**: `pytest` passed (437 passed).
- **Frontend**: `npm run lint` audit performed. Applied 20+ fixes for unused variables, empty blocks, and hook dependencies.
- **Knowledge Graph**: Run `/graphify --update` to sync 0DTE Order Flow classes.
- **Safety**: Verified OCO and risk limits in `core/risk.py` and `strategies/order_flow_0dte.py`.

---

# Project Updates - Tuesday, April 21, 2026

## 🚀 Key Improvements & Fix-Forward Actions

### 1. Connection Resilience & Market Data Fallback (TIER 2 - I7)
- **Exponential Backoff**: Refined `IBKRTrader.ensure_connected()` with a formal exponential backoff (5s, 10s, 20s, up to 60s) and timeout protection.
- **Data Subscription Fallback**: The system now automatically requests delayed market data (Type 3) upon connection. This resolves the `Error 10089` (missing subscriptions) identified during the April 20th paper dry-run, ensuring the scanner and monitor can still operate with delayed quotes if live ones are unavailable.
- **Enhanced Logging**: Added explicit warnings and errors for connection drops and failures to support TIER 2 health monitoring.

### 2. Precise Commission & P&L Accounting (TIER 2 - I4)
- **Net P&L Reconciliation**: Updated `core/fill_watcher.py` to calculate `realized_pnl` as a true Net figure. It now sums commissions from both entry and exit orders associated with a position before closing it.
- **Journal Integrity**: Finalized exit events now carry both the current order commission and the total position commission for better auditability.

### 3. Smart Market Hours & Holiday Support (TIER 2 - I8)
- **Early-Close Awareness**: Refined `core/calendar.py::minutes_to_close()` to utilize `pandas_market_calendars` schedules. The system now correctly identifies early market closes (e.g., 1:00 PM ET) instead of assuming a hard 4:00 PM ET close.
- **Robust Fallbacks**: Maintained simple 9:30-16:00 ET fallbacks for environments where market calendar libraries or timezone data are missing.

### 4. Console Control Board (Interactive)
- **Rich Dashboard**: Rebuilt `console_live.py` using `rich.layout` and `Live` for a multi-panel terminal UI (Header, Health, Positions, Logs).
- **Interactive Command Loop**: Added a threaded CLI listener supporting `ls`, `use`, `pos`, `orders`, `flatten`, and `scan` toggles.
- **Dynamic Reconfiguration**: The system now supports switching scanner presets and toggling automated scanning without restarting the process.
- **Deployment Documentation**: Added `docs/server_deployment.md` with full command reference.

### 5. Verification & Baseline
- **Test Stability**: Verified 309 passing tests after all logic shifts.
- **Dry-Run Fixes**: Addressed major blockers from yesterday's dry-run logs, preparing the system for a cleaner 5-day paper burn-in.

# Project Updates - Sunday, April 19, 2026

## 🚀 Key Improvements & New Features

### 1. UI Robustness & Performance
- **Zero-Crash Rendering**: Added defensive null-handling and `Number()` casting across all chart components. The UI no longer crashes when simulation data contains `NaN` or `Inf`.
- **Chart Performance**: Optimized trade plotting and scaling loops. Replaced `Math.max(...spread)` with manual loops to prevent stack overflow on large datasets (1yr+ history).
- **Responsive Scales**: Candlestick charts now include a 10% vertical padding and handled invalid price points gracefully.

### 2. Idempotency & Safety (TIER 2 - I5)
- **Client-Side UUIDs**: The Live trading view now generates a unique `client_order_id` for every submission, preventing accidental double-fills on button double-clicks or network retries.
- **Backend Suppression**: The `ibkr_execute` endpoint now verifies idempotency keys against the SQLite journal before routing orders to TWS.
- **Scanner Idempotency**: Automated scanner signals now use date-scoped keys (`scan:YYYY-MM-DD:SYMBOL:PRESET`) to ensure a signal only executes once per day.

### 3. Connection Resilience (TIER 2 - I7)
- **Exponential Backoff**: Reconnection logic for Interactive Brokers now uses a backoff strategy (5s, 10s, 20s, up to 60s) to prevent "hammering" TWS during local network instability.
- **Health Awareness**: The system now provides clear "Backoff: waiting Ns" status messages during reconnection phases.

### 4. Advanced Market Awareness (TIER 2 - I8)
- **Full Holiday Support**: Integrated `pandas_market_calendars` for accurate NYSE holiday detection.
- **Pre-Market Awareness**: The market-hours gate now distinguishes between `closed`, `pre_market`, `open`, and `outside_rth`, providing better visibility into trading windows.

### 5. Unified Preset & Sync System
- **Server-Side Persistence**: Backtest presets are now automatically saved to the backend database. Optimized strategies are immediately available in the Live view.
- **Full Schema Parity**: Presets now capture all 30+ trading parameters, including topology, strike width, target DTE, commission, and risk caps.

### 6. Live Scanner Enhancement
- **Background Execution**: The scanner now runs as a robust background process on the server, even if the UI is closed.
- **Pure Live Mode**: Added a toggle to fetch historical bars and real-time prices exclusively from IBKR, bypassing Yahoo Finance for maximum accuracy.

### 7. Dashboard Features
- **Manual Exit**: Added a one-click "Close" button to tracked positions in the Live view, allowing for quick manual intervention outside of automated rules.
- **Live Position Tracking**: The Live view now reconciles IBKR portfolio data with the internal SQLite journal to show "Tracked" vs "Orphaned" positions.
- **Safe Serialization**: Implemented `_safe_json` across all backtest endpoints to handle IEEE-754 special floats safely.

### 8. Testing & Validation
- **Engine Stability**: Validated backtest engine against 40+ unit tests, ensuring parity between synthetic Black-Scholes pricing and realized P&L.
- **Idempotency Tests**: Added test cases for duplicate suppression in the fill watcher and execution paths.

## 📜 Senior Trading Engineer Protocol (April 19, 2026)
- **High-Autonomy Mandate**: Formally integrated "Hard Gate" verification and "Trading Safety Audits" into `GEMINI.md`.
- **Automated Verification**: Gemini is now mandated to provide empirical test/lint evidence for all task completions.
- **Safety Hardening**: Established mandatory idempotency audits for all order execution logic.
- **Baseline Results**: Verified 309 passing tests and identified 25 frontend lint issues.
- **Knowledge Graph Sync**: Updated `graphify` with 1244 nodes and 3550 edges across 75 communities.
