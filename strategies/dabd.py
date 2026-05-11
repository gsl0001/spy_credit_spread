"""Day-After-Big-Down (DABD) reversal on SPY.

STATUS: REJECTED 2026-05-10 — near-miss on Sharpe across 5 configs.

Backtest results (3y SPY bull-call, $10k cap, 50% SL):
  baseline (down 1.5%, trend on, 7DTE):
    19 trades, 68.4% WR, PF 1.72, Sharpe 0.62, DD -3.9%, 1.5d hold
  sweep 1 (down 1.0%):
    37 trades, 70.3% WR, PF 1.55, Sharpe 0.77, DD -4.3%, 1.4d hold
  sweep 2 (down 0.75%, max_hold=5, 14DTE):
    47 trades, 72.3% WR, PF 1.43, Sharpe 0.72
  sweep 3 (intraday Close-vs-Open drop):
    25 trades, 68.0% WR, PF 1.35, Sharpe 0.46
  sweep 4 (down 1.0%, max_hold=2, 14DTE) ← best:
    38 trades, 71.1% WR, PF 1.65, Sharpe 0.85, DD -4.4%, 1.4d hold
  sweep 5 (down 1.25%, TP 75%):
    26 trades, 65.4% WR, PF 1.35, Sharpe 0.44

Project bar: ≥20 trades, PF ≥1.5, Sharpe ≥1.0, WR ≥55% mean-rev.
Every sweep clears WR (consistently 65-72%) and most clear PF and trade
count. None clear Sharpe ≥ 1.0; best is 0.85.

Why Sharpe stays sub-1.0 despite strong WR/PF: average trade hold is
1.4-1.5 days — the wins are quick and small (~$25-40 EV per win), and
day-to-day variance in spread P&L is enough that the return distribution's
standard deviation drags the risk-adjusted ratio under 1.0. The strategy
is empirically positive but not paid enough for the variance.

This is the second near-miss in this codebase (vix_spike was the first).
Both fail on a single threshold by 15-25%. Pattern: SPY-spread strategies
with very short holds (≤2 days) and small per-trade EV struggle to clear
the Sharpe bar even when WR is high.

To re-attempt:
  1. Combine DABD with IBS (only enter when BOTH a >1.5% down day AND
     close near low) — should reduce trade count but increase per-trade EV.
  2. Use a wider strike width ($10) to amplify the per-trade move and
     compress the noise floor's relative size.
  3. Consider a credit-spread topology (sell put spread) — the same panic
     reversal is captured with theta tailwind, which improves Sharpe.

The class stays in the registry. NO moomoo preset is created today.

Sources (kept for context):
  - Atkins & Dyl (1990) "Price Reversals, Bid-Ask Spreads, and Market
    Efficiency", Journal of Financial and Quantitative Analysis: documents
    statistically significant next-day reversal after extreme one-day moves.
  - Bremer & Sweeney (1991) "The Reversal of Large Stock-Price Decreases",
    Journal of Finance: independently replicates the reversal effect on
    individual equities.
  - Larson & Madura (2003) "What Drives Stock Price Behavior Following
    Extreme One-Day Returns?", Journal of Financial Research: ties the
    reversal to liquidity-driven overreaction; finds the effect persists
    out-of-sample.
  - Modern SPY replications (2000-2024): mean next-day return after a
    ≥1.5% down close is +0.30 to +0.50%, t-stat > 3.0.

Logic:
  Entry (long bias):
    - Today's close < entry_threshold% below today's open AND/OR yesterday's
      close — i.e. a "big down day" by daily-return magnitude.
    - Optional: SPY > SMA(trend) — only fade panic in established uptrends
      (the panic-reversal effect is much weaker in confirmed downtrends
      where every down day might continue down).

  Exit:
    - First up-close (Close > prior Close), OR
    - max_hold_days hard limit (default 3 — the reversal effect decays
      sharply after 2-3 days), OR
    - Close > SMA(exit_sma) — short-term recovery, OR
    - position expires.

Bull-call debit spreads at 7-DTE entered at the close of the big-down day,
exited on the first up-close. Average hold 1-2 days. Very low trade count
(~10-15 events / 3y) but historically high single-trade EV.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class DabdStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Day-After-Big-Down"

    @classmethod
    def id(cls) -> str:
        return "dabd"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "down_pct": {"type": "number", "default": 1.5, "min": 0.5, "max": 5.0,
                         "label": "Down-Day Threshold %",
                         "description": "Trigger when today's close is this % below prior close"},
            "use_intraday_drop": {"type": "boolean", "default": False,
                                  "label": "Use Intraday Drop Instead",
                                  "description": "Trigger on Close vs Open (today's intraday move) "
                                  "instead of Close vs prior Close"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA"},
            "use_trend_filter": {"type": "boolean", "default": True,
                                 "label": "Use Trend Filter"},
            "exit_sma": {"type": "number", "default": 5, "min": 3, "max": 20,
                         "label": "Exit SMA"},
            "max_hold_days": {"type": "number", "default": 3, "min": 1, "max": 10,
                              "label": "Max Hold Days",
                              "description": "Reversal effect decays after 2-3 days"},
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
        df["close_vs_prev_pct"] = (df["Close"] / df["prev_close"] - 1.0) * 100.0
        df["intraday_pct"] = (df["Close"] / df["Open"] - 1.0) * 100.0

        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
        df[f"SMA_{exit_n}"] = df["Close"].rolling(window=exit_n).mean()

        # Shared filter pipeline
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

        threshold = float(self._get(req, "down_pct", 1.5))
        use_intra = bool(self._get(req, "use_intraday_drop", False))
        ret_col = "intraday_pct" if use_intra else "close_vs_prev_pct"
        ret = row.get(ret_col)
        if ret is None or pd.isna(ret):
            return False

        if is_bear:
            # Mirror: enter on a big-up-day expecting reversal in a downtrend
            if float(ret) < threshold:
                return False
        else:
            if float(ret) > -threshold:
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
        max_hold = int(self._get(req, "max_hold_days", 3))
        days_held = i - trade_state["entry_idx"]
        is_bear = self._is_bear(req)

        if bool(self._get(req, "exit_on_up_close", True)) and i >= 1:
            prev_close = row.get("prev_close")
            if prev_close is not None and not pd.isna(prev_close):
                if is_bear and float(row["Close"]) < float(prev_close):
                    return True, "down_close"
                if (not is_bear) and float(row["Close"]) > float(prev_close):
                    return True, "up_close"

        exit_n = int(self._get(req, "exit_sma", 5))
        sma_exit = row.get(f"SMA_{exit_n}")
        if sma_exit is not None and not pd.isna(sma_exit):
            if is_bear and float(row["Close"]) < float(sma_exit):
                return True, "sma_cross"
            if (not is_bear) and float(row["Close"]) > float(sma_exit):
                return True, "sma_cross"

        if days_held >= max_hold:
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
