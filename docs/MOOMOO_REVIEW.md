# Moomoo Trading Subsystem — Multi-Agent Review

Date: 2026-05-09
Reviewers: architect, python-reviewer, security-reviewer, database-reviewer
Scope: every code path touching moomoo OpenD — broker adapter, FastAPI endpoints, journal, strategies, telegram bot, frontend.

---

## TL;DR

The system places real-money paper/live orders against moomoo OpenD with **zero authentication on any endpoint**, has a **structurally unsafe spread leg flow** (sequential limit orders with naked-long window between leg1 fill and leg2 placement), and is **silently exposing the trade-PIN hash and `bypass_event_blackout` flag** through the public API. There is no MTM monitor parity with IBKR, no server-side flatten kill-switch tied to the journal, and the new claim-on-entry dedup uses a `LIKE '%...%'` scan that won't survive a week of trading volume.

The week of fixes (sizing cap, per-bar idempotency, orphan journaling, telegram overlap, picker weekend-skip) closed the loudest bugs. The reviewers found **24 more** spanning architecture, code quality, security, and persistence.

---

## CRITICAL — fix before any further runs

### C1. No authentication on any broker-mutating endpoint
`main.py` — every `@app.post("/api/moomoo/*")`, `/api/connection/auto`, `/api/scanner/preset/*`.
Any local process can place orders, flatten, disable the IBKR/moomoo toggles, or stop the scanner. The whole platform assumes localhost is trusted; if the port leaks (VPN, ngrok, dev tunnel, accidental bind to 0.0.0.0) every endpoint is wide open.
**Fix:** generate a server secret at startup, require `X-Api-Key` on every state-mutating route. Two-tier: read token vs. kill-switch token.

### C2. `bypass_event_blackout` is a public request field
`main.py:2438` (`MoomooOrderRequest.bypass_event_blackout`). Caller can flip the news-blackout gate from the wire. The flag was added for smoke testing and never moved server-side.
**Fix:** delete from request schema; gate test overrides on a server env var only.

### C3. Spread legs are placed sequentially with no atomic guarantee
`brokers/moomoo_trading.py:623-673` (`place_spread`). Between leg1 fill and leg2 placement the account is naked-long; if leg2 fails, the flatten is a market order with no price guard. This is the structural source of the 22-spread / $8K incident.
**Fix:** prefer moomoo's combo/strategy order if available for the firm; otherwise (a) write `Position` to journal in `pending` state before leg1, (b) bound flatten with a marketable-limit (NBBO + buffer), (c) add a hard global circuit-breaker that on any leg2 failure halts new entries until manual ack.

### C4. `LIKE '%client_order_id%'` dedup is a full table scan
`main.py:2887-2890` — the claim-on-entry dedup I added yesterday. Leading-wildcard, no index, on the order-entry critical path. Wrapped in `except: pass` (`main.py:2900`) so a query failure silently skips the dedup → duplicate orders re-fire.
**Fix:** add a `claim_key TEXT` column on `events` with a UNIQUE index, write `client_order_id` there explicitly, lookup by equality. Stop the bare-except.

### C5. Sequential `asyncio.get_event_loop()` calls inside coroutines
`brokers/moomoo_trading.py:130, 171, 497, 534, 749, 812, 835, 856, 875, 901, 955` and `strategies/order_flow_0dte.py:213, 256, 375, 469, 505`. Deprecated since 3.10, raises `RuntimeError` on 3.12+ when called from a worker thread without a loop on it.
**Fix:** mechanical replace with `asyncio.get_running_loop()` inside every `async def`.

### C6. Race on `_active_position` in 0DTE bot
`strategies/order_flow_0dte.py:395-456`. `_execute_signal` mutates a `@dataclass` (not frozen) across `await` boundaries. `_monitor_position` runs on the same loop and can observe a half-initialized `ActivePosition` (entry_price/stop/target all 0.0) → instant false stop-out.
**Fix:** construct one fully-populated immutable record post-fill and assign atomically.

### C7. `/api/moomoo/close_one` accepts arbitrary `code` and `qty`
`main.py:2630-2658` — added yesterday for the manual flatten loop. `req: dict` (untyped), no symbol allowlist, no qty cap, no ownership check. With C1, this is an unauthenticated arbitrary-market-order endpoint.
**Fix:** Pydantic model + regex on `code` + qty cap + auth.

---

## HIGH

### H1. `_wait_for_fill` ignores partial fills
`brokers/moomoo_trading.py:834-854`. Only treats `FILLED_ALL` as a fill; `FILLED_PART` is treated as not-filled and triggers `cancel_order`, which only cancels the remaining qty — leaving an orphan partial fill no one knows about.
**Fix:** branch on `FILLED_PART`, journal what filled, cancel remainder, decide flatten policy.

### H2. `_cancel_order_sync` discards return code
`brokers/moomoo_trading.py:861-864`. `modify_order` returns `(ret, data)` but the code ignores both. A failed cancel silently leaves an orphan long open after a leg2 timeout.
**Fix:** unpack and warn on `ret != ft.RET_OK`.

### H3. Trade PIN on the wire and in error messages
`brokers/moomoo_trading.py:249` and `main.py:2469`. `unlock_trade` exception bubbles `data` (which can include the attempted credential) into the JSON response. PIN itself is accepted in every connect/execute body in plaintext over HTTP.
**Fix:** PIN comes from server env only, never wire-accepted; sanitize exception strings before raising.

### H4. Telegram `/flatten confirm` has no replay protection
`core/telegram_bot.py:427-503`. Two-step confirm flow doesn't track acted-upon `update_id`s. Telegram can re-deliver during network instability; an old `/flatten confirm` re-executes.
**Fix:** persist a TTL set of acted-upon update_ids; never reset polling offset in production.

### H5. `daily_pnl` is a sync `@property` doing network IO
`brokers/moomoo_trading.py:992-1006`. Blocks the event loop on every read. Called from async handlers and the monitor loop.
**Fix:** convert to `async def get_daily_pnl()` with `run_in_executor`, plus a periodic background refresh that caches.

### H6. `_moomoo_trader` global replaced without disconnecting old instance
`main.py:2452-2469`. Reconnect path orphans the previous `MoomooTrader`, leaking its `_trd_ctx` / `_quote_ctx` sockets to OpenD.
**Fix:** `if _moomoo_trader: _moomoo_trader.disconnect()` before assignment.

### H7. `_moomoo_instances` dict mutated without a lock
`brokers/moomoo_trading.py:1027-1061`. Concurrent `/api/moomoo/connect` calls can construct two `MoomooTrader` instances and orphan one.
**Fix:** wrap mutation in `asyncio.Lock`.

### H8. `update_position` allows arbitrary state transitions
`core/journal.py:244-258`. No state-machine validation; state can drift `pending → closed` without a fill ever recorded.
**Fix:** validate transitions; emit a state-change event row.

### H9. `fills.exec_id` has no UNIQUE constraint
`core/journal.py` schema. A duplicate fill webhook (broker retry, reconnect) inserts a second row — double-counts P&L and commission.
**Fix:** unique partial index + `INSERT OR IGNORE`.

### H10. `/api/connection/auto` disables safety toggles without auth
`main.py:1599-1649`. Local attacker can POST `{"broker":"moomoo","enabled":false}` mid-trade to prevent stops/exits.
**Fix:** auth + `chmod 0600` on `data/connection_flags.json`.

### H11. Schedule reconnect uses `asyncio.get_event_loop()` from APScheduler thread
`brokers/moomoo_trading.py:419`. APScheduler workers don't have a loop set; this either raises or creates a detached loop, leaking reconnect tasks.
**Fix:** capture the FastAPI loop at startup, pass explicitly, or `run_coroutine_threadsafe`.

---

## MEDIUM

### M1. `_option_code` hardcodes "SPY"
`brokers/moomoo_trading.py:814, 878`. Class is generic but only works for SPY today; non-SPY would silently produce wrong codes.
**Fix:** thread `req.symbol` through, or document SPY-only.

### M2. N+1 query in `daily_reconciliation_report`
`core/journal.py:583-600`. One `SELECT SUM(commission)` per closed position. Replace with a single `LEFT JOIN ... GROUP BY`.

### M3. Idempotency key can be pinned by caller
`main.py:2433`. Caller-supplied `client_order_id` lets an attacker pre-claim a key and suppress a real submission.
**Fix:** server always generates the key; ignore caller value in production paths.

### M4. UTC-vs-ET date drift in 0DTE expiry
`strategies/order_flow_0dte.py:94-97`. `dt.strftime("%y%m%d")` on UTC datetime can produce wrong expiry pre-market.
**Fix:** convert to `America/New_York` before formatting.

### M5. `daily_pnl` rollup drift
`core/journal.py`. `close_position` bumps `daily_pnl`, but `update_position(state='closed')` does not. Manual reconcile paths produce a wrong rollup.
**Fix:** centralize close logic, or add a trigger / verify step.

### M6. `dynamic` sizing returns 1 contract on tiny budget
`core/risk.py:160-220`. `max(1, floor(budget/risk))` hides config errors — if you can't afford one, you shouldn't get one.
**Fix:** return 0; let the caller reject.

### M7. `connection_flags._load()` fail-open
`core/connection_flags.py:44`. Corrupt JSON warns and returns defaults (`{ibkr: True, moomoo: True}`). A bad write can re-enable a deliberately-disabled broker.
**Fix:** fail closed (default both False).

### M8. `RISK_FREE_RATE = 0.053` hardcoded
`core/chain.py:562`. Inside a function body, invisible to callers, will silently diverge from reality.
**Fix:** move to `core/settings.py`.

### M9. `update_position` builds SQL via f-string
`core/journal.py:244-258`. Currently safe (allowed-set guard), but one careless field rename away from injection.
**Fix:** generate the `{col} = ?` template from the static `allowed` set, not from caller keys.

### M10. Preset name from Telegram passed unsanitized to scanner
`core/telegram_bot.py:572-581`. If the preset loader does any FS lookup with the name, `../` traversal is possible.
**Fix:** validate against `^[\w\- ]+$`.

---

## LOW / Nitpicks

- `core/chain.py:378` — `datetime.utcnow()` deprecated; use `datetime.now(timezone.utc)`.
- `strategies/order_flow_0dte.py:30-32` — `sys.path` mutation at import time.
- `brokers/moomoo_trading.py:992` — `except Exception: return 0.0` in `daily_pnl` swallows everything silently.
- `core/telegram_bot.py:517` — `acc_id` published to Telegram chat unmasked.
- `_LEG_TIMEOUT_S = 30.0` and `_POLL_INTERVAL_S = 0.5` (`brokers/moomoo_trading.py:38-39`) hardcoded module constants.
- Status mapping in `_wait_for_fill` non-exhaustive (FAILED, DISABLED, DELETED fall through as raw strings).
- `today_realized_pnl` uses local-date while broker reports UTC — boundary bug for daily-loss circuit.
- `_reconnect_attempt` only resets on success — backoff sticks at 60s after temporary blips.
- 0DTE bot lacks type annotations on `_main_loop`, `_execute_signal`, `_monitor_position`, `_close_position`.

---

## Missing must-have features

For a moomoo options trading system that aspires to production:

1. **Position MTM monitor for moomoo** — IBKR has one, moomoo doesn't. `get_spread_mid` exists but no loop applies stop / take-profit / trailing / time-exit. **This is the single largest IBKR-parity gap.**
2. **Server-side flatten-all kill-switch** tied to journal, not just the broker-direct version we added. With auth.
3. **Boot-time + post-reconnect reconciler** — diff `journal.list_open()` against `MoomooTrader.get_positions()` and `get_active_orders()`; auto-mark orphans; re-stitch missing fills.
4. **Pre-trade NBBO sanity inside `place_spread`** — re-fetch `get_spread_mid` + `validate_spread_quality` immediately before leg1, so a stale signal-time snapshot doesn't push a $0.00-bid leg.
5. **BP recheck between leg1 fill and leg2 submission** — leg1 cash leaves the account; leg2 may now exceed BP.
6. **Atomic claim+ack inside `place_spread`** — move the idempotency claim into the broker adapter so any caller path (manual UI button, telegram, smoke test) is safe.
7. **Order rate-limiter** — a global "max N orders/minute" cap distinct from concurrent-positions. Would have bounded the 22-spread blast radius.
8. **Slippage / commission attribution on fills** — currently `commission=0.0` hardcoded.
9. **Health watchdog** — `_last_healthy_iso` is recorded but no scheduled task alerts on staleness > N minutes during RTH.
10. **Schema version table + migration path** — `CREATE TABLE IF NOT EXISTS` + ad-hoc ALTERs is fragile.
11. **Daily WAL checkpoint + DB backup** — `data/trades.db` is single-file, no backup, no checkpoint schedule.
12. **Audit log for every order** — `log_event` exists but isn't consistently called at every state transition inside `place_spread`.
13. **Symbol allowlist** for all order endpoints (post-Finding C7).
14. **TLS even on localhost** — Telegram PIN and credentials shouldn't ride plaintext.

---

## Top 7 fix-now (by ROI)

| # | Effort | Impact | Item |
|---|---|---|---|
| 1 | M | CRITICAL | Add API auth (C1) + remove `bypass_event_blackout` from public schema (C2) |
| 2 | S | CRITICAL | Replace LIKE-scan dedup with indexed `claim_key` column (C4) |
| 3 | S | HIGH | Mechanical `get_event_loop()` → `get_running_loop()` (C5) |
| 4 | M | CRITICAL | Type + validate + auth `/api/moomoo/close_one` (C7) |
| 5 | L | HIGH | Build moomoo MTM monitor + kill-switch parity with IBKR (must-have #1+#2) |
| 6 | S | HIGH | Fix `_active_position` race in 0DTE bot (C6) |
| 7 | S | MEDIUM | UNIQUE on `fills.exec_id` + `INSERT OR IGNORE` (H9) |

---

## Index DDL recommended

```sql
-- Fix LIKE-scan claim-dedup (after refactoring to claim_key column)
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_claim_key
    ON events(claim_key) WHERE claim_key IS NOT NULL;

-- broker_order_id reconciliation hot path
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_broker_order_id
    ON orders(broker, broker_order_id) WHERE broker_order_id IS NOT NULL;

-- list_orders_by_status + fill-watcher
CREATE INDEX IF NOT EXISTS idx_orders_status_kind
    ON orders(status, kind);

-- Prevent double-counted fills
CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_exec_id
    ON fills(exec_id) WHERE exec_id IS NOT NULL;

-- daily_reconciliation_report perf
CREATE INDEX IF NOT EXISTS idx_positions_state_exit_time
    ON positions(state, exit_time) WHERE state = 'closed';
```
