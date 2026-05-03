# Plan: use_request.md + res.jpeg Implementation

## Context

User has supplied two new artifacts in the repo root:
- **`use_request.md`** — backend logic changes across 4 areas: exit logic priority, position sizing dropdown, expose all strategy params (incl. optimizer), and a dedicated scanner module.
- **`res.jpeg`** — hand-drawn wireframes for the **Live** and **Backtest** dashboards, both with a calendar strip on top and specific multi-column layouts.

Today the live exit path in `core/monitor.py:75-148` only checks DTE/TP/SL/TS — it never calls `strategy.check_exit()`, so strategy-driven exits (the user's TOP priority) are silently dropped. Position sizing is split across two boolean toggles instead of a single mode selector. ComboSpread strategy parameters are wired into `BacktestRequest` but not exposed in `BacktestView.jsx`, and the optimizer's `param_x/param_y` selectors are hardcoded. The scanner is entry-only and lives inside `main.py` rather than its own module. The current React views don't match the wireframe layout, and no Calendar strip / Optimiser UI exists yet.

User constraint (verbatim): *"do it step by step dont loose progress before session limit use lighter models for non complex tasks"* — so this plan is phased so each phase is independently shippable and verifiable.

## Execution Phases

Each phase ends with a verification step. Stop after each phase, confirm green, then proceed.

### Phase 1 — Backend: Exit Priority (use_request §1)

**Files:** `core/monitor.py`, `core/calendar.py` (read-only ref), tests in `tests/test_arch_fixes.py`

- In `core/monitor.py:evaluate_exit()`, reorder so the priority is:
  1. **Strategy exit** — instantiate the position's strategy from `position.strategy_name`, build a minimal `df` slice from recent bars, call `strategy.check_exit(df, i, entry_state, req)` first. If `(should_exit, reason)` returns True → return `ExitDecision(reason="strategy:" + reason)`.
  2. **Before-close-on-expiration-day** — if `position.expiration == today` and `now >= close_time - exit_buffer_minutes`, return `ExitDecision(reason="expiration_close")`. Use `core/calendar.py` for market close time.
  3. **TP / SL / TS** (existing logic, unchanged order).
- Keep the existing `force_exit` short-circuit at top (kill switch).
- Add unit tests in `tests/test_monitor_exit_priority.py` covering all three branches and the priority order.

**Verify:** `pytest tests/test_monitor_exit_priority.py -v` and `pytest tests/test_arch_fixes.py -v` both green.

### Phase 2 — Backend: Position Sizing Dropdown (use_request §2)

**Files:** `core/risk.py`, `core/settings.py`, `main.py` (BacktestRequest model)

- Add `position_size_method: Literal["fixed", "dynamic_risk", "targeted_spread"]` to `Settings` and `BacktestRequest`. Keep `contracts_per_trade`, `dynamic_risk_pct`, `targeted_spread_pct`, `targeted_spread_cap` as siblings (only the relevant one is read per mode).
- `core/risk.py:size_position()` switches on `position_size_method`:
  - `fixed` → `contracts_per_trade`
  - `dynamic_risk` → existing Kelly-style sizing
  - `targeted_spread` → existing path; **if computed contracts × spread cost > cap, fall back to `contracts_per_trade`** (this is the "res n-contracts" fallback the user described).
- Migrate the two boolean fields (`use_dynamic_sizing`, `use_targeted_spread`) — derive `position_size_method` from them in a one-time settings load shim, then drop them.

**Verify:** unit test `tests/test_position_sizing.py` covers all 3 modes + the cap-fallback. Run existing backtest curl with each mode; assert `result.trades[0].contracts` differs.

### Phase 3 — Backend: Preset Persistence (prereq for §4)

**Files:** new `core/presets.py`, `main.py` (new endpoints)

- Schema: `ScannerPreset { name, ticker, strategy_name, strategy_params, entry_filters, position_size_method, scan_interval_seconds, ... }`.
- Storage: JSON file at `config/presets.json` (atomic write via tempfile+rename).
- Endpoints: `GET /api/presets`, `GET /api/presets/{name}`, `POST /api/presets`, `DELETE /api/presets/{name}`.

**Verify:** curl create → list → get → delete cycle.

### Phase 4 — Backend: Dedicated Scanner Module (use_request §4)

**Files:** new `core/scanner.py`, `main.py` (replace inline scanner job)

- Move the APScheduler job out of `main.py` into `core/scanner.py:Scanner` class.
- `Scanner.tick()` requires an active preset (`raise PresetRequired` if none) — UI must select before scan starts.
- For each symbol in preset: fetch bars, run `strategy.check_entry()` AND `strategy.check_exit()` for any open positions; emit two queues:
  - `entry_signals` → consumed by Live/Paper auto-execute
  - `exit_signals` → consumed by `core/monitor.py` (poll queue inside evaluate loop)
- Each signal carries a **trade ticket** dict: `{symbol, side, contracts, max_risk, preset_name, signal_type}` for the ticketing UI.
- New endpoints: `POST /api/scanner/start`, `POST /api/scanner/stop`, `GET /api/scanner/status` (returns active preset + last N signals).

**Verify:** start scanner with preset → curl `/api/scanner/status` shows preset + signals; stop → status shows idle.

### Phase 5 — Frontend: Strategy Params + Optimiser (use_request §3)

**Files:** `frontend/src/views/BacktestView.jsx`, new `frontend/src/components/StrategyParamsForm.jsx`, new `frontend/src/components/OptimiserCard.jsx`

- Fetch `GET /api/strategies/{name}/schema` (already exists) and render every field generically — covers `consecutive_days` AND all 7 ComboSpread params automatically.
- Optimiser card: dropdowns for `param_x` / `param_y` populated from the same schema (not hardcoded). Range inputs, "Run Sweep" button → existing `/api/optimize` endpoint. Render heatmap (use existing chart lib).

**Verify:** preview, switch strategy → form re-renders; run optimizer with combo params → heatmap renders.

### Phase 6 — Frontend: Calendar Strip Component

**Files:** new `frontend/src/components/CalendarStrip.jsx` + CSS

- Months row (FEB-DEC) + days row (1-31) per wireframe. Highlights today; click month/day filters the dashboard date range (callback prop).
- Used at top of both LiveView and BacktestView.

**Verify:** preview both views, click cells, assert callback fires.

### Phase 7 — Frontend: LiveView Layout (res.jpeg LIVE)

**Files:** `frontend/src/views/LiveView.jsx`, lift `frontend/src/views/ScannerView.jsx` signals table into a shared component.

Layout per wireframe:
1. CalendarStrip
2. Row: `[Account Info] [IBKR Connection]`
3. Row: `[Monitor] [Kill Switch (button card)] [Alerts]` (3 cols)
4. Row: `[Scanner Load Presets] [Scanning Shows]`
5. Row: `[Signals Log] [Positions]`
6. Full-width: `Order Ticket` (already partially built — chain preview + submit)

**Verify:** preview LiveView, screenshot vs wireframe, all panels render.

### Phase 8 — Frontend: BacktestView Layout (res.jpeg BACK-TESTER)

**Files:** `frontend/src/views/BacktestView.jsx`, lift trades list from `JournalView.jsx`.

Layout per wireframe:
1. CalendarStrip
2. Header (current title row)
3. Row: `[Strategy & Capital] [Historic Chart] [Equity Drawdown]`
4. Row: `[Stats] [chart]`
5. Row: `[Filters] [Trades List]`
6. Row: `[Analytics] [Optimiser]` (Optimiser from Phase 5)

**Verify:** preview, run a backtest, all panels populate; trades list shows journal rows.

## Critical Files to Modify

- `core/monitor.py` (Phase 1)
- `core/risk.py`, `core/settings.py` (Phase 2)
- `core/presets.py` *new*, `main.py` (Phase 3)
- `core/scanner.py` *new*, `main.py` (Phase 4)
- `frontend/src/views/BacktestView.jsx` (Phases 5, 8)
- `frontend/src/views/LiveView.jsx` (Phase 7)
- `frontend/src/components/CalendarStrip.jsx` *new* (Phase 6)
- `frontend/src/components/StrategyParamsForm.jsx` *new* (Phase 5)
- `frontend/src/components/OptimiserCard.jsx` *new* (Phase 5)

## Reused Existing Code

- `strategies/base.py:Strategy.check_exit` — exists, just needs to be called from monitor.
- `core/risk.py:size_position` — already has all 3 sizing branches; needs only the dispatch + cap fallback.
- `main.py /api/strategies/{name}/schema` — already exposes all params; drives Phases 5 generically.
- `main.py /api/optimize` — backend exists; only UI is missing.
- `core/calendar.py` — market close time for Phase 1's expiration-day gate.
- `frontend/src/views/ScannerView.jsx:99-119` signals table — lift into shared component for Phase 7.

## Verification (End-to-End)

After all phases:
1. `pytest -q` — full suite green (target ≥80% on changed modules).
2. Start backend + frontend, open preview.
3. Save a preset → start scanner → see entry signal → auto-execute in Paper mode.
4. Open position → trigger strategy exit condition → confirm exit reason starts with `strategy:`.
5. Switch position sizing dropdown across 3 modes, run backtests, verify `contracts` differs.
6. Run optimizer with ComboSpread params, see heatmap.
7. Visual: both dashboards match wireframe layout (screenshot diff vs `res.jpeg`).
