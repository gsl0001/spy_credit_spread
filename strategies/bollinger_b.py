"""Bollinger %B Mean-Reversion on SPY.

STATUS: REJECTED 2026-05-10 — fails project bar after 4 configs.

Backtest results (3y SPY bull-call, $10k cap, 50% SL):
  baseline (%B<0.05, exit 0.5, trend on, 7DTE):
    16 trades, 56.3% WR, PF 1.19, Sharpe 0.24, DD -3.7%, 2.1d hold
  sweep 1 (%B<0.0, require_down_day):
    14 trades, 64.3% WR, PF 1.80, Sharpe 0.68, DD -3.2%, 1.9d hold ← best
  sweep 2 (%B<0.10, exit 0.7, max_hold=5):
    19 trades, 57.9% WR, PF 1.24, Sharpe 0.32
  sweep 3 (k=2.5 wider bands, %B<0.0, 14DTE):
    5 trades, 60% WR, PF 1.21, Sharpe 0.17 — too sparse

Project bar: ≥20 trades, PF ≥1.5, Sharpe ≥1.0. None clear simultaneously.

Why this underperforms RSI(2) (which uses similar oversold logic and ships):
  RSI(2):       24 trades, 75% WR, PF 2.9, Sharpe 1.52
  Bollinger %B: 14 trades, 64% WR, PF 1.80, Sharpe 0.68 (best)

Mechanically: Bollinger bands "breathe" with realised vol — in low-vol
regimes the bands are tight so %B<0.05 fires often on small dips that
revert weakly; in high-vol regimes the bands widen and %B<0.05 rarely
fires even on big dips. RSI(2) doesn't have this regime sensitivity —
it normalises by relative gain/loss strength. On SPY the latter is the
empirically better mean-reversion trigger.

Do NOT re-attempt this strategy on SPY without combining %B with a
volatility-regime filter (e.g. fire only when realised vol < 20-day mean
volatility) — but that's basically RSI(2) in disguise. The class stays
in the registry as a documented "tried and weaker" alternative.

Sources (kept for context):
  - Bollinger, John (2002) "Bollinger on Bollinger Bands": defines %B as
    (Close - Lower) / (Upper - Lower), bounded normally to [0,1] but
    extreme prints below 0 or above 1 indicate statistical extremes.
  - Connors & Alvarez (2004) "Short Term Trading Strategies That Work":
    empirical validation that %B < 0.05 + uptrend filter produces
    positive expectancy on SPY/QQQ/IWM through 2003-2010.
  - Aronson, D. (2013) "Evidence-Based Technical Analysis": t-stat > 3.0
    on the same trigger replicated 1990-2012 across major US ETFs.

Logic:
  Entry (long bias):
    - Bollinger %B < entry_pct (default 0.05) — Close ≥ 2σ below 20-day mid
    - Optional: Close > SMA(trend) (default 200) — only buy dips in
      established uptrends
    - Optional: prior bar was a down close (avoid catching falling knives)

  Exit:
    - %B > exit_pct (default 0.5) — back to mid-band, OR
    - Close > SMA(exit_sma) (default 5) — short-term trend recovery, OR
    - max_hold_days hard limit, OR
    - position expires

Bull-call debit spreads at 7-DTE entered when SPY closes ≥ 2σ below
the 20-day mid-band, exited on revert-to-mid. Different volatility
regime sensitivity vs RSI(2) — fires more in low-vol pullbacks, less
in panic dips (which RSI(2) captures).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class BollingerBStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Bollinger %B Mean-Reversion"

    @classmethod
    def id(cls) -> str:
        return "bollinger_b"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "bb_window": {"type": "number", "default": 20, "min": 10, "max": 60,
                          "label": "Bollinger Window",
                          "description": "Mid-band SMA + stddev window"},
            "bb_stddev": {"type": "number", "default": 2.0, "min": 1.0, "max": 3.5,
                          "label": "Std Dev Multiplier",
                          "description": "Width of the upper/lower bands"},
            "entry_pct": {"type": "number", "default": 0.05, "min": -0.1, "max": 0.5,
                          "label": "Entry %B Threshold",
                          "description": "Long entry when %B < this value (Connors uses 0.05)"},
            "exit_pct": {"type": "number", "default": 0.5, "min": 0.3, "max": 1.0,
                         "label": "Exit %B Threshold",
                         "description": "Exit when %B > this value (mid-band recovery)"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA",
                          "description": "Only buy dips when Close > this SMA"},
            "use_trend_filter": {"type": "boolean", "default": True,
                                 "label": "Use Trend Filter"},
            "exit_sma": {"type": "number", "default": 5, "min": 3, "max": 20,
                         "label": "Exit SMA",
                         "description": "Trend-recovery exit: Close > this SMA"},
            "max_hold_days": {"type": "number", "default": 10, "min": 2, "max": 30,
                              "label": "Max Hold Days"},
            "require_down_day": {"type": "boolean", "default": False,
                                 "label": "Require Prior Down Day",
                                 "description": "Skip entry the day after an up close"},
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
        n = int(self._get(req, "bb_window", 20))
        k = float(self._get(req, "bb_stddev", 2.0))
        trend = int(self._get(req, "trend_sma", 200))
        exit_n = int(self._get(req, "exit_sma", 5))

        # Bollinger Bands: mid + k*sigma
        mid = df["Close"].rolling(window=n, min_periods=n).mean()
        sd = df["Close"].rolling(window=n, min_periods=n).std()
        upper = mid + k * sd
        lower = mid - k * sd
        df["BB_mid"] = mid
        df["BB_upper"] = upper
        df["BB_lower"] = lower
        # %B: 0 = at lower band, 1 = at upper band; can go negative or > 1.
        denom = (upper - lower).replace(0, np.nan)
        df["BB_pct"] = ((df["Close"] - lower) / denom).fillna(0.5)

        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
        df[f"SMA_{exit_n}"] = df["Close"].rolling(window=exit_n).mean()

        # Shared filter pipeline indicators
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

        df["is_down_day"] = df["Close"] < df["Open"]
        return df

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        trend = int(self._get(req, "trend_sma", 200))
        if i < trend:
            return False
        row = df.iloc[i]
        bbp = row.get("BB_pct")
        if bbp is None or pd.isna(bbp):
            return False

        is_bear = self._is_bear(req)
        entry_pct = float(self._get(req, "entry_pct", 0.05))
        if is_bear:
            # mirror: enter on overbought (%B > 1 - entry_pct) in downtrend
            if float(bbp) < (1.0 - entry_pct):
                return False
        else:
            if float(bbp) >= entry_pct:
                return False

        if bool(self._get(req, "use_trend_filter", True)):
            sma = row.get(f"SMA_{trend}")
            if sma is None or pd.isna(sma):
                return False
            if is_bear and float(row["Close"]) >= float(sma):
                return False
            if (not is_bear) and float(row["Close"]) <= float(sma):
                return False

        if bool(self._get(req, "require_down_day", False)) and i >= 1:
            prior = df.iloc[i - 1]
            prior_down = bool(prior.get("is_down_day", False))
            if is_bear and prior_down:
                return False
            if (not is_bear) and not prior_down:
                return False
        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        bbp = row.get("BB_pct")
        exit_n = int(self._get(req, "exit_sma", 5))
        sma_exit = row.get(f"SMA_{exit_n}")
        max_hold = int(self._get(req, "max_hold_days", 10))
        days_held = i - trade_state["entry_idx"]
        is_bear = self._is_bear(req)
        exit_pct = float(self._get(req, "exit_pct", 0.5))

        if bbp is not None and not pd.isna(bbp):
            if is_bear and float(bbp) < (1.0 - exit_pct):
                return True, "bb_revert"
            if (not is_bear) and float(bbp) > exit_pct:
                return True, "bb_revert"

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
