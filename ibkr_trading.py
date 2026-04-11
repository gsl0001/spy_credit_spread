import asyncio
from ib_insync import *
from typing import List, Dict, Optional
import logging

class IBKRTrader:
    def __init__(self, host='127.0.0.1', port=7497, client_id=1):
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
            return {"success": False, "msg": str(e)}

    def disconnect(self):
        self.ib.disconnect()
        self.connected = False

    async def get_account_summary(self):
        if not self.connected: await self.connect()
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
        if not self.connected: await self.connect()
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
        if not self.connected: await self.connect()
        
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
        if not self.connected: await self.connect()
        
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
        if not self.connected: await self.connect()
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
            if not self.connected: await self.connect()
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
        if not self.connected: await self.connect()
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
    port = int(creds.get("port", 7497))
    cid = int(creds.get("client_id", 1))
    key = f"{creds.get('host', '127.0.0.1')}:{port}:{cid}"
    
    if key not in _ib_instances:
        _ib_instances[key] = IBKRTrader(host=creds.get("host", '127.0.0.1'), port=port, client_id=cid)
    
    trader = _ib_instances[key]
    if not trader.connected:
        res = await trader.connect()
        if not res["success"]:
            return None, res["msg"]
    return trader, "OK"
