"""Gap-down reversal on SPY.

Sources:
  - QuantifiedStrategies.com gap-down mean-reversion studies on SPY:
    overnight gaps lower tend to mean-revert when the ETF closes weak but
    remains in a broader uptrend.
  - Connors/Alvarez short-term ETF reversal work: combine a short-term
    dislocation with an uptrend filter and a quick time exit.

Logic:
  Entry (long bias):
    - Today's open gaps below prior close by at least gap_down_pct.
    - Today's close remains down from prior close by at least min_close_down_pct
      so the setup is still available at the daily close.
    - Optional: close in the lower part of the day via IBS <= entry_ibs.
    - Optional uptrend filter: Close > SMA(trend_sma).

  Exit:
    - First up-close, or
    - Close > SMA(exit_sma), or
    - max-hold / expiry.

Topology: bull-call debit spread, 7 DTE, $5 wide. The thesis expects a
directional rebound within 1-3 days.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class GapDownReversalStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Gap-Down Reversal"

    @classmethod
    def id(cls) -> str:
        return "gap_down_reversal"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "gap_down_pct": {"type": "number", "default": 0.35, "min": 0.1, "max": 2.0,
                             "label": "Gap Down %"},
            "min_close_down_pct": {"type": "number", "default": 0.25, "min": 0.0, "max": 2.0,
                                   "label": "Min Close Down %"},
            "entry_ibs": {"type": "number", "default": 0.4, "min": 0.05, "max": 0.8,
                          "label": "Entry IBS Max"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA"},
            "use_trend_filter": {"type": "boolean", "default": True,
                                 "label": "Use Trend Filter"},
            "exit_sma": {"type": "number", "default": 5, "min": 3, "max": 20,
                         "label": "Exit SMA"},
            "max_hold_days": {"type": "number", "default": 3, "min": 1, "max": 10,
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
        exit_n = int(self._get(req, "exit_sma", 5))

        df["prev_close"] = df["Close"].shift(1)
        df["gap_pct"] = (df["Open"] / df["prev_close"] - 1.0) * 100.0
        df["close_vs_prev_pct"] = (df["Close"] / df["prev_close"] - 1.0) * 100.0
        rng = (df["High"] - df["Low"]).replace(0, np.nan)
        df["IBS"] = ((df["Close"] - df["Low"]) / rng).fillna(0.5)

        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
        df[f"SMA_{exit_n}"] = df["Close"].rolling(window=exit_n).mean()
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
        gap = row.get("gap_pct")
        close_ret = row.get("close_vs_prev_pct")
        ibs = row.get("IBS")
        if any(v is None or pd.isna(v) for v in (gap, close_ret, ibs)):
            return False
        if float(gap) > -float(self._get(req, "gap_down_pct", 0.35)):
            return False
        if float(close_ret) > -float(self._get(req, "min_close_down_pct", 0.25)):
            return False
        if float(ibs) > float(self._get(req, "entry_ibs", 0.4)):
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
        exit_n = int(self._get(req, "exit_sma", 5))
        sma_exit = row.get(f"SMA_{exit_n}")
        if sma_exit is not None and not pd.isna(sma_exit) and float(row["Close"]) > float(sma_exit):
            return True, "sma_cross"
        if days_held >= int(self._get(req, "max_hold_days", 3)):
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
