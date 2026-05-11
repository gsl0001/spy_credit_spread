"""Connors 3-Day High/Low pullback on SPY.

Sources:
  - Larry Connors and Cesar Alvarez, "High Probability ETF Trading":
    the 3-Day High/Low method buys liquid ETFs in long-term uptrends after
    three consecutive lower highs plus a short RSI pullback.
  - EdgeRater Academy, "Connors ETF strategy 1: Three day high/low method":
    describes the same ETF pullback family and frames it as positive edge
    versus baseline entries.

Logic:
  Entry (long bias):
    - Close > SMA(trend_sma), default 200.
    - High has made `lower_high_days` consecutive lower highs, default 3.
    - RSI(rsi_period), default 4, is below entry_rsi, default 20.

  Exit:
    - Close > SMA(exit_sma), default 5, or
    - RSI rises above exit_rsi, default 55, or
    - max-hold / expiry.

Topology: bull-call debit spread, 7 DTE, $5 wide. This is a short-hold
directional pullback-reversal setup.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class Connors3DayStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Connors 3-Day High/Low"

    @classmethod
    def id(cls) -> str:
        return "connors_3day"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "lower_high_days": {"type": "number", "default": 3, "min": 2, "max": 5,
                                "label": "Lower High Days"},
            "rsi_period": {"type": "number", "default": 4, "min": 2, "max": 8,
                           "label": "RSI Period"},
            "entry_rsi": {"type": "number", "default": 20, "min": 5, "max": 40,
                          "label": "Entry RSI Max"},
            "exit_rsi": {"type": "number", "default": 55, "min": 45, "max": 80,
                         "label": "Exit RSI Min"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA"},
            "exit_sma": {"type": "number", "default": 5, "min": 3, "max": 20,
                         "label": "Exit SMA"},
            "max_hold_days": {"type": "number", "default": 5, "min": 1, "max": 15,
                              "label": "Max Hold Days"},
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        rsi_period = int(self._get(req, "rsi_period", 4))
        trend = int(self._get(req, "trend_sma", 200))
        exit_n = int(self._get(req, "exit_sma", 5))
        lower_high_days = int(self._get(req, "lower_high_days", 3))

        lower_high = df["High"] < df["High"].shift(1)
        df["lower_high_streak"] = lower_high.astype(int).groupby((~lower_high).cumsum()).cumsum()

        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / rsi_period, min_periods=rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / rsi_period, min_periods=rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df[f"RSI_{rsi_period}"] = (100 - (100 / (1 + rs))).fillna(50)
        df["has_lower_highs"] = df["lower_high_streak"] >= lower_high_days

        avg_gain14 = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss14 = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs14 = avg_gain14 / avg_loss14.replace(0, np.nan)
        df["RSI"] = (100 - (100 / (1 + rs14))).fillna(50)

        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
        df[f"SMA_{exit_n}"] = df["Close"].rolling(window=exit_n).mean()
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
        if not bool(row.get("has_lower_highs", False)):
            return False
        sma = row.get(f"SMA_{trend}")
        if sma is None or pd.isna(sma) or float(row["Close"]) <= float(sma):
            return False
        rsi_period = int(self._get(req, "rsi_period", 4))
        rsi = row.get(f"RSI_{rsi_period}")
        return rsi is not None and not pd.isna(rsi) and float(rsi) < float(self._get(req, "entry_rsi", 20))

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        days_held = i - trade_state["entry_idx"]
        rsi_period = int(self._get(req, "rsi_period", 4))
        rsi = row.get(f"RSI_{rsi_period}")
        if rsi is not None and not pd.isna(rsi) and float(rsi) > float(self._get(req, "exit_rsi", 55)):
            return True, "rsi_revert"
        exit_n = int(self._get(req, "exit_sma", 5))
        sma_exit = row.get(f"SMA_{exit_n}")
        if sma_exit is not None and not pd.isna(sma_exit) and float(row["Close"]) > float(sma_exit):
            return True, "sma_cross"
        if days_held >= int(self._get(req, "max_hold_days", 5)):
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
