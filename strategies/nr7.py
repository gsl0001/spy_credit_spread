"""NR7 (Narrow Range 7) volatility-contraction breakout on SPY.

Source:
  Toby Crabel, "Day Trading with Short Term Price Patterns and Opening
  Range Breakout" (1990). NR7 = today's High-Low range is the smallest
  of the prior 7 sessions. Crabel showed (and many later studies on
  S&P futures + ETFs replicated) that NR7 days are followed by a
  range-expansion session ~70%+ of the time.

  Confirmation refs:
    - Linda Raschke / Larry Connors "Street Smarts" (1995) — NR4/NR7
      as compression-then-expansion pattern.
    - Quantpedia "Volatility Contraction Pattern" (2017) replication.

Logic:
  Volatility-compression detection (NR7):
    - Today's Range = High - Low is strictly the smallest of the last
      `nr_lookback` sessions (default 7).

  Direction inference (avoid direction-agnostic since engine doesn't
  support straddles):
    - Bull bias if Close > SMA(`trend_sma`)
    - Bear bias if Close < SMA(`trend_sma`)
    - Otherwise no trade.

  Entry:
    - On the bar after the NR7 (next session open) — captured in the
      backtester as "entry on close of the NR7 day".

  Exit:
    - Range-expansion target: today's range > `expansion_mult` × NR7
      range AND price moved in our favor → take profit.
    - Adverse breakout: price closes against direction by more than
      `adverse_pct` × NR7 range → cut.
    - Time exit at `max_hold_days` (default 4) — the expansion edge
      decays after a few sessions.
    - Mandatory expiry exit.

Topology: bull-call (or bear-put) debit spread, 7 DTE, $5 wide. Hold
1-4 days. Per-skill Step 2: directional move expected within short
hold → debit spread is correct. Trade count ~70-100 / 5y (NR7 fires
roughly 1-2× per month).

VETTING RESULT (2026-05-10): REJECTED.
  Tested SPY 5y, bull-only, bull_call $5-wide 7 DTE, 50/50 SL/TP.
  4 parameter sweeps + 1 strike-width sweep, best result:
    86 trades, WR 52.3%, PF 1.35, Sharpe 0.60, DD -12.6%
    (sweep nr_lookback=7,trend=50,expansion=1.3,adverse=1.5,hold=5):
    83 trades, WR 55.4%, PF 1.40, Sharpe 0.65, DD -11.7%
    (width=10 widening): 81 trades, WR 55.6%, PF 1.31, Sharpe 0.53.
  All sweeps fail PF≥1.5 and Sharpe≥1.0 thresholds.
  Root cause: same as `donchian` — SPY's per-bar range-expansion drift
  after compression averages ~0.4-0.6%, smaller than the 50% stop-loss
  noise floor on a 7-DTE $5-wide spread (~$2.50 = ~0.6% SPY move).
  The signal is real (WR is positive trending after 3 sweeps) but the
  per-trade EV is too small for debit-spread mechanics on SPY.
  Path to graduation:
    1. Run on a higher-vol underlying (e.g. QQQ, RUT, or TLT) where
       NR7-relative range expansion is wider in absolute terms.
    2. Combine with a vol-regime filter (VIX > 20 only) — but at that
       point it overlaps with `vix_spike` thesis.
    3. Pair with intraday breakout confirmation (open>NR7 high) — would
       require an intraday harness.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class Nr7Strategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "NR7 Volatility Contraction Breakout"

    @classmethod
    def id(cls) -> str:
        return "nr7"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "nr_lookback": {"type": "number", "default": 7, "min": 4, "max": 20,
                            "label": "NR Lookback Days",
                            "description": "Today's range must be smallest in this many sessions"},
            "trend_sma": {"type": "number", "default": 50, "min": 20, "max": 200,
                          "label": "Trend SMA",
                          "description": "Direction filter: long if Close>SMA, short if <SMA"},
            "expansion_mult": {"type": "number", "default": 1.5, "min": 1.1, "max": 3.0,
                               "label": "Expansion Take-Profit Multiplier",
                               "description": "Exit when today's range exceeds NR7 range × this"},
            "adverse_pct": {"type": "number", "default": 1.0, "min": 0.3, "max": 3.0,
                            "label": "Adverse Move Cutoff (× NR7 range)",
                            "description": "Cut if price moves against us by this × NR7 range"},
            "max_hold_days": {"type": "number", "default": 4, "min": 1, "max": 10,
                              "label": "Max Hold Days",
                              "description": "Hard time-based exit"},
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        nr_lb = int(self._get(req, "nr_lookback", 7) or 7)
        trend = int(self._get(req, "trend_sma", 50) or 50)

        df["Range"] = df["High"] - df["Low"]
        # NR7: today's range strictly smallest of last nr_lb (inclusive of today)
        rolling_min = df["Range"].rolling(window=nr_lb).min()
        df["is_nr7"] = (df["Range"] == rolling_min) & (df["Range"] > 0)
        # Lag NR7 flag — entry happens on the bar AFTER the NR7
        df["nr7_prev"] = df["is_nr7"].shift(1).fillna(False)
        df["nr7_range_prev"] = df["Range"].shift(1)
        df["nr7_close_prev"] = df["Close"].shift(1)

        # Shared indicators (skill mandates these regardless)
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

    @staticmethod
    def _is_bear(req) -> bool:
        return (
            getattr(req, "direction", "") == "bear"
            or getattr(req, "strategy_type", "") == "bear_put"
        )

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        trend = int(self._get(req, "trend_sma", 50) or 50)
        if i < max(trend, 8):
            return False
        row = df.iloc[i]
        if not bool(row.get("nr7_prev", False)):
            return False
        sma = row.get(f"SMA_{trend}")
        close = row.get("Close")
        if sma is None or pd.isna(sma) or close is None or pd.isna(close):
            return False
        is_bear = self._is_bear(req)
        if is_bear:
            return float(close) < float(sma)
        return float(close) > float(sma)

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        days_held = i - trade_state["entry_idx"]
        max_hold = int(self._get(req, "max_hold_days", 4) or 4)
        expansion_mult = float(self._get(req, "expansion_mult", 1.5) or 1.5)
        adverse_pct = float(self._get(req, "adverse_pct", 1.0) or 1.0)
        is_bear = self._is_bear(req)

        nr7_range = trade_state.get("nr7_range")
        nr7_close = trade_state.get("nr7_close")
        if nr7_range is None:
            # Capture NR7 reference values from entry bar's prev row
            entry_idx = trade_state["entry_idx"]
            if entry_idx >= 1:
                prev = df.iloc[entry_idx - 1]
                nr7_range = float(prev.get("Range") or 0.0)
                nr7_close = float(prev.get("Close") or row.get("Close") or 0.0)
                trade_state["nr7_range"] = nr7_range
                trade_state["nr7_close"] = nr7_close

        today_range = float(row.get("High", 0)) - float(row.get("Low", 0))
        close = float(row.get("Close", 0))

        if nr7_range and nr7_range > 0 and nr7_close:
            move = close - nr7_close
            favorable = (move < 0) if is_bear else (move > 0)
            adverse_move = (-move) if is_bear else move
            # Range-expansion take-profit: today's range > mult × NR7 range AND price moved our way
            if today_range > expansion_mult * nr7_range and favorable:
                return True, "expansion_target"
            # Adverse cut
            if (not favorable) and abs(move) > adverse_pct * nr7_range:
                return True, "adverse_break"

        if days_held >= max_hold:
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
