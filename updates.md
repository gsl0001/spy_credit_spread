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
