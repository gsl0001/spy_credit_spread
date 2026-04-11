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
            "avg_entry": float(p.avg_entry_price),
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


def scan_signal(api_key: str, api_secret: str, config: dict) -> dict:
    """
    Check if current market conditions meet entry criteria.
    Uses live price data to determine if a signal is firing RIGHT NOW.
    """
    try:
        import yfinance as yf
        import pandas as pd

        ticker = config.get('ticker', 'SPY')

        # Fetch recent data
        df = yf.download(ticker, period="1mo", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if len(df) < 20:
            return {"signal": False, "reason": "Insufficient data"}

        # Compute streaks
        df['is_red'] = df['Close'] < df['Open']
        df['is_green'] = df['Close'] > df['Open']

        # Count current streak
        is_bear = config.get('strategy_type', 'bull_call') == 'bear_put'
        col = 'is_green' if is_bear else 'is_red'

        current_streak = 0
        for i in range(len(df) - 1, -1, -1):
            if df[col].iloc[i]:
                current_streak += 1
            else:
                break

        entry_days = config.get('entry_red_days', 2)
        signal_firing = current_streak >= entry_days

        # RSI check
        delta = df['Close'].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = (100 - (100 / (1 + rs))).fillna(50).iloc[-1]

        # EMA check
        ema_len = config.get('ema_length', 10)
        ema = df['Close'].ewm(span=ema_len, adjust=False).mean().iloc[-1]
        current_price = float(df['Close'].iloc[-1])

        rsi_ok = True
        if config.get('use_rsi_filter', True):
            threshold = config.get('rsi_threshold', 30)
            if is_bear:
                rsi_ok = float(rsi) > (100 - threshold)
            else:
                rsi_ok = float(rsi) < threshold

        ema_ok = True
        if config.get('use_ema_filter', True):
            if is_bear:
                ema_ok = current_price > float(ema)
            else:
                ema_ok = current_price < float(ema)

        all_pass = signal_firing and rsi_ok and ema_ok

        return {
            "signal": all_pass,
            "streak": current_streak,
            "required": entry_days,
            "price": round(current_price, 2),
            "rsi": round(float(rsi), 2),
            "rsi_ok": rsi_ok,
            "ema": round(float(ema), 2),
            "ema_ok": ema_ok,
            "strategy": config.get('strategy_type', 'bull_call'),
            "timestamp": datetime.now().isoformat(),
        }

    except Exception as e:
        return {"signal": False, "error": str(e)}
