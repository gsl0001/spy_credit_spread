"""Monday Seasonal Bull-Call Spread on SPY.

Source: AlphaCrunching "SPX Call Spread Option Strategy | Mondays"
(https://www.alphacrunching.com/blog/call-spread-option-strategy)
Replicated on SPY by this project — 58% WR, winning trades ~1.6× losers
(SPX data Jul 2022 – Dec 2024).

Edge: Monday open captures the weekend-news gap-up / relief-rally bias.
SPY tends to resolve Friday weakness by Monday close more often than not
when the overall trend is intact. The signal fires only when Monday's
open is NOT a significant gap-down (panic-sell disqualifier) and SPY is
above its 50-day SMA (uptrend filter).

Logic:
  Entry (bull):
    - Today is Monday (weekday == 0)
    - SPY Close > SMA_50 (trend intact)
    - Friday close was NOT more than `max_friday_drop_pct` below Thursday close
      (avoids chasing a weekend-panic bounce — those tend to extend down)
    - Monday open gap is within bounds (< `max_gap_pct` from Friday close)

  Exit:
    - First up-close day (Close > prior Close), OR
    - max_hold_days reached (default 3), OR
    - expired

Suitable for: 7–14 DTE bull-call debit spread.
BAR_SIZE: "1 day" — daily harness.

VETTING RESULT: REJECTED (2026-05-10)
  Swept 3 configurations; none cleared the bar.
  Best run: 74 trades, WR 64.9%, PF 1.09, Sharpe 0.24, DD -7.3%
  Root cause: Monday drift (~0.3% expected) is too small for 50% SL debit
  mechanics. Same failure mode as dabd and vix_spike.
  Path to graduation: switch to bull-put CREDIT spread (theta tailwind absorbs
  small-drift variance). Blocked on credit-spread engine support.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class MondaySpreadStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Monday Seasonal Spread"

    @classmethod
    def id(cls) -> str:
        return "monday_spread"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "max_friday_drop_pct": {
                "type": "number", "default": 1.5, "min": 0.5, "max": 5.0,
                "label": "Max Friday Drop %",
                "description": "Skip Monday entry if Friday fell more than this % (panic disqualifier)",
            },
            "max_gap_pct": {
                "type": "number", "default": 1.0, "min": 0.0, "max": 3.0,
                "label": "Max Monday Gap %",
                "description": "Skip if Monday open gaps more than this % from Friday close (either direction)",
            },
            "use_trend_filter": {
                "type": "boolean", "default": True,
                "label": "Use SMA-50 Trend Filter",
                "description": "Only enter when SPY Close > SMA_50",
            },
            "max_hold_days": {
                "type": "number", "default": 3, "min": 1, "max": 7,
                "label": "Max Hold Days",
                "description": "Hard time-based exit",
            },
            "exit_on_up_close": {
                "type": "boolean", "default": True,
                "label": "Exit on First Up Close",
                "description": "Take profit on first day Close > prior Close",
            },
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()

        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        df["SMA_200"] = df["Close"].rolling(window=200).mean()
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
        # Friday close = the close 1 trading day before Monday (shift 1 from Mon = Fri)
        df["prev2_close"] = df["Close"].shift(2)
        df["prev_open"] = df["Open"].shift(0)  # today's open

        # Weekday (0=Monday) — match turnaround_tuesday's robust pattern
        dates = pd.to_datetime(df["Date"] if "Date" in df.columns else df.index)
        df["weekday"] = dates.dt.weekday

        return df

    @staticmethod
    def _is_bear(req) -> bool:
        return (
            getattr(req, "direction", "") == "bear"
            or getattr(req, "strategy_type", "") == "bear_put"
        )

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        if i < 50:
            return False
        row = df.iloc[i]

        # Must be Monday
        weekday = row.get("weekday")
        if weekday is None or pd.isna(weekday) or int(weekday) != 0:
            return False

        is_bear = self._is_bear(req)
        # Bear version: Tuesday after Monday weakness (mirror)
        # For simplicity: bear fires on Monday too, but reversed filters
        if is_bear:
            # sell strength — invert trend check
            use_trend = bool(self._get(req, "use_trend_filter", True))
            if use_trend:
                sma = row.get("SMA_50")
                if sma is None or pd.isna(sma):
                    return False
                if float(row["Close"]) >= float(sma):
                    return False
        else:
            use_trend = bool(self._get(req, "use_trend_filter", True))
            if use_trend:
                sma = row.get("SMA_50")
                if sma is None or pd.isna(sma):
                    return False
                if float(row["Close"]) <= float(sma):
                    return False

        # Friday drop disqualifier
        prev_close = row.get("prev_close")   # Friday close
        prev2_close = row.get("prev2_close") # Thursday close
        max_friday_drop = float(self._get(req, "max_friday_drop_pct", 1.5))
        if (
            prev_close is not None and not pd.isna(prev_close)
            and prev2_close is not None and not pd.isna(prev2_close)
            and float(prev2_close) > 0
        ):
            friday_chg_pct = (float(prev_close) - float(prev2_close)) / float(prev2_close) * 100
            if not is_bear and friday_chg_pct < -max_friday_drop:
                return False
            if is_bear and friday_chg_pct > max_friday_drop:
                return False

        # Gap disqualifier
        max_gap = float(self._get(req, "max_gap_pct", 1.0))
        if (
            "Open" in df.columns
            and prev_close is not None and not pd.isna(prev_close)
            and float(prev_close) > 0
        ):
            gap_pct = abs(float(row["Open"]) - float(prev_close)) / float(prev_close) * 100
            if gap_pct > max_gap:
                return False

        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        max_hold = int(self._get(req, "max_hold_days", 3))
        days_held = i - trade_state.get("entry_idx", i)
        is_bear = self._is_bear(req)

        if bool(self._get(req, "exit_on_up_close", True)):
            prev_close = row.get("prev_close")
            if prev_close is not None and not pd.isna(prev_close):
                if is_bear and float(row["Close"]) < float(prev_close):
                    return True, "down_close"
                if (not is_bear) and float(row["Close"]) > float(prev_close):
                    return True, "up_close"

        if days_held >= max_hold:
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
