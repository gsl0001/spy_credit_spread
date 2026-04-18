"""Market-hours + event blackout tests.

We don't exercise pandas_market_calendars here — that ships its own
holiday tests. Instead we feed a known datetime to ``is_market_open``
and verify the logic around weekends and RTH boundaries.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path

import pytest

from core.calendar import (
    MarketEvent,
    in_blackout,
    is_market_open,
    load_event_calendar,
    minutes_to_close,
)


@pytest.mark.unit
def test_weekend_is_closed():
    sat = datetime(2026, 4, 11, 12, 0)  # Saturday
    sun = datetime(2026, 4, 12, 12, 0)
    open_sat, why_sat = is_market_open(sat)
    open_sun, why_sun = is_market_open(sun)
    assert open_sat is False and why_sat == "weekend"
    assert open_sun is False and why_sun == "weekend"


@pytest.mark.unit
def test_minutes_to_close_midday():
    # 13:00 ET → 180 minutes to 16:00
    ts = datetime(2026, 4, 14, 13, 0)
    assert minutes_to_close(ts) == 180


@pytest.mark.unit
def test_minutes_to_close_after_hours_negative():
    ts = datetime(2026, 4, 14, 17, 0)
    assert minutes_to_close(ts) < 0


# ── Blackout ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_in_blackout_exact_match():
    events = [MarketEvent(date="2026-04-14", name="FOMC", severity="high")]
    hit, name = in_blackout(date(2026, 4, 14), events=events,
                            window_days_before=0, window_days_after=0)
    assert hit is True
    assert name == "FOMC"


@pytest.mark.unit
def test_in_blackout_window_before():
    events = [MarketEvent(date="2026-04-14", name="FOMC", severity="high")]
    hit, _ = in_blackout(date(2026, 4, 13), events=events,
                         window_days_before=1, window_days_after=0)
    assert hit is True


@pytest.mark.unit
def test_in_blackout_window_after():
    events = [MarketEvent(date="2026-04-14", name="FOMC", severity="high")]
    hit, _ = in_blackout(date(2026, 4, 16), events=events,
                         window_days_before=0, window_days_after=2)
    assert hit is True


@pytest.mark.unit
def test_outside_blackout():
    events = [MarketEvent(date="2026-04-14", name="FOMC", severity="high")]
    hit, _ = in_blackout(date(2026, 4, 20), events=events,
                         window_days_before=0, window_days_after=2)
    assert hit is False


@pytest.mark.unit
def test_severity_filter_drops_low():
    events = [MarketEvent(date="2026-04-14", name="Fed Speak", severity="low")]
    hit, _ = in_blackout(date(2026, 4, 14), events=events,
                         severity_min="medium")
    assert hit is False


@pytest.mark.unit
def test_empty_events_never_blackout():
    hit, name = in_blackout(date(2026, 4, 14), events=[])
    assert hit is False
    assert name == ""


# ── load_event_calendar ───────────────────────────────────────────────────

@pytest.mark.unit
def test_load_missing_file_returns_empty(tmp_path: Path):
    missing = tmp_path / "nope.json"
    assert load_event_calendar(str(missing)) == []


@pytest.mark.unit
def test_load_valid_json(tmp_path: Path):
    p = tmp_path / "events.json"
    p.write_text(json.dumps([
        {"date": "2026-04-14", "name": "FOMC", "severity": "high"},
        {"date": "2026-04-20", "name": "CPI"},
    ]), encoding="utf-8")
    events = load_event_calendar(str(p))
    assert len(events) == 2
    assert events[0].name == "FOMC"
    assert events[1].severity == "medium"  # default


@pytest.mark.unit
def test_load_malformed_json_returns_empty(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{this is not json", encoding="utf-8")
    assert load_event_calendar(str(p)) == []


@pytest.mark.unit
def test_load_skips_malformed_entries(tmp_path: Path):
    p = tmp_path / "events.json"
    p.write_text(json.dumps([
        {"date": "2026-04-14", "name": "FOMC"},
        {"name": "missing date"},            # should be skipped
        {"date": "2026-05-01", "name": "NFP"},
    ]), encoding="utf-8")
    events = load_event_calendar(str(p))
    assert len(events) == 2
    assert [e.name for e in events] == ["FOMC", "NFP"]
