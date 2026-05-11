"""SPY 0DTE Opening Range Breakout (ORB) strategy.

Based on SSRN paper 6355218:
"Regime-Conditional Alpha in SPY 0DTE Opening Range Breakout Strategies"

Logic:
- Bullish signal: SPY breaks above the 5-min Opening Range High
  → Buy Bull Call Debit Spread
- Bearish signal: SPY breaks below the 5-min Opening Range Low
  → Buy Bear Put Debit Spread

Key filters (from the paper — critical for lifting win rate from ~47% to ~65%):
- Day of week: Only Monday, Wednesday, Friday.
- VIX regime: VIX between 15 and 25 (inclusive).
- No major macroeconomic events on that day (FOMC, CPI, NFP).
- Max 1 trade per day.

Strike selection (paper's best EV zone):
- Long strike placed 0.96 to 2.00 points from the breakout price.
  Recommended starting point: 1.50 (midpoint).
- Short strike = long strike + width (default $5 wide).

Exits (paper's 25%/50% rules):
- Profit target: +50% of net debit paid.
- Stop loss: -50% of net debit paid.
- Time-based exit: 15:30 ET.
"""

from __future__ import annotations

import json
import logging
import threading
import time as _time_module
from datetime import date, time
from pathlib import Path

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# Days allowed per the paper: Monday=0, Wednesday=2, Friday=4
_ALLOWED_WEEKDAYS = {0, 2, 4}

_EVENTS_PATH = Path(__file__).resolve().parent.parent / "config" / "events_2026.json"

# VIX cache: guarded by a lock so APScheduler threads don't double-fetch.
_vix_cache: tuple[float | None, float] = (None, 0.0)
_vix_cache_lock = threading.Lock()
_VIX_TTL = 300.0  # seconds


def _load_news_dates() -> set[str]:
    """Load FOMC/CPI/NFP dates from events_2026.json as 'YYYY-MM-DD' strings."""
    try:
        raw = json.loads(_EVENTS_PATH.read_text(encoding="utf-8"))
        dates = {item["date"] for item in raw if item.get("severity") in ("high", "medium")}
        if dates:
            latest = max(dates)
            days_remaining = (date.fromisoformat(latest) - date.today()).days
            if days_remaining < 60:
                logger.warning(
                    "ORB events file %s expires in %d days (latest event: %s). "
                    "Update config/events_2026.json to maintain news-day filtering.",
                    _EVENTS_PATH.name, days_remaining, latest,
                )
        return dates
    except Exception:
        return set()


_NEWS_DATES: set[str] = _load_news_dates()


def _fetch_vix_cached() -> float | None:
    """Return latest VIX, cached for 5 minutes to avoid per-tick network calls.

    Thread-safe: APScheduler runs scanner ticks in a thread pool.
    """
    global _vix_cache
    with _vix_cache_lock:
        value, fetched_at = _vix_cache
        if value is not None and (_time_module.monotonic() - fetched_at) < _VIX_TTL:
            return value
        try:
            from core.yf_safe import safe_fast_info
            fetched = safe_fast_info("^VIX").get("lastPrice")
            if fetched is not None:
                _vix_cache = (float(fetched), _time_module.monotonic())
                return float(fetched)
        except Exception:
            pass
        return None  # fetch failed — callers decide whether to block or allow


class OrbStrategy(BaseStrategy):
    """5-minute Opening Range Breakout on SPY 0DTE options (SSRN 6355218)."""

    # Intraday — 5-min bars so the OR window (9:30–9:35 ET) is one bar.
    BAR_SIZE: str = "5 mins"
    HISTORY_PERIOD: str = "5d"
    VETTING_RESULT: str = "shipped"

    @property
    def name(self) -> str:
        return "ORB 5-min 0DTE"

    @classmethod
    def id(cls) -> str:
        return "orb"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "or_minutes": {
                "type": "number",
                "default": 5,
                "label": "Opening Range Minutes",
                "description": "Fixed at 5 per SSRN 6355218",
            },
            "offset": {
                "type": "number",
                "default": 1.50,
                "min": 0.96,
                "max": 2.00,
                "label": "Strike Offset (pts)",
                "description": "Long strike placed this many points from breakout price (paper best EV zone: 0.96–2.00)",
            },
            "width": {
                "type": "number",
                "default": 5,
                "label": "Spread Width ($)",
                "description": "Points between long and short strikes",
            },
            "min_range_pct": {
                "type": "number",
                "default": 0.05,
                "label": "Min OR Range (%)",
                "description": "Minimum OR size as % of price (volatility filter)",
            },
            "time_exit_hhmm": {
                "type": "string",
                "default": "15:30",
                "label": "Time Exit (ET HH:MM)",
                "description": "Close all positions by this time to avoid gamma/theta risk at close",
            },
            "vix_min": {
                "type": "number",
                "default": 15,
                "label": "VIX Min",
                "description": "Minimum VIX level for entry (paper filter)",
            },
            "vix_max": {
                "type": "number",
                "default": 25,
                "label": "VIX Max",
                "description": "Maximum VIX level for entry (paper filter)",
            },
            "allowed_days": {
                "type": "string",
                "default": "MWF",
                "label": "Allowed Days",
                "description": "Trading days per paper: Monday, Wednesday, Friday only",
            },
            "skip_news_days": {
                "type": "boolean",
                "default": True,
                "label": "Skip News Days",
                "description": "Skip FOMC, CPI, NFP days (loaded from config/events_2026.json)",
            },
        }

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()

        if not isinstance(df.index, pd.DatetimeIndex):
            return df

        # The bars fetcher already converts to America/New_York then strips tz.
        # The index is tz-naive but represents ET wall time — use it directly.
        # Do NOT re-localize/convert; that would shift times by UTC offset (5h).

        or_open_time = time(9, 30)
        or_close_time = time(9, 35)

        df["_date"] = df.index.date
        df["or_high"] = np.nan
        df["or_low"] = np.nan

        for day, grp in df.groupby("_date"):
            or_mask = (grp.index.time >= or_open_time) & (grp.index.time < or_close_time)
            or_bars = grp[or_mask]
            if or_bars.empty:
                continue
            oh = or_bars["High"].max()
            ol = or_bars["Low"].min()
            df.loc[grp.index, "or_high"] = oh
            df.loc[grp.index, "or_low"] = ol

        return df

    # ── helpers ──────────────────────────────────────────────────────────

    def _get_param(self, req, key: str, default):
        params = getattr(req, "strategy_params", {}) or {}
        return params.get(key, default)

    def _passes_day_filter(self, ts) -> bool:
        """Only Monday (0), Wednesday (2), Friday (4)."""
        return ts.weekday() in _ALLOWED_WEEKDAYS

    def _passes_vix_filter(self, req, vix_min: float, vix_max: float) -> bool:
        """VIX must be between 15 and 25 inclusive (paper filter).

        Uses a 5-minute module-level cache to avoid a live network call on
        every scanner tick. Fail-closed: if VIX is unknown, block the trade.
        """
        vix = getattr(req, "current_vix", None)
        if vix is None:
            vix = _fetch_vix_cached()
        if vix is None:
            # Can't verify VIX — block to stay safe (paper filter is critical)
            return False
        return vix_min <= float(vix) <= vix_max

    def _passes_news_filter(self, ts, skip_news: bool) -> bool:
        """Skip FOMC, CPI, NFP days per events_2026.json."""
        if not skip_news:
            return True
        date_str = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)
        return date_str not in _NEWS_DATES

    def _passes_range_filter(self, or_high: float, or_low: float, price: float, min_pct: float) -> bool:
        """OR size must be >= min_range_pct % of price (volatility filter)."""
        if price <= 0:
            return False
        return (or_high - or_low) >= (price * min_pct / 100.0)

    def _breakout_direction(self, close: float, or_high: float, or_low: float, preset_dir: str) -> str:
        """Return 'bull', 'bear', or '' based on breakout and preset direction.

        The preset direction constrains which side to trade:
        - 'bull' / 'bull_call': only bull breakouts
        - 'bear' / 'bear_put':  only bear breakouts
        - 'both':               either side (not typical for presets)
        """
        bull_break = close > or_high
        bear_break = close < or_low
        allow_bull = preset_dir in ("bull", "bull_call", "both")
        allow_bear = preset_dir in ("bear", "bear_put", "both")
        if bull_break and allow_bull:
            return "bull"
        if bear_break and allow_bear:
            return "bear"
        return ""

    # ── strategy interface ───────────────────────────────────────────────

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        if i < 1:
            return False

        row = df.iloc[i]
        ts = row.name
        if not hasattr(ts, "time"):
            return False

        t = ts.time()

        # Only fire after the 5-min OR window closes.
        # Bars are labeled by open time: 9:35 bar = 9:35–9:40 data, first tradeable bar.
        if t < time(9, 35):
            return False

        # Time-based cutoff — no new entries at or after time_exit
        exit_hhmm = self._get_param(req, "time_exit_hhmm", "15:30")
        try:
            eh, em = (int(x) for x in exit_hhmm.split(":"))
        except Exception:
            eh, em = 15, 30
        if t >= time(eh, em):
            return False

        # OR levels must be computed
        or_high = row.get("or_high", np.nan)
        or_low = row.get("or_low", np.nan)
        if pd.isna(or_high) or pd.isna(or_low):
            return False

        # Day-of-week filter: Mon/Wed/Fri only
        if not self._passes_day_filter(ts):
            return False

        # News day filter
        skip_news = self._get_param(req, "skip_news_days", True)
        if not self._passes_news_filter(ts, skip_news):
            return False

        # VIX filter: 15 <= VIX <= 25 (fail-closed if VIX unavailable)
        vix_min = float(self._get_param(req, "vix_min", 15))
        vix_max = float(self._get_param(req, "vix_max", 25))
        if not self._passes_vix_filter(req, vix_min, vix_max):
            return False

        # Range size filter
        min_range_pct = float(self._get_param(req, "min_range_pct", 0.05))
        close = float(row.get("Close", row.get("close", 0)))
        if not self._passes_range_filter(or_high, or_low, close, min_range_pct):
            return False

        # Direction check: preset constrains which side we trade.
        # This prevents a bull-preset from accidentally placing a bear spread.
        preset_dir = str(getattr(req, "strategy_type", getattr(req, "direction", "bull_call")))
        direction = self._breakout_direction(close, or_high, or_low, preset_dir)
        return direction != ""

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        ts = row.name
        if not hasattr(ts, "time"):
            return False, ""

        t = ts.time()

        # Time-based exit at 15:30 ET (avoid holding into close per paper)
        exit_hhmm = self._get_param(req, "time_exit_hhmm", "15:30")
        try:
            eh, em = (int(x) for x in exit_hhmm.split(":"))
        except Exception:
            eh, em = 15, 30
        if t >= time(eh, em):
            return True, "time_exit_15:30"

        return False, ""
