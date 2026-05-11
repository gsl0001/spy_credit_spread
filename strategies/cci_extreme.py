"""CCI (Commodity Channel Index) extreme oversold reversion on SPY.

Sources:
  - Donald Lambert, "Commodities Channel Index: Tools for Trading Cyclic
    Trends," Commodities (1980). Original definition with ±100 thresholds.
  - Connors Research replication on SPY/QQQ 2003-2023: CCI(20) closing
    < -150 in an uptrend (Close > SMA-200) has ~68% positive next-3-day
    return, average +0.72%, materially better than the unconditional
    base rate.

Distinct from existing strategies:
  - `rsi2`, `ibs` trigger on close-position-in-range. CCI uses typical
    price ((H+L+C)/3) deviation from a 20-day mean — fires on midday
    plunges that recover by close (which RSI2/IBS dampen) and on
    multi-day distribution selling that hasn't yet crashed the close.

Logic:
  CCI(N) = (typical_price - SMA_N(typical_price)) / (0.015 × MAD_N)
    where MAD_N = mean absolute deviation over N bars.

  Entry (long bias):
    - CCI(cci_period) < entry_cci (default -150)
    - Close > SMA(trend_sma) — uptrend filter to avoid catching falling
      knife in bear regime.

  Exit:
    - CCI > exit_cci (default 0) — return to neutral.
    - First up-close (Close > prior Close) [optional, default True].
    - max_hold_days hard limit (default 4).
    - Mandatory expiry exit.

Topology: bull-call (or bear-put) debit spread, 7 DTE, $5 wide. Hold 1-3
days. Per skill Step 2: short-hold mean-reversion → debit spread fits.
Trade count expected: 25-50 / 5y at -150 threshold.

VETTING RESULT (2026-05-10): REJECTED.
  Tested SPY 5y, bull-only, bull_call $5-wide 7 DTE, 50/50 SL/TP.
  3 parameter sweeps, best result:
    (cci=20, entry=-100, exit=100, trend=200, hold=4, no up_close)
    36 trades, WR 50.0%, PF 1.11, Sharpe 0.17, DD -7.8%
    (cci=20, entry=-150, exit=100): 22 tr, WR 41%, PF 0.74, Sh -0.29
    (cci=10, entry=-150, exit=100): 29 tr, WR 48%, PF 1.02, Sh 0.04
  All sweeps fail PF≥1.5 and Sharpe≥1.0 thresholds.
  Root cause: same family as `bollinger_b` — CCI's mean-deviation
  divisor scales with realized vol, so "extreme" readings cluster in
  high-vol regimes where SPY's next-day reversion is also noisier.
  The original hypothesis (CCI catches midday-plunge-recovered closes
  that RSI2/IBS miss) doesn't bear out: those days have weak follow-
  through because the close already absorbed the reversion.
  Path to graduation:
    1. Combine with explicit low-vol regime gate (HV_21 < 0.15 only) —
       but at that point trade count drops below 20.
    2. Use as a confluence filter on top of `rsi2` rather than a
       standalone trigger.
    3. Replace CCI with z-score of typical-price minus close-position
       (a true "midday plunge unrecovered" detector) — different
       strategy, not a re-tune.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class CciExtremeStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "CCI Extreme Reversion"

    @classmethod
    def id(cls) -> str:
        return "cci_extreme"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "cci_period": {"type": "number", "default": 20, "min": 5, "max": 50,
                           "label": "CCI Period",
                           "description": "Lookback for CCI calculation"},
            "entry_cci": {"type": "number", "default": -150, "min": -300, "max": -100,
                          "label": "Entry CCI Threshold",
                          "description": "Long entry when CCI below this (negative = oversold)"},
            "exit_cci": {"type": "number", "default": 0, "min": -100, "max": 100,
                         "label": "Exit CCI Threshold",
                         "description": "Exit when CCI rises above this"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA",
                          "description": "Only buy oversold readings when Close > this SMA"},
            "use_trend_filter": {"type": "boolean", "default": True,
                                 "label": "Use Trend Filter"},
            "max_hold_days": {"type": "number", "default": 4, "min": 1, "max": 10,
                              "label": "Max Hold Days"},
            "exit_on_up_close": {"type": "boolean", "default": True,
                                 "label": "Exit on First Up Close"},
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        cci_n = int(self._get(req, "cci_period", 20) or 20)
        trend = int(self._get(req, "trend_sma", 200) or 200)

        tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
        sma_tp = tp.rolling(window=cci_n).mean()
        mad = tp.rolling(window=cci_n).apply(
            lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
        )
        df["CCI"] = (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))
        df["CCI"] = df["CCI"].fillna(0)

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

        df["prev_close"] = df["Close"].shift(1)
        return df

    @staticmethod
    def _is_bear(req) -> bool:
        return (
            getattr(req, "direction", "") == "bear"
            or getattr(req, "strategy_type", "") == "bear_put"
        )

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        trend = int(self._get(req, "trend_sma", 200) or 200)
        if i < max(trend, int(self._get(req, "cci_period", 20) or 20) + 1):
            return False
        row = df.iloc[i]
        cci = row.get("CCI")
        if cci is None or pd.isna(cci):
            return False

        entry_cci = float(self._get(req, "entry_cci", -150) or -150)
        is_bear = self._is_bear(req)
        if is_bear:
            if float(cci) < abs(entry_cci):
                return False
            cci_cond = float(cci) > abs(entry_cci)
        else:
            cci_cond = float(cci) < entry_cci
        if not cci_cond:
            return False

        if bool(self._get(req, "use_trend_filter", True)):
            sma = row.get(f"SMA_{trend}")
            if sma is None or pd.isna(sma):
                return False
            close = float(row.get("Close", 0))
            if is_bear and close >= float(sma):
                return False
            if (not is_bear) and close <= float(sma):
                return False
        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        days_held = i - trade_state["entry_idx"]
        max_hold = int(self._get(req, "max_hold_days", 4) or 4)
        is_bear = self._is_bear(req)

        if bool(self._get(req, "exit_on_up_close", True)) and i >= 1:
            prev_close = row.get("prev_close")
            if prev_close is not None and not pd.isna(prev_close):
                if is_bear and float(row["Close"]) < float(prev_close):
                    return True, "down_close"
                if (not is_bear) and float(row["Close"]) > float(prev_close):
                    return True, "up_close"

        cci = row.get("CCI")
        if cci is not None and not pd.isna(cci):
            exit_cci = float(self._get(req, "exit_cci", 0) or 0)
            if is_bear and float(cci) < -abs(exit_cci):
                return True, "cci_target"
            if (not is_bear) and float(cci) > exit_cci:
                return True, "cci_target"

        if days_held >= max_hold:
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
