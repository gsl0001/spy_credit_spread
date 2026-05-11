"""Connors RSI 25/75 ETF pullback strategy on SPY.

Sources:
  - TradingMarkets, "ETF Software and RSI 25/75": describes Larry Connors'
    RSI 25/75 as buying ETFs after pullbacks above the 200-day moving average
    and selling into strength.
  - Easycators, "RSI 25-75 Trading Strategy": references Connors and Cesar
    Alvarez's "High Probability ETF Trading" and the strategy's ETF focus.

Logic:
  Entry (long bias):
    - Close > SMA(trend_sma), default 200.
    - RSI(rsi_period), default 4, closes below entry_rsi, default 25.

  Exit:
    - RSI(rsi_period) closes above exit_rsi, default 55, or
    - Close > SMA(exit_sma), default 5, or
    - max-hold / expiry.

Topology: bull-call debit spread, 7 DTE, $5 wide. This is a short-hold
mean-reversion pullback setup.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class Rsi2575Strategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Connors RSI 25/75"

    @classmethod
    def id(cls) -> str:
        return "rsi_25_75"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "rsi_period": {"type": "number", "default": 4, "min": 2, "max": 8,
                           "label": "RSI Period"},
            "entry_rsi": {"type": "number", "default": 25, "min": 10, "max": 40,
                          "label": "Entry RSI Max"},
            "exit_rsi": {"type": "number", "default": 55, "min": 45, "max": 80,
                         "label": "Exit RSI Min"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA"},
            "exit_sma": {"type": "number", "default": 5, "min": 3, "max": 20,
                         "label": "Exit SMA"},
            "max_hold_days": {"type": "number", "default": 7, "min": 1, "max": 20,
                              "label": "Max Hold Days"},
            "require_down_day": {"type": "boolean", "default": False,
                                 "label": "Require Down Day"},
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    @staticmethod
    def _is_bear(req) -> bool:
        return (
            getattr(req, "direction", "") == "bear"
            or getattr(req, "strategy_type", "") == "bear_put"
        )

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        period = int(self._get(req, "rsi_period", 4))
        trend = int(self._get(req, "trend_sma", 200))
        exit_sma = int(self._get(req, "exit_sma", 5))

        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df[f"RSI_{period}"] = (100 - (100 / (1 + rs))).fillna(50)

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
        df["is_down_day"] = df["Close"] < df["Open"]
        return df

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        trend = int(self._get(req, "trend_sma", 200))
        if i < trend:
            return False
        row = df.iloc[i]
        period = int(self._get(req, "rsi_period", 4))
        rsi = row.get(f"RSI_{period}")
        sma = row.get(f"SMA_{trend}")
        if rsi is None or pd.isna(rsi) or sma is None or pd.isna(sma):
            return False

        is_bear = self._is_bear(req)
        if is_bear:
            if float(row["Close"]) >= float(sma):
                return False
            if float(rsi) <= float(100 - self._get(req, "entry_rsi", 25)):
                return False
        else:
            if float(row["Close"]) <= float(sma):
                return False
            if float(rsi) >= float(self._get(req, "entry_rsi", 25)):
                return False

        if bool(self._get(req, "require_down_day", False)) and i >= 1:
            prior_down = bool(df.iloc[i - 1].get("is_down_day", False))
            if (not is_bear and not prior_down) or (is_bear and prior_down):
                return False
        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        period = int(self._get(req, "rsi_period", 4))
        rsi = row.get(f"RSI_{period}")
        is_bear = self._is_bear(req)
        if rsi is not None and not pd.isna(rsi):
            exit_rsi = float(self._get(req, "exit_rsi", 55))
            if (not is_bear and float(rsi) > exit_rsi) or (is_bear and float(rsi) < 100 - exit_rsi):
                return True, "rsi_revert"

        exit_sma = int(self._get(req, "exit_sma", 5))
        sma_exit = row.get(f"SMA_{exit_sma}")
        if sma_exit is not None and not pd.isna(sma_exit):
            if (not is_bear and float(row["Close"]) > float(sma_exit)) or (
                is_bear and float(row["Close"]) < float(sma_exit)
            ):
                return True, "sma_cross"

        days_held = i - trade_state["entry_idx"]
        if days_held >= int(self._get(req, "max_hold_days", 7)):
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
