"""Williams %R oversold-bounce strategy on SPY.

Sources:
  - Finwiz, "Williams %R: Momentum Oscillator Trading Guide": describes an
    oversold-bounce strategy that combines Williams %R with a 200-day moving
    average trend filter.
  - StockSharp, "Williams R Mean Reversion Strategy": describes entering
    oversold Williams %R readings and exiting as the oscillator mean-reverts.

Logic:
  Entry (long bias):
    - Close > SMA(trend_sma), default 200.
    - Williams %R(lookback), default 10, is below entry_wr, default -90.

  Exit:
    - Williams %R rises above exit_wr, default -50, or
    - Close > SMA(exit_sma), default 5, or
    - max-hold / expiry.

Topology: bull-call debit spread, 7 DTE, $5 wide. This is a short-hold
mean-reversion setup after SPY closes near the low of its recent range.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class WilliamsRStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Williams %R Pullback"

    @classmethod
    def id(cls) -> str:
        return "williams_r"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "lookback": {"type": "number", "default": 10, "min": 5, "max": 20,
                         "label": "Williams %R Lookback"},
            "entry_wr": {"type": "number", "default": -90, "min": -100, "max": -70,
                         "label": "Entry %R Max"},
            "exit_wr": {"type": "number", "default": -50, "min": -80, "max": -20,
                        "label": "Exit %R Min"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA"},
            "exit_sma": {"type": "number", "default": 5, "min": 3, "max": 20,
                         "label": "Exit SMA"},
            "max_hold_days": {"type": "number", "default": 7, "min": 1, "max": 20,
                              "label": "Max Hold Days"},
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        lookback = int(self._get(req, "lookback", 10))
        trend = int(self._get(req, "trend_sma", 200))
        exit_sma = int(self._get(req, "exit_sma", 5))

        highest = df["High"].rolling(window=lookback).max()
        lowest = df["Low"].rolling(window=lookback).min()
        denom = (highest - lowest).replace(0, np.nan)
        df[f"WILLR_{lookback}"] = ((highest - df["Close"]) / denom * -100).fillna(-50)

        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain14 = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss14 = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs14 = avg_gain14 / avg_loss14.replace(0, np.nan)
        df["RSI"] = (100 - (100 / (1 + rs14))).fillna(50)
        df["RSI_14"] = df["RSI"]

        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
        df[f"SMA_{exit_sma}"] = df["Close"].rolling(window=exit_sma).mean()
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
        lookback = int(self._get(req, "lookback", 10))
        willr = row.get(f"WILLR_{lookback}")
        return willr is not None and not pd.isna(willr) and float(willr) < float(self._get(req, "entry_wr", -90))

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        lookback = int(self._get(req, "lookback", 10))
        willr = row.get(f"WILLR_{lookback}")
        if willr is not None and not pd.isna(willr) and float(willr) > float(self._get(req, "exit_wr", -50)):
            return True, "willr_revert"
        exit_sma = int(self._get(req, "exit_sma", 5))
        sma_exit = row.get(f"SMA_{exit_sma}")
        if sma_exit is not None and not pd.isna(sma_exit) and float(row["Close"]) > float(sma_exit):
            return True, "sma_cross"
        days_held = i - trade_state["entry_idx"]
        if days_held >= int(self._get(req, "max_hold_days", 7)):
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
