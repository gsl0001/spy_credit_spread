"""Unit tests for OrbSpreadStrategy — pure logic, no live connectivity.

The strategy's entry/exit filters are tested by driving on_bar() with
synthetic Bar objects. NautilusTrader internals (order submission, cache)
are mocked minimally so the strategy logic is the only thing under test.
"""
from __future__ import annotations

import sys
from datetime import date, time, datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies.orb_spread import OrbSpreadConfig, OrbSpreadStrategy


# ── helpers ──────────────────────────────────────────────────────────────────

def _ts_ns(bar_date: date, bar_time: time) -> int:
    """Return UTC nanoseconds for a given ET wall-clock date+time (EDT offset -4h)."""
    dt_et = datetime(bar_date.year, bar_date.month, bar_date.day,
                     bar_time.hour, bar_time.minute, tzinfo=timezone(timedelta(hours=-4)))
    return int(dt_et.timestamp() * 1e9)


def _bar(bar_date: date, bar_time: time,
         close: float, high: float | None = None, low: float | None = None):
    """Minimal Bar-like object accepted by on_bar()."""
    b = SimpleNamespace()
    b.ts_event = _ts_ns(bar_date, bar_time)
    b.close = close
    b.high = high if high is not None else close
    b.low = low if low is not None else close
    b.open = close
    return b


def _make_strategy(vix: float | None = 20.0, **kwargs) -> OrbSpreadStrategy:
    cfg = OrbSpreadConfig(events_file="../config/events_2026.json", **kwargs)
    strat = OrbSpreadStrategy(config=cfg)
    # Minimal mocks so we don't need a full TradingNode
    strat.log = MagicMock()
    strat.cache = MagicMock()
    strat.order_factory = MagicMock()
    strat.submit_order_list = MagicMock()
    strat.close_position = MagicMock()
    strat.subscribe_bars = MagicMock()
    # Suppress news-date loading (file may not exist in test env)
    strat._news_dates = set()
    # Pin _get_vix so on_bar never overwrites _current_vix via cache MagicMock
    strat._get_vix = lambda: vix
    strat._current_vix = vix
    return strat


MONDAY = date(2026, 4, 27)    # Monday
WEDNESDAY = date(2026, 4, 29) # Wednesday (also an FOMC day — patched out in day filter tests)
TUESDAY = date(2026, 4, 28)   # Tuesday — blocked


# ── OR window detection ───────────────────────────────────────────────────────

def test_or_window_sets_high_low():
    s = _make_strategy()
    s.on_bar(_bar(MONDAY, time(9, 30), close=530.0, high=530.8, low=529.2))
    assert s._or_high == pytest.approx(530.8)
    assert s._or_low == pytest.approx(529.2)
    assert s._or_date == MONDAY


def test_or_window_accumulates_multiple_or_bars():
    """If the OR window spans more than one bar (< 9:35), we extend high/low."""
    s = _make_strategy()
    s.on_bar(_bar(MONDAY, time(9, 30), close=530.0, high=530.5, low=529.5))
    s.on_bar(_bar(MONDAY, time(9, 33), close=530.2, high=531.0, low=529.0))
    assert s._or_high == pytest.approx(531.0)
    assert s._or_low == pytest.approx(529.0)


def test_or_bar_does_not_trigger_entry():
    """Bars inside the OR window (< 9:35) must never trigger entry."""
    s = _make_strategy()
    s.on_bar(_bar(MONDAY, time(9, 30), close=532.0, high=531.0, low=528.0))
    assert not s._in_trade
    s.submit_order_list.assert_not_called()


# ── Entry: day-of-week filter ─────────────────────────────────────────────────

def test_entry_blocked_tuesday():
    s = _make_strategy()
    s.on_bar(_bar(TUESDAY, time(9, 30), close=530.0, high=530.8, low=529.2))
    s.on_bar(_bar(TUESDAY, time(9, 35), close=531.0, high=531.0, low=531.0))
    assert not s._in_trade


def test_entry_allowed_monday():
    s = _make_strategy()
    s.on_bar(_bar(MONDAY, time(9, 30), close=530.0, high=530.5, low=529.5))
    with patch.object(s, "_submit_spread") as mock_submit:
        s.on_bar(_bar(MONDAY, time(9, 35), close=531.0))
        mock_submit.assert_called_once()


# ── Entry: VIX filter ─────────────────────────────────────────────────────────

def test_entry_blocked_vix_too_high():
    s = _make_strategy(vix=30.0)
    s.on_bar(_bar(MONDAY, time(9, 30), close=530.0, high=530.5, low=529.5))
    with patch.object(s, "_submit_spread") as mock_submit:
        s.on_bar(_bar(MONDAY, time(9, 35), close=531.0))
        mock_submit.assert_not_called()


def test_entry_blocked_vix_unavailable():
    s = _make_strategy(vix=None)
    s.on_bar(_bar(MONDAY, time(9, 30), close=530.0, high=530.5, low=529.5))
    with patch.object(s, "_submit_spread") as mock_submit:
        s.on_bar(_bar(MONDAY, time(9, 35), close=531.0))
        mock_submit.assert_not_called()


def test_entry_allowed_vix_boundary_15():
    s = _make_strategy(vix=15.0)
    s.on_bar(_bar(MONDAY, time(9, 30), close=530.0, high=530.5, low=529.5))
    with patch.object(s, "_submit_spread") as mock_submit:
        s.on_bar(_bar(MONDAY, time(9, 35), close=531.0))
        mock_submit.assert_called_once()


def test_entry_allowed_vix_boundary_25():
    s = _make_strategy(vix=25.0)
    s.on_bar(_bar(MONDAY, time(9, 30), close=530.0, high=530.5, low=529.5))
    with patch.object(s, "_submit_spread") as mock_submit:
        s.on_bar(_bar(MONDAY, time(9, 35), close=531.0))
        mock_submit.assert_called_once()


# ── Entry: range filter ───────────────────────────────────────────────────────

def test_entry_blocked_tiny_range():
    s = _make_strategy()
    # range = 0.001 on $530 price → well below 0.05% min
    s.on_bar(_bar(MONDAY, time(9, 30), close=530.0, high=530.001, low=530.000))
    with patch.object(s, "_submit_spread") as mock_submit:
        s.on_bar(_bar(MONDAY, time(9, 35), close=530.002))
        mock_submit.assert_not_called()


# ── Entry: news day filter ────────────────────────────────────────────────────

def test_entry_blocked_news_day():
    s = _make_strategy()
    s._news_dates = {MONDAY.strftime("%Y-%m-%d")}
    s.on_bar(_bar(MONDAY, time(9, 30), close=530.0, high=530.5, low=529.5))
    with patch.object(s, "_submit_spread") as mock_submit:
        s.on_bar(_bar(MONDAY, time(9, 35), close=531.0))
        mock_submit.assert_not_called()


# ── Entry: no breakout ────────────────────────────────────────────────────────

def test_entry_no_breakout_inside_range():
    s = _make_strategy()
    s.on_bar(_bar(MONDAY, time(9, 30), close=530.0, high=530.5, low=529.5))
    with patch.object(s, "_submit_spread") as mock_submit:
        # Close exactly at OR_high — not > OR_high
        s.on_bar(_bar(MONDAY, time(9, 35), close=530.5))
        mock_submit.assert_not_called()


# ── Entry: time cutoff ────────────────────────────────────────────────────────

def test_entry_blocked_at_or_after_1530():
    s = _make_strategy()
    s.on_bar(_bar(MONDAY, time(9, 30), close=530.0, high=530.5, low=529.5))
    with patch.object(s, "_submit_spread") as mock_submit:
        s.on_bar(_bar(MONDAY, time(15, 30), close=531.0))
        mock_submit.assert_not_called()


# ── Exit: time exit ───────────────────────────────────────────────────────────

def test_exit_fires_at_1530():
    s = _make_strategy()
    s._in_trade = True
    s._long_instrument = MagicMock()
    s._short_instrument = MagicMock()
    with patch.object(s, "_close_all") as mock_close:
        s.on_bar(_bar(MONDAY, time(15, 30), close=530.0))
        mock_close.assert_called_once_with("time_exit_15:30")


def test_exit_no_fire_mid_day():
    s = _make_strategy()
    s._in_trade = True
    s._long_instrument = MagicMock()
    s._short_instrument = MagicMock()
    s._entry_cost = 250.0
    # Return None for option mids → no P&L exit
    with patch.object(s, "_get_option_mid", return_value=None):
        with patch.object(s, "_close_all") as mock_close:
            s.on_bar(_bar(MONDAY, time(11, 0), close=530.0))
            mock_close.assert_not_called()


# ── Exit: P&L exits ───────────────────────────────────────────────────────────

def test_exit_take_profit():
    s = _make_strategy()
    s._in_trade = True
    s._entry_cost = 200.0  # $200 per contract
    s._long_instrument = MagicMock()
    s._short_instrument = MagicMock()
    # current_value = (2.5 - 0.0) * 100 = 250 → +25% > 50% TP? No, let's make it 50%
    # entry_cost=200, target=+50% → need current_value >= 300
    # long_mid=3.5, short_mid=0.5 → (3.0)*100=300 → pnl_pct=50% → fires
    def mock_mid(inst):
        if inst == s._long_instrument:
            return 3.5
        return 0.5
    with patch.object(s, "_get_option_mid", side_effect=mock_mid):
        with patch.object(s, "_close_all") as mock_close:
            s.on_bar(_bar(MONDAY, time(11, 0), close=530.0))
            mock_close.assert_called_once_with("take_profit")


def test_exit_stop_loss():
    s = _make_strategy()
    s._in_trade = True
    s._entry_cost = 200.0
    s._long_instrument = MagicMock()
    s._short_instrument = MagicMock()
    # long_mid=0.5, short_mid=0.5 → current_value=0 → pnl_pct=-100% → stop loss
    def mock_mid(inst):
        return 0.5
    with patch.object(s, "_get_option_mid", side_effect=mock_mid):
        with patch.object(s, "_close_all") as mock_close:
            s.on_bar(_bar(MONDAY, time(11, 0), close=530.0))
            mock_close.assert_called_once_with("stop_loss")


# ── on_stop closes open position ──────────────────────────────────────────────

def test_on_stop_closes_open_position():
    s = _make_strategy()
    s._in_trade = True
    s._long_instrument = MagicMock()
    s._short_instrument = MagicMock()
    with patch.object(s, "_close_all") as mock_close:
        s.on_stop()
        mock_close.assert_called_once_with("strategy_stop")


def test_on_stop_no_position_no_close():
    s = _make_strategy()
    s._in_trade = False
    with patch.object(s, "_close_all") as mock_close:
        s.on_stop()
        mock_close.assert_not_called()
