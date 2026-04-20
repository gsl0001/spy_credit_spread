"""Market-hours + event blackout tests.

We don't exercise pandas_market_calendars here — that ships its own
holiday tests. Instead we feed a known datetime to ``is_market_open``
and verify the logic around weekends and RTH boundaries.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

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


# ── Early-close day helpers ───────────────────────────────────────────────

def _make_mcal_mock(open_utc: datetime, close_utc: datetime):
    """Return a fake pandas_market_calendars module with a canned schedule.

    ``open_utc`` and ``close_utc`` are timezone-aware UTC datetimes that
    will be placed in ``row["market_open"]`` / ``row["market_close"]``.
    """
    import pandas as pd

    row = pd.DataFrame(
        [{"market_open": open_utc, "market_close": close_utc}]
    )

    mock_nyse = MagicMock()
    mock_nyse.schedule.return_value = row

    mock_mcal = MagicMock()
    mock_mcal.get_calendar.return_value = mock_nyse
    return mock_mcal


UTC = timezone.utc

# 2026-11-27 (Black Friday) — NYSE early-close day: 9:30 AM–1:00 PM ET.
# November is EST (UTC-5):
#   market_open  = 14:30 UTC
#   market_close = 18:00 UTC  (13:00 ET)
_BF_OPEN_UTC  = datetime(2026, 11, 27, 14, 30, tzinfo=UTC)
_BF_CLOSE_UTC = datetime(2026, 11, 27, 18,  0, tzinfo=UTC)

# 2026-04-21 (Tuesday, regular session). April is EDT (UTC-4):
#   market_open  = 13:30 UTC
#   market_close = 20:00 UTC  (16:00 ET)
_REG_OPEN_UTC  = datetime(2026, 4, 21, 13, 30, tzinfo=UTC)
_REG_CLOSE_UTC = datetime(2026, 4, 21, 20,  0, tzinfo=UTC)


# ── Early-close: after the 1 PM ET close ──────────────────────────────────

@pytest.mark.unit
def test_early_close_after_1pm_is_closed():
    """13:30 ET on Black Friday is AFTER the 1 PM early close → market closed."""
    mock_mcal = _make_mcal_mock(_BF_OPEN_UTC, _BF_CLOSE_UTC)

    # 13:30 ET = 18:30 UTC on 2026-11-27 (UTC-5)
    ts_utc = datetime(2026, 11, 27, 18, 30, tzinfo=UTC)

    with patch.dict(sys.modules, {"pandas_market_calendars": mock_mcal}):
        # Pass a tz-aware UTC datetime so the code's UTC conversion works cleanly
        is_open, reason = is_market_open(ts_utc)

    assert is_open is False
    assert reason == "outside_rth"


@pytest.mark.unit
def test_early_close_2pm_is_closed():
    """14:00 ET on Black Friday (19:00 UTC) is well past the 1 PM early close."""
    mock_mcal = _make_mcal_mock(_BF_OPEN_UTC, _BF_CLOSE_UTC)

    ts_utc = datetime(2026, 11, 27, 19, 0, tzinfo=UTC)

    with patch.dict(sys.modules, {"pandas_market_calendars": mock_mcal}):
        is_open, reason = is_market_open(ts_utc)

    assert is_open is False
    assert reason == "outside_rth"


# ── Early-close: before the 1 PM ET close ────────────────────────────────

@pytest.mark.unit
def test_early_close_morning_is_open():
    """10:00 ET on Black Friday (15:00 UTC) is before the early close → open."""
    mock_mcal = _make_mcal_mock(_BF_OPEN_UTC, _BF_CLOSE_UTC)

    ts_utc = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)  # 10:00 AM ET

    with patch.dict(sys.modules, {"pandas_market_calendars": mock_mcal}):
        is_open, reason = is_market_open(ts_utc)

    assert is_open is True
    assert reason == "open"


# ── Fallback: no pandas_market_calendars installed ───────────────────────

@pytest.mark.unit
def test_early_close_fallback_without_pmc():
    """Without pandas_market_calendars the code falls back to 16:00 ET close,
    so 13:30 ET on any weekday is treated as open (no early-close awareness)."""

    # Remove mcal from sys.modules so the ImportError branch is taken.
    modules_without_mcal = {
        k: v for k, v in sys.modules.items()
        if k != "pandas_market_calendars"
    }
    modules_without_mcal["pandas_market_calendars"] = None  # blocks import

    # Use a naive datetime (no pytz either) to exercise the pure fallback path.
    # 2026-11-27 13:30 ET (Black Friday, mid-afternoon) — naive, treated as ET.
    ts_naive = datetime(2026, 11, 27, 13, 30)  # Friday, no tzinfo

    with patch.dict(sys.modules, {"pandas_market_calendars": None,
                                  "pytz": None}):
        is_open, reason = is_market_open(ts_naive)

    # Fallback uses simple 9:30–16:00 ET, so 13:30 is inside RTH.
    assert is_open is True
    assert reason == "open"


# ── Regular full-session day ──────────────────────────────────────────────

@pytest.mark.unit
def test_regular_day_1330_is_open():
    """13:30 ET on a normal Tuesday (2026-04-21) is well within regular hours."""
    mock_mcal = _make_mcal_mock(_REG_OPEN_UTC, _REG_CLOSE_UTC)

    # 13:30 ET = 17:30 UTC on 2026-04-21 (EDT, UTC-4)
    ts_utc = datetime(2026, 4, 21, 17, 30, tzinfo=UTC)

    with patch.dict(sys.modules, {"pandas_market_calendars": mock_mcal}):
        is_open, reason = is_market_open(ts_utc)

    assert is_open is True
    assert reason == "open"


# ── risk.evaluate_pre_trade rejects at early-close time ──────────────────

@pytest.mark.unit
def test_risk_rejects_order_at_early_close_time():
    """evaluate_pre_trade must return market_closed when is_market_open
    signals the market is shut (e.g. 13:30 ET on an early-close day)."""
    from core.risk import (
        AccountSnapshot, RiskContext, RiskLimits, evaluate_pre_trade,
    )

    ctx = RiskContext(
        account=AccountSnapshot(
            equity=100_000.0,
            buying_power=50_000.0,
            excess_liquidity=50_000.0,
        ),
        open_positions=0,
        today_realized_pnl=0.0,
        debit_per_contract=250.0,
        margin_per_contract=250.0,
        contracts=1,
        target_dte=14,
        limits=RiskLimits(require_market_open=True),
        today=date(2026, 11, 27),
        events=[],
    )

    # Simulate is_market_open returning False (early close already passed)
    with patch("core.calendar.is_market_open",
               return_value=(False, "outside_rth")):
        decision = evaluate_pre_trade(ctx)

    assert decision.allowed is False
    assert decision.reason == "market_closed"
    assert decision.details["why"] == "outside_rth"
