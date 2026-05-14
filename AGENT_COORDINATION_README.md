# Agent Coordination README

Last updated: 2026-05-10

## Scope Of This Update

This handoff covers the moomoo journal/execution fixes made after the project analysis found that the moomoo happy path was broken against the current `core.journal` API.

## Files Changed In This Pass

- `frontend/src/views/BacktestView.jsx`
  - Reorganized the backtester into a two-pane operational workspace: sticky action/config rail on the left, results on the right.
  - Redesigned the config rail into a cleaner setup panel with a summary header, primary actions, compact preset management, and clearer section labels.
  - Fixed cropped config summary display by using friendlier structure labels instead of raw underscore identifiers.
  - Moved preset controls into the always-visible setup header so the preset selector/save controls no longer collapse into a cropped strip.
  - Replaced the cramped accordion stack with a tabbed config workbench so only one editor group renders at a time; this fixes the cropped dropdown/section strips seen in the 1440x696 browser viewport.
  - Split the editor into Market, Strategy, Sizing, Structure, Risk, Filters, Analysis, and Optimizer groups.
  - Added responsive KPI, chart/stat, and lower analytics grids through shared CSS classes.
  - Added an empty chart state and a loading overlay so the screen does not look broken before or during a run.
  - Switched the execution stats panel to backend-provided recovery/hold/loss metrics where available.

- `frontend/src/views/MoomooView.jsx`
  - Reorganized the moomoo console into KPI, status/alerts, connection/scanner/signals/positions, and action/log stacks.
  - Removed stale hard-coded FOMC date copy from the scanner panel.
  - Replaced undefined CSS tokens (`--bg-card`, `--text-1`) with the existing design tokens.
  - Reduced custom card radii to match the app's tighter operational UI style.

- `frontend/src/index.css`
  - Added shared workspace, responsive grid, collapsible section, empty-state, and moomoo dashboard classes.
  - Added dedicated backtester config-panel styles for setup summary chips, preset cards, and primary actions.
  - Fixed Backtester workspace cropping by making the workspace fill the shell main area (`height: 100%`) with scrollable `min-height: 0` grid children.
  - Widened the Backtester config rail and allowed summary chip values to wrap instead of clipping.
  - Added inline preset control styling for the Backtester setup header.
  - Added tabbed config-workbench styles for the redesigned Backtester setup panel.
  - Added single-column strategy parameter styling so intraday/ORB controls no longer clip inside the narrow setup editor.
  - Added mobile/tablet breakpoints for the backtester and moomoo console.

- Frontend lint cleanup:
  - `frontend/src/App.jsx`
  - `frontend/src/api.js`
  - `frontend/src/backtestConfig.js`
  - `frontend/src/chart.jsx`
  - `frontend/src/statusbar.jsx`
  - `frontend/src/strategyParamsForm.jsx`
  - `frontend/src/primitives.jsx`
  - `frontend/src/useBackendData.jsx`

- `frontend/src/strategyParamsForm.jsx`
  - Changed schema-rendered strategy params to a single-column grid for the Backtester sidebar.
  - Renders boolean schema fields as checkboxes instead of text inputs, which makes ORB's `skip_news_days` usable.

- `frontend/src/App.jsx`
  - Confirmed the header `FLATTEN ALL` button opens the panic modal.
  - Changed the confirm action from IBKR-only flattening to a coordinated IBKR + Moomoo flatten-all submission, with per-broker success/error messaging.

- `frontend/src/useBackendData.jsx`
  - Wired heartbeat market-hours fields into the shared risk state so the topbar/statusbar do not stay stuck on the mock `MKT CLOSED · —` fallback.

- `main.py`
  - Removed stale `PositionState` imports from the moomoo execute path.
  - Changed successful moomoo execution journaling to construct a `core.journal.Position` object before calling `journal.open_position()`.
  - Records the filled long and short moomoo entry legs as `core.journal.Order` rows.
  - Uses the request/client id as the position id for the successful moomoo spread, keeping the broker request and journal position aligned.
  - Preserves stop/take-profit/trailing-stop values in `Position.meta`, because the current `Position` dataclass has no top-level fields for those values.
  - Logs an `entry_submitted` event with broker order ids and journal order ids.
  - Fixed the broken orphan-leg journaling branch to create a `Position` object instead of passing unsupported keyword arguments to `open_position()`.
  - Added market-hours status fields to `/api/ibkr/heartbeat`: `market_open`, `market_reason`, `minutes_to_close`, and `next_close`.
  - Integrated ORB into the UI-facing `/api/backtest` endpoint by routing `strategy_id="orb"` through `core.backtest_orb.run_orb_backtest`.
  - Updated `fetch_historical_data()` to honor strategy-declared intraday bar specs, so ORB receives 5-minute yfinance bars instead of daily bars.
  - Added flat ORB request fields (`or_minutes`, `offset`, `width`, `min_range_pct`, `time_exit_hhmm`, `allowed_days`, `skip_news_days`) while preserving nested `strategy_params` support.
  - Added ORB endpoint response adaptation so Backtester KPIs, trade log, equity curve, and price chart receive the same shape as daily strategies.

- `tests/test_backtest_orb_endpoint.py`
  - Added a regression test proving `/api/backtest` with `strategy_id="orb"` no longer fails with `"'SMA_200'"` and returns a normal intraday ORB trade result.

- `core/moomoo_reconciler.py`
  - Fixed orphan recording to use the current `Position` dataclass API.
  - Stores reconciler orphan risk settings and idempotency metadata under `Position.meta`.
  - Avoided function-local `datetime` imports that shadow the module-level import used earlier in `reconcile_once()`.

- `core/backtest_orb.py`
  - Includes `spread_value_at_exit` in serialized ORB trades so the Backtester trade adapter can display non-zero exit spread values.

## Verification Run

These commands passed:

```bash
npm run lint
npm run build
python3 -m py_compile main.py core/backtest_orb.py
git diff --check -- frontend/src/views/BacktestView.jsx frontend/src/index.css frontend/src/App.jsx frontend/src/useBackendData.jsx main.py AGENT_COORDINATION_README.md
pytest -q tests/test_orb_strategy.py tests/test_backtest_orb.py tests/test_backtest_orb_endpoint.py
curl -sS -X POST http://127.0.0.1:8000/api/backtest -H 'Content-Type: application/json' -d '{"ticker":"SPY","strategy_id":"orb","strategy_type":"bull_call","direction":"bull","target_dte":0,"years_history":1,"capital_allocation":10000,"contracts_per_trade":1,"stop_loss_pct":50,"take_profit_pct":50,"commission_per_contract":0.65,"use_rsi_filter":false,"use_ema_filter":false,"use_vix_filter":false,"enable_mc_histogram":false,"enable_walk_forward":false,"or_minutes":5,"offset":1.5,"width":5,"min_range_pct":0.05,"time_exit_hhmm":"15:30","skip_news_days":false}'
curl -sS -X POST http://127.0.0.1:8000/api/ibkr/heartbeat -H 'Content-Type: application/json' -d '{"host":"127.0.0.1","port":7497,"client_id":1}'
curl -sS 'http://127.0.0.1:8000/api/spy/intraday?host=127.0.0.1&port=7497&client_id=1'
pytest -q tests/test_moomoo_execute.py
pytest -q tests/test_reconcile.py tests/test_reconciliation.py tests/test_fill_watcher.py
```

Observed results:

- `npm run lint`: passed
- `npm run build`: passed, with Vite chunk-size warning for the main JS bundle
- `python3 -m py_compile main.py core/backtest_orb.py`: passed
- `git diff --check`: passed for the files changed in this pass
- `pytest -q tests/test_orb_strategy.py tests/test_backtest_orb.py tests/test_backtest_orb_endpoint.py`: 42 passed, with the existing `eventkit` deprecation warning
- `/api/backtest` ORB probe: returned no error, 35 trades, 4680 intraday price points, and non-zero `spread_exit` values
- `/api/ibkr/heartbeat`: returned market-hours fields (`market_open=false`, `market_reason=weekend`, `next_close=weekend`) while IBKR was not connected.
- `/api/spy/intraday`: returned live-looking yfinance-backed SPY data (`current=737.74`, `change=6.31`, `change_pct=0.86`, `data_len=1890`) after the backend was started locally.
- `tests/test_moomoo_execute.py`: 20 passed
- `tests/test_reconcile.py tests/test_reconciliation.py tests/test_fill_watcher.py`: 43 passed

Both runs emitted only the existing `eventkit` deprecation warning about `asyncio.get_event_loop_policy()`.

Full-suite check after this pass:

```bash
pytest -q
```

Observed result:

- 434 passed
- 2 skipped
- 3 failed
- 6 errors
- 6 warnings

## Known Issues Not Fixed Here

- Full `pytest -q` still fails because:
  - `tests/test_ibkr_adapter.py` is affected by persisted local state in `data/connection_flags.json` where `ibkr` is currently `false`.
  - `tests/test_monitor.py::test_tick_survives_trader_factory_failure` expects `[]`, while the current monitor returns a structured skipped result.
  - `tests/test_notifier.py` binds local HTTP servers and fails under the current sandbox with `PermissionError: [Errno 1] Operation not permitted`.
- `npm run build` passes, but Vite warns that the main JS chunk is larger than 500 kB. Code splitting is still a good follow-up.
- Broker-mutating API routes still do not enforce an API key. This is a live-trading safety item and should be handled in a separate, coordinated pass.
- `/api/moomoo/close_one` still accepts a raw dict and submits broker-direct market close orders. It needs auth, Pydantic validation, symbol allowlisting, and quantity caps.
- Dynamic sizing still forces at least 1 contract in some underfunded paths in `core/risk.py`.

## Coordination Notes For Other Agents

- The worktree had many uncommitted changes before this pass. Treat all unrelated modified files as user-owned.
- Do not revert `data/connection_flags.json` casually; it is local runtime state and currently affects IBKR tests.
- Prefer adding tests around any broker-mutating endpoint before changing live-trading behavior.
- Keep moomoo journal writes compatible with `core.journal.Position` and `core.journal.Order`; `Journal.open_position()` does not accept keyword arguments.
- If adding top-level risk fields to `Position`, migrate existing rows and update `_row_to_position()` before moving values out of `meta`.

## Suggested Next Agent Tasks

1. Add API-key enforcement to broker-mutating routes.
2. Replace `/api/moomoo/close_one` raw dict handling with a validated request model.
3. Decide whether `monitor.tick()` should return `[]` or structured skipped records when no trader is available, then align tests and implementation.
4. Make IBKR tests independent from `data/connection_flags.json`.
5. Consider code splitting for the Vite bundle warning.

---

# Pass 2: Moomoo Hardening Sweep (2026-05-09)

This pass took every CRITICAL/HIGH item from the multi-agent moomoo review and worked through them, plus the two SDK-feature gaps the gap analysis surfaced.

## Files Changed In This Pass

- `core/journal.py`
  - Added nullable `claim_key` column to `events` table with a forward migration (`_migrate()`) and a `_POST_MIGRATE_INDEXES` block. Existing DBs upgrade automatically.
  - Added `UNIQUE INDEX idx_events_claim_key` (partial, `WHERE claim_key IS NOT NULL`).
  - Added `idx_events_kind` for general kind-based queries.
  - Added `UNIQUE INDEX idx_fills_exec_id` (partial, `WHERE exec_id IS NOT NULL`).
  - `record_fill()` now uses `INSERT OR IGNORE` so duplicate fill webhooks (broker retries) don't double-count P&L.
  - `log_event()` accepts an optional `claim_key=` kwarg.
  - Added `has_event_claim(claim_key, kind=None)` for indexed dedup lookups.

- `core/connection_flags.py`
  - Persists `{ibkr, moomoo}` toggles to `data/connection_flags.json` so a uvicorn `--reload` doesn't silently re-enable a broker the user disabled.
  - Survives parse errors by falling back to defaults; corrupt JSON warns and continues.

- `core/yf_safe.py` (new)
  - `safe_download()` / `safe_fast_info()` wrap yfinance with cache-corruption recovery (catches `OperationalError('unable to open database file')`, deletes WAL/SHM remnants, retries; hard-resets cache on second failure).
  - `reset_cache()` / `_hard_reset_cache()` helpers.
  - `raise_fd_limit(target=8192)` — bumps `RLIMIT_NOFILE` at startup so long sessions don't hit "Too many open files" from leaked sockets.

- `core/monitor.py`
  - `tick()` no longer bails when `trader_factory()` returns None — moomoo positions now tick through `_process_position_moomoo` regardless of IBKR state. Only IBKR-broker positions get skipped (with a `monitor_skip_ibkr` event).
  - `_process_position()` returns `{skipped: True, reason: 'ibkr_trader_unavailable'}` for IBKR-broker positions when trader is None instead of crashing.

- `core/moomoo_reconciler.py`
  - Auto-records broker orphans as `single_leg_orphan` `Position` rows so the MTM monitor manages them. Uses a deterministic `claim_key` (`reconcile_orphan:SPY:732.0:C:20260508:long:30`) + the new `has_event_claim()` so re-runs don't double-record.
  - Telegram alert now includes the count of auto-recorded orphans.
  - Return shape adds `orphans_recorded: list[str]`.
  - (Note: a separate prior pass already migrated the orphan recording to use the current `Position` dataclass API; this pass adds the auto-record + dedup on top.)

- `core/risk.py`
  - `dynamic` sizing mode now honors `max_allocation_cap` (was only honoring the legacy `max_trade_cap`). Without this, $1M × 0.5% with a `$1k` cap was returning ~29 contracts.

- `core/presets.py`
  - Added `bypass_event_blackout: bool = False` field to `ScannerPreset` dataclass + `from_dict()` mapping. Earlier the field was being silently dropped on JSON load.

- `brokers/moomoo_trading.py` (largest changes)
  - **Auto-reconnect gating:** `schedule_reconnect`, `ensure_connected`, and the `_reconnect_loop` body all bail when `core.connection_flags.is_auto_enabled('moomoo')` is False.
  - **Asyncio modernization:** all 21 `asyncio.get_event_loop()` call sites migrated to `asyncio.get_running_loop()`. The single sync-context caller (`schedule_reconnect`) uses a new `_get_loop_safe()` helper that walks `running → policy → new`.
  - **Partial fill handling (H1):** new `_wait_for_fill_detail()` returns `{outcome, status, filled_qty, total_qty}`. Outcomes: `filled | partial | cancelled | failed | timeout`. The old `_wait_for_fill()` is retained as a bool wrapper.
  - **Cancel return code (H2):** `_cancel_order_sync()` now unpacks `(ret, data)` from `modify_order` and returns `{ok, order_id, reason?}`. A failed cancel no longer disappears silently.
  - **Atomic-ish `place_spread` (C3):**
    - New `_preflight_spread_quotes()` snapshots both legs and rejects on zero/crossed/>50%-wide quotes before placing leg1.
    - New `_marketable_limit_close()` uses `bid - $0.05` instead of raw market for partial-leg flatten.
    - Returns new `status: "broken_spread"` with `leg1_filled_qty`, `leg2_filled_qty`, `leg2_outcome`, `flatten_order_id`, `cancel_ok` so the caller can journal an accurate orphan.
    - leg1 partial fills now flatten the partial via marketable-limit.
  - **Session=ALL on close paths:** new `_close_session_kwargs(ft)` helper picks the most permissive `Session` enum value available at runtime (`ALL → ETH → OVERNIGHT → RTH`) and passes it to `place_order` from `_marketable_limit_close` and `_place_market_close`. Entry orders unchanged (still RTH-default).

- `strategies/order_flow_0dte.py`
  - **`_active_position` race fix (C6):** the pending-then-mutate-then-flip pattern is replaced with a single atomic `self._active_position = ActivePosition(..., status="filled", entry_price=..., stop_price=..., target_price=...)` swap so `_monitor_position` never observes a half-init record.
  - All `asyncio.get_event_loop()` calls migrated to `get_running_loop()`.
  - Cancel-on-timeout site now logs an explicit error if the cancel itself failed.

- `main.py`
  - **Idempotency claim-on-entry (rewrite):** the claim-dedup query in `_moomoo_execute_impl` was a `LIKE '%client_order_id%'` full-table scan in `events.payload_json` (added in a prior session). Replaced with `journal.has_event_claim(client_id, kind='moomoo_execute_claim')` against the new indexed column. Catches `sqlite3.IntegrityError` from the UNIQUE constraint to handle parallel submitters.
  - **Server-side `bypass_event_blackout` gate:** added `field_validator` on `MoomooOrderRequest.bypass_event_blackout` that zeroes the field unless `ALLOW_BYPASS_EVENT_BLACKOUT=1` is in the server's env. Same gate applied to the preset auto-execute path.
  - **Boot reconciler:** lifespan startup schedules a 10-second-delayed `reconcile_once()` so drift from prior sessions is caught on every boot.
  - **Post-reconnect reconciler:** `/api/moomoo/connect` fires a 2-second-delayed `reconcile_once()` after a successful connect.
  - **Trader-leak fix (H6):** `/api/moomoo/connect` calls `_moomoo_trader.disconnect()` on the prior instance before swapping in the new one.
  - **Connection-flag toggles:** new `GET /api/connection/auto` and `POST /api/connection/auto {broker, enabled}` endpoints. Toggle OFF disconnects the live trader; toggle ON kicks an immediate IBKR connect.
  - **Broker-direct flatten:** new `POST /api/moomoo/flatten_broker` (iterates `position_list_query` and submits MARKET closes — used when journal is empty but broker has positions). Hits moomoo's 15-orders/30-seconds rate limit; caller should retry with throttling.
  - **Single-leg close:** new `POST /api/moomoo/close_one {code, qty, side}` for throttled manual cleanup. **Still untyped (raw `dict`)** — flagged in the security review; not yet hardened.
  - **Telegram bot lifespan registration:** Telegram poll job now registers in the FastAPI lifespan startup (was previously only inside `/api/monitor/start`). `max_instances=1, coalesce=True` on the scheduler job.
  - **`Session=ALL` plumbing:** verified the moomoo broker-direct paths `moomoo_close_one` and `_close_leg` inside `moomoo_flatten_broker` accept the same `_close_session_kwargs` plumbing — the broker-side helpers do; the main.py inline `place_order` call sites still need the same kwarg added.
  - Imports: added `os`, `sqlite3`, `field_validator`.

- `brokers/ibkr_trading.py`
  - `get_ib_connection()` and `IBKRTrader.ensure_connected()` short-circuit when the connection-flag toggle says IBKR is disabled. `creds.force=True` bypasses the gate (used only by `/api/ibkr/connect`).

- `frontend/src/topbar.jsx`
  - Header IBKR/Moomoo pills now have toggle buttons that POST `/api/connection/auto` to the backend. localStorage syncs to the server on mount; user preference is authoritative across page reloads.

- `frontend/src/views/MoomooView.jsx`
  - Added moomoo-scoped Heartbeat, Alerts, Signals Log, and Telegram cards. Heartbeat polls `/api/ibkr/heartbeat` directly to read moomoo-specific fields. Alerts and signals log filtered by broker so IBKR/moomoo views don't bleed into each other.
  - Reordered cards by operator priority: KPIs → Heartbeat+Alerts → Connection → Scanner → Signals Log → Positions → 0DTE bot → Order Ticket → Order Log → Telegram.

- `frontend/src/views/LiveView.jsx`
  - Filters `m.alerts` and `m.scanner.logs` to exclude moomoo-broker entries (the moomoo view shows them).

- `frontend/src/useBackendData.jsx`
  - `mergeScanner` now passes `preset_name` and `reason` through, and reads the correct `fired` flag (was checking unused `signal` field — green dot was always off).

- `config/presets.json`
  - Added `full-system-test-moomoo` preset (1-DTE bull-call, dynamic_risk @ 0.5%/$1k cap, dryrun strategy fires every 10 min).
  - Added `bypass_event_blackout: true` on the dryrun-moomoo preset (only honored when env gate is set).

- `config/.env.example` → `config/.env` (gitignored)
  - Telegram bot token + chat id wired (`@Charm_squeeze_0dte_bot`, chat 5657353623).
  - `TELEGRAM_POLL_INTERVAL_SECONDS` bumped from 3 → 10 to eliminate APScheduler "max instances" overlap warnings.

- `strategies/dryrun.py`
  - Trade every 10 min during RTH (09:30–15:50 ET) instead of 3 fixed times.

- `scripts/smoke_moomoo_fixes.py` (new)
  - 4-case smoke test: sizing cap honored, journal recording, idempotency dedup, telegram poll overlap. Auto-connects moomoo if disconnected.

- `docs/MOOMOO_REVIEW.md` (new)
  - Multi-agent review report (architect, python-reviewer, security-reviewer, database-reviewer). 7 critical, 11 high, 14 must-have features. Used as the source for this hardening pass.

- `.claude/moomoo_api_reference.md` (new) + `.claude/moomoo_usage_inventory.md` (new) + `.claude/moomoo_gap_analysis.md` (new)
  - Researched moomoo OpenAPI v10.5 reference; audited current SDK usage; gap analysis with prioritized feature list.

## Verification Run

Targeted tests that ran clean during this pass:

```bash
python3 -c "from core.journal import get_journal; ..."   # claim_key migration + UNIQUE index
python3 -c "from core.risk import size_position; ..."    # dynamic_risk now honors max_allocation_cap (5 contracts vs 29)
curl -X POST /api/moomoo/execute  → first 'order_rejected'  → second 'duplicate / idempotency_key_claimed'
ALLOW_BYPASS_EVENT_BLACKOUT unset → field_validator zeroes the flag
ALLOW_BYPASS_EVENT_BLACKOUT=1     → preserved
curl -X POST /api/moomoo/reconcile → orphans_recorded list populated when journal/broker drift exists
```

Smoke test: `python scripts/smoke_moomoo_fixes.py` — all four cases pass when the server is running and moomoo is connected.

Did NOT run the full pytest suite this pass (the prior pass's known fails are still open).

## Live Trading Verification (2026-05-07 incident review)

Thursday 2026-05-07 the system fired **22 unintended bull-call spreads** on moomoo paper, opened net **−$8K** unrealized, and exhausted buying power. Root causes (now fixed):

1. **Sizing cap silently ignored** — `dynamic` mode in `core.risk.size_position` only checked `max_trade_cap`, not the moomoo-path's `max_allocation_cap`. Result: $1M × 0.5% / ~$170 ≈ 29 contracts per fire instead of 1.
2. **Per-day idempotency couldn't dedup** — the day-key `scan:{date}:{symbol}:{preset}` was looked up in `orders` (which never got written because every spread returned status=error after leg2 timeouts).
3. **Orphan longs not journaled** — `place_spread` returned without writing a `Position` row when leg2 timed out, so the MTM monitor couldn't manage the orphans.

By Friday 2026-05-08 the close orders queued Thursday night had filled at the open and the paper account ended **+$3.6K vs starting $1M** (the deep-ITM 730–736 calls auto-exercised positively).

The hardening sweep above closes all three failure modes plus 8 other high-severity issues from the multi-agent review.

## Known Issues Still Open After This Pass

Carried over from the prior pass + new ones:
- API auth on broker-mutating routes — still not enforced. **Highest open severity.**
- `/api/moomoo/close_one` still takes raw `dict`, no symbol allowlist or qty cap.
- Trade PIN is accepted in connect/execute request bodies; should be server-env only.
- Telegram `/flatten confirm` has no replay protection (`update_id` dedup TTL).
- Server-side STOP / TRAILING_STOP not yet implemented (REAL-only feature; broker-side stops survive process death).
- Push-based fill handlers (`TradeDealHandlerBase`) not yet implemented (REAL-only; would replace the 0.5s poll loop).
- Greeks-aware chain gate not yet wired (we already fetch IV/delta in every snapshot and discard the columns).
- `get_option_expiration_date()` not yet wired (we still walk-forward over weekends; doesn't know about market holidays).
- Schema migration table — there's still no version column on `data/trades.db`.
- WAL checkpoint + DB backup schedule still not configured.
- Tests from prior pass still failing: `test_ibkr_adapter.py` (depends on `connection_flags.json` state), `test_monitor.py::test_tick_survives_trader_factory_failure` (test now wrong; new structured-skipped behavior is correct), `test_notifier.py` (sandbox networking).

## Coordination Notes For Other Agents

- `data/connection_flags.json` is **gitignored** (`.gitignore` line for `data/*.json` already covers it). Never commit it. It is also the source of truth for the IBKR/moomoo header toggles — don't reset it as part of CI cleanup.
- `config/.env` is **gitignored** and contains the live Telegram bot token + chat id. Treat as secret material.
- The `claim_key` column on `events` is the new dedup spine. Any new idempotent code path should write `claim_key=` and call `has_event_claim()` rather than re-introducing payload-string scans.
- `_moomoo_execute_impl` is the only place that should construct `Position` objects for the moomoo path. Callers should journal via `journal.open_position(Position(...))` — never via free kwargs.
- `_close_session_kwargs(ft)` exists on the broker module and should be used for any new close/flatten path so afterhours fills work.
- The MTM monitor *does* tick moomoo positions now (was previously broken when IBKR had no work); don't re-introduce the `if trader is None: return []` early-bail in `monitor.tick()`.

## 2026-05-10 Pass — Strategy Vetting + Pipeline Hardening + Paper-Trading Gate

### Strategy vetting (skill: `.claude/skills/strategy-vetting`)
Four new strategies vetted, all REJECTED (rationale + graduation path documented in each file's docstring):
- `strategies/nr7.py` — Crabel narrow-range-7, daily debit. Best PF 1.40 / Sharpe 0.65; SPY per-day drift too small for $5 debit mechanics. Same family as `donchian`.
- `strategies/cci_extreme.py` — Lambert CCI(20) oversold reversion. Best PF 1.11 / Sharpe 0.17; CCI's mean-deviation divisor scales with vol → noisy extremes. Same family as `bollinger_b`.
- `strategies/ldm_0dte.py` — afternoon-OR 0DTE continuation (Heston-Korajczyk-Sadka). 30% WR / Sharpe −7.39 on 60d; thesis empirically inverted by 0DTE gamma pinning.
- `strategies/ldm_fade_0dte.py` — inverted twin of LDM (fade afternoon breakouts). Best WR 50% / Sharpe −0.76 / PnL −$188; symmetric 50/50 brackets still eat per-trade EV.

### Backtest engine extensions
- `core/backtest_orb.py`: added `or_start_time: time` and `fade_mode: bool` config knobs (defaults 09:30, False — backward compatible). Reusable for any future 0DTE breakout/fade strategy.
- `main.py:_orb_config_from_request`: strategy-id-aware defaults; `fade_mode` wired from `req.strategy_id == "ldm_fade_0dte"`.
- `main.py:/api/backtest`: intraday dispatch now driven by `cls.BAR_SIZE != "1 day"` instead of hardcoded id tuple. New intraday strategies route through ORB harness without touching `main.py`.
- `main.py:_adapt_orb_report`: now computes `profit_factor`, `recovery_factor`, `avg_hold_days` (session fraction), `max_consec_losses` from the trade list — was hardcoded 0. Skill bar is now evaluable for intraday strategies.

### Pipeline hardening (skill-enforcement)
- `strategies/base.py`: new `VETTING_RESULT: str = "pending"` class attribute (`pending` | `shipped` | `rejected`). Tagged across all 21 strategy classes.
- `main.py:/api/strategies`: surfaces `bar_size`, `history_period`, `vetting_result` per strategy.
- `core/presets.py`:
  - Added `bar_size: str` field to `ScannerPreset` (self-describing timeframe).
  - `PresetStore.save` enforces two soft invariants — refuses presets for `VETTING_RESULT='rejected'` strategies and rejects `bar_size` drift from the class's `BAR_SIZE`. Logs warning for `pending`.
- `config/presets.json`: all 14 presets normalized to a uniform 27-key shape (added missing `broker`, `bypass_event_blackout`, `bar_size`; canonical key order).
- `core/chain.py`: new `pick_bear_put_strikes` mirrors bull-call picker. `main.py:_moomoo_execute_impl` now branches on `is_bear` to use the correct picker (was a latent bug — bear_put through moomoo would mis-strike).
- `main.py:MoomooOrderRequest.preset_name`: threaded from the auto-execute build site into `Position.meta` so journal positions are attributable per-preset for live↔backtest comparison.

### Paper-trading gate (NEW — replaces Alpaca PaperView)
**Backend (`core/paper_gate.py`, ~320 lines):**
- `PaperTrial` dataclass + `PaperGateStore` (SQLite table `paper_trials` in journal DB).
- `evaluate_trial(trial)` — scores live journal stats per preset vs trial's expected WR / cadence / max DD. Same math as `/api/paper_validation`, in-process.
- `run_daily_evaluator()` — iterates `status='trialing'` rows; transitions:
  - `trialing → passed` when verdict='pass' AND positions_closed ≥ min_trades AND days_open ≥ min_days
  - `trialing → failed` when sample-size ready AND verdict='fail'
  - `trialing → failed` immediately when a single loss exceeds `fast_fail_dd_multiplier × backtest max DD$` (default 2×)
  - On `failed`, automatically flips the preset's `auto_execute` to False
- `trial_allows_fire(preset_name)` — pre-fire concurrency gate (per-trial position cap, status check).

**Backend wiring (`main.py`):**
- APScheduler cron `16:30 ET Mon-Fri` → `_run_paper_gate_evaluator`.
- Auto-execute moomoo path consults `trial_allows_fire` before firing; for `trialing` presets it overrides sizing to `contracts=1, position_size_method="fixed"` so live samples stay apples-to-apples with the backtest (skill: "fixed_contracts: 1 for first-time presets").
- 6 new endpoints:
  - `POST /api/paper_trials/start {preset_name, expected_*, min_trades, min_days, max_open_positions, allow_overlap, fast_fail_dd_multiplier, notes}`
  - `GET /api/paper_trials[?status=]` (returns rows + folded-in live evaluation per trial)
  - `POST /api/paper_trials/evaluate_now` (manual cron trigger)
  - `POST /api/paper_trials/{preset_name}/stop` (demote — disables `auto_execute`)
  - `POST /api/paper_trials/{preset_name}/promote` (audit flag; only valid when status=`passed`; does NOT move funds)
  - `DELETE /api/paper_trials/{preset_name}` (erase audit row)

**Frontend (`frontend/src/views/PaperView.jsx`, rewritten):**
- 4 KPI counters: trialing / passed / failed / promoted.
- "Start Trial" form: preset dropdown + expected WR%, trades/wk, max DD%, min_trades, min_days.
- Active Trials table: preset · status pill · verdict pill · days/min_days · trades/min_trades · WR vs target · cadence ratio · worst loss · per-row Stop / Promote / × actions, with inline finding messages.
- Status filter dropdown (all / trialing / passed / failed / promoted / demoted).
- 30-second auto-refresh + manual "Evaluate Now" button.
- Sidebar label: "Paper (Alpaca)" → "Paper Trials". Topbar sub: "Alpaca · equity surrogate" → "multi-preset moomoo paper gate · skill Step 10".
- `api.js`: removed `paperConnect/paperPositions/paperOrders/paperExecute/paperScan`; added `paperTrialsList/paperTrialStart/paperTrialEvaluate/paperTrialStop/paperTrialPromote/paperTrialDelete`.

### Backtester UI changes
- `BacktestView.jsx`: new "Timeframe" chip + read-only "Bar Size" field; strategy dropdown labels intraday with `· 5 mins` and rejection/pending verdicts (`⊘ rejected` / `· pending`); `years_history` auto-clamps to yfinance caps when an intraday strategy is selected.
- `index.css`: removed `overflow: hidden` from `.config-editor` (added `min-height: 0`); tall tab bodies (Strategy params, Filters, Optimizer) now flow into the sidebar's vertical scroll instead of being clipped.

### Verification this pass
```bash
python3 -c "from main import app; from core.paper_gate import get_paper_gate_store; print('ok')"
python3 -c "from core.scanner import list_strategy_classes; print(len(list_strategy_classes()))"  # 21
cd frontend && npm run build  # green
curl -sS http://127.0.0.1:8000/api/strategies | jq '.[] | {id, bar_size, vetting_result}'
curl -sS -X POST http://127.0.0.1:8000/api/paper_trials/start -H 'Content-Type: application/json' \
  -d '{"preset_name":"rsi2-moomoo","expected_win_rate_pct":75,"expected_trades_per_week":0.5,"expected_max_drawdown_pct":-4}'
curl -sS http://127.0.0.1:8000/api/paper_trials       # roundtrip OK
curl -sS -X DELETE http://127.0.0.1:8000/api/paper_trials/rsi2-moomoo
```

### Known open items from this pass
- Resolved in follow-up below: stale `nr7-moomoo` was removed, and `streak_ibs` / `rsi_ibs_confluence` were vetted and flipped to `shipped`.
- `_adapt_orb_report` capital proxy: uses `max(entry_cost) * 10` to scale max-DD% to dollars when computing the catastrophic-loss threshold. Crude; consider threading the actual `capital_allocation` from the trial config when this matters.
- Paper-trading gate UI doesn't yet show the trial's `started_at` timestamp or expected/observed ratio chart.
- Promotion currently only sets a status flag. No automated cloning to a `*-live` preset with `trd_env=REAL` — intentional (skill Step 10 requires human review), but a "Clone to Live" button would reduce manual work.

### 2026-05-10 Follow-up — Strategy Vetting Rerun + Audit Reports
- Re-ran `strategy-vetting` for `nr7`, `rsi_ibs_confluence`, and `streak_ibs`.
- Added required per-strategy markdown reports:
  - `docs/strategy-vetting/2026-05-10-nr7.md`
  - `docs/strategy-vetting/2026-05-10-rsi-ibs-confluence.md`
  - `docs/strategy-vetting/2026-05-10-streak-ibs.md`
- Removed stale `nr7-moomoo` from `config/presets.json`; `Nr7Strategy.VETTING_RESULT` remains `rejected`.
- Promoted `RsiIbsConfluenceStrategy.VETTING_RESULT` and `StreakIbsStrategy.VETTING_RESULT` to `shipped`.
- Confirmed `rsi-ibs-confluence-moomoo`, `streak-ibs-strict-moomoo`, and `streak-ibs-balanced-moomoo` load through `PresetStore`.
- Verification:
  - `python3 -m json.tool config/presets.json`
  - `python3 -m compileall -q strategies/rsi_ibs_confluence.py strategies/streak_ibs.py core/scanner.py`
  - `pytest -q tests/test_scanner.py tests/test_presets.py` -> 12 passed

### 2026-05-10 Follow-up 2 — Pending Strategy Vetting
- Re-ran `strategy-vetting` for the remaining pending daily candidates:
  - `gap_down_reversal`
  - `turnaround_tuesday`
- Added required per-strategy markdown reports:
  - `docs/strategy-vetting/2026-05-10-gap-down-reversal.md`
  - `docs/strategy-vetting/2026-05-10-turnaround-tuesday.md`
- Marked both strategies `VETTING_RESULT='rejected'`.
- No presets were created:
  - `gap_down_reversal`: loose 3y config passed, but 1y recency was negative and nearby IBS robustness dropped below Sharpe gate.
  - `turnaround_tuesday`: too sparse and too weak for debit-spread mechanics; best Sharpe 0.75.
- Verification:
  - `python3 -m json.tool config/presets.json`
  - `python3 -m compileall -q strategies/gap_down_reversal.py strategies/turnaround_tuesday.py core/scanner.py`
  - `pytest -q tests/test_scanner.py tests/test_presets.py` -> 12 passed

### 2026-05-10 Follow-up 3 — External Strategy Vetting
- Sourced and implemented two Connors-family daily mean-reversion candidates:
  - `connors_3day`
  - `double7`
- Added required per-strategy markdown reports:
  - `docs/strategy-vetting/2026-05-10-connors-3day.md`
  - `docs/strategy-vetting/2026-05-10-double7.md`
- Marked both strategies `VETTING_RESULT='rejected'`.
- No presets were created:
  - `connors_3day`: too sparse for SPY debit spreads; 6 baseline trades and 0 recency trades.
  - `double7`: literal rule failed PF/Sharpe; tuned 5-day channel passed 3y metrics but failed 1y recency and 6-day robustness.

### 2026-05-10 Follow-up 4 — RSI 25/75 Vetting
- Sourced and implemented Connors/Cesar Alvarez `rsi_25_75`.
- Added required report: `docs/strategy-vetting/2026-05-10-rsi-25-75.md`.
- Marked `Rsi2575Strategy.VETTING_RESULT='rejected'`.
- No preset was created:
  - 7-DTE baseline passed 3y debit metrics: 22 trades, 72.73% WR, PF 2.64, Sharpe 1.42, DD -4.73%.
  - Rejected because 1y recency was directionally negative and loose-entry robustness fell below PF/Sharpe gate.

### 2026-05-10 Follow-up 5 — Cumulative RSI Vetting
- Sourced and implemented Connors/Cesar Alvarez `cumulative_rsi`.
- Added required report: `docs/strategy-vetting/2026-05-10-cumulative-rsi.md`.
- Marked `CumulativeRsiStrategy.VETTING_RESULT='rejected'`.
- No preset was created:
  - Baseline 7-DTE had 22 trades, 68.18% WR, PF 1.74, but Sharpe 0.87.
  - Better-quality thresholds fell below 20 trades; looser threshold collapsed to PF 1.31 and Sharpe 0.52.

### 2026-05-10 Follow-up 6 — R3 Vetting
- Sourced and implemented Connors `r3`.
- Added required report: `docs/strategy-vetting/2026-05-10-r3.md`.
- Marked `R3Strategy.VETTING_RESULT='rejected'`.
- No preset was created:
  - Literal R3 was sparse and weak: 9 trades, PF 1.41, Sharpe 0.33.
  - Relaxed 2-drop candidate passed 3y stats: 23 trades, 78.26% WR, PF 2.93, Sharpe 1.57, DD -4.94%.
  - Rejected because 1y recency was directionally negative with PF 0.97 and PnL -$4.31.

### 2026-05-10 Follow-up 7 — Williams %R Vetting
- Sourced and implemented `williams_r`.
- Added required report: `docs/strategy-vetting/2026-05-10-williams-r.md`.
- Marked `WilliamsRStrategy.VETTING_RESULT='rejected'`.
- No preset was created:
  - 7-day lookback candidate passed 3y stats: 22 trades, 81.82% WR, PF 4.95, Sharpe 2.06, DD -2.64%.
  - Nearby 6/8-day lookbacks also passed 3y checks.
  - Rejected because 1y recency was directionally negative: 2 trades, PF 0.93, PnL -$13.34.

## Suggested Next Agent Tasks (updated)

1. Add API-key enforcement to broker-mutating routes (highest open severity).
2. Replace `/api/moomoo/close_one` raw dict with a typed Pydantic model + symbol regex + qty cap.
3. Move `trade_password` out of API request bodies; load from env only.
4. Add `update_id` dedup TTL to Telegram bot polling.
5. Wire `get_option_expiration_date()` for holiday-aware DTE resolution (small, no live trading needed).
6. Wire greeks-aware chain-quality gate using the `delta` / `option_open_interest` columns we already fetch (small).
7. Implement `TradeDealHandlerBase` push handlers for REAL accounts (medium); keep polling fallback for SIMULATE.
8. Implement broker-side STOP / TRAILING_STOP for REAL accounts (medium); keep MTM monitor as belt-and-suspenders.
9. Realign the failing tests with the new behavior (`test_monitor.py`, `test_ibkr_adapter.py`).
10. Add a `schema_version` table + migration runner to `core/journal.py`.
11. Start paper trials for `rsi-ibs-confluence-moomoo`, `streak-ibs-strict-moomoo`, and `streak-ibs-balanced-moomoo` before any live promotion.
12. Add a report index page for `docs/strategy-vetting/` so rejected/promoted strategies are easier to audit from the UI or docs.
13. Wire **engine support for credit verticals** in `run_backtest_engine` (negative entry_cost, max_loss = width × 100 − credit). Unlocks `turn_of_month` + `dabd` rejections and any future small-drift strategy.
14. Build a **generic intraday harness** (not ORB-shaped) so 0DTE strategies whose triggers aren't opening-range can be vetted. Eod-drift class.
15. Add a **"Clone to Live" button** on passed paper trials — duplicates the preset with `*-live` suffix and `trd_env=REAL`, behind a confirmation modal.
16. Show paper-trial **started_at + progress sparkline** in PaperView for visual debugging of slow-cadence trials.
17. Add an **iron condor topology** to the engine — unlocks range-bound triggers (vol-contraction, neutral RSI bands) and the cleanest path-to-graduation for `ldm_fade_0dte`.

### Strategy vetting — 2026-05-10 — `vwap_reversion` REJECTED
- 5-min SPY 0DTE debit vertical, session-VWAP ±k·σ band reversion (first generic-intraday-engine strategy in the codebase).
- 60d sample (yfinance 5m cap). Best across 3 sweeps: k=2.5, width=5, TP/SL=50/50, bull side:
  - trades=57, win_rate=57.9%, PF=0.69, Sharpe=-2.35, max_dd=-10.3% — below intraday-debit bar on PF and Sharpe.
  - Bear side consistently worse (PF 0.53), fights SPY drift.
- Failure mode same as `dabd` / `vix_spike`: small-reversion edge cannot survive linear-delta payoff + $20/trade slippage in debit form. Tighter TP (15-25%) raised WR to ~64% but PF collapsed further.
- Path to graduation: **credit spread topology** (sell put-spread below lower band / sell call-spread above upper band). Blocks on backlog item #13 (credit-spread engine support).
