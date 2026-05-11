"""Turn-of-Month (TOM) seasonality on SPY.

STATUS: REJECTED 2026-05-09 — fails project bar after 4 configs.

Backtest results (3y SPY bull-call, $10k cap, 50% SL):
  baseline (last3+first3, trend filter, 14DTE, 50% TP):
    52 trades, 40.4% WR, PF 0.93, Sharpe -0.14, DD -5.8% — losing
  sweep 1 (last2+first2, exit_sma=5, max_hold=5, 7DTE):
    42 trades, 45.2% WR, PF 1.25, Sharpe 0.48 — best, still fails
  sweep 2 (last4+first0, no trend filter, 7DTE):
    41 trades, 41.5% WR, PF 0.94, Sharpe -0.08 — losing
  sweep 3 (last3+first3, TP=75, 14DTE):
    50 trades, 40.0% WR, PF 1.06, Sharpe 0.15 — barely positive

Project bar: PF ≥ 1.5 and Sharpe ≥ 1.0. None clear.

Why the well-documented underlying effect doesn't translate:
  - The TOM drift (~0.13%/day during the window per Lakonishok 1988) is
    real but small. On a $250-debit / $5-wide bull-call spread, this
    translates to maybe $5-15 of spread P&L per TOM day.
  - The 50% stop-loss ($125 risk) fires on routine intraday noise that
    is multiples larger than the calendar drift. The strategy wins
    when spreads expire ITM and loses on every noise-stop.
  - The realism factor (1.15× slippage + commissions) also eats most of
    the modest edge.

To re-attempt: this is a candidate for **selling premium** instead of buying
debit spreads — short put or short call spread expiring in the TOM window
would harvest the calendar edge without paying premium for it. That requires
a different topology than the bull-call default and more careful margin
analysis.

Sources (kept for context):
  - Lakonishok & Smidt (1988) "Are Seasonal Anomalies Real?": equity returns
    in the US concentrate disproportionately at the turn of the month
    (last few trading days + first few of the next month).
  - McConnell & Xu (2008) "Equity Returns at the Turn of the Month",
    Financial Analysts Journal: replicates the effect 1926-2005, finds
    ~0.13%/day in the TOM window vs ~0.02%/day other days. Statistically
    significant t > 4 across multiple sub-periods.
  - Persistence: independently reproduced in DJ/SPY/QQQ studies through
    2024. Mechanism: institutional rebalancing + 401(k) / pension
    contribution flows clustered around month-end paychecks.

Logic:
  Entry (long bias):
    - Today is one of the last ``last_n_days`` trading days of the
      current month (default 3), OR
    - Today is one of the first ``first_n_days`` trading days of the
      following month (default 3).

  Filters:
    - Optional uptrend filter: Close > SMA(trend) (default 200) — the
      effect is stronger in bull regimes; in bear regimes the calendar
      flow is overwhelmed by selling.

  Exit:
    - Past the TOM window (entered late-month, today is past first_n_days
      of next month), OR
    - Close < SMA(exit_sma) trend break, OR
    - max_hold_days hard limit, OR
    - position expires.

Bull-call debit spreads at 14-DTE entered on the last 3 trading days of
the month, ridden through the new-month flow, exited around the first 3
trading days. Average hold 3-6 trading days.
"""
from __future__ import annotations

import calendar
from datetime import date as _date

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class TurnOfMonthStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Turn-of-Month"

    @classmethod
    def id(cls) -> str:
        return "turn_of_month"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "last_n_days": {"type": "number", "default": 3, "min": 1, "max": 7,
                            "label": "Last N Trading Days",
                            "description": "Enter on these last trading days of the month"},
            "first_n_days": {"type": "number", "default": 3, "min": 0, "max": 7,
                             "label": "First N Trading Days",
                             "description": "Also fire on these first trading days of next month"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA",
                          "description": "Only enter when Close > this SMA"},
            "use_trend_filter": {"type": "boolean", "default": True,
                                 "label": "Use Trend Filter",
                                 "description": "Skip TOM entries in confirmed downtrend"},
            "exit_sma": {"type": "number", "default": 10, "min": 3, "max": 30,
                         "label": "Exit SMA",
                         "description": "Exit if Close < this SMA"},
            "max_hold_days": {"type": "number", "default": 7, "min": 2, "max": 15,
                              "label": "Max Hold Days",
                              "description": "Hard time-based exit"},
        }

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _trading_days_in_month(year: int, month: int) -> list[_date]:
        """List of weekdays (Mon-Fri) in the given calendar month.

        We don't have an authoritative US holiday calendar in the project,
        so we approximate trading days as weekdays. The TOM effect is
        defined per Lakonishok in trading-day terms; rare holiday edges
        (e.g., Memorial Day on the 30th) shift the window by 1 trading
        day. Acceptable approximation for a calendar effect ±1 day.
        """
        last = calendar.monthrange(year, month)[1]
        return [
            _date(year, month, d)
            for d in range(1, last + 1)
            if _date(year, month, d).weekday() < 5
        ]

    def _is_in_tom_window(self, today: _date, last_n: int, first_n: int) -> tuple[bool, str]:
        """Return (in_window, position) where position ∈ {'late','early',''}."""
        # Late-month: today is in the last `last_n` trading days of THIS month
        days_this = self._trading_days_in_month(today.year, today.month)
        if today in days_this[-last_n:]:
            return True, "late"
        # Early-month: today is in the first `first_n` trading days of THIS month
        if first_n > 0 and today in days_this[:first_n]:
            return True, "early"
        return False, ""

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

    # ── strategy interface ──────────────────────────────────────────────

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        trend = int(self._get(req, "trend_sma", 200))
        exit_n = int(self._get(req, "exit_sma", 10))

        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
        df[f"SMA_{exit_n}"] = df["Close"].rolling(window=exit_n).mean()

        # Shared filter pipeline indicators (project convention).
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
        return df

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        trend = int(self._get(req, "trend_sma", 200))
        if i < trend:
            return False
        row = df.iloc[i]
        is_bear = self._is_bear(req)

        ts = row.name
        if hasattr(ts, "date"):
            today = ts.date()
        elif "Date" in df.columns:
            today = pd.Timestamp(row["Date"]).date()
        else:
            return False

        last_n = int(self._get(req, "last_n_days", 3))
        first_n = int(self._get(req, "first_n_days", 3))
        in_window, _pos = self._is_in_tom_window(today, last_n, first_n)
        if not in_window:
            return False

        if bool(self._get(req, "use_trend_filter", True)):
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
        exit_n = int(self._get(req, "exit_sma", 10))
        sma_exit = row.get(f"SMA_{exit_n}")
        max_hold = int(self._get(req, "max_hold_days", 7))
        days_held = i - trade_state["entry_idx"]
        is_bear = self._is_bear(req)

        # Past TOM window: if we've left both the late-month and early-month
        # zones, the calendar edge is gone — exit.
        ts = row.name
        if hasattr(ts, "date"):
            today = ts.date()
        elif "Date" in df.columns:
            today = pd.Timestamp(row["Date"]).date()
        else:
            today = None

        if today is not None:
            last_n = int(self._get(req, "last_n_days", 3))
            first_n = int(self._get(req, "first_n_days", 3))
            in_window, _pos = self._is_in_tom_window(today, last_n, first_n)
            if not in_window and days_held >= 1:
                return True, "tom_exit"

        if sma_exit is not None and not pd.isna(sma_exit):
            if is_bear and float(row["Close"]) > float(sma_exit):
                return True, "sma_cross"
            if (not is_bear) and float(row["Close"]) < float(sma_exit):
                return True, "sma_cross"

        if days_held >= max_hold:
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
