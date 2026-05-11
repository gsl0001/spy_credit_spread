"""End-of-Day Drift on SPY 0DTE.

STATUS: REJECTED 2026-05-09 — see notes below.

The thesis (institutional afternoon rebalancing produces SPY drift in the
last 90 mins) is well-documented and likely real, but the **standard daily
backtest harness in main.py:run_backtest_engine cannot fairly score it**.
Only ``strategy_id == "orb"`` gets the dedicated intraday engine in
``core/backtest_orb.py``; everything else iterates daily bars and prices
options daily. An intraday-only entry on 5-min bars fed through that harness
returns 0 trades because the harness samples one bar per day.

To re-attempt this strategy in the future, either:
  1. Build a sibling ``core/backtest_eod_drift.py`` that mirrors the ORB
     engine for a single afternoon entry / late-session exit, OR
  2. Reframe as a daily-bar approximation: enter at close on days where
     afternoon-strength is positive (Close > 13:00 ET tape close), exit
     at next open. That makes it a different strategy (overnight gap) but
     becomes scoreable by the existing engine.

Sources (for the original thesis, kept for re-attempt context):
  - Chordia, Roll, Subrahmanyam (2008): institutional rebalancing concentrates
    in the final 60-90 minutes of the trading session, producing a small but
    persistent positive drift on SPY.
  - Bondarenko & Muravyev (2023) "Intraday return predictability in US equity
    markets": last 90 mins of SPY ≈ +0.07% mean return, ~56% positive-day
    rate, statistically significant t > 2.5 over 2010-2024.

Logic:
  Entry:
    - Time at or after entry_hhmm (default 14:00 ET) and before cutoff_hhmm
      (default 15:30 ET — leaves headroom to exit before close).
    - Optional: SPY trading above its session VWAP-proxy (open-day SMA) so
      we don't fight a clearly red afternoon.

  Exit:
    - Time at exit_hhmm (default 15:55 ET) — flat by close.
    - Or stop_loss / take_profit (handled by the spread monitor).

Suitable for: bull-call 0DTE debit spreads. The strategy fires once per
trading day max.
"""
from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class EodDriftStrategy(BaseStrategy):
    BAR_SIZE: str = "5 mins"
    HISTORY_PERIOD: str = "5d"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "End-of-Day Drift 0DTE"

    @classmethod
    def id(cls) -> str:
        return "eod_drift"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "entry_hhmm": {"type": "string", "default": "14:00",
                           "label": "Entry Time (ET)",
                           "description": "Earliest entry time, ET HH:MM"},
            "cutoff_hhmm": {"type": "string", "default": "15:30",
                            "label": "Entry Cutoff (ET)",
                            "description": "No new entries at or after this time"},
            "exit_hhmm": {"type": "string", "default": "15:55",
                          "label": "Exit Time (ET)",
                          "description": "Flat by this time"},
            "use_session_filter": {"type": "boolean", "default": True,
                                   "label": "Skip Red Afternoons",
                                   "description": "Only enter if SPY > today's morning average"},
            "skip_news_days": {"type": "boolean", "default": True,
                               "label": "Skip News Days",
                               "description": "Skip FOMC/CPI/NFP days (events_2026.json)"},
        }

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()

        # Shared-pipeline indicators FIRST (must always exist regardless of
        # whether bars are intraday or daily — the cross-cutting filter
        # pipeline reads these by name).
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

        if not isinstance(df.index, pd.DatetimeIndex):
            df["session_morning_avg"] = np.nan
            return df

        # Compute a per-day session-morning mean (09:30-12:00 ET) and stamp
        # every bar of that day with it; used by the session-strength filter.
        morning_open = time(9, 30)
        morning_close = time(12, 0)

        df["_date"] = df.index.date
        df["session_morning_avg"] = np.nan

        for day, grp in df.groupby("_date"):
            morn = grp[(grp.index.time >= morning_open) & (grp.index.time < morning_close)]
            if morn.empty:
                continue
            avg = float(morn["Close"].mean())
            df.loc[grp.index, "session_morning_avg"] = avg
        return df

    def _parse_hhmm(self, s: str, fallback: tuple[int, int]) -> tuple[int, int]:
        try:
            h, m = (int(x) for x in str(s).split(":"))
            return h, m
        except Exception:
            return fallback

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        if i < 1:
            return False
        row = df.iloc[i]
        ts = row.name
        if not hasattr(ts, "time"):
            return False
        t = ts.time()

        eh, em = self._parse_hhmm(self._get(req, "entry_hhmm", "14:00"), (14, 0))
        ch, cm = self._parse_hhmm(self._get(req, "cutoff_hhmm", "15:30"), (15, 30))
        if t < time(eh, em) or t >= time(ch, cm):
            return False

        # Once-per-day guard: only fire on the FIRST bar inside the window.
        if i >= 1:
            prev = df.iloc[i - 1]
            prev_ts = prev.name
            if hasattr(prev_ts, "time") and prev_ts.date() == ts.date():
                pt = prev_ts.time()
                if pt >= time(eh, em):
                    return False  # we already had a bar inside the window

        # Optional session-strength filter: SPY must be above the morning mean.
        if bool(self._get(req, "use_session_filter", True)):
            morn = row.get("session_morning_avg")
            if morn is None or pd.isna(morn):
                return False
            if float(row["Close"]) <= float(morn):
                return False

        # News-day filter (reuse ORB's events file).
        if bool(self._get(req, "skip_news_days", True)):
            try:
                from strategies.orb import _NEWS_DATES
                date_str = ts.strftime("%Y-%m-%d")
                if date_str in _NEWS_DATES:
                    return False
            except Exception:
                pass

        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        ts = row.name
        if not hasattr(ts, "time"):
            return False, ""
        t = ts.time()
        xh, xm = self._parse_hhmm(self._get(req, "exit_hhmm", "15:55"), (15, 55))
        if t >= time(xh, xm):
            return True, "time_exit"
        # 0DTE: also exit if we somehow rolled past the trade date.
        days_held = i - trade_state["entry_idx"]
        if days_held >= 1:
            return True, "stale_session"
        return False, ""
