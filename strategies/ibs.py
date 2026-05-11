"""Internal Bar Strength (IBS) mean-reversion on SPY.

IBS = (Close - Low) / (High - Low). Range [0, 1].

Source: Quantpedia "Internal Bar Strength as Equity Market Predictor" (2014),
replicated across SPY/QQQ/IWM 2003-2024:
  - IBS < 0.2 → next-day return positive ~62% of the time, average +0.35%
  - IBS > 0.8 → next-day return positive ~45% of the time, slightly negative

Logic:
  Entry (long bias):
    - IBS < entry_ibs (default 0.2) — closed near the day's low
    - Optional: Close > 200-day SMA (uptrend filter)

  Exit:
    - First up-close day (Close > previous Close), OR
    - IBS > exit_ibs (default 0.7), OR
    - Time exit at max_hold_days

Suitable for: bull-call debit spreads with 7–14 DTE bought on a weak close,
exited on the first strong-close reversal. Stable, very short hold (~1-3 days
average).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class IbsStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "shipped"

    @property
    def name(self) -> str:
        return "Internal Bar Strength"

    @classmethod
    def id(cls) -> str:
        return "ibs"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "entry_ibs": {"type": "number", "default": 0.2, "min": 0.05, "max": 0.5,
                          "label": "Entry IBS Max",
                          "description": "Long entry when IBS < this value (close near day low)"},
            "exit_ibs": {"type": "number", "default": 0.7, "min": 0.5, "max": 0.95,
                         "label": "Exit IBS Min",
                         "description": "Exit when IBS > this value (close near day high)"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA",
                          "description": "Only buy dips when Close > this SMA"},
            "use_trend_filter": {"type": "boolean", "default": True,
                                 "label": "Use Trend Filter",
                                 "description": "When off, IBS fires regardless of trend"},
            "max_hold_days": {"type": "number", "default": 5, "min": 1, "max": 15,
                              "label": "Max Hold Days",
                              "description": "Hard time-based exit"},
            "exit_on_up_close": {"type": "boolean", "default": True,
                                 "label": "Exit on First Up Close",
                                 "description": "Take profit on first day Close > prior Close"},
        }

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        # Internal Bar Strength
        rng = (df["High"] - df["Low"]).replace(0, np.nan)
        df["IBS"] = ((df["Close"] - df["Low"]) / rng).fillna(0.5)

        # Trend filter SMA
        trend = int(getattr(req, "trend_sma", 200) or 200)
        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()

        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        df[f"EMA_{getattr(req, 'ema_length', 10)}"] = (
            df["Close"].ewm(span=int(getattr(req, "ema_length", 10) or 10), adjust=False).mean()
        )
        df["Volume_MA"] = df["Volume"].rolling(window=10).mean()
        log_ret = np.log(df["Close"] / df["Close"].shift(1))
        df["HV_21"] = (log_ret.rolling(window=21).std() * np.sqrt(252)).fillna(0.15)

        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["RSI"] = (100 - (100 / (1 + rs))).fillna(50)

        df["prev_close"] = df["Close"].shift(1)
        return df

    @staticmethod
    def _is_bear(req) -> bool:
        return (
            getattr(req, "direction", "") == "bear"
            or getattr(req, "strategy_type", "") == "bear_put"
        )

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        trend = int(getattr(req, "trend_sma", 200) or 200)
        if i < trend:
            return False
        row = df.iloc[i]
        ibs = row.get("IBS")
        if ibs is None or pd.isna(ibs):
            return False

        entry_ibs = float(getattr(req, "entry_ibs", 0.2) or 0.2)
        is_bear = self._is_bear(req)
        if is_bear:
            # mirrored: enter on strong close in a downtrend
            ibs_cond = float(ibs) > (1.0 - entry_ibs)
        else:
            ibs_cond = float(ibs) < entry_ibs

        if not ibs_cond:
            return False

        if bool(getattr(req, "use_trend_filter", True)):
            sma = row.get(f"SMA_{trend}")
            if sma is None or pd.isna(sma):
                return False
            if is_bear and float(row["Close"]) >= float(sma):
                return False
            if (not is_bear) and float(row["Close"]) <= float(sma):
                return False
        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        ibs = row.get("IBS")
        max_hold = int(getattr(req, "max_hold_days", 5) or 5)
        days_held = i - trade_state["entry_idx"]
        is_bear = self._is_bear(req)

        # Up-close exit (first day with Close > prior Close)
        if bool(getattr(req, "exit_on_up_close", True)) and i >= 1:
            prev_close = row.get("prev_close")
            if prev_close is not None and not pd.isna(prev_close):
                if is_bear and float(row["Close"]) < float(prev_close):
                    return True, "down_close"
                if (not is_bear) and float(row["Close"]) > float(prev_close):
                    return True, "up_close"

        if ibs is not None and not pd.isna(ibs):
            exit_ibs = float(getattr(req, "exit_ibs", 0.7) or 0.7)
            if is_bear and float(ibs) < (1.0 - exit_ibs):
                return True, "ibs_target"
            if (not is_bear) and float(ibs) > exit_ibs:
                return True, "ibs_target"

        if days_held >= max_hold:
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
