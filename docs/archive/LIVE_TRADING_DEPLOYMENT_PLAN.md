# SPY Credit Spread — Live Trading Deployment Plan

**Target:** Deploy live trading by 2026-04-30
**Generated:** 2026-04-14
**Last updated:** 2026-04-16
**Focus:** Bullish call (debit) spreads on SPY — backtester + live execution
**Scope rule:** Strategy logic stays as-is. All work goes into reliability, risk,
execution, monitoring, persistence, and test coverage around the existing signals.

---

## Progress Tree (updated 2026-04-17)

```
TIER 1 — CRUCIAL  ✅ ALL COMPLETE
  [x] C1  Persistent trade journal (SQLite)         core/journal.py     106 tests passing
  [x] C2  Live position monitor loop                 core/monitor.py     wired via /api/monitor/start
  [x] C3  Pre-trade risk check layer                 core/risk.py        wired into /api/ibkr/execute
  [x] C4  Real option chain integration (IBKR)       core/chain.py       wired into /api/ibkr/execute
  [x] C5  Fix hardcoded order bugs                   main.py             side derived from debit/credit; real expiry
  [x] C6  Kill switch / flatten-all                  main.py             POST /api/ibkr/flatten_all
  [x] C7  Fill verification + cancel-on-timeout      core/fill_watcher.py wired via /api/monitor/start
  [x] C8  Scanner filter parity                      core/filters.py     wired into run_market_scan()
  [x] C9  Position sizing in live path               core/risk.py        wired into /api/ibkr/execute
  [x] C10 Config-as-code secret loading              core/settings.py    .env.example + .gitignore
  [x] C11 Health & heartbeat endpoint                main.py             extended with journal/scheduler/positions
  [x] C12 Backtest <-> live reconciliation test      tests/test_reconcile.py 19 tests (signal/filter/sizing parity)

TIER 2 — IMPORTANT
  [x] I1  Structured logging + log retention      core/logger.py   19 tests; root JSON handler wired in main.py
  [x] I2  Daily summary email/webhook         core/notifier.py; Discord/Slack/generic; 28 tests
  [x] I3  Event calendar blackout                    config/events_2026.json + core/calendar.py
  [ ] I4  Slippage / commission accounting
  [x] I5  Idempotency keys                           UI generates UUID; scanner uses date-scoped keys; 5 tests pass
  [x] I6  Monitor-loop lock / leader election   core/leader.py    11 tests; fcntl.flock advisory lock
  [~] I7  Reconnect-on-drop                          IBKRTrader.ensure_connected() exists; no exp backoff
  [~] I8  Market hours + holiday calendar            core/calendar.py exists; risk check uses it
  [x] I9  Backtest realism upgrades             bid_ask_haircut field; entry+exit haircut; 9 tests
  [x] I10 UI alerts for critical conditions         heartbeat returns alerts[] + flags; 11 tests
  [x] I11 Snapshotting of prior scanner runs        scanner_logs table + hydrate on startup; 11 tests
  [~] I12 Stronger pytest coverage                   195 tests exist (2 skipped); engine tests still missing

TIER 3 — OPTIONAL (post-launch)
  [ ] O1-O10 all deferred

INTEGRATION (wired into main.py 2026-04-15)
  [x] /api/ibkr/execute           rewired: chain + risk + journal + sizing
  [x] /api/ibkr/flatten_all       new endpoint
  [x] /api/ibkr/heartbeat         extended with journal/scheduler status
  [x] /api/monitor/start          new: registers monitor + fill watcher jobs
  [x] /api/monitor/stop           new: removes scheduler jobs
  [x] /api/journal/positions      new: list open/all positions
  [x] /api/journal/daily_pnl      new: today + 30-day history
  [x] /api/journal/events         new: audit trail
  [x] run_market_scan()           now uses core.filters.apply_filters (C8)
```

---

## 0. Executive Summary

The codebase has a working backtester, a functional IBKR adapter, a working
scanner, and a polished terminal UI. What it does **not** have — and what
separates "runs on my laptop" from "I can trade real money on it" — is:

1. **A live position lifecycle.** Right now, `/api/ibkr/execute` opens a spread
   and forgets it. There is no monitor loop that evaluates DTE, stop-loss,
   take-profit, or trailing-stop against live mark-to-market. The exit logic
   only exists inside the backtest engine's `run_backtest_engine()`.
2. **A risk layer.** Position sizing is only computed during backtest. Live
   mode takes `contracts` as an integer input with no buying-power check,
   no daily-loss cap, no max-concurrent-positions limit, no kill switch.
3. **Persistence.** Scanner state, open positions, trade journal — all live in
   a Python dict. A server restart loses everything, potentially stranding
   real open orders.
4. **Order reliability.** No idempotency key, no fill verification, no retry
   on partial fills, no cancellation-on-timeout, and the hardcoded
   `side = 'BUY'` in `/api/ibkr/execute` is a live-trading footgun.
5. **Real option data.** The live scanner prices spreads with Black-Scholes
   against a 21-day realized-vol proxy. Real bid/ask/IV from the chain is
   never consulted. That is fine for backtest but unacceptable for live
   entry — you can submit a limit that is 20% inside the true mid.

With 16 working days, delivering all of the above is achievable **if** we
scope tightly to bullish call spreads on SPY and defer the multi-topology /
multi-strategy ambition that lives in `upgrade/QUESTIONS.md`.

---

## 1. Program Logic Flow — As It Stands Today

```
┌─────────────────────┐      ┌─────────────────────┐      ┌─────────────────────┐
│  Frontend (React)   │◄────►│  FastAPI (main.py)  │◄────►│ Backtest Engine     │
│  App.jsx 1278 LOC   │      │  996 LOC monolith   │      │ run_backtest_engine │
└─────────────────────┘      └──────────┬──────────┘      └──────────┬──────────┘
                                        │                            │
                       ┌────────────────┼─────────────────┐          │
                       │                │                 │          │
                       ▼                ▼                 ▼          ▼
               ┌──────────────┐  ┌─────────────┐  ┌────────────┐  ┌───────────────┐
               │ Scanner      │  │ IBKR adapter│  │ Alpaca     │  │ Black-Scholes │
               │ APScheduler  │  │ ib_insync   │  │ paper API  │  │ pricer +      │
               │ (cron/int)   │  │             │  │            │  │ topology      │
               └──────┬───────┘  └──────┬──────┘  └──────┬─────┘  │ builder       │
                      │                 │                │        └───────────────┘
                      ▼                 ▼                ▼
               run_market_scan()  place_combo_order  place_equity_order
                      │                 │                │
                      ▼                 ▼                ▼
                scan_signal()     TWS/Gateway       Alpaca REST
                 (paper only)     (7497 paper /
                                   7496 live)
```

### Request path for the three modes

**BACKTEST** — pure offline:
1. `POST /api/backtest` receives `BacktestRequest`
2. `fetch_historical_data(ticker, years)` → yfinance daily bars (cached)
3. `strategy.compute_indicators(df, req)` → sma/ema/rsi/hv
4. `run_backtest_engine()` walks bars, calls `check_entry` / `check_exit`
5. On entry: `OptionTopologyBuilder.construct_legs()` prices synthetic spread
6. On exit: `OptionTopologyBuilder.price_topology()` marks-to-market
7. `compute_analytics()` → metrics, MC, heatmap, walk-forward
8. Response rendered in frontend

**PAPER (Alpaca)** — signal + equity surrogate:
1. User enters Alpaca keys → `/api/paper/connect`
2. User clicks Start Scan → `/api/scanner/start` with `mode: "paper"`
3. APScheduler fires `run_market_scan()` on cadence
4. `run_market_scan` calls `scan_signal()` which re-runs `check_entry` on
   the last bar of a freshly-downloaded 1mo daily feed
5. On signal + auto_execute: `place_equity_order(SPY, qty, buy/sell)` — a
   100-share equity proxy, **not** a spread
6. No exit logic in paper mode. Positions sit until user manually exits.

**LIVE (IBKR)** — real spreads, broken lifecycle:
1. User connects: `/api/ibkr/connect` → ib_insync socket to TWS
2. User manually clicks Execute: `/api/ibkr/execute`
3. Server re-fetches 1y history, rebuilds spread, computes midpoint
4. `place_combo_order(..., side='BUY', lmtPrice=mid)` — **bug:**
   side is hardcoded, target_dte→expiry calendar is naive (counts every
   day+1, not actual SPY expirations)
5. Response returns orderId. Server does not track it.
6. No MTM, no exit, no reconciliation.

### Shared state (all in-memory, lost on restart)

- `scanner_state: dict` — logs, config, mode, auto_execute flag
- `_ib_instances: dict[str, IBKRTrader]` — host:port:cid keyed singletons
- `_api: alpaca.REST` — module-level singleton (thread-unsafe)
- `@lru_cache` on `fetch_historical_data`, `fetch_risk_free_rate`, `fetch_vix_data`

---

## 2. Strategy Logic — Current Understanding (keep unchanged)

User wants strategy math untouched. Here is what it does today so we can
defend it during review and wire live execution around it correctly.

### 2.1 `ConsecutiveDaysStrategy` (`strategies/consecutive_days.py`)

- Tracks `greenDays` and `redDays` streak counters
- **Entry (bullish):** `redDays == N` or `redDays == N+1`
- **Entry (bearish):** `greenDays == N` or `greenDays == N+1`
- **Exit:** opposite-color streak reaches `exit_green_days`, OR DTE ≤ 0
- Indicators produced: EMA_{N}, SMA_200, SMA_50, Volume_MA, HV_21, RSI
- **Assessment:** simple, robust, interpretable. No changes.

### 2.2 `ComboSpreadStrategy` (`strategies/combo_spread.py`)

- SMA(3,8,10) + EMA(5,3) + OHLC-average composite
- **Entry (bullish):** one of two composite conditions
  - `e1`: Close < SMA3 & Close ≤ SMA1 & prevClose > prevSMA1 & Open > EMA1 & SMA2 down
  - `e2`: Close < EMA2 & Open > OHLC_avg & Volume ≤ prevVolume & outside_body
- **Exit:** DTE ≤ 0, OR `profit_closes ≥ max_profit_closes`, OR `days_held ≥ max_bars`
- **Assessment:** captures short-term reversal after weakness. Keep as-is.

### 2.3 Filters (universal, applied in `run_backtest_engine` + `scan_signal`)

- RSI filter (bull: `RSI < rsi_threshold`; bear: inverted)
- EMA filter (bull: `Close < EMA`; bear: inverted)
- SMA200 filter (bull: `Close > SMA200`)
- Volume filter (`Volume > Volume_MA`)
- VIX filter (range `[vix_min, vix_max]`)
- Regime filter (`bull` / `bear` / `sideways` / `all`)

**Gap:** `scan_signal()` implements RSI + EMA filters but **skips** SMA200,
Volume, VIX, regime. Live scanner entries can therefore fire on conditions
the backtest would reject — making live results diverge from backtest.
**This must be fixed (crucial task #3).**

### 2.4 Topology Builder (`strategies/builder.py`)

The builder supports `long_call`, `long_put`, `vertical_spread` (bull_call,
bear_put, bear_call, bull_put), `straddle`, `iron_condor`, `butterfly`.

For the live-trading MVP we only need `vertical_spread` + `direction="bull_call"`.
The other topologies stay in code for future expansion; we only harden + test
the bull-call path.

**Bull call spread construction (what we care about):**
```
K_long  = round(SPY_price)                       # ATM long call
K_short = scan K_long+1 .. K_long+40, pick where
          (c_long - c_short) matches target_cost / 100
net_cost = (c_long - c_short) × 100              # debit paid, positive
margin_req = net_cost                            # debit spread margin = cost
max_loss = net_cost                              # full debit
max_gain = (K_short - K_long) × 100 − net_cost
```

---

## 3. Risk & Position Sizing — Current & Gaps

### 3.1 Backtest sizing modes (code exists, works)

| Mode | Formula | Cap |
|------|---------|-----|
| Fixed | `contracts_per_trade` (user input) | — |
| Dynamic | `floor((equity × risk_pct/100) / risk_per_contract)` | `max_trade_cap` |
| Targeted spread | `floor(min(equity × target_pct/100, max_allocation_cap) / risk_per_contract)` | `max_allocation_cap` |

### 3.2 Risk measures in backtest (code exists)

- `stop_loss_pct` — exit when `pnl_pct ≤ -stop_loss_pct`
- `take_profit_pct` — exit when `pnl_pct ≥ take_profit_pct`
- `trailing_stop_pct` — exit when `(high_water - cv) > trailing_pct × cost_basis`

### 3.3 What's missing for live

| Gap | Why it matters |
|-----|----------------|
| **No buying-power pre-check** | IBKR will reject but we get no friendly error; for credit spreads, `margin_req = (K_wide) × 100` must be ≤ excess liquidity |
| **No max-concurrent-positions cap** | Rapid-fire signals can 10x your exposure in one morning |
| **No daily-loss circuit breaker** | After -$X realized, stop all new entries for the day |
| **No per-symbol exposure cap** | N/A for now (SPY only) but architecturally required |
| **No kill switch / flatten-all** | If something goes wrong, you need one-button close |
| **No commission/slippage in live PnL** | Backtest uses `commission_per_contract` but live has no fee tracking |
| **No event-window blackout** | FOMC / CPI / NFP often cause the exact opposite of backtest conditions |
| **No market-hours check before order** | Order placement outside RTH will sit in queue or rejected |
| **Sizing only runs at signal time** | If signal fires but fill delayed, stale sizing input |

---

## 4. Order Flow — Current Flaws & Improved Design

### 4.1 Current `/api/ibkr/execute` flow (flaws annotated)

```python
# CURRENT — main.py:871-934
trader, msg = await get_ib_connection(req.creds.model_dump())
raw_df = fetch_historical_data(req.symbol, 1)               # ① 1y of daily bars per order
S = float(raw_df.iloc[-1]['Close'])                         # ② uses last daily close, not live
sigma = log_ret.rolling(21).std().iloc[-1] * sqrt(252)      # ③ realized vol, not IV
pos = OptionTopologyBuilder.construct_legs(...)             # ④ synthetic strikes
expiry_date = today + timedelta(days=req.target_dte)        # ⑤ naive calendar math
while expiry_date.weekday() >= 5: expiry_date += 1 day      # ⑥ weekday fallback, not SPY expiry
midpoint = await trader.get_combo_midpoint(symbol, ib_legs) # ⑦ only now do we touch live bid/ask
side = 'BUY'                                                # ⑧ HARDCODED — credit spreads break
res = await trader.place_combo_order(..., lmtPrice=midpoint)
# ⑨ no tracking of orderId in persistent store
# ⑩ no fill monitoring, no cancel-on-timeout
# ⑪ no exit leg scheduling
```

### 4.2 Improved live order flow (target design)

```
┌──────────────────┐
│  Signal fires    │ (scanner or manual)
└────────┬─────────┘
         ▼
┌──────────────────────┐     ┌──────────────────────┐
│ PreTradeRiskCheck    │────►│ REJECTED → log,      │
│  - buying power      │     │ skip, no order       │
│  - max concurrent    │     └──────────────────────┘
│  - daily loss guard  │
│  - market hours      │
│  - event blackout    │
└────────┬─────────────┘
         │ PASS
         ▼
┌──────────────────────┐
│ Resolve real chain   │  ib.reqSecDefOptParams(SPY)
│  - live SPY quote    │  ib.reqTickers(stock)
│  - real IV surface   │  ib.reqMktData(ATM call)
│  - valid expirations │
└────────┬─────────────┘
         ▼
┌──────────────────────┐
│ Pick strikes (live)  │
│  - K_long = ATM      │  # use SPY rounding rules
│  - K_short scanned   │  # against live bid/ask
│    on real premiums  │  # target debit = user config
└────────┬─────────────┘
         ▼
┌──────────────────────┐
│ Sizing               │
│  target_risk = min(  │
│    equity × pct/100, │
│    max_trade_cap,    │
│    account.excess    │
│  )                   │
│  contracts = floor(  │
│   target_risk / debit│
│  )                   │
└────────┬─────────────┘
         ▼
┌──────────────────────┐
│ Submit combo LMT     │
│  - idempotency key   │  # uuid + persist in journal
│  - lmt = mid         │
│  - outsideRth=False  │
└────────┬─────────────┘
         ▼
┌──────────────────────┐     ┌──────────────────────┐
│ Fill watcher         │────►│ Partial fill?        │
│  timeout = 30s       │     │  cancel remainder    │
│  on filled → persist │     │  adjust MTM basis    │
│  on rejected → log   │     └──────────────────────┘
└────────┬─────────────┘
         ▼
┌──────────────────────┐
│ Position registered  │  SQLite trade journal
│ in journal + hot     │  state = "open"
│ state dict           │  entry_fill_price, legs, ts
└────────┬─────────────┘
         ▼
┌──────────────────────┐
│ Monitor loop (5s)    │
│  for each open pos:  │
│   - fetch combo mid  │
│   - check stop/tp/   │
│     trailing/DTE     │
│   - fire exit order  │
└──────────────────────┘
```

### 4.3 Exit flow (missing today — must add)

```
monitor_tick():
    for pos in journal.open_positions():
        mid = trader.get_combo_midpoint(pos.symbol, pos.legs)
        if mid is None: continue                      # data glitch — skip tick
        mtm = mid × 100 × contracts
        pnl_pct = (mtm - entry_cost) / |entry_cost| × 100
        dte = (pos.expiry - today).days

        exit_reason = None
        if dte <= 0:                                   exit_reason = "expired"
        elif pnl_pct <= -cfg.stop_loss_pct:            exit_reason = "stop_loss"
        elif pnl_pct >= cfg.take_profit_pct:           exit_reason = "take_profit"
        elif cfg.trailing_stop_pct > 0:
            pos.high_water = max(pos.high_water, mtm)
            if (pos.high_water - mtm) > cfg.trailing_stop_pct/100 × |entry_cost|:
                exit_reason = "trailing_stop"
        elif strategy.check_exit(live_df, -1, pos.state, cfg)[0]:
            exit_reason = "strategy_signal"

        if exit_reason:
            order = trader.place_combo_order(pos.symbol, pos.legs,
                                              contracts, side='SELL',
                                              lmtPrice=mid)
            journal.mark_closing(pos.id, order.orderId, exit_reason)
```

---

## 5. Ranked Task List — Crucial → Optional

### TIER 1 — CRUCIAL (must ship by 2026-04-30)

These gate any live trading. Without them you are trading blind.

**C1. Persistent trade journal (SQLite)** --- DONE 2026-04-15
- *File:* `core/journal.py` (509 LOC)
- *Tables:* `positions`, `orders`, `fills`, `daily_pnl`, `events`
- *Tests:* `tests/test_journal.py` — 6 tests (roundtrip, restart, PnL rollup, idempotency, fills, audit)
- *AC met:* server restart reloads open positions via `Journal(db_path)`;
  all order paths journal via `open_position`, `record_order`, `record_fill`, `close_position`.
- *Wired:* `/api/ibkr/execute` journals position + order; `/api/journal/*` endpoints expose data.

**C2. Live position monitor loop (exit lifecycle)** --- DONE 2026-04-15
- *File:* `core/monitor.py` (380 LOC)
- *Tests:* `tests/test_monitor.py` — 14 tests (stop/TP/trailing/DTE/HWM/force/tick)
- *AC met:* `evaluate_exit()` checks DTE, stop_loss, take_profit, trailing_stop.
  `tick()` fetches live combo mid, rolls HWM, fires `submit_exit_order()` on trigger.
  Registered as APScheduler job via `POST /api/monitor/start`.
- *Note (gurwinder edit):* exit signals close open positions (SELL to close the
  debit spread). They do NOT open opposite-side new positions. The monitor only
  submits a closing order for the existing spread, never a new entry.

**C3. Pre-trade risk check layer** --- DONE 2026-04-15
- *File:* `core/risk.py` (226 LOC)
- *Tests:* `tests/test_risk.py` — 19 tests (market hours, concurrent cap, daily loss,
  buying power, event blackout, sizing modes)
- *AC met:* `evaluate_pre_trade(ctx) -> Decision(allowed, reason)` called in
  `/api/ibkr/execute` before any order. Checks: market open, max concurrent (default 2,
  configurable via `MAX_CONCURRENT_POSITIONS` env), daily loss %, buying power, event blackout.
- *Config:* `MAX_CONCURRENT_POSITIONS=2` in `.env` (change to 1 for first live week).

**C4. Real option chain integration (IBKR)** --- DONE 2026-04-15
- *File:* `core/chain.py` (322 LOC)
- *Tests:* `tests/test_chain.py` — 6 tests (expiry picking, strike selection, edge cases)
- *AC met:* `resolve_bull_call_spread(trader, symbol, target_dte, target_cost)` calls
  `reqSecDefOptParams` for real expirations, `reqMktData` for live bid/ask/IV on candidate
  strikes, picks ATM long + OTM short to match target debit. Returns `SpreadSpec` with
  legs, net_debit, margin_req, and real expiry string.

**C5. Fix hardcoded order bugs in `/api/ibkr/execute`** --- DONE 2026-04-15
- *Fixed:* `side` now derived from `spread.net_debit > 0 ? BUY : SELL` (was hardcoded BUY).
- *Fixed:* expiry now comes from `core/chain.py` which uses `reqSecDefOptParams` real SPY
  expirations (was naive `today + timedelta(days=dte)` with weekday skip).
- *Fixed:* limit price computed with configurable haircut from SETTINGS.

**C6. Kill switch / flatten-all endpoint** --- DONE 2026-04-15
- *Endpoint:* `POST /api/ibkr/flatten_all`
- *AC met:* iterates `journal.list_open()`, fetches live mid for each, submits opposite-side
  combo LMT with 0% haircut (aggressive fill), marks positions `closing`, journals event.

**C7. Fill verification + cancel-on-timeout** --- DONE 2026-04-15
- *File:* `core/fill_watcher.py` (386 LOC)
- *Tests:* `tests/test_fill_watcher.py` — 17 tests (waiting, filled, rejected, timeout,
  partial, no-record, cancelled, finalize entry/exit, reconcile)
- *AC met:* `reconcile_once(trader)` polls `get_order_status()`, applies `next_action()`
  FSM (waiting/filled/partial/cancel_timeout/cancelled/rejected), calls `finalize_filled`
  or `finalize_cancelled` which update journal. Registered as APScheduler job alongside monitor.

**C8. Parity between scanner filters and backtest filters** --- DONE 2026-04-15
- *File:* `core/filters.py` (153 LOC)
- *Tests:* `tests/test_filters.py` — 9 tests (RSI, EMA, SMA200, Volume, VIX, regime, bias)
- *AC met:* `apply_filters(row, req)` is the single filter function. `run_market_scan()` in
  `main.py` now calls it after `scan_signal()`, applying the full filter set (SMA200, Volume,
  VIX, regime) that the backtest engine uses. Scanner log shows rejection reason if filtered.

**C9. Position sizing in live path** --- DONE 2026-04-15
- *Function:* `core/risk.py::size_position()` + `sizing_mode_from_request()`
- *AC met:* `/api/ibkr/execute` reads `use_dynamic_sizing`, `use_targeted_spread`,
  `risk_percent`, `max_allocation_cap` from request body. When `contracts=0` (default),
  auto-sizes against live account equity and live spread debit. Clamped by excess_liquidity.

**C10. Config-as-code secret loading** --- DONE 2026-04-14
- *File:* `core/settings.py` (171 LOC), `config/.env.example`
- *AC met:* `SETTINGS` singleton loads from `.env` or `config/.env` via python-dotenv
  (with manual fallback). IBKR/Alpaca creds, risk defaults, journal path all configurable.
  `.gitignore` covers `.env`, `config/.env`, `data/`, `logs/`, `*.db`.

**C11. Health & heartbeat endpoint** --- DONE 2026-04-15
- *Endpoint:* `POST /api/ibkr/heartbeat`
- *AC met:* returns `alive`, `status`, `ibkr_available`, `journal_ok`, `open_positions`,
  `today_pnl`, `today_trades`, `scheduler_jobs[]`, `monitor_registered`. UI can key off
  `alive=false` or `monitor_registered=false` to show red banners.

**C12. Backtest <-> live reconciliation test** --- DONE 2026-04-16
- *File:* `tests/test_reconcile.py` (19 tests, `@pytest.mark.reconcile`)
- *Layers verified:*
  - **Signal parity** — `strategy.check_entry()` fires on same bars in both paths
    (forced red-day streak scenarios; bull + bear direction)
  - **Filter parity** — inline backtest filters ≡ `core/filters.apply_filters()`
    for RSI, EMA, SMA200, Volume, Regime, and all-combined
  - **Sizing parity** — inline backtest sizing ≡ `core/risk.size_position()`
    for fixed / dynamic / targeted_spread modes, including cap binding and
    excess-liquidity clamp
  - **End-to-end** — 4 integration tests replay every backtest entry through
    the live-path building blocks and assert identical contracts
- *AC met:* `pytest -m reconcile` green (19 passed, 87 deselected).
- *Deterministic:* synthetic SPY DataFrame seeded with `np.random.default_rng(42)`;
  no network calls, identical results every run.

### TIER 2 — IMPORTANT (should ship by 2026-04-30 if possible)

**I1. Structured logging + log retention** --- DONE 2026-04-16
- *File:* `core/logger.py` (stdlib only, no new deps)
- *Tests:* `tests/test_logger.py` — 19 tests (formatter, file creation,
  idempotency, redaction, robustness, retention, root propagation)
- *AC met:* JSON-line logs at `logs/YYYY-MM-DD.jsonl` with 14-day retention
  (configurable via `LOG_BACKUP_COUNT`), daily rotation at UTC midnight.
  Every record carries: `time`, `level`, `logger`, `message`, `event_type`
  plus arbitrary structured extras. `log_event(logger, "order_filled",
  order_id=..., symbol=..., pnl=...)` is the stable-schema helper.
- *Wired:* `configure_root_logging()` in `main.py` startup; all existing
  `logging.getLogger(__name__)` calls (core.monitor, core.fill_watcher,
  apscheduler, etc.) now emit JSON via root propagation — zero call-site
  changes required in those modules.
- *Security:* redacts any key named `api_key`, `api_secret`, `password`,
  `token`, `secret` before serialisation.

**I2. Daily summary email/webhook** --- DONE (2026-04-17)
- *File:* `core/notifier.py` (~220 LOC, stdlib only — uses urllib.request).
- ``build_daily_digest(journal)`` → dict with date, pnl, trades, win/loss, open positions.
- Payload auto-formatted for Discord (embeds), Slack (text), or generic JSON.
- ``send_webhook(url, payload)`` — best-effort POST; returns True on 2xx, False otherwise.
- ``send_daily_digest(journal, url=None)`` — reads ``SETTINGS.notify_webhook_url`` when
  url is omitted; skips (returns False) if URL is empty.
- APScheduler cron job registered at 21:05 UTC (16:05 ET) by ``/api/monitor/start``; removed by ``/api/monitor/stop``.
- Manual trigger: ``POST /api/notify/digest`` — returns ``{"sent": bool, "digest": {...}}``.
- Success writes a ``digest_sent`` event to the journal for audit.
- 28 tests in ``tests/test_notifier.py`` covering all branches.

**I3. Event calendar blackout** --- DONE 2026-04-15
- `config/events_2026.json` seeded with FOMC (8 dates), CPI (12 dates), NFP (12 dates).
- `core/calendar.py::in_blackout()` checks date against event windows.
- `core/risk.py::evaluate_pre_trade()` scans today through today+DTE for blackout events.
- Configurable window: `blackout_window_before=0`, `blackout_window_after=2` days.

**I4. Slippage / commission accounting in live journal** --- PARTIAL
- `core/fill_watcher.py::finalize_filled()` records commission from IBKR execReport.
- `core/journal.py` stores commission per order and per fill.
- EOD reconciliation report not yet implemented.

**I5. Idempotency keys on order submission** --- PARTIAL
- Schema: `orders.idempotency_key TEXT UNIQUE` with `ON CONFLICT` upsert.
- `get_order_by_idempotency(key)` method exists on Journal.
- `/api/ibkr/execute` generates `entry:{pos_id}` key per order.
- Scanner auto-execute path does not yet generate idempotency keys.

**I6. Monitor-loop lock / leader election** --- DONE 2026-04-16
- *File:* `core/leader.py` (stdlib, uses `fcntl.flock(LOCK_EX|LOCK_NB)`)
- *Tests:* `tests/test_leader.py` — 11 tests (acquire, idempotency, release,
  re-acquire, real-subprocess mutual exclusion, lock-file inspection,
  auto dir creation)
- *AC met:* `/api/monitor/start` calls `try_acquire_leadership("data/monitor.lock")`
  FIRST — if busy returns `{"status": "not_leader", "holder": {...}}` and
  registers no jobs. `_run_monitor_tick` and `_run_fill_reconcile` each
  short-circuit via `is_leader()` as a second line of defence. Lock
  metadata (pid, host, acquired_at) is written inside the lock file so
  peers can show WHO is the leader. Lock is released on
  `/api/monitor/stop`; kernel releases on process exit too.
- *Degradation:* Windows (no fcntl) falls back to "every instance is leader"
  with a single warning — the project is POSIX-first for now.
- *Observability:* `/api/ibkr/heartbeat` now returns `is_leader` and
  `leader_info`.

**I7. Reconnect-on-drop for IBKR** --- PARTIAL
- `IBKRTrader.ensure_connected()` reconnects if socket dropped.
- Every broker method calls `ensure_connected()` first.
- Missing: exponential backoff, reconnect event logging to journal.

**I8. Market hours + holiday calendar** --- PARTIAL
- `core/calendar.py::is_market_open()` uses `pandas_market_calendars` (NYSE)
  with fallback to simple 9:30-16:00 ET rule.
- `core/risk.py` checks market hours + min 5 min before close.
- Missing: early-close day support (1 PM days) is handled by `pandas_market_calendars`
  when installed, but not tested.

**I9. Backtest realism upgrades** --- DONE (2026-04-17)
- ``BacktestRequest.bid_ask_haircut: float = 0.0`` — new field, backward-compat default.
- Applied at entry: ``one_cc += |one_cc| * haircut`` → debit trades cost more, credit trades
  receive less.
- Applied at exit: ``cv_exit = cv - |cv| * haircut`` → closing value is always adversarially worse.
- ``spread_exit`` in trade records uses ``cv_exit`` (was raw ``cv``).
- 9 tests in ``tests/test_backtest_realism.py``: field defaults, backward compat (haircut=0 ≡ no haircut),
  monotone direction (higher haircut → lower PnL), endpoint 422-free.
- Optional real historical options prices (Polygon / Tradier) remains stretch / not implemented.

**I10. UI alerts for critical conditions** --- DONE (2026-04-16)
- `/api/ibkr/heartbeat` now returns `alerts: [{level, code, message}]` plus
  discrete flags: `daily_loss_warning`, `monitor_stalled`, `ibkr_dropped`,
  `monitor_last_tick_iso`, `monitor_seconds_since_tick`, `daily_loss_pct_used`.
- `_run_monitor_tick` records `_last_monitor_tick_iso` on every successful drive.
- Staleness threshold: 30s since last tick → critical alert.
- Daily-loss trip: ≥80% of configured cap → warning alert.
- Socket dropped (isConnected=False on a registered trader) → critical alert.
- 11 tests in `tests/test_heartbeat_alerts.py` covering all three alert classes.
- UI surface-area left for frontend to consume; API contract complete.

**I11. Snapshotting of prior scanner runs** --- DONE (2026-04-16)
- New `scanner_logs` table in `core/journal.py` with time/signal/price/rsi/msg/details.
- `Journal.record_scan_log()` + `Journal.list_scan_logs(limit=50)` API added.
- `main.run_market_scan()` writes every tick (success + error branches) to SQLite
  alongside the in-memory `scanner_state["logs"]` buffer.
- Server startup hydrates `scanner_state["logs"]` from the most recent 50 rows
  so post-restart the UI shows continuity.
- 11 tests in `tests/test_scanner_logs.py`: round-trip, ordering, limit, JSON
  storage shape, cross-instance persistence.

**I12. Stronger pytest coverage (target 60%+ on hot paths)** --- PARTIAL
- 158 tests across 13 test files, all passing.
- Covered: journal CRUD, risk checks (19 tests), monitor exits (14 tests),
  fill watcher FSM (17 tests), filters (9 tests), chain (6 tests), calendar (13 tests),
  backtest↔live reconcile (19 tests), structured logging (19 tests),
  leader lock (11 tests), heartbeat alerts (11 tests), scanner logs (11 tests).
- Missing: backtest engine parametrized tests (profit-target / trailing-stop paths),
  IBKR adapter mocked socket tests.

### TIER 3 — OPTIONAL (post-launch, nice-to-have)

**O1. Multi-symbol support** — SPY-only now; QQQ / IWM later.
**O2. Multi-strategy hot-swap** — the `upgrade/` plan ambitions. Revisit May.
**O3. WebSocket push to frontend** — replace 5s polling with WS.
**O4. Web UI trade journal browser** — currently CLI-only (SQLite).
**O5. Parameter optimizer → auto-deploy** — best-performing preset auto-promoted.
**O6. Hosted deployment** — Docker + systemd + nginx reverse proxy.
**O7. Telegram bot for remote flatten-all** — panic button from phone.
**O8. IV-rank filter** — instead of / alongside VIX-range filter.
**O9. Greek-based exit** — close on delta breach or theta decay rate.
**O10. Multi-account support** — sub-account routing.

---

## 6. Core Module Layout (IMPLEMENTED 2026-04-15)

```
spy_credit_spread/
├── core/
│   ├── __init__.py          # package init, lists all modules
│   ├── settings.py          # env-backed config (C10) — 171 LOC
│   ├── journal.py           # SQLite persistence (C1) — 509 LOC
│   ├── risk.py              # pre-trade risk + sizing (C3, C9) — 226 LOC
│   ├── chain.py             # live IBKR chain resolver (C4) — 322 LOC
│   ├── monitor.py           # exit lifecycle loop (C2) — 380 LOC
│   ├── fill_watcher.py      # fill reconciliation (C7) — 386 LOC
│   ├── filters.py           # shared filter logic (C8) — 153 LOC
│   ├── calendar.py          # market hours + event blackout — 151 LOC
│   ├── logger.py            # JSON structured logging (I1) — ~230 LOC
│   └── leader.py            # fcntl advisory lock (I6) — ~195 LOC
├── core/
│   ├── ...                  # (see above)
│   └── notifier.py          # daily digest webhook (I2) — ~220 LOC
├── strategies/              # unchanged — strategy logic frozen
│   ├── base.py
│   ├── consecutive_days.py
│   ├── combo_spread.py
│   └── builder.py
├── main.py                  # FastAPI routes + scheduler — ~1580 LOC
├── ibkr_trading.py          # IBKRTrader class — 326 LOC
├── paper_trading.py         # Alpaca paper adapter
├── config/
│   ├── .env.example
│   └── events_2026.json     # FOMC/CPI/NFP 2026
├── data/
│   └── trades.db            # SQLite (gitignored)
├── logs/                    # JSON log files (gitignored)
├── tests/
│   ├── conftest.py
│   ├── test_journal.py           # 6 tests
│   ├── test_risk.py              # 19 tests
│   ├── test_monitor.py           # 14 tests
│   ├── test_fill_watcher.py      # 17 tests
│   ├── test_filters.py           # 9 tests
│   ├── test_chain.py             # 6 tests
│   ├── test_calendar.py          # 13 tests
│   ├── test_reconcile.py         # 19 tests  (C12: signal/filter/sizing parity)
│   ├── test_logger.py            # 19 tests  (I1: JSON shape, retention, redaction)
│   ├── test_leader.py            # 11 tests  (I6: fcntl lock, mutual exclusion)
│   ├── test_scanner_logs.py      # 11 tests  (I11: scanner persistence)
│   ├── test_heartbeat_alerts.py  # 11 tests  (I10: daily-loss/stall/dropped alerts)
│   ├── test_notifier.py          # 28 tests  (I2: digest build, Slack/Discord format, HTTP POST)
│   └── test_backtest_realism.py  # 9 tests   (I9: bid_ask_haircut field + directional effect)
└── frontend/                # unchanged for MVP
```

195 tests passing (2 skipped) across 15 test files.
The backtest engine remains inline; a future refactor could extract it to
`core/engine.py` but this is not blocking for the 04-30 deadline.

---

## 7. AI-Assisted Development Plan (16-day sprint to 2026-04-30)

### Constraints you set

- Keep strategy logic unchanged
- Focus on bullish call spreads on SPY
- Don't use token-intensive skills (no brainstorming/TDD/multi-agent ceremony)
- Output to this file
- Must update this file after completeing a task to mark it complete
- generate a tree like structure at top to save time and track progress.

### How to actually use Claude through this

**Workflow per task (repeat for each C/I item):**
1. Ask Claude to implement the specific task with a one-paragraph prompt that:
   - names the target file
   - lists acceptance criteria from this doc
   - tells Claude which existing code it may freely read
2. Claude writes code + at least one pytest
3. Run `python3 -m pytest <new-test> -q` yourself
4. If green → `git add -p` → `git commit -m "feat(core): <item>"`
5. Move to next task

**Don't use:**
- Subagent ceremonies (superpowers:subagent-driven-development adds latency, not value, for this sized project)
- Multiple parallel code-review agents per commit
- Planner agents — this document *is* the plan

**Do use:**
- `Edit`/`Write` tools directly for code changes
- `Bash` to run pytest after each change
- `Grep` / `Read` to locate existing patterns before writing new ones
- **One** code-review pass on each Tier-1 item before committing

### Daily breakdown — 2026-04-14 to 2026-04-30

| Day | Date | Planned | Actual | Status |
|-----|------|---------|--------|--------|
| 1 | Tue 04-14 | Read plan, approve; C10 | C10 done; plan written | DONE |
| 2 | Wed 04-15 | C1 journal | C1-C11 core modules built + integrated into main.py | DONE (ahead of schedule) |
| 3 | Thu 04-16 | C4+C5 chain/bugs | **C12 reconcile test DONE** (19 tests, 106 total) | DONE |
| 4 | Fri 04-17 | C2 monitor tick | I1 structured logging + I10 UI alerts + I11 scanner snapshots | DONE (pulled in from 04-16) |
| 5 | Mon 04-20 | C2 exit conditions | Paper dry-run on TWS paper port | |
| 6 | Tue 04-21 | C3+C9 risk/sizing | Fix-forward from paper dry-run | |
| 7 | Wed 04-22 | C7+C8 fills/filters | I7 reconnect backoff + I6 leader election | |
| 8 | Thu 04-23 | C6+C11 flatten/hb | I2 daily digest webhook | |
| 9 | Fri 04-24 | C12 reconcile | Buffer / fix-forward | |
| 10 | Mon 04-27 | I7+I8 reconnect/hrs | 5-day paper trading run begins | |
| 11 | Tue 04-28 | I5+I1 idem/logging | Paper trading continues | |
| 12 | Wed 04-29 | Paper dry-run | Paper trading continues | |
| 13 | Thu 04-30 | Fix-forward + I10 | Review metrics; go/no-go for live | |

**Status as of 2026-04-17:** ALL Tier-1 (C1-C12) AND ALL planned Tier-2 items DONE.
I1 + I2 + I6 + I9 + I10 + I11 complete.
195 tests passing (2 skipped) across 15 files.
Next: paper dry-run on TWS paper port (Day 5, Mon 04-20).
Remaining optional work: I12 stronger coverage, I3-I8 partial items.

If you slip, **drop I-tier items first**, never Tier-1.

### Claude prompt templates (copy-paste for each task)

Example for C1:
```
Implement core/journal.py — a SQLite-backed trade journal.

Schema:
  positions(id TEXT PRIMARY KEY, symbol, topology, direction, contracts,
            entry_cost, entry_time, expiry, state, exit_cost, exit_time,
            exit_reason, realized_pnl, legs_json)
  orders(id TEXT PRIMARY KEY, position_id, broker, broker_order_id,
         side, limit_price, status, submitted_at, filled_at,
         fill_price, commission)
  fills(id INTEGER PRIMARY KEY, order_id, qty, price, time, exec_id)
  daily_pnl(date TEXT PRIMARY KEY, realized, trades, win_count, loss_count)

API:
  class Journal:
      def __init__(self, db_path: str = "data/trades.db"): ...
      def open_position(pos: Position) -> str: ...
      def record_order(order: Order) -> None: ...
      def record_fill(fill: Fill) -> None: ...
      def close_position(pos_id: str, exit_cost, reason, time): ...
      def list_open() -> list[Position]: ...
      def today_realized_pnl() -> float: ...
      def today_trade_count() -> int: ...

Constraints:
  - frozen dataclasses for Position, Order, Fill
  - use contextlib.contextmanager for connections
  - no raw SQL in callers — only through Journal methods

Tests in tests/test_journal.py:
  - test_open_and_close_roundtrip
  - test_restart_loads_open_positions
  - test_today_realized_pnl_excludes_open
```

### Code-review pass (run once per Tier-1 commit)

```
Review the diff against LIVE_TRADING_DEPLOYMENT_PLAN.md section 5 item <Cn>.
Check:
  1. Does it meet the acceptance criteria listed there?
  2. Any unchecked edge case? (market closed, partial fill, network drop)
  3. Money-loss footguns? (hardcoded values, missing units, sign errors)
  4. Does it degrade gracefully when IBKR is offline?
Keep review under 200 words. No style nits.
```

---

## 8. Pre-Flight Checklist (before first live order)

Check every box before flipping Live mode for the first time.

- [ ] `pip install -r requirements.txt` in fresh venv runs green
- [ ] `pytest -q` passes (all Tier-1 tests)
- [ ] `pytest -m reconcile` passes
- [ ] IBKR TWS paper account connected; can place and cancel test order
- [ ] `POST /api/ibkr/flatten_all` works on an empty portfolio (no crash)
- [ ] Place manual 1-contract bull call spread on paper → verify:
  - journal entry written
  - monitor tick picks it up
  - stop_loss=20% triggers close when simulated mtm drop breaches
- [ ] Kill TWS mid-position — server logs reconnect attempts, monitor
      does not fire spurious exits on stale data
- [ ] Trigger daily_loss_limit → next `/api/ibkr/execute` returns
      `{"error": "risk_rejected", "reason": "daily_loss_limit"}`
- [ ] Market-hours rejection works (test by faking time)
- [ ] Event blackout works (add a test event for tomorrow, verify reject)
- [ ] UI: red PANIC button is visible and functional only in Live mode
- [ ] Secrets: `.env` loaded, no key echoed in logs, no key in journal
- [ ] Run 5 full trading days in paper with real scanner → 0 orphaned orders,
      0 unhandled exceptions in `logs/`
- [ ] Only after ALL of the above: switch port from 7497 (paper) → 7496 (live)
      with **max_concurrent=1 and max_allocation_cap=$500** for the first week
- [ ] Set calendar reminder 2026-05-07 to review first-week metrics and
      decide whether to raise caps

---

## 9. Architecture Decisions Locked for This Sprint

| Decision | Value | Rationale |
|----------|-------|-----------|
| Symbol | SPY only | User's core strategy |
| Topology | vertical_spread bull_call only for live | Reduce surface area |
| Broker | IBKR for live, Alpaca for paper-equity surrogate | Alpaca doesn't support spreads |
| Persistence | SQLite (single file) | Zero-ops; adequate for single-user bot |
| Scheduler | APScheduler BackgroundScheduler (in-process) | Already in use; works for this scale |
| Monitor interval | 15s during RTH, stopped outside RTH | Balance between responsiveness and rate limits |
| Order type | LIMIT at live mid ± $0.05 haircut | Market orders on combos = bad fills |
| Fill timeout | 30s → cancel | Prevents ghost orders |
| Max concurrent positions | 2 (configurable) | First live week safety |
| Daily loss limit | 2% of equity (configurable) | Conservative default |
| Event blackout window | 0h before → 2h after event | FOMC especially causes chop |
| Sizing default | targeted_spread, 1% of equity, max $500 | First-week conservative |

---

## 10. What's NOT in This Sprint (explicit non-goals)

- Multi-strategy runtime hot-swap (in `upgrade/` plan — defer)
- Multi-leg topologies other than bull_call vertical (iron condors etc.) in live path — they exist in backtest already, stay there
- Rewriting strategy logic (user locked this)
- Multi-ticker support
- Web hosting / multi-user auth
- Historical options-price backtest upgrade (Polygon/Tradier)
- WebSocket push (polling is fine for this cadence)
- Docker / CI / CD
- Full React Router hub (existing SPA UI is sufficient)

Each of these is a good follow-up after you have two weeks of successful
live-paper and then live-small-size behind you.

---

## 11. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| IBKR TWS disconnect during monitor tick | High | High | reconnect logic (I7) + monitor skips tick rather than exiting |
| Backtest ≠ live divergence (filters/sizing) | Medium | High | C8 parity + C12 reconcile test |
| Orphaned orders from server crash | Medium | High | C1 journal + restart replay on startup |
| Chain resolver picks wrong expiry | Medium | High | C4 real chain + integration test with `reqSecDefOptParams` |
| Partial fill leaves naked leg | Low | Critical | C7 fill watcher + auto-cancel remainder |
| Daily loss spiral past limit | Low | High | C3 risk check enforces cap per tick |
| yfinance rate limit on scanner | Medium | Low | Replace daily-bar fetch with IBKR historical bars once IBKR connected |
| User forgets to update event calendar | Medium | Medium | Log warning if blackout file is > 30 days old |
| Scanner scheduler misses fire (laptop sleep) | High | Medium | Accept — document: "run on a server or disable sleep" |

---

## 12. Success Criteria

We call this sprint successful if on 2026-04-30:

1. The full pre-flight checklist passes.
2. Two consecutive paper-trading days in Live-mode-against-paper-port complete
   with zero unhandled exceptions, zero orphaned orders, and trade journal
   shows all entries and exits reconciled to IBKR execReports.
3. An artificial stop-loss test on a live paper position closes within two
   monitor ticks of breaching the threshold.
4. The panic flatten-all endpoint closes a 2-position paper portfolio in
   under 10 seconds end-to-end.
5. Reconcile test (C12) is green.

On 2026-05-01, you can flip the port from 7497 to 7496 with a $500 cap.

---

*End of plan. Next action: read this, approve or amend, then start on
C10 (.env secrets loading) — cheapest Tier-1 item, lets every subsequent
task stop passing creds around in request bodies.*
