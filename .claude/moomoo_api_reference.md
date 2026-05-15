# moomoo / Futu OpenAPI — Python SDK reference for US options automation

Compiled: 2026-05-09
SDK target: `moomoo-api` on PyPI (formerly `futu-api`)
Doc set: v10.5 (May 2026)
OpenD: 8.3+ required (project has been testing on this)

All Python imports below assume `import moomoo as ft` (the namespace `futu` is identical and still ships).

---

## 1. Connection / lifecycle
- `OpenSecTradeContext(filter_trdmarket, host='127.0.0.1', port=11111, security_firm, is_encrypt=None)` — used for stocks **and stock/index options**. `OpenFutureTradeContext` is separate; `OpenCryptoTradeContext` is separate and has no SIMULATE.
- `OpenQuoteContext(host, port)` — single context for all market data.
- Enums (`moomoo.common.constant`):
  - `TrdMarket.US | HK | CN | SG | JP | HKCC | FUTURES | …`
  - `SecurityFirm.FUTUSECURITIES | FUTUINC | FUTUSG | MOOMOOMY | MOOMOOSG | MOOMOOJP | MOOMOOAU | MOOMOOCA`. For US options on a US account use `FUTUINC` or the moomoo-US firm constant.
  - `TrdEnv.REAL | SIMULATE`
- Lifecycle: `unlock_trade(password, password_md5, is_unlock=True)` is **required for REAL** trading; SIMULATE does not require unlock. `close()` every context, otherwise OpenD connection slots leak.
- Account selection: `get_acc_list()` → pass `acc_id=` or `acc_index=` to every trade call. Paper accounts are flagged `trd_env=SIMULATE` in the list.
- v10.1 disabled the OpenD GUI unlock; production must use the headless OpenD binary.

## 2. Market data
- `subscribe(code_list, subtype_list, is_first_push=True, subscribe_push=True, is_detailed_orderbook=False, extended_time=False, session=Session.NONE)`. `extended_time=True` enables US pre/post; `session=Session.OVERNIGHT` for 24h US sessions where entitled.
- `SubType`: `QUOTE, ORDER_BOOK, TICKER, RT_DATA, K_DAY/K_1M/…, BROKER`. Push handlers (subclass and `set_handler`): `StockQuoteHandlerBase`, `OrderBookHandlerBase`, `TickerHandlerBase`, `RTDataHandlerBase`, `BrokerHandlerBase`, `CurKlineHandlerBase`.
- Snapshots / queries:
  - `get_market_snapshot(code_list)` — 60 req / 30s; returns option-specific fields (delta, gamma, theta, vega, rho, IV, OI, strike, expiry, contract_size, contract_multiplier).
  - `get_option_expiration_date(code, index_option_type=IndexOptionType.NORMAL)`.
  - `get_option_chain(code, index_option_type, start, end, option_type=OptionType.ALL, option_cond_type=OptionCondType.ALL, data_filter=OptionDataFilter)` — **10 req / 30s**, ≤30-day window, returns static metadata only; you must `subscribe(QUOTE)` returned codes for live IV/Greeks.
  - `get_stock_quote(code_list)` (post-subscribe), `get_order_book`, `get_rt_ticker`, `get_cur_kline`.
- Level-2 US quotes: free LV1 globally; LV2/LV3 by entitlement or promo. SIMULATE accounts share live market data with REAL (data is real, only fills are simulated).

## 3. Order placement
`place_order(price, qty, code, trd_side, order_type=OrderType.NORMAL, adjust_limit=0, trd_env=TrdEnv.REAL, acc_id=0, acc_index=0, remark=None, time_in_force=TimeInForce.DAY, fill_outside_rth=False, aux_price=None, trail_type=None, trail_value=None, trail_spread=None, session=Session.NONE, jp_acc_type=…, position_id=None)`

- `OrderType`: `NORMAL` (limit), `MARKET`, `ABSOLUTE_LIMIT`, `AUCTION`, `AUCTION_LIMIT`, `SPECIAL_LIMIT` (HK), `STOP`, `STOP_LIMIT`, `MARKET_IF_TOUCHED`, `LIMIT_IF_TOUCHED`, `TRAILING_STOP`, `TRAILING_STOP_LIMIT`, plus query-only `TWAP/VWAP` variants.
  - **US options REAL**: NORMAL, MARKET, STOP, STOP_LIMIT, MIT, LIT, TRAILING_STOP, TRAILING_STOP_LIMIT
  - **US options SIMULATE/PAPER**: NORMAL + MARKET only.
- `TimeInForce`: `DAY`, `GTC` (max 90 days), `IOC` (crypto-market-only per docs; not for US options). No FOK/GTD exposed in v10.
- `Session`: `NONE, RTH, ETH, OVERNIGHT, ALL` (US equities/options). `fill_outside_rth` is **deprecated** in favor of `session`.
- `TrailType`: `RATIO | AMOUNT`; combine with `trail_value` (and `trail_spread` for STOP_LIMIT trail).
- `aux_price` = trigger price for STOP/MIT/LIT.
- **Multi-leg / combo / vertical-spread orders are NOT exposed in the SDK** as of v10.5. Combined-position queries are read-only and "combined order queries are not yet available." You must leg in/out separately and manage margin yourself.
- Rate limit: 15 req / 30s per `acc_id`, ≥0.02s between consecutive calls.

## 4. Modify / cancel
`modify_order(modify_order_op, order_id, qty, price, adjust_limit=0, trd_env, acc_id, acc_index, aux_price=None, trail_type=None, trail_value=None, trail_spread=None)`.
- `ModifyOrderOp`: `NORMAL` (price/qty change), `CANCEL`, `DISABLE`, `ENABLE`, `DELETE`. **US (incl. options) only supports NORMAL + CANCEL.**
- Partial cancel is not supported on US; reduce qty via NORMAL modify instead. Trailing-stop fields can be modified.

## 5. Position / portfolio
- `position_list_query(code='', pl_ratio_min=None, pl_ratio_max=None, trd_env, acc_id, acc_index, refresh_cache=False, position_market=PositionMarket.NONE)` — includes `position_side`, `qty`, `can_sell_qty`, `cost_price`, `pl_val`, `pl_ratio`, `today_pl_val`, option Greeks.
- `accinfo_query(trd_env, acc_id, acc_index, refresh_cache, currency=Currency.USD)` — `power` (buying power), `max_power_short`, `cash`, `market_val`, `available_funds`, `unrealized_pl`, `realized_pl`, `total_assets`, margin call fields.
- `acc_cash_flow_query(clearing_date, ...)` — REAL only; **paper does not support cash-flow queries**.
- `history_order_list_query`, `history_deal_list_query`, `order_fee_query` — **paper does not support deals or fees**; only open/today orders work.
- Real-time P&L: derive from position push (subscribe via order/deal handlers) + quote push; no direct P&L push API.

## 6. Order push / execution callbacks
- `TradeOrderHandlerBase.on_recv_rsp(rsp_pb)` — every order-status transition.
- `TradeDealHandlerBase.on_recv_rsp(rsp_pb)` — every fill. **REAL only**: paper does not push deals and does not support `deal_list_query` / historical deals.
- Dedup field is `deal_id` (per-fill exchange ID). `order_id` groups deals, `order_id_ex` handles re-issued IDs after broker side moves. Pushes are not strictly monotonic across reconnects — dedupe on `(deal_id, order_id, update_time)`.
- `OrderStatus`: `WAITING_SUBMIT, SUBMITTING, SUBMITTED, FILLED_PART, FILLED_ALL, CANCELLING_PART, CANCELLING_ALL, CANCELLED_PART, CANCELLED_ALL, FAILED, DISABLED, DELETED`.

## 7. Options-specific
- Greeks + IV are returned in `get_market_snapshot` and pushed in `StockQuote` updates after subscribing the option code. No separate IV-surface API; you must build it from chain + snapshots.
- `get_option_expiration_date` + `get_option_chain` cover discovery; `OptionDataFilter` lets you bound by strike/IV/delta/OI server-side (saves chain-fetch quota).
- **No exercise / assignment / strategy-template API**. Multi-leg margin must be computed client-side; combined-position view is read-only.
- Index options: pass `index_option_type=IndexOptionType.NORMAL|SMALL` (HK only — irrelevant for US SPY/SPX).

## 8. Rate limits / concurrency
- `get_market_snapshot`: 60 / 30s. `get_option_chain`: 10 / 30s, 30-day window. `place_order`/`modify_order`: 15 / 30s per acc_id, ≥20 ms apart. Subscriptions: per-quota system, 100 / 300 / 1000–2000 codes by tier (assets, monthly volume); SF-authorized users capped at 50 simultaneous order-book/broker subs.
- One OpenD instance, multiple contexts allowed; close every context to free slots. Recommended backoff: exponential on `RET_ERROR` with rate-limit message; respect 30 s windows.

## 9. Error codes (`RetCode` / `RET_*`)
`RET_OK = 0`, `RET_ERROR = -1`, plus `RetType_TimeOut = -100`, `RetType_Unknown = -400`. The string `ret_msg` carries the specific cause; SDK does not expose stable named constants for the sub-codes, but common substrings: `not login`, `invalid request param`, `unlock trade first` (auth fail), `frequency limited` (rate limit), `order rejected` (broker reject), `no permission`, `account not exist`. Always branch on `ret == RET_OK` and log `ret_msg` verbatim.

## 10. Sandbox / paper specifics
- v10.2 (Mar 2026) added US-stock **margin** paper accounts; cash paper still exists. Paper supports stocks **and options** (limit + market only).
- Paper does **NOT** support: deal queries / deal push, historical deals, order fees, cash-flow queries, conditional/STOP/STOP_LIMIT/trailing/MIT/LIT orders, GTC, modify-disable/enable/delete, short selling on non-US, combined orders. Modify/cancel of working orders **is** supported.
- Quote feed in paper is the real live feed; only execution is simulated. Slippage and option fills are optimistic.

---

## Deprecations / breaking changes since v6
- **v7** unified the `futu` namespace into `moomoo` (both packages still ship).
- **v8** split `OpenSecTradeContext` from the legacy `OpenUSTradeContext` / `OpenHKTradeContext` and made `filter_trdmarket` + `security_firm` mandatory.
- **v8.4** deprecated `fill_outside_rth` in favor of `Session`.
- **v9** reworked option-chain to return DataFrames and added `OptionDataFilter`; server-side filter args replaced client-side filtering.
- **v10.1** disabled the OpenD GUI unlock (headless required).
- **v10.2** enabled US margin paper.
- **v10.3** changed historical-kline quota to a 7-day reset.
- **v10.5** added crypto contexts. There is no longer any combined/multi-leg order placement API in the public SDK as of v10.5.

## Sources
- [Place Orders — moomoo API v10.5](https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html)
- [Trading Definitions — moomoo API v10.4](https://openapi.moomoo.com/moomoo-api-doc/en/trade/trade.html)
- [Transaction Objects — moomoo API v9.2](https://openapi.moomoo.com/moomoo-api-doc/en/trade/base.html)
- [Transaction FAQ — moomoo API v10.5](https://openapi.moomoo.com/moomoo-api-doc/en/qa/trade.html)
- [Get Option Chain — moomoo API v10.2](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html)
- [Subscribe / Unsubscribe — moomoo API v10.4](https://openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html)
- [Authorities and Limitations — Futu API v10.1](https://openapi.futunn.com/futu-api-doc/en/intro/authority.html)
- [Update History — moomoo OpenAPI](https://www.moomoo.com/download/OpenAPI)
- [General Definitions / RetType — moomoo API v9.4](https://openapi.moomoo.com/moomoo-api-doc/en/ftapi/common.html)
- [py-moomoo-api on GitHub](https://github.com/MoomooOpen/py-moomoo-api)
