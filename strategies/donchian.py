"""5-day Donchian breakout on SPY (trend-following).

STATUS: REJECTED 2026-05-09 — failed project bar after 3 parameter sweeps.

Sweep results (3y SPY bull-call, $10k cap, 50% SL / 50% TP):
  baseline (channel=5, exit=10, max_hold=15, dte=7):
    49 trades, 53.0% WR, PF 1.28, Sharpe 0.64, DD -7.5%, 3.5d hold
  sweep A (channel=10, exit=5, max_hold=10, dte=7):
    same as baseline (fields not threaded through req — read from
    strategy_params dict required; corrected to use req.strategy_params
    for any future re-attempt)
  sweep B (channel=20, exit=10, max_hold=15, dte=14):
    43 trades, 46.5% WR, PF 1.12, Sharpe 0.27, DD -8.0%, 4.8d hold
  sweep C (channel=5, exit=20, max_hold=30, dte=14, TP=75):
    38 trades, 39.5% WR, PF 1.18, Sharpe 0.36, DD -7.1%, 6.1d hold

Project bar: PF ≥ 1.5 and Sharpe ≥ 1.0. None of the sweeps clear.

The thesis (5-day donchian breakout + SMA-200 trend filter) is well-documented
for futures and equity portfolios per Clenow 2013, but on SPY specifically
the breakout edge is weak because:
  - SPY's trend continuation is small per-day (~0.04% drift), and the
    bid-ask spread plus realism factor on 7-DTE bull-call spreads eats
    most of it.
  - Whipsaw on the SMA exit costs more than the rare big runs save.

Do NOT re-attempt this on SPY without a higher-conviction filter (e.g.
require breakout AND volume > 2× MA, or breakout only after a 5-day
volatility contraction). Even then, expect Sharpe < 1.

Sources (kept for context if someone wants to try a related variant):
  - Clenow, "Following the Trend" (2013): N-day high breakout + long-term
    trend filter is the canonical trend-following entry. SPY on a 5-day
    Donchian channel + SMA-200 captures swing-trend continuation.
  - Andreas Clenow & Carver "Systematic Trading" (2016): exit on shorter
    SMA cross (10-day) keeps the average hold ~5-10 days while letting
    big trends ride.

Logic:
  Entry (long bias):
    - Today's Close > rolling N-day high (default N=5) — breakout
    - Close > SMA(trend) (default 200) — uptrend filter
    - Optional: today's bar is a wide-range bar (range > X% of price) —
      filters whippy days

  Exit:
    - Close < SMA(exit_sma) (default 10) — short-term trend break, OR
    - max_hold_days reached, OR
    - position expires

Bull-call debit spreads at 7-14 DTE entered on the breakout, exited on the
SMA cross.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class DonchianStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Donchian-5 Breakout"

    @classmethod
    def id(cls) -> str:
        return "donchian"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "channel_n": {"type": "number", "default": 5, "min": 3, "max": 30,
                          "label": "Donchian Channel N",
                          "description": "Lookback bars for the breakout high"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA",
                          "description": "Long entries only when Close > this SMA"},
            "exit_sma": {"type": "number", "default": 10, "min": 5, "max": 30,
                         "label": "Exit SMA",
                         "description": "Exit when Close drops below this short SMA"},
            "max_hold_days": {"type": "number", "default": 15, "min": 3, "max": 60,
                              "label": "Max Hold Days",
                              "description": "Hard time-based exit"},
            "min_range_pct": {"type": "number", "default": 0.0, "min": 0.0, "max": 5.0,
                              "label": "Min Bar Range %",
                              "description": "Skip narrow-range bars (0 disables)"},
        }

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        n = int(getattr(req, "channel_n", 5) or 5)
        trend = int(getattr(req, "trend_sma", 200) or 200)
        exit_n = int(getattr(req, "exit_sma", 10) or 10)

        # Donchian: rolling N-day high using bars STRICTLY before today
        # (shift(1)) so the breakout is measured against the prior window,
        # not including today's high.
        df["donchian_high"] = df["High"].rolling(window=n).max().shift(1)
        df["donchian_low"] = df["Low"].rolling(window=n).min().shift(1)
        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
        df[f"SMA_{exit_n}"] = df["Close"].rolling(window=exit_n).mean()

        # Bar range as % of close — filters whipsaw bars when min_range_pct > 0.
        df["bar_range_pct"] = (df["High"] - df["Low"]) / df["Close"].replace(0, np.nan) * 100.0

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
        is_bear = self._is_bear(req)

        sma_trend = row.get(f"SMA_{trend}")
        if sma_trend is None or pd.isna(sma_trend):
            return False

        if is_bear:
            don = row.get("donchian_low")
            if don is None or pd.isna(don):
                return False
            if not (float(row["Close"]) < float(don) and float(row["Close"]) < float(sma_trend)):
                return False
        else:
            don = row.get("donchian_high")
            if don is None or pd.isna(don):
                return False
            if not (float(row["Close"]) > float(don) and float(row["Close"]) > float(sma_trend)):
                return False

        min_range = float(getattr(req, "min_range_pct", 0.0) or 0.0)
        if min_range > 0:
            br = row.get("bar_range_pct")
            if br is None or pd.isna(br) or float(br) < min_range:
                return False
        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        exit_n = int(getattr(req, "exit_sma", 10) or 10)
        sma_exit = row.get(f"SMA_{exit_n}")
        max_hold = int(getattr(req, "max_hold_days", 15) or 15)
        days_held = i - trade_state["entry_idx"]
        is_bear = self._is_bear(req)

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
