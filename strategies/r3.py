"""Connors R3 mean-reversion strategy on SPY.

Source: The Robust Trader, "The R3 Mean-Reversion Strategy Explained",
summarizing Larry Connors' High Probability ETF Trading rules:
  - Close above the 200-day moving average.
  - RSI(2) drops three days in a row; the first day's reading is below 60.
  - RSI(2) is below 10 today.
  - Exit when RSI(2) is above 70.

Topology: bull-call debit spread, 7 DTE, $5 wide. The setup is a short-hold
directional mean-reversion trade after persistent RSI deterioration.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class R3Strategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Connors R3"

    @classmethod
    def id(cls) -> str:
        return "r3"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "rsi_period": {"type": "number", "default": 2, "min": 2, "max": 4,
                           "label": "RSI Period"},
            "drop_days": {"type": "number", "default": 3, "min": 2, "max": 4,
                          "label": "RSI Drop Days"},
            "first_drop_max": {"type": "number", "default": 60, "min": 30, "max": 80,
                               "label": "First Drop RSI Max"},
            "entry_rsi": {"type": "number", "default": 10, "min": 5, "max": 30,
                          "label": "Entry RSI Max"},
            "exit_rsi": {"type": "number", "default": 70, "min": 50, "max": 90,
                         "label": "Exit RSI Min"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA"},
            "max_hold_days": {"type": "number", "default": 7, "min": 1, "max": 20,
                              "label": "Max Hold Days"},
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        period = int(self._get(req, "rsi_period", 2))
        trend = int(self._get(req, "trend_sma", 200))
        drop_days = int(self._get(req, "drop_days", 3))

        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi_col = f"RSI_{period}"
        df[rsi_col] = (100 - (100 / (1 + rs))).fillna(50)
        drops = df[rsi_col] < df[rsi_col].shift(1)
        df[f"RSI_DROP_STREAK_{period}"] = drops.astype(int).groupby((~drops).cumsum()).cumsum()
        df[f"RSI_DROP_START_{period}_{drop_days}"] = df[rsi_col].shift(drop_days)

        avg_gain14 = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss14 = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs14 = avg_gain14 / avg_loss14.replace(0, np.nan)
        df["RSI"] = (100 - (100 / (1 + rs14))).fillna(50)
        df["RSI_14"] = df["RSI"]

        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
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

        period = int(self._get(req, "rsi_period", 2))
        drop_days = int(self._get(req, "drop_days", 3))
        rsi = row.get(f"RSI_{period}")
        streak = row.get(f"RSI_DROP_STREAK_{period}", 0)
        start = row.get(f"RSI_DROP_START_{period}_{drop_days}")
        if rsi is None or pd.isna(rsi) or start is None or pd.isna(start):
            return False
        if int(streak) < drop_days:
            return False
        if float(start) >= float(self._get(req, "first_drop_max", 60)):
            return False
        return float(rsi) < float(self._get(req, "entry_rsi", 10))

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        period = int(self._get(req, "rsi_period", 2))
        rsi = row.get(f"RSI_{period}")
        if rsi is not None and not pd.isna(rsi) and float(rsi) > float(self._get(req, "exit_rsi", 70)):
            return True, "rsi_revert"
        days_held = i - trade_state["entry_idx"]
        if days_held >= int(self._get(req, "max_hold_days", 7)):
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
