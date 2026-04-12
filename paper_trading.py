"""
Alpaca Paper Trading Integration Module

Connects to Alpaca's paper trading API to:
- Check account status and buying power
- Scan current market conditions against your backtest entry criteria
- Execute paper trades (buy/sell spreads) when signals fire
"""

import os
import json
from datetime import datetime
from typing import Optional
import numpy as np

# Lazy import — only load alpaca when actually needed
_api = None


def get_api(api_key: str, api_secret: str):
    """Initialize or return cached Alpaca API connection."""
    global _api
    import alpaca_trade_api as tradeapi
    _api = tradeapi.REST(
        api_key, api_secret,
        base_url='https://paper-api.alpaca.markets',
        api_version='v2'
    )
    return _api


def check_connection(api_key: str, api_secret: str) -> dict:
    """Test API connection and return account info."""
    try:
        api = get_api(api_key, api_secret)
        account = api.get_account()
        return {
            "connected": True,
            "account_id": account.account_number,
            "equity": float(account.equity),
            "buying_power": float(account.buying_power),
            "cash": float(account.cash),
            "portfolio_value": float(account.portfolio_value),
            "status": account.status,
            "paper": True,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


def get_positions(api_key: str, api_secret: str) -> list:
    """Get all open positions."""
    try:
        api = get_api(api_key, api_secret)
        positions = api.list_positions()
        return [{
            "symbol": p.symbol,
            "qty": int(p.qty),
            "side": p.side,
            "avg_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "market_value": float(p.market_value),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
        } for p in positions]
    except Exception as e:
        return []


def get_orders(api_key: str, api_secret: str, limit: int = 20) -> list:
    """Get recent orders."""
    try:
        api = get_api(api_key, api_secret)
        orders = api.list_orders(status='all', limit=limit)
        return [{
            "id": o.id,
            "symbol": o.symbol,
            "side": o.side,
            "qty": str(o.qty),
            "type": o.type,
            "status": o.status,
            "submitted_at": str(o.submitted_at),
            "filled_avg_price": str(o.filled_avg_price) if o.filled_avg_price else None,
        } for o in orders]
    except Exception as e:
        return []


def place_equity_order(api_key: str, api_secret: str, symbol: str, qty: int, side: str) -> dict:
    """Place a simple equity order (for paper trading demonstration).
    
    Note: Alpaca paper trading doesn't support multi-leg options spreads directly.
    This places equity orders as a proxy to simulate the strategy signal execution.
    """
    try:
        api = get_api(api_key, api_secret)
        order = api.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,  # 'buy' or 'sell'
            type='market',
            time_in_force='day'
        )
        return {
            "success": True,
            "order_id": order.id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": str(order.qty),
            "status": order.status,
            "submitted_at": str(order.submitted_at),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def scan_signal(api_key: str, api_secret: str, config_dict: dict) -> dict:
    """
    Check if current market conditions meet entry criteria.
    Uses live price data to determine if a signal is firing RIGHT NOW.
    """
    try:
        import yfinance as yf
        import pandas as pd
        from main import BacktestRequest, StrategyFactory

        # Convert dict to BacktestRequest object
        req = BacktestRequest(**config_dict)
        ticker = req.ticker

        # Fetch recent data (enough for indicators)
        df = yf.download(ticker, period="1mo", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if len(df) < 20:
            return {"signal": False, "reason": "Insufficient data"}

        df.reset_index(inplace=True)
        if 'Date' not in df.columns and 'index' in df.columns:
            df.rename(columns={'index': 'Date'}, inplace=True)
        if hasattr(df['Date'].dt, 'tz') and df['Date'].dt.tz is not None:
            df['Date'] = df['Date'].dt.tz_localize(None)

        # Use modular strategy logic
        strategy = StrategyFactory.get_strategy(req.strategy_id)
        df_ind = strategy.compute_indicators(df, req)
        
        # Add basic regime for filters
        df_ind['regime'] = 'sideways'
        if 'SMA_200' in df_ind.columns and 'SMA_50' in df_ind.columns:
            bull_mask = (df_ind['Close'] > df_ind['SMA_200']) & (df_ind['SMA_50'] > df_ind['SMA_200'])
            bear_mask = (df_ind['Close'] < df_ind['SMA_200']) & (df_ind['SMA_50'] < df_ind['SMA_200'])
            df_ind.loc[bull_mask, 'regime'] = 'bull'
            df_ind.loc[bear_mask, 'regime'] = 'bear'

        i = len(df_ind) - 1
        row = df_ind.iloc[i]
        is_bear = req.strategy_type == 'bear_put'

        signal_firing = strategy.check_entry(df_ind, i, req)

        # Filters logic (Global)
        rsi = float(row['RSI'])
        rsi_ok = True
        if req.use_rsi_filter:
            if is_bear:
                rsi_ok = rsi > (100 - req.rsi_threshold)
            else:
                rsi_ok = rsi < req.rsi_threshold

        ema_ok = True
        if req.use_ema_filter:
            ema = float(row[f'EMA_{req.ema_length}'])
            current_price = float(row['Close'])
            if is_bear:
                ema_ok = current_price > ema
            else:
                ema_ok = current_price < ema

        all_pass = signal_firing and rsi_ok and ema_ok
        current_price = float(row['Close'])

        return {
            "signal": all_pass,
            "price": round(current_price, 2),
            "rsi": round(rsi, 2),
            "rsi_ok": rsi_ok,
            "ema_ok": ema_ok,
            "strategy": req.strategy_id,
            "type": req.strategy_type,
            "timestamp": datetime.now().isoformat(),
        }

    except Exception as e:
        import traceback; traceback.print_exc()
        return {"signal": False, "error": str(e)}
