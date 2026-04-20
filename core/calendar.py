"""Market hours + event blackout calendar.

Uses `pandas_market_calendars` (NYSE) when available, falls back to a
simple 9:30–16:00 ET rule when the library is missing so the server
still boots.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional


ET_OFFSET_STD = timedelta(hours=-5)
ET_OFFSET_DST = timedelta(hours=-4)


def _now_et() -> datetime:
    """Return current time in US/Eastern. DST handled via pytz when present."""
    try:
        import pytz
        return datetime.now(pytz.timezone("US/Eastern"))
    except ImportError:
        # Fallback approximation — assume DST Mar-Nov.
        now = datetime.now(timezone.utc)
        m = now.month
        offset = ET_OFFSET_DST if 3 <= m <= 11 else ET_OFFSET_STD
        return (now + offset).replace(tzinfo=None)


def is_market_open(now: Optional[datetime] = None) -> tuple[bool, str]:
    """Return ``(open, reason)`` for regular-hours trading now."""
    ts = now or _now_et()
    weekday = ts.weekday()  # Mon=0 … Sun=6
    if weekday >= 5:
        return False, "weekend"
    # Try pandas_market_calendars for full holiday + early-close support.
    try:
        import pandas_market_calendars as mcal  # type: ignore
        nyse = mcal.get_calendar("NYSE")
        sched = nyse.schedule(start_date=ts.date().isoformat(),
                              end_date=ts.date().isoformat())
        if sched.empty:
            return False, "holiday"
        row = sched.iloc[0]
        # Schedule is in UTC; convert current time to UTC for comparison.
        try:
            import pytz
            et = pytz.timezone("US/Eastern")
            if ts.tzinfo is None:
                ts_aware = et.localize(ts)
            else:
                ts_aware = ts
            utc_ts = ts_aware.astimezone(pytz.UTC)
        except ImportError:
            utc_ts = ts
        open_utc, close_utc = row["market_open"], row["market_close"]
        if utc_ts >= open_utc and utc_ts < close_utc:
            return True, "open"
        return False, "outside_rth"
    except ImportError:
        pass
    # Fallback: 9:30 to 16:00 ET
    t = ts.time()
    if time(9, 30) <= t < time(16, 0):
        return True, "open"
    return False, "outside_rth"


def minutes_to_close(now: Optional[datetime] = None) -> int:
    """How many minutes until 16:00 ET? Negative after close."""
    ts = now or _now_et()
    close = datetime.combine(ts.date(), time(16, 0))
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        close = close.replace(tzinfo=ts.tzinfo)
    delta = close - ts
    return int(delta.total_seconds() // 60)


# ── Event calendar ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MarketEvent:
    date: str           # YYYY-MM-DD
    name: str
    severity: str = "medium"   # high | medium | low


def load_event_calendar(path: Optional[str] = None) -> list[MarketEvent]:
    """Read the events JSON file. Missing file → empty list."""
    if path is None:
        from core.settings import SETTINGS
        path = SETTINGS.event_calendar_file
    p = Path(path)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out: list[MarketEvent] = []
    for item in raw:
        try:
            out.append(MarketEvent(
                date=item["date"],
                name=item.get("name", "event"),
                severity=item.get("severity", "medium"),
            ))
        except (KeyError, TypeError):
            continue
    return out


def in_blackout(
    target_date: date,
    events: Optional[list[MarketEvent]] = None,
    window_days_before: int = 0,
    window_days_after: int = 0,
    severity_min: str = "medium",
) -> tuple[bool, str]:
    """Does `target_date` fall inside any event's blackout window?"""
    events = events or load_event_calendar()
    if not events:
        return False, ""
    levels = {"low": 0, "medium": 1, "high": 2}
    threshold = levels.get(severity_min, 1)
    for ev in events:
        try:
            ed = datetime.strptime(ev.date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if levels.get(ev.severity, 1) < threshold:
            continue
        start = ed - timedelta(days=window_days_before)
        end = ed + timedelta(days=window_days_after)
        if start <= target_date <= end:
            return True, ev.name
    return False, ""


__all__ = [
    "is_market_open",
    "minutes_to_close",
    "in_blackout",
    "load_event_calendar",
    "MarketEvent",
]
