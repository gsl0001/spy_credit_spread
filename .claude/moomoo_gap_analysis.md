# moomoo SDK — gap analysis & feature recommendations

Compiled: 2026-05-09.
Inputs: `.claude/moomoo_api_reference.md` (what the SDK exposes) + `.claude/moomoo_usage_inventory.md` (what we use).
Filter: only items that actually move the needle for an automated SPY 0DTE / short-DTE credit-spread system on a paper or live moomoo account.

---

## What the SDK gives us that we should be using

### A. Push-based execution callbacks  *(REAL only — not in paper)*
**SDK:** subclass `TradeOrderHandlerBase.on_recv_rsp` and `TradeDealHandlerBase.on_recv_rsp`, register via `set_handler`. Every order-status transition and every fill is pushed live.
**We have:** zero push handlers. We poll `order_list_query` every 0.5s in `_wait_for_fill_detail` and again in the fill watcher.
**Why it matters:** removes a class of latency (poll lag), removes API quota waste, gives us the canonical `deal_id` for fill dedup (we currently dedup on `exec_id` we hope is set). Also fixes the partial-fill timing window the reviewers flagged.
**Cost:** medium. Need a handler class, registration on connect, journal write into `fills` keyed on `deal_id`.
**Live-only caveat:** paper does not push deals. So we keep the polling path as a fallback when `trd_env == SIMULATE` and switch to push when `REAL`.

### B. Real STOP / STOP_LIMIT / TRAILING_STOP orders  *(REAL only)*
**SDK:** `OrderType.STOP`, `STOP_LIMIT`, `TRAILING_STOP`, `TRAILING_STOP_LIMIT` with `aux_price`, `trail_type`, `trail_value`, `trail_spread`.
**We have:** every stop/take-profit/trailing-stop is enforced client-side by our MTM monitor at 15s tick. If the server crashes, no protection.
**Why it matters:** broker-side stops survive process death, network partition, and reconnect. Right now a 15s monitor outage is an open risk window.
**Cost:** medium. New `place_stop` / `place_trailing` paths; per-position state to remember broker-side stop order ids. Only enabled when `REAL`. Can run alongside the MTM monitor as belt-and-suspenders.

### C. `OptionDataFilter` server-side chain filter
**SDK:** `get_option_chain(..., data_filter=OptionDataFilter(strike_min=..., delta_min=..., open_interest_min=..., ...))`.
**We have:** pull entire chain (~350 strikes) then filter in Python.
**Why it matters:** `get_option_chain` is rate-limited to **10 / 30s**. We hit this when scanning multiple expiries. Server-side filtering shrinks payload and saves a chain quote.
**Cost:** small. One-line argument addition in `MoomooTrader.get_option_chain` plumbing.

### D. Greeks + IV from existing snapshots
**SDK:** `get_market_snapshot` returns `delta`, `gamma`, `theta`, `vega`, `rho`, `option_implied_volatility`, `option_open_interest`. We're already calling this — we throw the columns away.
**We have:** zero greek-aware sizing or filtering.
**Why it matters:** lets us add a `delta_max` / `iv_max` chain-quality gate alongside the bid/ask % gate. Catches "the strike picker chose a 0.05-delta wing because the chain was sparse" failure mode. Also the proper way to dynamically size based on portfolio delta exposure.
**Cost:** small. Plumb columns from snapshot DataFrame through the existing chain-quality gate; one extra preset knob.

### E. `Session` parameter on `place_order`
**SDK:** `Session.RTH | ETH | OVERNIGHT | ALL` (US). `fill_outside_rth` is deprecated.
**We have:** default (RTH-only). 0DTE bot tries to flatten after 16:00 ET and silently can't.
**Why it matters:** lets us enable extended-hours flattens. Recall the Thursday afternoon mess — the close orders queued at 23:00 ET sat pending until 09:30 ET because we didn't pass `session=ALL`. With ETH a flatten on a partial leg has a fighting chance after the regular close.
**Cost:** small. One parameter on the close paths.

### F. `OrderStatus` constants (instead of string match)
**SDK:** stable enum.
**We have:** string compare against `"FILLED_ALL"`, `"CANCELLED_ALL"`, etc. Brittle if SDK ever changes wording.
**Why it matters:** correctness — current code already enumerates the canonical strings inside `_wait_for_fill_detail`. Replacing them with `ft.OrderStatus.X` makes the dependency explicit and breaks loudly if the SDK changes.
**Cost:** trivial.

### G. `get_option_expiration_date(code)` instead of calendar math
**SDK:** authoritative list of valid US-options expiries for a symbol.
**We have:** `today + timedelta(days=target_dte)` then walk forward over weekends. Doesn't know about market holidays (Memorial Day, July 4th, Thanksgiving, etc.).
**Why it matters:** on holiday-adjacent days the picker still resolves to a non-trading-day expiry and falls back. SPY does have Mon/Wed/Fri 0DTEs but holiday weeks remove some.
**Cost:** trivial. Replace the walk-forward loop with one SDK call + cache.

### H. Push-based quote subscriptions for active positions
**SDK:** `subscribe(option_codes, [SubType.QUOTE])` + `StockQuoteHandlerBase.on_recv_rsp`.
**We have:** `get_market_snapshot` polled in `get_spread_mid` every monitor tick.
**Why it matters:** for the MTM monitor, push quotes mean stop-loss reacts in <1s instead of up-to-15s. Also a hard cap on snapshot quota: 60/30s shared across the whole app — the monitor uses all of it on busy days.
**Cost:** medium. Subscribe on position-open, unsubscribe on close, plumb into the existing `_process_position_moomoo` path.

### I. Margin-paper US accounts (v10.2+)
**SDK:** new in March 2026. We're on cash-paper today (`buying_power=$2M` after the loss but `cash=$971K` — accurate). Margin paper exposes the **same options margin** rules as REAL, including spread net-margin reduction.
**Why it matters:** today our paper run has unrealistic BP for short-leg margin. A bull-call spread reserves the full debit, but a credit spread (bear-call etc., which we don't trade today but might) reserves max-loss × contracts × 100. Margin paper lets us validate sizing math against real margin numbers before going live.
**Cost:** zero — flip the OpenD account type via the moomoo desktop app.

### J. `position_id` on `place_order` for server-side close-by-position
**SDK:** if you pass `position_id` from a `position_list_query` row, the order is bound to that position (helps with FIFO cost-basis on closes).
**We have:** order legs are independent of any tracked position.
**Why it matters:** closes against a specific lot give cleaner P&L attribution and prevent partial-leg orphaning when multiple legs of the same strike exist.
**Cost:** small. Plumb `position_id` from `position_list_query` into the close paths.

---

## What the SDK does NOT give us (don't waste effort)

- **Atomic combo / multi-leg orders.** Confirmed not in v10.5 SDK. Spreads will remain sequentially legged. Best mitigation is what we already shipped: pre-flight quote check + marketable-limit flatten + journal-as-orphan if leg2 fails.
- **Exercise / assignment APIs.** Not in SDK. We have to monitor expiry day positions ourselves and rely on broker auto-exercise.
- **Strategy templates.** Not in SDK.
- **Direct P&L push.** Derive from order/deal pushes + quote pushes.

---

## Top 7 features to add (prioritized)

| # | Feature | Effort | Live-only | Why |
|---|---|---|---|---|
| 1 | **Push-based fill handler (B/A combined)** subclassing `TradeDealHandlerBase` + `TradeOrderHandlerBase` | M | yes (paper polls) | Eliminates poll latency, gives us authoritative `deal_id` for fill dedup, frees rate quota |
| 2 | **Server-side STOP / TRAILING_STOP** on every position via `OrderType.STOP_LIMIT` / `TRAILING_STOP_LIMIT` | M | yes | Survives server crashes; removes the 15s exposure window |
| 3 | **`Session=ALL` on flatten paths** | S | partial (data feed extended-hours requires entitlement) | Closes work after 16:00 ET; catches Thursday-style afterhours flattens |
| 4 | **Greeks-aware chain-quality gate** using snapshot's `delta` and `option_open_interest` columns we already fetch | S | no | Catches sparse-chain mispicks before they hit `place_order` |
| 5 | **`get_option_expiration_date` for accurate target_dte resolution** | S | no | Holiday-aware; eliminates the weekend-walk hack |
| 6 | **Quote-push subscriptions for active positions** | M | no | <1s MTM stop reaction; conserves snapshot quota |
| 7 | **`OptionDataFilter` server-side chain filter** | S | no | Stops chain quota exhaustion when scanning multiple expiries |

---

## Operational / non-SDK improvements (carry-overs from earlier review)

These aren't SDK features but came up in the same investigation and matter for correctness:

- **API auth on every state-mutating endpoint** — covered in the earlier review.
- **Symbol allowlist + qty cap on `/api/moomoo/close_one`** — same.
- **Schema migration table** — `data/trades.db` has no version column.
- **Daily WAL checkpoint + backup of `data/trades.db`** — single file, no rotation.
- **Central exception sanitizer** — moomoo SDK error messages can include the trade PIN; sanitize before they reach `journal.log_event` or HTTP responses.

---

## Files
- `.claude/moomoo_api_reference.md` — full API surface
- `.claude/moomoo_usage_inventory.md` — what we currently use
- `.claude/moomoo_gap_analysis.md` — this document
