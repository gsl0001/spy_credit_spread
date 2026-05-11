"""Connors/Alvarez Cumulative RSI(2) pullback strategy on SPY.

Sources:
  - Easycators, "Cumulative RSI-2 Trading Strategy": identifies the strategy
    as coming from Larry Connors and Cesar Alvarez's "Short Term Trading
    Strategies That Work" and describes high historical SPY accuracy.
  - Trade Loss Tracker's extended summary of the book: describes cumulative
    RSI as summing RSI(2) values over consecutive days to require sustained
    selling pressure.

Logic:
  Entry (long bias):
    - Close > SMA(trend_sma), default 200.
    - Sum of RSI(2) over `cum_days`, default 2, is below entry_sum.

  Exit:
    - RSI(2) closes above exit_rsi, default 65, or
    - Close > SMA(exit_sma), default 5, or
    - max-hold / expiry.

Topology: bull-call debit spread, 7 DTE, $5 wide. This is a short-hold
mean-reversion setup for sustained pullbacks inside an uptrend.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class CumulativeRsiStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Connors Cumulative RSI"

    @classmethod
    def id(cls) -> str:
        return "cumulative_rsi"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "rsi_period": {"type": "number", "default": 2, "min": 2, "max": 4,
                           "label": "RSI Period"},
            "cum_days": {"type": "number", "default": 2, "min": 2, "max": 4,
                         "label": "Cumulative RSI Days"},
            "entry_sum": {"type": "number", "default": 35, "min": 5, "max": 80,
                          "label": "Entry Cumulative RSI Max"},
            "exit_rsi": {"type": "number", "default": 65, "min": 50, "max": 90,
                         "label": "Exit RSI Min"},
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
        period = int(self._get(req, "rsi_period", 2))
        cum_days = int(self._get(req, "cum_days", 2))
        trend = int(self._get(req, "trend_sma", 200))
        exit_sma = int(self._get(req, "exit_sma", 5))

        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi_col = f"RSI_{period}"
        df[rsi_col] = (100 - (100 / (1 + rs))).fillna(50)
        df[f"CUM_RSI_{period}_{cum_days}"] = df[rsi_col].rolling(window=cum_days).sum()

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
        period = int(self._get(req, "rsi_period", 2))
        cum_days = int(self._get(req, "cum_days", 2))
        cum = row.get(f"CUM_RSI_{period}_{cum_days}")
        return cum is not None and not pd.isna(cum) and float(cum) < float(self._get(req, "entry_sum", 35))

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        period = int(self._get(req, "rsi_period", 2))
        rsi = row.get(f"RSI_{period}")
        if rsi is not None and not pd.isna(rsi) and float(rsi) > float(self._get(req, "exit_rsi", 65)):
            return True, "rsi_revert"
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
