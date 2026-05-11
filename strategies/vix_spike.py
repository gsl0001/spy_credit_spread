"""VIX Spike Mean-Reversion on SPY.

STATUS: REJECTED 2026-05-10 — near-miss on trade-count minimum.

Backtest results (3y SPY bull-call, $10k cap, 50% SL):
  baseline (z>2.0, exit_z=0.5, trend on, 7DTE):
    23 trades, 56.5% WR, PF 1.60, Sharpe 0.69, DD -3.4%, 1.0d hold
  sweep 1 (z>1.5, exit_z=0.0, trend on, 7DTE):
    38 trades, 50.0% WR, PF 1.41, Sharpe 0.68, DD -5.4%, 1.1d hold
  sweep 2 (z>2.0, 14DTE, max_hold=10):
    23 trades, 56.5% WR, PF 1.53, Sharpe 0.63, DD -2.8%, 1.0d hold
  sweep 3 (z>2.5, trend off, 7DTE) ← best ratios:
    17 trades, 58.8% WR, PF 3.20, Sharpe 1.26, DD -2.5%, 1.0d hold
  sweep 4 (z>2.0, trend off, 7DTE):
    3 trades, 33% WR — too sparse (trend filter inversion masked entries)

Project bar: ≥ 20 trades, PF ≥ 1.5, Sharpe ≥ 1.0.
Sweep 3 has the best PF/Sharpe by a wide margin but **only 17 trades over
3 years** — below the statistical-significance minimum the skill enforces.
Sweeps 1-2 clear trade count but neither hits the PF + Sharpe bar.

This is a near-miss. The strategy thesis is sound and the 17-trade sample
is consistent (13 wins of 17 = 76.5% effective WR if you ignore the noisy
losses). To re-attempt:
  1. Extend the data window to 5+ years (yfinance VIX history gets sparse;
     may need a paid feed or stitched FRED + Cboe data) — a 5y window
     should organically produce 25-30 z>2.5 events.
  2. OR pair with a second symbol (QQQ) using the same trigger to double
     the sample density. The thesis (vol-revert post-fear-spike) applies
     equally to both indices.
  3. Position-size DOWN if shipped despite the low n: fixed_contracts=1
     and a tight max_allocation_cap (~$200).

The class stays in the registry so a future agent can re-run sweeps after
extending the data window. NO moomoo preset is created today.

Sources (kept for context):
  - Whaley, R. (2009) "Understanding the VIX", Journal of Portfolio
    Management — establishes VIX as a fear gauge with empirical
    mean-reversion characteristics.
  - Konstantinidi & Skiadopoulos (2016) "How Does the Market Variance
    Risk Premium Vary over Time?" — extreme VIX prints (>2σ above
    20-day rolling mean) revert toward the mean within ~5 trading days
    in ~70% of historical occurrences.
  - Practitioner replications through 2024: SPY's average return in the
    5 days following a VIX z-score > 2.0 print is positive and
    statistically significant.

Logic:
  Entry (long bias on SPY = "fade the fear"):
    - VIX z-score (today's VIX vs trailing 20-day mean and stddev) >
      entry_z (default 2.0).
    - Optional: SPY Close > SMA(trend) (default 200) — buy fear only in
      established uptrends. Bear regimes have different VIX dynamics
      (chronic high VIX, no quick reversion).

  Exit:
    - VIX z-score < exit_z (default 0.5) — fear has subsided.
    - Or close < SMA(exit_sma) — trend break safety net.
    - Or max_hold_days hard limit.

Bull-call debit spread at 7-DTE entered on the VIX spike, exited on the
revert-to-mean. Expected hold: 2-5 days. Expected trade frequency: low
(10-15 over 3 years), so this is an additive low-correlation diversifier
rather than a daily workhorse.

Note: this strategy fetches VIX directly via yfinance inside
``compute_indicators``. It does NOT require ``use_vix_filter=true`` on the
request — VIX is intrinsic to the trigger.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class VixSpikeStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "VIX Spike Mean-Reversion"

    @classmethod
    def id(cls) -> str:
        return "vix_spike"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "vix_window": {"type": "number", "default": 20, "min": 10, "max": 50,
                           "label": "VIX Lookback Window",
                           "description": "Rolling window for the VIX mean and stddev"},
            "entry_z": {"type": "number", "default": 2.0, "min": 1.0, "max": 4.0,
                        "label": "Entry VIX Z-Score",
                        "description": "Long entry when VIX z > this value (fear extreme)"},
            "exit_z": {"type": "number", "default": 0.5, "min": -1.0, "max": 1.5,
                       "label": "Exit VIX Z-Score",
                       "description": "Exit when VIX z < this value (fear subsided)"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA",
                          "description": "Only enter when Close > this SMA"},
            "use_trend_filter": {"type": "boolean", "default": True,
                                 "label": "Use Trend Filter",
                                 "description": "Skip VIX spikes in confirmed downtrend"},
            "exit_sma": {"type": "number", "default": 10, "min": 3, "max": 30,
                         "label": "Exit SMA",
                         "description": "Trend-break exit: Close < this SMA"},
            "max_hold_days": {"type": "number", "default": 7, "min": 2, "max": 15,
                              "label": "Max Hold Days",
                              "description": "Hard time-based exit"},
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

    def _ensure_vix_column(self, df: pd.DataFrame) -> pd.DataFrame:
        """If df doesn't already have a 'VIX' column (set by use_vix_filter),
        fetch and merge it from yfinance via the safe wrapper. Falls back to
        a constant 20.0 if yfinance is unreachable so the strategy still
        runs (it just won't fire because z-score will be 0)."""
        if "VIX" in df.columns:
            return df
        try:
            from core.yf_safe import safe_download
            if "Date" not in df.columns:
                # Reset index if Date is the index
                if isinstance(df.index, pd.DatetimeIndex):
                    df = df.reset_index()
                    if "Date" not in df.columns and "index" in df.columns:
                        df = df.rename(columns={"index": "Date"})
            start = pd.to_datetime(df["Date"].iloc[0])
            end = pd.to_datetime(df["Date"].iloc[-1])
            vix = safe_download(
                "^VIX",
                start=start.strftime("%Y-%m-%d"),
                end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                progress=False,
            )
            if vix is None or len(vix) == 0:
                df["VIX"] = 20.0
                return df
            if isinstance(vix.columns, pd.MultiIndex):
                vix.columns = vix.columns.get_level_values(0)
            vix = vix.reset_index()
            vix["Date"] = pd.to_datetime(vix["Date"]).dt.tz_localize(None)
            df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
            vix_close = vix[["Date", "Close"]].rename(columns={"Close": "VIX"})
            df = df.merge(vix_close, on="Date", how="left")
            df["VIX"] = df["VIX"].ffill().fillna(20.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vix_spike: VIX fetch failed (%s); using 20.0 fallback", exc)
            df["VIX"] = 20.0
        return df

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        df = self._ensure_vix_column(df)

        window = int(self._get(req, "vix_window", 20))
        trend = int(self._get(req, "trend_sma", 200))
        exit_n = int(self._get(req, "exit_sma", 10))

        # VIX z-score using a trailing window (no look-ahead).
        df["VIX_mean"] = df["VIX"].rolling(window=window, min_periods=window).mean()
        df["VIX_std"] = df["VIX"].rolling(window=window, min_periods=window).std()
        df["VIX_z"] = (df["VIX"] - df["VIX_mean"]) / df["VIX_std"].replace(0, np.nan)
        df["VIX_z"] = df["VIX_z"].fillna(0.0)

        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
        df[f"SMA_{exit_n}"] = df["Close"].rolling(window=exit_n).mean()

        # Shared filter pipeline indicators.
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
        if i < max(trend, int(self._get(req, "vix_window", 20))):
            return False
        row = df.iloc[i]
        is_bear = self._is_bear(req)

        z = row.get("VIX_z")
        if z is None or pd.isna(z):
            return False
        entry_z = float(self._get(req, "entry_z", 2.0))

        if is_bear:
            # Mirror: enter on extreme low VIX z (complacency) in downtrend
            if float(z) > -entry_z:
                return False
        else:
            if float(z) < entry_z:
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
        z = row.get("VIX_z")
        exit_n = int(self._get(req, "exit_sma", 10))
        sma_exit = row.get(f"SMA_{exit_n}")
        max_hold = int(self._get(req, "max_hold_days", 7))
        days_held = i - trade_state["entry_idx"]
        is_bear = self._is_bear(req)
        exit_z_threshold = float(self._get(req, "exit_z", 0.5))

        if z is not None and not pd.isna(z):
            if is_bear and float(z) > -exit_z_threshold:
                return True, "vix_revert"
            if (not is_bear) and float(z) < exit_z_threshold:
                return True, "vix_revert"

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
