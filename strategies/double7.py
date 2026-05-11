"""Connors/Alvarez Double 7s mean-reversion strategy on SPY.

Sources:
  - Cesar Alvarez, "Double 7's Strategy": documents the original rules from
    Larry Connors and Cesar Alvarez's "Short Term Trading Strategies That
    Work": buy when close is above the 200-day moving average and at a 7-day
    closing low, sell when close reaches a 7-day closing high.
  - TradingView, "Connors Double Seven (with options)": restates the same
    long-only daily rules for options-oriented use.

Topology: bull-call debit spread, 7 DTE, $5 wide. The edge is a short-term
oversold close inside a long-term uptrend, aiming for a quick mean reversion.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class Double7Strategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Connors Double 7s"

    @classmethod
    def id(cls) -> str:
        return "double7"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "lookback_days": {"type": "number", "default": 7, "min": 3, "max": 15,
                              "label": "Close Channel Days"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA"},
            "max_hold_days": {"type": "number", "default": 7, "min": 1, "max": 15,
                              "label": "Max Hold Days"},
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        lookback = int(self._get(req, "lookback_days", 7))
        trend = int(self._get(req, "trend_sma", 200))

        df[f"Close_Low_{lookback}"] = df["Close"].rolling(window=lookback).min()
        df[f"Close_High_{lookback}"] = df["Close"].rolling(window=lookback).max()
        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()

        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain14 = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss14 = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs14 = avg_gain14 / avg_loss14.replace(0, np.nan)
        df["RSI"] = (100 - (100 / (1 + rs14))).fillna(50)

        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        ema_len = int(getattr(req, "ema_length", 10) or 10)
        df[f"EMA_{ema_len}"] = df["Close"].ewm(span=ema_len, adjust=False).mean()
        df["Volume_MA"] = df["Volume"].rolling(window=10).mean()
        log_ret = np.log(df["Close"] / df["Close"].shift(1))
        df["HV_21"] = (log_ret.rolling(window=21).std() * np.sqrt(252)).fillna(0.15)
        return df

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        trend = int(self._get(req, "trend_sma", 200))
        if i < trend:
            return False
        row = df.iloc[i]
        sma = row.get(f"SMA_{trend}")
        if sma is None or pd.isna(sma) or float(row["Close"]) <= float(sma):
            return False
        lookback = int(self._get(req, "lookback_days", 7))
        low = row.get(f"Close_Low_{lookback}")
        return low is not None and not pd.isna(low) and float(row["Close"]) <= float(low)

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        lookback = int(self._get(req, "lookback_days", 7))
        high = row.get(f"Close_High_{lookback}")
        if high is not None and not pd.isna(high) and float(row["Close"]) >= float(high):
            return True, "close_channel_high"
        days_held = i - trade_state["entry_idx"]
        if days_held >= int(self._get(req, "max_hold_days", 7)):
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
