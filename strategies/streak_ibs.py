"""Down-streak + IBS mean-reversion on SPY.

Sources:
  - Larry Connors / Cesar Alvarez short-term ETF mean-reversion work:
    consecutive down closes in liquid index ETFs tend to mean-revert.
  - Quantpedia, "Internal Bar Strength as Equity Market Predictor": low IBS
    strengthens the next-session reversal signal.

Logic:
  Entry (long bias):
    - SPY has closed down for `down_days` consecutive sessions.
    - Today's IBS is below `entry_ibs`, meaning the close is near the daily low.
    - Optional uptrend filter: Close > SMA(trend_sma).

  Exit:
    - First up-close, or
    - IBS rebounds above `exit_ibs`, or
    - max-hold / expiry.

Topology: bull-call debit spread, 7 DTE, $5 wide. This is a short-hold,
directional reversal setup after clustered weakness.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class StreakIbsStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "shipped"

    @property
    def name(self) -> str:
        return "Down-Streak + IBS"

    @classmethod
    def id(cls) -> str:
        return "streak_ibs"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "down_days": {"type": "number", "default": 2, "min": 1, "max": 5,
                          "label": "Consecutive Down Closes"},
            "entry_ibs": {"type": "number", "default": 0.3, "min": 0.05, "max": 0.6,
                          "label": "Entry IBS Max"},
            "exit_ibs": {"type": "number", "default": 0.7, "min": 0.4, "max": 0.95,
                         "label": "Exit IBS Min"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA"},
            "use_trend_filter": {"type": "boolean", "default": True,
                                 "label": "Use Trend Filter"},
            "max_hold_days": {"type": "number", "default": 5, "min": 1, "max": 15,
                              "label": "Max Hold Days"},
            "exit_on_up_close": {"type": "boolean", "default": True,
                                 "label": "Exit on First Up Close"},
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
        trend = int(self._get(req, "trend_sma", 200))

        df["prev_close"] = df["Close"].shift(1)
        df["down_close"] = df["Close"] < df["prev_close"]
        streak = []
        cur = 0
        for is_down in df["down_close"].fillna(False):
            cur = cur + 1 if bool(is_down) else 0
            streak.append(cur)
        df["down_streak"] = streak

        rng = (df["High"] - df["Low"]).replace(0, np.nan)
        df["IBS"] = ((df["Close"] - df["Low"]) / rng).fillna(0.5)

        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
        ema_len = int(getattr(req, "ema_length", 10) or 10)
        df[f"EMA_{ema_len}"] = df["Close"].ewm(span=ema_len, adjust=False).mean()
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
        return df

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        trend = int(self._get(req, "trend_sma", 200))
        if i < trend:
            return False
        row = df.iloc[i]
        is_bear = self._is_bear(req)
        down_days = int(self._get(req, "down_days", 2))
        ibs = row.get("IBS")
        if ibs is None or pd.isna(ibs):
            return False

        if is_bear:
            return False
        if int(row.get("down_streak", 0)) < down_days:
            return False
        if float(ibs) >= float(self._get(req, "entry_ibs", 0.3)):
            return False

        if bool(self._get(req, "use_trend_filter", True)):
            sma = row.get(f"SMA_{trend}")
            if sma is None or pd.isna(sma) or float(row["Close"]) <= float(sma):
                return False
        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        days_held = i - trade_state["entry_idx"]
        if bool(self._get(req, "exit_on_up_close", True)) and i >= 1:
            prev_close = row.get("prev_close")
            if prev_close is not None and not pd.isna(prev_close) and float(row["Close"]) > float(prev_close):
                return True, "up_close"
        ibs = row.get("IBS")
        if ibs is not None and not pd.isna(ibs) and float(ibs) > float(self._get(req, "exit_ibs", 0.7)):
            return True, "ibs_target"
        if days_held >= int(self._get(req, "max_hold_days", 5)):
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
