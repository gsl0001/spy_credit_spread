"""FOMC Pre-Meeting Drift on SPY.

Source: QuantifiedStrategies.com "FOMC Meetings Trading Strategy Backtest"
(https://www.quantifiedstrategies.com/fomc-meeting-trading-strategy/)
Edge: SPY averages +0.5% over the 2-day window before FOMC decision vs +0.08%
for random 2-day windows (1997–present). Institutional pre-positioning drives
this persistent anomaly.

Logic:
  Entry:
    - Two trading days before a scheduled FOMC meeting date.
    - Optional: SPY > SMA_200 (avoid recession-era anomalies).

  Exit:
    - FOMC decision day close (hold = 2 trading days), OR
    - max_hold_days fallback (default 3), OR
    - expired.

FOMC dates: hard-coded through end of 2026. Update annually.

Topology note: The drift (~0.5% over 2 days) is too small for a 50% SL debit
spread to survive consistently. A bull-put CREDIT spread would harvest theta
during the hold and not need the underlying to move. Debit version is tested
here; credit version is the graduation path once the engine supports it.

BAR_SIZE: "1 day" — daily harness.

VETTING RESULT: REJECTED (2026-05-10)
  3 sweeps: best was 34 trades, WR 47%, PF 1.25, Sharpe 0.29.
  Root cause: debit spread buys into elevated pre-FOMC IV, then gets IV-crushed
  on announcement. The +0.5% underlying drift doesn't survive option mechanics.
  Path to graduation: SELL IV into FOMC using a credit spread or iron condor
  (sell the pre-meeting IV spike, profit from post-announcement crush).
  Blocked on credit-spread / iron-condor engine support.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy

# FOMC decision dates — trading day when the statement is released (typically Wed/Thu)
# Source: Federal Reserve website. Update annually.
_FOMC_DATES: list[str] = [
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-11-05", "2025-12-17",
    # 2026
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
]


class FomcPreStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "FOMC Pre-Meeting Drift"

    @classmethod
    def id(cls) -> str:
        return "fomc_pre"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "days_before": {
                "type": "number", "default": 2, "min": 1, "max": 4,
                "label": "Days Before FOMC",
                "description": "Enter N trading days before the FOMC decision date",
            },
            "use_trend_filter": {
                "type": "boolean", "default": True,
                "label": "Use SMA-200 Trend Filter",
                "description": "Only enter when SPY Close > SMA_200",
            },
            "max_hold_days": {
                "type": "number", "default": 3, "min": 1, "max": 5,
                "label": "Max Hold Days",
                "description": "Hard time-based exit (covers the FOMC day + buffer)",
            },
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()

        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["SMA_50"] = df["Close"].rolling(window=50).mean()
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

        # Mark FOMC decision dates and N-days-before dates
        fomc_ts = pd.to_datetime(_FOMC_DATES)
        df_dates = pd.to_datetime(df["Date"] if "Date" in df.columns else df.index)

        days_before = int(self._get(req, "days_before", 2))
        df["is_fomc_day"] = df_dates.isin(fomc_ts)

        # For each trading day, check if a FOMC date falls exactly `days_before` bars ahead
        # We do this by shifting the FOMC flag backward
        df["is_fomc_entry"] = df["is_fomc_day"].shift(-days_before, fill_value=False)

        return df

    @staticmethod
    def _is_bear(req) -> bool:
        return (
            getattr(req, "direction", "") == "bear"
            or getattr(req, "strategy_type", "") == "bear_put"
        )

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        if i < 200:
            return False
        row = df.iloc[i]

        is_entry = bool(row.get("is_fomc_entry", False))
        if not is_entry:
            return False

        is_bear = self._is_bear(req)

        if bool(self._get(req, "use_trend_filter", True)):
            sma = row.get("SMA_200")
            if sma is None or pd.isna(sma):
                return False
            if not is_bear and float(row["Close"]) <= float(sma):
                return False
            if is_bear and float(row["Close"]) >= float(sma):
                return False

        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        days_held = i - trade_state.get("entry_idx", i)
        max_hold = int(self._get(req, "max_hold_days", 3))

        # Exit on FOMC day (days_before trading days after entry)
        row = df.iloc[i]
        if bool(row.get("is_fomc_day", False)) and days_held >= 1:
            return True, "fomc_day"

        if days_held >= max_hold:
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
