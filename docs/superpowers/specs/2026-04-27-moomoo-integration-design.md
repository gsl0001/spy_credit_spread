# Moomoo Trading Integration — Design Spec
**Date:** 2026-04-27  
**Status:** Approved  
**Scope:** Full live-trading broker integration for moomoo Canada accounts

---

## 1. Overview

Add moomoo as a first-class live trading broker alongside IBKR. Any preset can declare `"broker": "moomoo"` and the full auto-execute + monitor + fill-watcher pipeline routes to moomoo's OpenD gateway instead of IBKR TWS.

A new **orange-themed MoomooView** in the frontend provides connection management, account KPIs, manual order entry, and live position management.

The `console_live.py` control board gains a broker selector so operators can switch active broker without restarting.

---

## 2. Architecture

```
ScannerPreset { broker: "ibkr" | "moomoo" }
        │
        ▼
_run_preset_tick()
        │
        ├─ broker == "ibkr"   → _ibkr_execute_impl()   → ibkr_trading.py
        └─ broker == "moomoo" → _moomoo_execute_impl()  → moomoo_trading.py
                                                               │
                                                         moomoo OpenD (localhost:11111)
                                                               │
                                                         moomoo servers → US options markets

core/monitor.py  →  get_broker(position.meta["broker"])  →  close_position()
core/fill_watcher.py  →  broker-aware reconciliation
```

---

## 3. New File: `core/broker.py`

Thin protocol + registry. Both brokers implement it; all system components (monitor, scanner, console) use `get_broker()` rather than importing broker modules directly.

```python
class BrokerProtocol(Protocol):
    def is_alive(self) -> bool: ...
    async def get_account_summary(self) -> dict: ...
    async def get_positions(self) -> list: ...
    async def get_live_price(self, symbol: str) -> dict: ...
    async def place_spread(self, req: SpreadRequest) -> dict: ...
    async def close_position(self, position_id: str, legs: list) -> dict: ...
    async def cancel_order(self, order_id: str) -> dict: ...

def get_broker(name: str) -> BrokerProtocol:
    """Return connected broker instance or raise BrokerNotConnected."""

def register_broker(name: str, instance: BrokerProtocol) -> None:
    """Called on successful connect to register the instance."""
```

**`SpreadRequest` dataclass:**
```python
@dataclass
class SpreadRequest:
    symbol: str
    long_leg: LegSpec    # {expiry, strike, right, price}
    short_leg: LegSpec
    qty: int
    net_debit_limit: float   # max total debit to pay (dollars)
    position_id: str         # for journaling
    client_order_id: str     # idempotency
```

---

## 4. New File: `moomoo_trading.py`

Full adapter implementing `BrokerProtocol`. Requires moomoo OpenD running locally.

### 4.1 Connection

```python
class MoomooTrader:
    def __init__(self, host="127.0.0.1", port=11111, trade_password="")
    async def connect() -> dict          # unlock_trade + accinfo_query
    def disconnect()
    def is_alive() -> bool
```

Auth flow:
1. `OpenSecTradeContext(filter_trdmarket=TrdMarket.US, SecurityFirm.FUTUINC)` 
2. `unlock_trade(trade_password)` — required before any order
3. Cache `acc_id` from universal account query

### 4.2 Account field mapping

| moomoo field | project field |
|---|---|
| `total_assets` | `equity` |
| `power` | `buying_power` |
| `net_cash_power` | `excess_liquidity` |
| `unrealized_pl` | `unrealized_pnl` |
| `realized_pl` | `realized_pnl` |
| `cash` | `cash` |

### 4.3 Option chain

```python
async def get_option_chain(symbol: str, expiry_date: str) -> pd.DataFrame
# calls quote_ctx.get_option_chain(code=f"US.{symbol}", start=expiry_date, end=expiry_date)
# returns DataFrame with: strike, right, bid, ask, iv, delta, volume, open_interest
```

Moomoo option code format: `US.SPY260425C580000` (US.{symbol}{YYMMDD}{C|P}{strike*1000 zero-padded})

### 4.4 Legged spread execution

Critical design: moomoo has **no atomic combo order API**. Spreads are placed as two sequential single-leg orders with a safety net.

```
place_spread(req: SpreadRequest) -> dict:
  1. Place long leg (BUY limit @ req.long_leg.price)
  2. Poll for fill every 0.5s, timeout = req.timeout_s (default 30s)
  3a. If long fills within timeout:
      → Place short leg (SELL limit @ req.short_leg.price)
      → Poll for short fill, timeout = 30s
      3a-i.  Short fills → return success, log both leg fills
      3a-ii. Short times out → cancel short order
                             → place MARKET SELL of long leg (flatten)
                             → return error "short_leg_timeout_flattened"
  3b. If long times out → cancel long → return error "long_leg_timeout"
```

All outcomes (success, partial fill, flatten) are journaled.

### 4.5 Live price

```python
async def get_live_price(symbol: str) -> dict:
# quote_ctx.get_market_snapshot([f"US.{symbol}"])
# returns {last, bid, ask, volume}
```

### 4.6 Close position

```python
async def close_position(position_id: str, legs: list) -> dict:
# For each leg in position: place opposing order (BUY for short leg, SELL for long leg)
# Uses market orders at close to guarantee fill
```

---

## 5. `core/presets.py` changes

Add `broker` field to `ScannerPreset`:

```python
broker: str = "ibkr"   # "ibkr" | "moomoo"
```

`from_dict` reads `data.get("broker", "ibkr")`.

All existing presets default to `"ibkr"` — no behavioural change.

---

## 6. `main.py` changes

### 6.1 New API endpoints

```
POST /api/moomoo/connect        {host, port, trade_password}  → {connected, account}
POST /api/moomoo/disconnect     {}                            → {status}
GET  /api/moomoo/account        {}                            → account KPIs
GET  /api/moomoo/positions      {}                            → positions list
GET  /api/moomoo/chain          ?symbol=SPY&date=2026-04-27   → option chain
POST /api/moomoo/execute        SpreadRequest                 → order result
POST /api/moomoo/exit           {position_id}                 → exit result
POST /api/moomoo/cancel         {order_id}                    → cancel result
```

### 6.2 `_run_preset_tick` broker routing

```python
if p.broker == "moomoo":
    order_req = MoomooOrderRequest(...)   # parallel to IBKROrderRequest
    coro = _moomoo_execute_impl(order_req)
else:
    order_req = IBKROrderRequest(...)
    coro = _ibkr_execute_impl(order_req)
asyncio.run_coroutine_threadsafe(coro, loop)
```

### 6.3 `_moomoo_execute_impl`

Mirrors `_ibkr_execute_impl` step-by-step:
1. Idempotency check (same key format: `scan:{date}:{symbol}:{preset_name}`)
2. Connect check via `get_broker("moomoo")`
3. Account snapshot → `AccountSnapshot`
4. Option chain resolution via `MoomooTrader.get_option_chain()`
5. Strike picking via existing `pick_bull_call_strikes()` with `otm_offset`
6. Position sizing via existing `size_position()`
7. Pre-trade risk gate via existing `evaluate_pre_trade()`
8. `MoomooTrader.place_spread()` (legged execution)
9. Journal entry: position + both leg orders
10. Telegram notification

### 6.4 `MoomooOrderRequest`

```python
class MoomooOrderRequest(BaseModel):
    host: str = "127.0.0.1"
    port: int = 11111
    trade_password: str = ""
    symbol: str = "SPY"
    direction: str = "bull_call"      # "bull_call" | "bear_put"
    contracts: int = 1
    strike_width: int = 5
    target_dte: int = 0
    spread_cost_target: float = 250.0
    otm_offset: float = 0.0
    position_size_method: str = "fixed"
    risk_percent: float = 1.0
    max_allocation_cap: float = 500.0
    stop_loss_pct: float = 50.0
    take_profit_pct: float = 50.0
    trailing_stop_pct: float = 0.0
    client_order_id: Optional[str] = None
```

---

## 7. `core/monitor.py` changes

Position metadata stored at entry time includes `"broker": "ibkr"|"moomoo"`.

`monitor_tick` for a position:
```python
broker_name = pos.meta.get("broker", "ibkr")
broker = get_broker(broker_name)
price = await broker.get_live_price(pos.symbol)
# ... evaluate_exit_decision ...
if exit_decision.exit:
    await broker.close_position(pos.id, pos.meta["legs"])
```

---

## 8. `core/fill_watcher.py` changes

`reconcile_once` currently calls IBKR to check order fills. Add broker routing:

```python
broker_name = order.meta.get("broker", "ibkr")
broker = get_broker(broker_name)
# check fill status via broker.get_order_status(order_id)
```

For moomoo, fill detection uses `trd_ctx.order_list_query(order_id=...)`.

---

## 9. Frontend

### 9.1 `MoomooView.jsx` — orange theme

**Color system** (Tailwind built-in orange scale, no config changes):
- Accent: `orange-500` (#f97316)
- Hover: `orange-400`  
- Card bg: `orange-950/20` with `border-orange-800/40`
- Positive P&L: `orange-400`; Negative: `red-400`
- Buttons: `bg-orange-500 hover:bg-orange-400 text-black`

**Sections (top → bottom):**

1. **Connection Panel**
   - OpenD host (default `127.0.0.1`), port (default `11111`), trade password (masked)
   - Connect / Disconnect button (orange)
   - Status pill: Connected · account_id · equity

2. **Account KPIs** (4 cards, orange border)
   - Total Assets | Buying Power | Unrealized P&L | Today P&L

3. **Legged Execution Warning** (permanent orange banner)
   - "Spreads execute as 2 sequential legs. Leg 2 failure auto-flattens leg 1 at market."

4. **Order Ticket**
   - Symbol (SPY), Expiry (today default), Direction toggle (Bull Call ↔ Bear Put)
   - Strike offset (1.50), Width (5), Qty, Net debit limit
   - Execute button → shows leg 1 / leg 2 status in real-time

5. **Preset Scanner** (same as LiveView but scoped to moomoo presets)
   - Lists presets where `broker == "moomoo"`
   - Start/stop scanner

6. **Positions Table**
   - Code, Side, Qty, Cost/contract, Mkt Value, P&L, Close button

7. **Order Log**
   - Recent orders: time, symbol, side, status (leg1_filled / leg2_filled / flattened / error)

### 9.2 `App.jsx`

```js
const VIEWS = {
  ...existing,
  moomoo: MoomooView,
};
const VIEW_TITLES = {
  ...existing,
  moomoo: { title: 'Moomoo Trading', sub: 'moomoo OpenD · legged spreads · CA' },
};
```

### 9.3 `sidebar.jsx`

Add after Paper (Alpaca):
```js
{ id: 'moomoo', icon: 'zap', label: 'Moomoo' }
```

Active state uses `orange-500` instead of default accent colour for the moomoo item.

### 9.4 `api.js`

Add moomoo API methods:
```js
moomoo: {
  connect: (body) => post('/api/moomoo/connect', body),
  disconnect: () => post('/api/moomoo/disconnect'),
  account: () => get('/api/moomoo/account'),
  positions: () => get('/api/moomoo/positions'),
  chain: (symbol, date) => get(`/api/moomoo/chain?symbol=${symbol}&date=${date}`),
  execute: (body) => post('/api/moomoo/execute', body),
  exit: (body) => post('/api/moomoo/exit', body),
  cancel: (body) => post('/api/moomoo/cancel', body),
}
```

---

## 10. `console_live.py` changes

### 10.1 Broker state

```python
_broker: str = "ibkr"   # "ibkr" | "moomoo"
```

### 10.2 Status bar

Add broker indicator to the header panel:
- `[green]● IBKR[/green]` or `[orange1]● Moomoo[/orange1]`

### 10.3 New commands

| Command | Effect |
|---|---|
| `broker ibkr` | Switch to IBKR; connect if not connected |
| `broker moomoo` | Switch to moomoo; prompt for OpenD creds if not connected |
| `broker` | Show current broker + connection status |

### 10.4 Command routing

All existing commands (`pos`, `orders`, `flatten`, `scan on/off`) check `_broker` and call the appropriate `/api/ibkr/*` or `/api/moomoo/*` endpoint.

---

## 11. New preset example

```json
{
  "name": "orb-5m-moomoo",
  "broker": "moomoo",
  "ticker": "SPY",
  "strategy_name": "orb",
  "strategy_params": { ...same as orb-5m... },
  "target_dte": 0,
  "stop_loss_pct": 50.0,
  "take_profit_pct": 50.0,
  "auto_execute": true,
  ...
}
```

---

## 12. Out of scope

- Moomoo paper/sandbox trading (requires separate sandbox account)
- Moomoo-specific Telegram bot commands
- TFSA/RRSP account trading (spreads not permitted in registered accounts per moomoo Canada rules)
- Moomoo historical data for backtesting (yfinance continues to serve this role)

---

## 13. Dependencies

```
pip install moomoo-api --upgrade   # v10.4.6408+
```

No other new dependencies. All existing risk, journal, sizing, and calendar infrastructure reused unchanged.

---

## 14. Implementation phases

| Phase | Files | Description |
|---|---|---|
| 1 | `core/broker.py`, `moomoo_trading.py` | Protocol + adapter |
| 2 | `core/presets.py`, `main.py` | Preset broker field + API endpoints + execute routing |
| 3 | `core/monitor.py`, `core/fill_watcher.py` | Broker-aware exits + fill reconciliation |
| 4 | `frontend/src/views/MoomooView.jsx`, `App.jsx`, `sidebar.jsx`, `api.js` | Orange UI |
| 5 | `console_live.py` | Broker selector + command routing |
| 6 | `config/presets.json` | Add `broker` field to existing presets + new orb-5m-moomoo preset |
