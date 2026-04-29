# NautilusTrader + moomoo ORB System — Design Spec
**Date:** 2026-04-28  
**Status:** Approved  
**Location:** `spy_credit_spread/nautilus/` (self-contained sub-project)

---

## 1. Overview

A standalone NautilusTrader-based live trading system that runs the SPY 0DTE ORB strategy (SSRN 6355218) through moomoo's OpenD gateway instead of IBKR TWS. Lives as a `nautilus/` sub-directory in the existing repo — shares no runtime code with the parent FastAPI system, only config files.

**Why NautilusTrader:** Production-grade event-driven engine (22k+ GitHub stars, weekly releases, Rust core). Identical strategy code runs in both backtest and live. Built-in OMS, risk engine, portfolio tracking, position reconciliation.

**Why `nautilus-futu` as base:** Reimplements Futu/moomoo OpenD binary TCP protocol in Rust via PyO3, avoiding the `protobuf` version conflict between `moomoo-api` and `nautilus_trader`. We extend it in pure Python to add options support.

---

## 2. Dependencies

```toml
# nautilus/pyproject.toml
[project]
name = "spy-orb-nautilus"
requires-python = ">=3.12"

dependencies = [
    "nautilus_trader>=1.225.0",
    "nautilus-futu>=0.4.2",
    "yfinance>=0.2",        # backtest data + VIX fallback
    "pandas>=2.0",
    "python-dotenv",
]
```

Infrastructure: **moomoo OpenD** desktop app must be running locally (port 11111 default). Download from moomoo developer portal. Real trading requires OpenD v8.3+.

---

## 3. Directory Structure

```
spy_credit_spread/
└── nautilus/
    ├── pyproject.toml
    ├── README.md
    ├── run_live.py                      # entry point — TradingNode
    ├── run_backtest.py                  # entry point — BacktestEngine
    ├── config/
    │   ├── live_config.py               # TradingNodeConfig assembly
    │   └── settings.py                  # env var loading (OpenD host/port/PIN)
    ├── adapters/
    │   └── futu_options/
    │       ├── __init__.py
    │       ├── config.py                # FutuOptionsDataClientConfig + ExecClientConfig
    │       ├── providers.py             # FutuOptionsInstrumentProvider
    │       ├── data.py                  # FutuOptionsDataClient
    │       ├── execution.py             # FutuOptionsExecClient
    │       └── factories.py             # factory classes for TradingNode registration
    ├── strategies/
    │   └── orb_spread.py                # OrbSpreadStrategy(Strategy)
    └── tests/
        ├── test_orb_strategy.py
        └── test_instruments.py
```

---

## 4. Adapter Layer: `adapters/futu_options/`

### 4.1 `config.py`

Two Pydantic config classes registered with `TradingNodeConfig`:

```python
class FutuOptionsDataClientConfig(NautilusConfig):
    host: str = "127.0.0.1"
    port: int = 11111
    market: str = "US"           # US options only
    spy_bar_spec: str = "5-MINUTE"  # bar resolution for ORB

class FutuOptionsExecClientConfig(NautilusConfig):
    host: str = "127.0.0.1"
    port: int = 11111
    trd_env: int = 1             # 1=real, 0=simulate
    unlock_pwd_md5: str = ""     # MD5 of trade PIN (required for live)
    acc_id: int = 0              # 0 = auto-discover first margin account
    leg_fill_timeout_s: int = 30 # seconds to wait for each leg fill
```

### 4.2 `providers.py` — `FutuOptionsInstrumentProvider`

Called during `_connect()` before the strategy starts. Loads today's SPY option chain from OpenD and registers all `OptionContract` instruments in the NautilusTrader cache.

```python
async def load_all_async(self, filters: dict | None = None) -> None:
    # calls asyncio.to_thread(quote_ctx.get_option_chain,
    #     "US.SPY", today, today, OptionType.ALL)
    # for each row: builds OptionContract(
    #     instrument_id=InstrumentId("{FUTU_CODE}.FUTU"),
    #     underlying=InstrumentId("SPY.FUTU"),
    #     option_kind=OptionKind.CALL | PUT,
    #     strike_price=Price(row.strike_price),
    #     expiry_date=date.fromisoformat(row.strike_time[:10]),
    #     multiplier=Quantity(100),
    #     currency=USD,
    # )
    # self._cache.add_instrument(contract)
```

Futu symbol format: `US.SPY260428C580000` → mapped to NautilusTrader `InstrumentId("US.SPY260428C580000.FUTU")`.

### 4.3 `data.py` — `FutuOptionsDataClient`

Extends `FutuLiveDataClient`. Adds:

**`_subscribe_bars`** — subscribes SPY 5-min bars via `quote_ctx.subscribe(["US.SPY"], SubType.K_5M)`. Translates Futu `KLineHandlerBase` push → NautilusTrader `Bar` events on the message bus.

**`_subscribe_quote_ticks`** — subscribes option quote ticks via `quote_ctx.subscribe([futu_code], SubType.QUOTE)`. Translates bid/ask → `QuoteTick`.

**`_subscribe_mark_prices`** — subscribes `^VIX` snapshot for VIX filter. Refreshed every 5 minutes via `quote_ctx.get_market_snapshot(["US.VIX"])` called in a background task.

All Futu callback handlers run via `asyncio.to_thread()` and post events to the NautilusTrader message bus via `self._handle_bar()` / `self._handle_quote_tick()`.

### 4.4 `execution.py` — `FutuOptionsExecClient`

Extends `FutuLiveExecClient`. Adds options-specific order handling.

**`_submit_order(command: SubmitOrder)`**

Maps `OptionContract` InstrumentId → Futu code, calls:
```python
await asyncio.to_thread(
    trd_ctx.place_order,
    price=command.order.price,
    qty=command.order.quantity,
    code=futu_code,
    trd_side=TrdSide.BUY if command.order.side == OrderSide.BUY else TrdSide.SELL,
    order_type=OrderType.NORMAL,
    trd_env=self._trd_env,
    acc_id=self._acc_id,
)
```

Generates `self.generate_order_submitted(...)` immediately, then starts a fill-polling background task.

**`_submit_order_list(command: SubmitOrderList)`** — legged spread execution:

```
For a 2-leg order list [long_order, short_order]:

1. Submit long_order via _submit_order()
2. Poll order_list_query every 0.5s, timeout = leg_fill_timeout_s
3a. Long fills within timeout:
    → submit short_order via _submit_order()
    → poll short fill, timeout = leg_fill_timeout_s
    3a-i.  Short fills → generate_order_filled() for both → SUCCESS
    3a-ii. Short times out:
           → cancel_order(short_order_id)
           → place MARKET sell of long leg
           → generate_order_rejected() with reason="short_leg_timeout_flattened"
3b. Long times out:
    → cancel_order(long_order_id)
    → generate_order_rejected() with reason="long_leg_timeout"
```

**Reconciliation methods** (called on node startup):
- `generate_order_status_reports()` — queries today's orders from OpenD `order_list_query()`
- `generate_fill_reports()` — queries today's fills from OpenD `history_deal_list()`
- `generate_position_status_reports()` — queries positions from OpenD `position_list_query()`

### 4.5 `factories.py`

```python
class FutuOptionsDataClientFactory(LiveDataClientFactory):
    @staticmethod
    def create(...) -> FutuOptionsDataClient: ...

class FutuOptionsExecClientFactory(LiveExecClientFactory):
    @staticmethod
    def create(...) -> FutuOptionsExecClient: ...
```

---

## 5. Strategy: `strategies/orb_spread.py`

### 5.1 Config

```python
@dataclass
class OrbSpreadConfig(StrategyConfig):
    instrument_id: str = "SPY.FUTU"      # underlying for bar subscription
    venue: str = "FUTU"
    bar_spec: str = "5-MINUTE"
    offset: float = 1.50                  # strike offset from breakout (paper: 0.96–2.00)
    width: int = 5                        # spread width in dollars
    min_range_pct: float = 0.05           # OR size filter (% of price)
    vix_min: float = 15.0
    vix_max: float = 25.0
    take_profit_pct: float = 50.0         # % of entry debit
    stop_loss_pct: float = 50.0
    time_exit_hhmm: str = "15:30"         # ET wall time
    events_file: str = "../config/events_2026.json"  # relative to nautilus/
```

### 5.2 Lifecycle

```
on_start():
  - load news dates from events_file
  - subscribe to SPY 5-min bars
  - subscribe to ^VIX mark price
  - log "ORB strategy started"

on_bar(bar: Bar):
  - if bar.ts_event.time() == 09:30 ET → record OR_high, OR_low
  - if bar.ts_event.time() < 09:35 ET → return (still in OR window)
  - if position open → check exits (see below)
  - if no position → check entry (see below)

on_order_filled(event: OrderFilled):
  - update leg fill tracker
  - if both legs filled → record entry_cost, set position state

on_stop():
  - if open position → submit market close orders for both legs
  - log final P&L
```

### 5.3 Entry logic

```
check_entry(bar):
  1. Day-of-week: bar.ts_event.weekday() in {0, 2, 4}  (Mon/Wed/Fri)
  2. News filter: today's date not in news_dates set
  3. VIX: vix_min <= current_vix <= vix_max  (fail-closed if unavailable)
  4. Range: (OR_high - OR_low) >= price * min_range_pct / 100
  5. Breakout: bar.close > OR_high (bull) or bar.close < OR_low (bear)
     (direction constrained by config; default bull_call)
  6. No open position already

  On signal:
    - breakout_price = bar.close
    - long_strike = round(breakout_price + offset) for bull
                  = round(breakout_price - offset) for bear
    - short_strike = long_strike + width (bull) / long_strike - width (bear)
    - resolve InstrumentIds from cache (today's expiry, nearest strikes)
    - build OrderList: [LimitOrder(long_leg, BUY), LimitOrder(short_leg, SELL)]
    - self.submit_order_list(order_list)
```

### 5.4 Exit logic

```
check_exit(bar):
  1. Time exit: bar.ts_event.time() >= time(15, 30) ET
     → self.close_all_positions()

  2. P&L exit (evaluated on each bar's mark price):
     current_value = mark_price(long_leg) - mark_price(short_leg)  [per contract * 100]
     pnl_pct = (current_value - entry_cost) / entry_cost * 100
     if pnl_pct >= take_profit_pct → close_all_positions("take_profit")
     if pnl_pct <= -stop_loss_pct  → close_all_positions("stop_loss")
```

---

## 6. Entry Points

### 6.1 `run_live.py`

```python
from nautilus_trader.live.node import TradingNode
from config.live_config import build_config
from config.settings import load_settings
from adapters.futu_options.factories import (
    FutuOptionsDataClientFactory, FutuOptionsExecClientFactory
)
from strategies.orb_spread import OrbSpreadStrategy, OrbSpreadConfig

settings = load_settings()   # reads .env from parent config/
config = build_config(settings)

node = TradingNode(config=config)
node.add_data_client_factory("FUTU", FutuOptionsDataClientFactory)
node.add_exec_client_factory("FUTU", FutuOptionsExecClientFactory)
node.trader.add_strategy(OrbSpreadStrategy(config=OrbSpreadConfig()))
node.build()

if __name__ == "__main__":
    try:
        node.run()       # blocks until SIGINT / SIGTERM
    finally:
        node.dispose()
```

### 6.2 `run_backtest.py`

Same `OrbSpreadStrategy` wired to `BacktestEngine`. Historical SPY 5-min bars loaded from yfinance and converted to NautilusTrader `Bar` format. VIX loaded separately. Validates strategy logic without live connectivity.

### 6.3 `config/settings.py`

```python
# Reads from parent project's config/.env (dotenv)
FUTU_HOST = os.getenv("FUTU_HOST", "127.0.0.1")
FUTU_PORT = int(os.getenv("FUTU_PORT", "11111"))
FUTU_TRD_ENV = int(os.getenv("FUTU_TRD_ENV", "0"))  # 0=simulate, 1=real
FUTU_UNLOCK_PWD_MD5 = os.getenv("FUTU_UNLOCK_PWD_MD5", "")
FUTU_ACC_ID = int(os.getenv("FUTU_ACC_ID", "0"))
```

Add to parent `config/.env.example`:
```
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_TRD_ENV=0
FUTU_UNLOCK_PWD_MD5=   # md5sum of your moomoo trade PIN
FUTU_ACC_ID=0
```

---

## 7. What Is NOT Shared With Parent Project

| Item | Reason |
|---|---|
| FastAPI / REST endpoints | NautilusTrader is a standalone process; no HTTP server |
| Parent journal (`core/journal.py`) | NautilusTrader has its own OMS cache |
| IBKR adapter (`ibkr_trading.py`) | Replaced by `adapters/futu_options/` |
| React frontend | Not applicable; console/log output only |
| APScheduler | NautilusTrader's event loop handles scheduling |

**Shared (read-only):**
- `config/events_2026.json` — news blackout dates (loaded by path)
- `config/.env` — env vars extended with `FUTU_*` vars

---

## 8. Key Constraints & Known Risks

| Risk | Mitigation |
|---|---|
| `nautilus-futu` is a 2-star community package | Adapter layer isolated in `adapters/futu_options/`; can swap to custom impl if needed |
| moomoo no atomic spread orders | Legged execution with safety net in `_submit_order_list`; all outcomes journaled |
| OpenD must be running | Documented in README; `_connect()` fails fast with clear error if unreachable |
| VIX not available via Futu | `get_market_snapshot(["US.VIX"])` — fail-closed if unavailable |
| options support not in `nautilus-futu` | We add it in our `FutuOptionsDataClient` / `FutuOptionsExecClient` extension |
| Python >=3.12 required by nautilus_trader | Isolated `pyproject.toml` in `nautilus/` prevents conflict with parent project |

---

## 9. Implementation Phases

| Phase | Files | Description |
|---|---|---|
| 1 | `pyproject.toml`, `config/settings.py`, `config/live_config.py` | Project scaffold + settings |
| 2 | `adapters/futu_options/config.py`, `providers.py`, `factories.py` | Instrument provider + configs |
| 3 | `adapters/futu_options/data.py` | Live data client (bars + quotes + VIX) |
| 4 | `adapters/futu_options/execution.py` | Execution client (single-leg + legged spread) |
| 5 | `strategies/orb_spread.py` | ORB strategy (entry filters + exit logic) |
| 6 | `run_live.py`, `run_backtest.py` | Entry points + backtest wiring |
| 7 | `tests/` | Unit tests for strategy logic + instrument provider |
| 8 | `README.md`, `.env.example` updates | Operator documentation |
