import asyncio
from typing import List, Dict, Optional
import logging

# ── Defensive ib_insync import ─────────────────────────────────────────────
# `ib_insync` transitively imports `eventkit`, which calls
# `asyncio.get_event_loop_policy().get_event_loop()` at import time.
# On Python 3.14 this raises `RuntimeError: There is no current event loop`
# in the main thread, which would crash the entire FastAPI server before it
# can boot. We pre-install a loop, then attempt the import inside try/except
# so the rest of the app keeps working even when ib_insync is unavailable.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

HAS_IBSYNC = False
_IBSYNC_IMPORT_ERROR: Optional[str] = None
IB = Stock = Option = ComboLeg = Bag = LimitOrder = MarketOrder = None  # type: ignore
pd = None  # type: ignore


def _try_load_ibsync() -> bool:
    """Attempt to import ib_insync. Safe to call repeatedly.

    The eager import at module load time can fail under uvicorn (which
    replaces the asyncio event-loop policy before our app modules are
    imported). Calling this from inside an async request handler — where a
    real loop exists — recovers cleanly.
    """
    global HAS_IBSYNC, _IBSYNC_IMPORT_ERROR
    global IB, Stock, Option, ComboLeg, Bag, LimitOrder, MarketOrder, pd

    if HAS_IBSYNC:
        return True
    try:
        # Eventkit's module-level code calls policy.get_event_loop(), which
        # in Python 3.14 raises unless `policy.set_event_loop(loop)` has
        # been explicitly called on the current thread. Uvicorn installs a
        # running loop without calling set_event_loop, so we have to bind
        # it ourselves. Outside of a running loop (eager import path) we
        # create and install a fresh loop.
        try:
            running = asyncio.get_running_loop()
            asyncio.set_event_loop(running)
        except RuntimeError:
            try:
                asyncio.set_event_loop(asyncio.new_event_loop())
            except Exception:  # noqa: BLE001
                pass
        from ib_insync import (
            IB as _IB,
            Stock as _Stock,
            Option as _Option,
            ComboLeg as _ComboLeg,
            Bag as _Bag,
            LimitOrder as _LimitOrder,
            MarketOrder as _MarketOrder,
        )
        import pandas as _pd
        IB, Stock, Option = _IB, _Stock, _Option
        ComboLeg, Bag = _ComboLeg, _Bag
        LimitOrder, MarketOrder = _LimitOrder, _MarketOrder
        pd = _pd
        HAS_IBSYNC = True
        _IBSYNC_IMPORT_ERROR = None
        return True
    except Exception as e:  # noqa: BLE001
        _IBSYNC_IMPORT_ERROR = f"{type(e).__name__}: {e}"
        return False


# Best-effort eager attempt — silent failure is fine, we retry lazily.
if not _try_load_ibsync():
    logging.warning(
        "ib_insync unavailable at import time — will retry at first IBKR call (%s)",
        _IBSYNC_IMPORT_ERROR,
    )

class IBKRTrader:
    def __init__(self, host: str = '127.0.0.1', port: int = 7497, client_id: int = 1):
        if not HAS_IBSYNC:
            raise RuntimeError(
                f"ib_insync is not available: {_IBSYNC_IMPORT_ERROR}. "
                "Install with `pip install ib_insync` and ensure Python "
                "compatibility (3.10–3.12 recommended)."
            )
        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self.connected = False

    async def connect(self):
        try:
            await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
            self.connected = True
            return {"success": True, "msg": "Connected to IBKR"}
        except Exception as e:
            self.connected = False
            return {"success": False, "msg": str(e)}

    def disconnect(self):
        try:
            self.ib.disconnect()
        except Exception:
            pass
        self.connected = False

    def is_alive(self) -> bool:
        """Return True only when the socket is actually connected."""
        try:
            return self.connected and self.ib.isConnected()
        except Exception:
            self.connected = False
            return False

    async def ensure_connected(self):
        """Reconnect if the socket dropped."""
        if not self.is_alive():
            self.disconnect()
            return await self.connect()
        return {"success": True, "msg": "Already connected"}

    async def get_account_summary(self):
        await self.ensure_connected()
        # Request account summary for specific tags
        summary = await self.ib.accountSummaryAsync()
        data = {}
        for item in summary:
            data[item.tag] = item.value
            
        # Also try to get PnL from the account
        # Note: reqPnL is more detailed but needs to be managed. 
        # For now we'll stick to summary tags which are sufficient for HUD.
        return {
            "equity": float(data.get("NetLiquidation", 0)),
            "buying_power": float(data.get("BuyingPower", 0)),
            "excess_liquidity": float(data.get("ExcessLiquidity", 0)),
            "daily_pnl": float(data.get("DailyPnL", 0)),
            "unrealized_pnl": float(data.get("UnrealizedPnL", 0))
        }

    async def get_positions(self):
        await self.ensure_connected()
        # ib.portfolio() is a cached property in ib_insync, but we should ensure it's synced
        # Use positions() for a fresh fetch if needed, but portfolio() has P&L info
        pf = self.ib.portfolio()
        return [
            {
                "symbol": p.contract.symbol,
                "type": p.contract.secType,
                "qty": p.position,
                "avg_price": p.averageCost,
                "unrealized_pl": p.unrealizedPNL,
                "realized_pl": p.realizedPNL,
                "market_price": p.marketPrice
            } for p in pf
        ]

    async def place_combo_order(self, symbol: str, legs: List[Dict], sc: int, side: str = 'BUY', lmtPrice: Optional[float] = None):
        """
        Place a multi-leg combo order.
        If lmtPrice is provided, uses LimitOrder. Otherwise, uses MarketOrder (not recommended).
        """
        await self.ensure_connected()
        
        # Build the Combo contract
        ib_legs = []
        for leg in legs:
            action = 'BUY' if leg["side"] == 'long' else 'SELL'
            opt = Option(symbol, leg["expiry"], leg["strike"], leg["type"].upper(), 'SMART')
            await self.ib.qualifyContractsAsync(opt)
            ib_legs.append(ComboLeg(conId=opt.conId, ratio=1, action=action, exchange='SMART'))
        
        combo = Bag(symbol=symbol, secType='BAG', exchange='SMART', currency='USD', comboLegs=ib_legs)
        
        if lmtPrice is not None:
            order = LimitOrder(side.upper(), sc, lmtPrice)
        else:
            order = MarketOrder(side.upper(), sc)
            
        trade = self.ib.placeOrder(combo, order)
        return {
            "orderId": trade.order.orderId,
            "status": trade.orderStatus.status,
            "success": True,
            "lmtPrice": lmtPrice
        }

    async def get_combo_midpoint(self, symbol: str, legs: List[Dict]):
        """Fetches current bid/ask and returns midpoint for a combo."""
        await self.ensure_connected()
        
        ib_legs = []
        for leg in legs:
            action = 'BUY' if leg["side"] == 'long' else 'SELL'
            opt = Option(symbol, leg["expiry"], leg["strike"], leg["type"].upper(), 'SMART')
            await self.ib.qualifyContractsAsync(opt)
            ib_legs.append(ComboLeg(conId=opt.conId, ratio=1, action=action, exchange='SMART'))
            
        combo = Bag(symbol=symbol, secType='BAG', exchange='SMART', currency='USD', comboLegs=ib_legs)
        ticker = self.ib.reqMktData(combo, '', False, False)
        
        # Wait for data
        timeout = 5
        start = asyncio.get_event_loop().time()
        while (pd.isna(ticker.bid) or pd.isna(ticker.ask)) and (asyncio.get_event_loop().time() - start < timeout):
            await asyncio.sleep(0.1)
            
        mid = (ticker.bid + ticker.ask) / 2 if not (pd.isna(ticker.bid) or pd.isna(ticker.ask)) else None
        self.ib.cancelMktData(combo)
        return mid

    async def get_active_orders(self):
        await self.ensure_connected()
        # openTrades returns a list of Trade objects
        trades = self.ib.openTrades()
        return [
            {
                "orderId": t.order.orderId,
                "symbol": t.contract.symbol,
                "action": t.order.action,
                "qty": t.order.totalQuantity,
                "type": t.order.orderType,
                "lmtPrice": t.order.lmtPrice,
                "status": t.orderStatus.status
            } for t in trades
        ]

    async def place_test_order(self):
        """Places a non-filling limit order for SPY at $1.00."""
        try:
            await self.ensure_connected()
            contract = Stock('SPY', 'SMART', 'USD')
            log_msg = f"Qualifying contract {contract}..."
            await self.ib.qualifyContractsAsync(contract)
            
            if not contract.conId:
                return {"success": False, "error": "Could not qualify SPY contract. Check TWS connection."}

            order = LimitOrder('BUY', 1, 1.05) # Sligthly higher to avoid some filter limits
            trade = self.ib.placeOrder(contract, order)
            
            # Brief wait to ensure the order is registered
            await asyncio.sleep(0.5)
            
            return {
                "success": True, 
                "orderId": trade.order.orderId, 
                "status": trade.orderStatus.status,
                "msg": "Test order placed at $1.05"
            }
        except Exception as e:
            return {"success": False, "error": f"IBKR Error: {str(e)}"}

    async def cancel_order(self, orderId: int):
        await self.ensure_connected()
        # Find the trade for this orderId
        trades = self.ib.openTrades()
        for t in trades:
            if t.order.orderId == orderId:
                self.ib.cancelOrder(t.order)
                return {"success": True, "msg": f"Order {orderId} cancellation submitted"}
        return {"success": False, "msg": f"Order {orderId} not found"}

# Global singleton or instance mapper
_ib_instances = {}

async def get_ib_connection(creds: dict):
    if not _try_load_ibsync():
        return None, f"IBKR disabled: ib_insync unavailable ({_IBSYNC_IMPORT_ERROR})"

    port = int(creds.get("port", 7497))
    cid = int(creds.get("client_id", 1))
    key = f"{creds.get('host', '127.0.0.1')}:{port}:{cid}"

    if key not in _ib_instances:
        try:
            _ib_instances[key] = IBKRTrader(
                host=creds.get("host", '127.0.0.1'), port=port, client_id=cid
            )
        except Exception as e:  # noqa: BLE001
            return None, str(e)

    trader = _ib_instances[key]
    if not trader.is_alive():
        res = await trader.connect()
        if not res["success"]:
            return None, res["msg"]
    return trader, "OK"
