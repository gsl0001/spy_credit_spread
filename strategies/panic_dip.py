"""Panic-Dip Confluence on SPY (DABD + IBS confluence).

Sources:
  - Atkins & Dyl (1990) "Price Reversals, Bid-Ask Spreads, and Market
    Efficiency" + Bremer & Sweeney (1991): magnitude-based one-day
    reversal effect.
  - Larson & Madura (2003) "What Drives Stock Price Behavior Following
    Extreme One-Day Returns?": liquidity-driven overreaction.
  - Quantpedia (2014) "Internal Bar Strength as Equity Market Predictor":
    IBS < 0.2 (close near day low) signals next-day mean reversion.

  This strategy is a confluence of the two: only fire when BOTH
  signals trigger simultaneously, producing a "real panic" filter.
  Documented as the path-to-graduation in `strategies/dabd.py` after
  DABD-alone failed Sharpe (5 sweeps best 0.85; 71% WR but small per-trade
  EV drowned in variance).

Logic:
  Entry (long bias):
    - Today's close ≤ down_pct% below prior close (DABD trigger)
    - AND today's IBS < entry_ibs (close in bottom IBS-fraction of bar)
    - AND Close > SMA(trend) — uptrend filter (panic-reversal effect is
      stronger in established uptrends)

  Exit:
    - First up-close (Close > prior Close), OR
    - max_hold_days hard limit (default 3), OR
    - Close > SMA(exit_sma), OR
    - position expires.

Topology: bull-call debit spread, 7 DTE, $5 wide. Hold 1-2 days. Trade
count expected: lower than DABD alone (15-25 / 3y) but per-trade EV
expected higher because the trigger requires both a big down move AND
weak intraday close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class PanicDipStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "shipped"

    @property
    def name(self) -> str:
        return "Panic Dip (DABD + IBS confluence)"

    @classmethod
    def id(cls) -> str:
        return "panic_dip"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "down_pct": {"type": "number", "default": 1.0, "min": 0.5, "max": 3.0,
                         "label": "Down-Day % Threshold",
                         "description": "Trigger when Close is this % below prior close"},
            "entry_ibs": {"type": "number", "default": 0.2, "min": 0.05, "max": 0.4,
                          "label": "Entry IBS Max",
                          "description": "Trigger only when close is in bottom this fraction of bar"},
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
        df["close_vs_prev_pct"] = (df["Close"] / df["prev_close"] - 1.0) * 100.0

        rng = (df["High"] - df["Low"]).replace(0, np.nan)
        df["IBS"] = ((df["Close"] - df["Low"]) / rng).fillna(0.5)

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

        threshold = float(self._get(req, "down_pct", 1.0))
        ret = row.get("close_vs_prev_pct")
        if ret is None or pd.isna(ret):
            return False

        ibs = row.get("IBS")
        if ibs is None or pd.isna(ibs):
            return False
        entry_ibs = float(self._get(req, "entry_ibs", 0.2))

        if is_bear:
            # Mirror: big up move + close near high (greed exhaustion)
            if float(ret) < threshold:
                return False
            if float(ibs) < (1.0 - entry_ibs):
                return False
        else:
            if float(ret) > -threshold:
                return False
            if float(ibs) >= entry_ibs:
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
