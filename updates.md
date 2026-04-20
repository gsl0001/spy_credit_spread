# Project Updates - Sunday, April 19, 2026

## 🚀 Key Improvements & New Features

### 1. Unified Preset & Sync System
- **Server-Side Persistence**: Backtest presets are now automatically saved to the backend database. Optimized strategies are immediately available in the Live view.
- **Full Schema Parity**: Presets now capture all 30+ trading parameters, including topology, strike width, target DTE, commission, and risk caps.

### 2. Live Scanner Enhancement
- **Advanced Scheduling**: Added support for high-frequency intervals (15s+) and market event triggers (Market Open, Close, N-mins after Open, etc.).
- **Background Execution**: The scanner now runs as a robust background process on the server, even if the UI is closed.
- **Pure Live Mode**: Added a toggle to fetch historical bars and real-time prices exclusively from IBKR, bypassing Yahoo Finance for maximum accuracy.

### 3. Extended Hours & 24/7 Data
- **Always-On Monitoring**: The system now pulls data and evaluates exits (Stop Loss/TP) 24/7, ignoring standard RTH restrictions to support overnight trading.
- **Extended Sparkline**: Topbar now shows a 2-day historical sparkline with real-time "Gap" detection relative to previous close.
- **Dual-Source Quotes**: Prioritizes live IBKR ticks with an automatic fallback to Yahoo Finance's real-time feed if subscriptions are missing.

### 4. UI/UX Overhaul
- **Dual-State Controls**: Implemented smart buttons (Connect → Disconnect, Start → Stop) with clear visual feedback.
- **Preset Info Modal**: Added a read-only ⓘ info button to view active strategy parameters without leaving the live trading view.
- **Symmetrical Layout**: Standardized all grids and cards for a professional "trading terminal" aesthetic.
- **State Persistence**: UI now remembers connection state and account data on page reload.

### 5. Connectivity & Reliability
- **Auto-Connect**: Backend now automatically establishes IBKR connection on startup.
- **Leader Election**: Automated processes now explicitly claim "LEADER" status to prevent duplicate order execution.
- **Dependency Stability**: Upgraded FastAPI/Starlette stack to resolve recent HTTPX compatibility issues.

### 6. Testing & Dry Run
- **Dry Run Strategy**: Added a deterministic, time-based test strategy that trades 3 times daily (at Open, Midday, and Close) to verify end-to-end execution.
- **Cross-Platform Fixes**: Stabilized leader-election tests for Windows environments.
