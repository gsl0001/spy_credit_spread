"""Connors RSI(2) mean-reversion on SPY.

Source: Larry Connors, "Short-Term Trading Strategies That Work" (2008) and
QuantPedia "RSI(2) Mean-Reversion" study (replicated on SPY 2002-2024 with
~70% win rate, profit factor ~1.6 on liquid US equities).

Logic:
  Entry (long bias):
    - 2-period RSI < entry_rsi (default 10) — extreme short-term oversold
    - Close > 200-day SMA (uptrend filter — only buy dips in established uptrends)
    - Optional: previous day was a down day (avoid catching falling knives)

  Exit:
    - 2-period RSI > exit_rsi (default 70), OR
    - Close > 5-day SMA (the standard Connors short-term reversal signal), OR
    - Time exit at max_hold_days bars

Suitable for: bull-call debit spreads with 7–14 DTE on the dip, exited on
RSI mean-reversion. Bear path uses RSI > 90 + price < SMA(200) for short
entries (bear-put debits) but trades less frequently in modern bull tape.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class Rsi2Strategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "shipped"

    @property
    def name(self) -> str:
        return "Connors RSI(2)"

    @classmethod
    def id(cls) -> str:
        return "rsi2"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "rsi_period": {"type": "number", "default": 2, "min": 2, "max": 5,
                           "label": "RSI Period",
                           "description": "Connors uses 2; some studies prefer 3-4 for tamer signals"},
            "entry_rsi": {"type": "number", "default": 10, "min": 1, "max": 30,
                          "label": "Entry RSI Threshold",
                          "description": "Long entry when RSI(period) < this value"},
            "exit_rsi": {"type": "number", "default": 70, "min": 50, "max": 90,
                         "label": "Exit RSI Threshold",
                         "description": "Exit when RSI(period) > this value"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA",
                          "description": "Only buy dips when Close > this SMA (uptrend filter)"},
            "exit_sma": {"type": "number", "default": 5, "min": 3, "max": 20,
                         "label": "Exit SMA",
                         "description": "Alternative exit: Close > this short SMA"},
            "max_hold_days": {"type": "number", "default": 10, "min": 1, "max": 30,
                              "label": "Max Hold Days",
                              "description": "Time-based exit fallback"},
            "require_down_day": {"type": "boolean", "default": True,
                                 "label": "Require Prior Down Day",
                                 "description": "Avoid entry the day after an up close"},
        }

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        period = int(getattr(req, "rsi_period", 2) or 2)
        trend = int(getattr(req, "trend_sma", 200) or 200)
        exit_sma = int(getattr(req, "exit_sma", 5) or 5)

        # Standard Wilder-style RSI but with short period (Connors).
        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df[f"RSI_{period}"] = (100 - (100 / (1 + rs))).fillna(50)

        # Long-term trend filter and short-term exit reference.
        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
        df[f"SMA_{exit_sma}"] = df["Close"].rolling(window=exit_sma).mean()

        # Standard 14-period RSI for compatibility with existing entry filters.
        avg_gain14 = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss14 = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs14 = avg_gain14 / avg_loss14.replace(0, np.nan)
        df["RSI"] = (100 - (100 / (1 + rs14))).fillna(50)
        df["RSI_14"] = df["RSI"]

        # Common ancillary indicators for the shared filter pipeline.
        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        df[f"EMA_{getattr(req, 'ema_length', 10)}"] = (
            df["Close"].ewm(span=int(getattr(req, "ema_length", 10) or 10), adjust=False).mean()
        )
        df["Volume_MA"] = df["Volume"].rolling(window=10).mean()
        log_ret = np.log(df["Close"] / df["Close"].shift(1))
        df["HV_21"] = (log_ret.rolling(window=21).std() * np.sqrt(252)).fillna(0.15)

        df["is_down_day"] = df["Close"] < df["Open"]
        return df

    @staticmethod
    def _is_bear(req) -> bool:
        return (
            getattr(req, "direction", "") == "bear"
            or getattr(req, "strategy_type", "") == "bear_put"
        )

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        if i < int(getattr(req, "trend_sma", 200) or 200):
            return False
        row = df.iloc[i]
        period = int(getattr(req, "rsi_period", 2) or 2)
        rsi = row.get(f"RSI_{period}")
        if rsi is None or pd.isna(rsi):
            return False

        trend = int(getattr(req, "trend_sma", 200) or 200)
        sma_trend = row.get(f"SMA_{trend}")
        if sma_trend is None or pd.isna(sma_trend):
            return False

        is_bear = self._is_bear(req)
        if is_bear:
            # Mirror: enter when oversold flips to overbought in a downtrend.
            entry_rsi_threshold = 100 - float(getattr(req, "entry_rsi", 10) or 10)
            in_trend = float(row["Close"]) < float(sma_trend)
            rsi_cond = float(rsi) > entry_rsi_threshold
        else:
            entry_rsi_threshold = float(getattr(req, "entry_rsi", 10) or 10)
            in_trend = float(row["Close"]) > float(sma_trend)
            rsi_cond = float(rsi) < entry_rsi_threshold

        if not (in_trend and rsi_cond):
            return False

        require_down = bool(getattr(req, "require_down_day", True))
        if require_down and i >= 1:
            prior = df.iloc[i - 1]
            prior_down = bool(prior.get("is_down_day", False))
            if is_bear:
                # for bear, require prior up day
                if prior_down:
                    return False
            else:
                if not prior_down:
                    return False
        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        period = int(getattr(req, "rsi_period", 2) or 2)
        rsi = row.get(f"RSI_{period}")
        exit_sma_n = int(getattr(req, "exit_sma", 5) or 5)
        sma_exit = row.get(f"SMA_{exit_sma_n}")
        max_hold = int(getattr(req, "max_hold_days", 10) or 10)

        days_held = i - trade_state["entry_idx"]
        is_bear = self._is_bear(req)

        if rsi is not None and not pd.isna(rsi):
            exit_rsi_threshold = float(getattr(req, "exit_rsi", 70) or 70)
            if is_bear and float(rsi) < (100 - exit_rsi_threshold):
                return True, "rsi_target"
            if (not is_bear) and float(rsi) > exit_rsi_threshold:
                return True, "rsi_target"

        if sma_exit is not None and not pd.isna(sma_exit):
            if is_bear and float(row["Close"]) < float(sma_exit):
                return True, "sma_cross"
            if (not is_bear) and float(row["Close"]) > float(sma_exit):
                return True, "sma_cross"

        if days_held >= max_hold:
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
