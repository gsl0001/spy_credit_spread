"""Turnaround Tuesday weakness-reversal on SPY.

Sources:
  - QuantifiedStrategies.com, "The Turnaround Tuesday Trading Strategy":
    Monday weakness in SPY has historically shown positive Tuesday reversal
    expectancy, especially when Monday closes down and IBS is low.
  - Larry Connors / Cesar Alvarez short-term mean-reversion work: combine
    day-of-week seasonality with oversold daily range location.

Logic:
  Entry (long bias):
    - Today is Monday.
    - Monday closes below its open.
    - Internal Bar Strength (IBS) is below entry_ibs (default 0.2).
    - Optional: Monday close is down at least min_down_pct from prior close.
    - Optional uptrend filter: Close > SMA(trend_sma).

  Exit:
    - Tuesday close, or
    - max_hold_days fallback, or
    - position expiry.

Topology: bull-call debit spread, 7 DTE, $5 wide. This is a short-hold
directional reversal setup, so debit verticals match the trigger profile.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class TurnaroundTuesdayStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Turnaround Tuesday"

    @classmethod
    def id(cls) -> str:
        return "turnaround_tuesday"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "entry_ibs": {"type": "number", "default": 0.2, "min": 0.05, "max": 0.5,
                          "label": "Entry IBS Max"},
            "min_down_pct": {"type": "number", "default": 0.0, "min": 0.0, "max": 3.0,
                             "label": "Min Close-vs-Previous Down %"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA"},
            "use_trend_filter": {"type": "boolean", "default": True,
                                 "label": "Use Trend Filter"},
            "max_hold_days": {"type": "number", "default": 2, "min": 1, "max": 5,
                              "label": "Max Hold Days"},
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

        rng = (df["High"] - df["Low"]).replace(0, np.nan)
        df["IBS"] = ((df["Close"] - df["Low"]) / rng).fillna(0.5)
        df["prev_close"] = df["Close"].shift(1)
        df["close_vs_prev_pct"] = (df["Close"] / df["prev_close"] - 1.0) * 100.0
        df["is_down_day"] = df["Close"] < df["Open"]

        dates = pd.to_datetime(df["Date"] if "Date" in df.columns else df.index)
        df["weekday"] = dates.dt.weekday

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
        if i < trend or self._is_bear(req):
            return False
        row = df.iloc[i]
        if int(row.get("weekday", -1)) != 0:
            return False
        if not bool(row.get("is_down_day", False)):
            return False
        ibs = row.get("IBS")
        if ibs is None or pd.isna(ibs) or float(ibs) >= float(self._get(req, "entry_ibs", 0.2)):
            return False
        min_down = float(self._get(req, "min_down_pct", 0.0))
        ret = row.get("close_vs_prev_pct")
        if min_down > 0 and (ret is None or pd.isna(ret) or float(ret) > -min_down):
            return False
        if bool(self._get(req, "use_trend_filter", True)):
            sma = row.get(f"SMA_{trend}")
            if sma is None or pd.isna(sma) or float(row["Close"]) <= float(sma):
                return False
        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        days_held = i - trade_state["entry_idx"]
        if days_held >= 1 and int(row.get("weekday", -1)) == 1:
            return True, "tuesday_close"
        if days_held >= int(self._get(req, "max_hold_days", 2)):
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
