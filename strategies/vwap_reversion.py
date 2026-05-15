"""SPY intraday VWAP-band mean-reversion (0DTE debit spread).

Thesis
------
On 5-min bars during the regular session, when SPY's deviation from session
VWAP exceeds ``band_k`` standard deviations of in-session price-vs-VWAP
dispersion, price tends to revert back toward VWAP within the same session.

We harvest the reversion with a same-day 0DTE debit spread aimed in the
reversion direction:

  - Close > VWAP + k·σ  → bear-put debit spread, expecting drift down to VWAP
  - Close < VWAP − k·σ  → bull-call debit spread, expecting drift up to VWAP

References
----------
- QuantifiedStrategies, "VWAP Trading Strategy" — SPY 2017-2025 backtest of
  close-vs-VWAP mean reversion, PF ≈ 1.69 with WR < 50% on the equity itself.
  Asymmetric R:R lifts when transported to debit spreads.
- VWAP mean reversion is the dominant edge in large-scale signal studies
  (1.6M permutations, Bonferroni-significant short side at +0.89pp).

Differentiation
---------------
Existing intraday strategies in this codebase:
  - ``orb``               — opening-range breakout-touch (trend-follow)
  - ``ldm_0dte``          — late-day momentum (trend-follow)
  - ``ldm_fade_0dte``     — late-day fade (single-window mean-rev)
  - ``order_flow_0dte``   — tick-flow imbalance
None use session-VWAP band reversion as the trigger; this strategy is
direction-agnostic mean-reversion that fires anywhere in the session
between the warmup minute and the entry cutoff.

Engine
------
``BAR_SIZE = "5 mins"`` + ``INTRADAY_ENGINE = "generic"`` routes through
``core/backtest_intraday.py``. Force-flat at session close is enforced by
the engine (0DTE cannot carry overnight). Pricing uses the linear
delta-vs-entry approximation from the ORB engine — adequate for screening.
"""
from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class VwapReversionStrategy(BaseStrategy):
    BAR_SIZE: str = "5 mins"
    HISTORY_PERIOD: str = "60d"  # yfinance cap for 5m bars
    INTRADAY_ENGINE: str = "generic"
    VETTING_RESULT: str = "rejected"
    # Rejection log (2026-05-10, 60d of 5-min SPY bars via yfinance):
    #   Best param sweep: band_k=2.5, strike_width=5, TP/SL=50/50, bull side.
    #     trades=57, win_rate=57.9%, profit_factor=0.69, sharpe=-2.35, dd=-10.3%
    #   Bear side consistently worse (-PF 0.53, fights SPY drift).
    #   Tighter TP (15-25%) raised WR to 64% but PF collapsed because
    #   $20/trade slippage swamps small wins (linear-delta model: TP needs
    #   ~$3 SPY move; small VWAP reversions rarely deliver that before
    #   continuation triggers the stop).
    # Path to graduation:
    #   1. Topology change → credit spread (sell put-spread when below
    #      lower band / sell call-spread when above upper band). Theta
    #      tailwind + non-linear payoff captures the small-reversion edge
    #      that this debit version can't. Requires engine work first
    #      (run_backtest_engine doesn't price credit spreads).
    #   2. Or: tighten the slippage model — current $20/trade per-spread
    #      is conservative for SPY 0DTE; a realistic $8-10 model may push
    #      best-case PF above 1.4. Re-run after slippage retune.

    @property
    def name(self) -> str:
        return "VWAP-Band Reversion (0DTE)"

    @classmethod
    def id(cls) -> str:
        return "vwap_reversion"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "band_k": {
                "type": "number", "default": 2.0, "min": 1.0, "max": 4.0,
                "label": "Band σ (k)",
                "description": "Fire when |close - vwap| exceeds this many σ of session price-vs-vwap dispersion",
            },
            "warmup_bars": {
                "type": "number", "default": 6, "min": 3, "max": 30,
                "label": "Warmup bars",
                "description": "Skip the first N bars after open (let σ stabilize)",
            },
            "min_session_dispersion": {
                "type": "number", "default": 0.10, "min": 0.0, "max": 2.0,
                "label": "Min σ ($)",
                "description": "Require at least this much price-vs-vwap σ to filter no-vol opens",
            },
            "vwap_touch_exit": {
                "type": "boolean", "default": True,
                "label": "Exit on VWAP touch",
                "description": "Take strategy exit when price revisits VWAP",
            },
            "entry_cutoff_hhmm": {
                "type": "string", "default": "14:30",
                "label": "No-new-entry time (ET)",
                "description": "Engine refuses new entries after this; reversion has too little time later",
            },
            "time_exit_hhmm": {
                "type": "string", "default": "15:55",
                "label": "Force-flat (ET)",
                "description": "Engine force-closes any open position at this time",
            },
        }

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _get(req, key: str, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    @staticmethod
    def _resolve_direction(req) -> str:
        st = getattr(req, "strategy_type", "")
        if st == "bear_put":
            return "bear"
        if st == "bull_call":
            return "bull"
        return "bear" if getattr(req, "direction", "bull") == "bear" else "bull"

    # ── indicators ─────────────────────────────────────────────────────────

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        out = df.copy()
        # Engine passes lowercase OHLCV; tolerate either case.
        close_col = "close" if "close" in out.columns else "Close"
        high_col = "high" if "high" in out.columns else "High"
        low_col = "low" if "low" in out.columns else "Low"
        vol_col = "volume" if "volume" in out.columns else (
            "Volume" if "Volume" in out.columns else None
        )

        idx = out.index
        if not isinstance(idx, pd.DatetimeIndex):
            return out
        et = idx.tz_convert("America/New_York").tz_localize(None) if idx.tz is not None else idx
        out["_session_date"] = pd.DatetimeIndex(et).date

        typical = (out[high_col] + out[low_col] + out[close_col]) / 3.0
        if vol_col is not None:
            vol = out[vol_col].clip(lower=0).fillna(0)
        else:
            vol = pd.Series(1.0, index=out.index)
        # Replace zero-volume bars with 1 to avoid div-by-zero in the cumsum.
        vol = vol.where(vol > 0, 1.0)

        pv = typical * vol
        # Cumulative sums reset per session date.
        cum_pv = pv.groupby(out["_session_date"]).cumsum()
        cum_v = vol.groupby(out["_session_date"]).cumsum()
        out["VWAP"] = cum_pv / cum_v

        # Session bar number (0-indexed) for the warmup gate.
        out["_bar_in_session"] = out.groupby("_session_date").cumcount()

        # Per-session running std of (close - vwap) using a 20-bar rolling
        # window; reset implicitly because warmup_bars filter skips early
        # bars and the std uses min_periods so early NaNs propagate.
        dev = out[close_col] - out["VWAP"]
        out["VWAP_DEV"] = dev
        out["VWAP_SIGMA"] = (
            dev.groupby(out["_session_date"])
               .transform(lambda s: s.rolling(window=20, min_periods=5).std())
        )
        out["VWAP_Z"] = out["VWAP_DEV"] / out["VWAP_SIGMA"].replace(0, np.nan)
        return out

    # ── entries ────────────────────────────────────────────────────────────

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        if i < 1:
            return False
        row = df.iloc[i]
        bar_in_session = row.get("_bar_in_session")
        if bar_in_session is None or pd.isna(bar_in_session):
            return False
        warmup = int(self._get(req, "warmup_bars", 6) or 6)
        if int(bar_in_session) < warmup:
            return False

        sigma = row.get("VWAP_SIGMA")
        z = row.get("VWAP_Z")
        if sigma is None or pd.isna(sigma) or z is None or pd.isna(z):
            return False
        min_sigma = float(self._get(req, "min_session_dispersion", 0.10) or 0.0)
        if float(sigma) < min_sigma:
            return False

        k = float(self._get(req, "band_k", 2.0) or 2.0)
        direction = self._resolve_direction(req)
        # Bull spread reverts UP — fires when price is below lower band.
        if direction == "bull":
            return float(z) <= -k
        return float(z) >= k

    # ── exits ──────────────────────────────────────────────────────────────

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        if not bool(self._get(req, "vwap_touch_exit", True)):
            return False, ""
        row = df.iloc[i]
        z = row.get("VWAP_Z")
        if z is None or pd.isna(z):
            return False, ""

        direction = trade_state.get("direction") or self._resolve_direction(req)
        # Bull entry fired when z << 0 — exit when price reverts back to/through VWAP.
        if direction == "bull" and float(z) >= 0.0:
            return True, "vwap_touch"
        if direction == "bear" and float(z) <= 0.0:
            return True, "vwap_touch"
        return False, ""
