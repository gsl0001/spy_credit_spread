# moomoo SDK — current usage inventory

Compiled: 2026-05-09. Read-only audit of every moomoo SDK symbol the codebase touches.

## Quote-context methods we use

| Method | Where | What it gives us |
|---|---|---|
| `get_market_snapshot(codes)` | `brokers/moomoo_trading.py` (preflight, chain quote build, get_spread_mid), `nautilus/.../data.py` | bid/ask/last/volume/OI per option code |
| `get_option_chain(code, start, end)` | `brokers/moomoo_trading.py`, nautilus data client | chain metadata (strike/right/expiry) — we then snapshot for live quotes |
| `get_rt_ticker(code, num)` | `strategies/order_flow_0dte.py` | tick-level prints for the 0DTE order-flow signals |
| `request_history_kline(...)` | `brokers/moomoo_trading.get_historical_bars` | OHLCV |
| `subscribe / unsubscribe` | nautilus data client only | push subscriptions |
| `close()` | broker on disconnect | cleanup |

**Push handlers:** none. The whole stack polls.

## Trade-context methods we use

| Method | Where | What it gives us |
|---|---|---|
| `place_order(...)` | broker `_place_single_leg` / `_place_market_close` / `_marketable_limit_close`; 0DTE bot; `/api/moomoo/close_one`; `/api/moomoo/flatten_broker` | every order we send |
| `order_list_query(...)` | broker `_wait_for_fill_detail` / `get_order_status` / `get_active_orders` | poll order status |
| `position_list_query(...)` | broker `get_positions`; reconciler; flatten_broker | broker-truth positions |
| `accinfo_query(...)` | broker `get_account_summary` / `daily_pnl` | equity / BP / cash / P&L |
| `modify_order(... CANCEL)` | broker `_cancel_order_sync` | cancel-only (no NORMAL modify) |
| `unlock_trade(pwd)` | broker `connect` (REAL only) | authenticate trading |
| `get_acc_list()` | broker `connect` + `probe` | discover accounts |
| `history_deal_list(...)` | nautilus exec client | historical fills |
| `close()` | broker on disconnect | cleanup |

## Enums / constants used

- **Return:** `RET_OK`
- **Side:** `TrdSide.BUY/SELL`
- **OrderType:** `NORMAL` (limit) and `MARKET` only
- **TrdEnv:** `REAL`, `SIMULATE`
- **TrdMarket:** `NONE`, `US`
- **SecurityFirm:** `NONE`, `FUTUINC`
- **OrderStatus:** `SUBMITTING`, `SUBMITTED`, `WAITING_SUBMIT`, `FILLED_PART`, `FILLED_ALL` (cancel/fail terminals only checked by string match in `_wait_for_fill_detail`)
- **ModifyOrderOp:** `CANCEL` only
- **KLType:** `K_DAY`, `K_WEEK`, `K_MON`, `K_1M`, `K_5M`, `K_15M`, `K_60M`
- **SubType:** `K_5M`, `QUOTE` (nautilus only)

## Order types we actually send

1. **Single-leg limit BUY/SELL** — every spread leg (`OrderType.NORMAL`)
2. **Single-leg market** — flatten_broker, close_one, leg2-timeout flatten, 0DTE close
3. **Marketable-limit** — bid−$0.05 sell-to-close on partial-leg cleanup (recently added)

## Things we never touch

- `STOP / STOP_LIMIT / TRAILING_STOP / TRAILING_STOP_LIMIT / MIT / LIT` order types (REAL-only on US — wouldn't help on SIMULATE anyway)
- `time_in_force` (we're DAY-only by default)
- `Session` parameter — `extended_time` / pre/post / overnight
- `aux_price`, `trail_type`, `trail_value`, `trail_spread`
- Push handlers: `TradeOrderHandlerBase`, `TradeDealHandlerBase`, `StockQuoteHandlerBase`, `OrderBookHandlerBase`, `TickerHandlerBase`, `RTDataHandlerBase`
- `subscribe(... session=Session.OVERNIGHT)` — extended-hours data
- `OptionDataFilter` (chain server-side filter — we pull everything then filter client-side)
- `get_option_expiration_date` (we compute calendar dates ourselves)
- `acc_cash_flow_query`, `history_order_list_query`, `order_fee_query`
- Greeks / IV from snapshots (returned by SDK but we ignore those columns)
- `position_id` chaining on `place_order` (server-side OCO/closes)
- `is_detailed_orderbook=True` on subscribe (level-2)
- Persistent OpenD reconnect via the SDK; we manage our own retry backoff

## Version

Pinned: `moomoo-api` (no version constraint in `requirements.txt`).
Code comment requires OpenD ≥ 8.3.
