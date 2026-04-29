"""Tests for OrbStrategy (SSRN 6355218) — strategies/orb.py."""
from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from strategies.orb import OrbStrategy
from core.chain import pick_bull_call_strikes


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_req(
    strategy_type: str = "bull_call",
    strategy_params: dict | None = None,
    current_vix: float | None = 20.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        strategy_type=strategy_type,
        strategy_params=strategy_params or {},
        current_vix=current_vix,
    )


def _make_df(
    rows: list[dict],
    weekday: int = 0,  # 0=Monday
) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with or_high/or_low already set.

    `rows` is a list of dicts with keys: time_str, Close, or_high, or_low.
    The index is a DatetimeIndex whose date has the given weekday.
    """
    # Anchor on a known Monday (2026-04-27 = Monday)
    monday = date(2026, 4, 27)
    delta = (weekday - monday.weekday()) % 7
    from datetime import timedelta
    day = monday + timedelta(days=delta)

    timestamps = [
        pd.Timestamp(f"{day} {r['time_str']}")
        for r in rows
    ]
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps))
    for col in ("or_high", "or_low", "Close", "High", "Low", "Open"):
        if col not in df.columns:
            df[col] = np.nan
    return df


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def orb() -> OrbStrategy:
    return OrbStrategy()


@pytest.fixture
def bull_df_monday() -> pd.DataFrame:
    """5-min bars on a Monday with a bull breakout at 9:35."""
    rows = [
        {"time_str": "09:30", "Close": 530.0, "or_high": 530.5, "or_low": 529.5},
        {"time_str": "09:35", "Close": 531.0, "or_high": 530.5, "or_low": 529.5},
        {"time_str": "09:40", "Close": 531.5, "or_high": 530.5, "or_low": 529.5},
    ]
    return _make_df(rows, weekday=0)


# ── check_entry: happy path ───────────────────────────────────────────────────

def test_check_entry_monday_bull_breakout_at_935(orb, bull_df_monday):
    """Bull breakout on Monday at 9:35 with VIX=20 — must signal entry."""
    req = _make_req(current_vix=20.0)
    assert orb.check_entry(bull_df_monday, 1, req) is True


def test_check_entry_also_true_at_940(orb, bull_df_monday):
    """Signal still valid at 9:40 bar (still within trading window)."""
    req = _make_req(current_vix=20.0)
    assert orb.check_entry(bull_df_monday, 2, req) is True


# ── check_entry: day-of-week filter ──────────────────────────────────────────

@pytest.mark.parametrize("weekday", [1, 3])  # Tuesday=1, Thursday=3
def test_check_entry_blocks_tue_thu(orb, weekday):
    """No trade on Tuesday or Thursday (paper filter)."""
    rows = [
        {"time_str": "09:30", "Close": 530.0, "or_high": 530.5, "or_low": 529.5},
        {"time_str": "09:35", "Close": 531.0, "or_high": 530.5, "or_low": 529.5},
    ]
    df = _make_df(rows, weekday=weekday)
    req = _make_req(current_vix=20.0)
    assert orb.check_entry(df, 1, req) is False


@pytest.mark.parametrize("weekday", [0, 2, 4])  # Mon, Wed, Fri
def test_check_entry_allows_mwf(orb, weekday):
    """Monday, Wednesday, Friday should all pass the day filter."""
    rows = [
        {"time_str": "09:30", "Close": 530.0, "or_high": 530.5, "or_low": 529.5},
        {"time_str": "09:35", "Close": 531.0, "or_high": 530.5, "or_low": 529.5},
    ]
    df = _make_df(rows, weekday=weekday)
    req = _make_req(current_vix=20.0)
    # Patch out news dates so this test isolates the day-of-week filter only.
    with patch("strategies.orb._NEWS_DATES", set()):
        assert orb.check_entry(df, 1, req) is True


# ── check_entry: time window ──────────────────────────────────────────────────

def test_check_entry_blocks_before_935(orb):
    """The 9:30 bar (i=0) is inside the OR window — must not signal."""
    rows = [
        {"time_str": "09:30", "Close": 531.0, "or_high": 530.5, "or_low": 529.5},
    ]
    df = _make_df(rows, weekday=0)
    req = _make_req(current_vix=20.0)
    assert orb.check_entry(df, 0, req) is False


def test_check_entry_blocks_at_or_after_1530(orb):
    """No new entries at or after 15:30 ET (time-based cutoff)."""
    rows = [
        {"time_str": "09:30", "Close": 530.0, "or_high": 530.5, "or_low": 529.5},
        {"time_str": "15:30", "Close": 531.0, "or_high": 530.5, "or_low": 529.5},
    ]
    df = _make_df(rows, weekday=0)
    req = _make_req(current_vix=20.0)
    assert orb.check_entry(df, 1, req) is False


# ── check_entry: VIX filter ───────────────────────────────────────────────────

def test_check_entry_blocks_vix_too_high(orb, bull_df_monday):
    """VIX=30 exceeds 25 threshold — block."""
    req = _make_req(current_vix=30.0)
    assert orb.check_entry(bull_df_monday, 1, req) is False


def test_check_entry_blocks_vix_too_low(orb, bull_df_monday):
    """VIX=10 is below 15 threshold — block."""
    req = _make_req(current_vix=10.0)
    assert orb.check_entry(bull_df_monday, 1, req) is False


def test_check_entry_blocks_vix_unavailable(orb, bull_df_monday):
    """If VIX is unknown (None), fail-closed and block."""
    req = _make_req(current_vix=None)
    with patch("strategies.orb._fetch_vix_cached", return_value=None):
        assert orb.check_entry(bull_df_monday, 1, req) is False


def test_check_entry_vix_boundary_15(orb, bull_df_monday):
    """VIX=15 is at the lower bound — should pass."""
    req = _make_req(current_vix=15.0)
    assert orb.check_entry(bull_df_monday, 1, req) is True


def test_check_entry_vix_boundary_25(orb, bull_df_monday):
    """VIX=25 is at the upper bound — should pass."""
    req = _make_req(current_vix=25.0)
    assert orb.check_entry(bull_df_monday, 1, req) is True


# ── check_entry: news day filter ─────────────────────────────────────────────

def test_check_entry_blocks_news_day(orb, bull_df_monday):
    """Inject a news date matching the bar's date — must block."""
    ts_date = bull_df_monday.index[1].strftime("%Y-%m-%d")
    with patch("strategies.orb._NEWS_DATES", {ts_date}):
        req = _make_req(current_vix=20.0)
        assert orb.check_entry(bull_df_monday, 1, req) is False


def test_check_entry_allows_skip_news_false(orb, bull_df_monday):
    """When skip_news_days=False, news dates are ignored."""
    ts_date = bull_df_monday.index[1].strftime("%Y-%m-%d")
    with patch("strategies.orb._NEWS_DATES", {ts_date}):
        req = _make_req(current_vix=20.0, strategy_params={"skip_news_days": False})
        assert orb.check_entry(bull_df_monday, 1, req) is True


# ── check_entry: OR NaN / range filter ───────────────────────────────────────

def test_check_entry_blocks_or_nan(orb):
    """No OR levels computed — must block."""
    rows = [
        {"time_str": "09:30", "Close": 530.0, "or_high": np.nan, "or_low": np.nan},
        {"time_str": "09:35", "Close": 531.0, "or_high": np.nan, "or_low": np.nan},
    ]
    df = _make_df(rows, weekday=0)
    req = _make_req(current_vix=20.0)
    assert orb.check_entry(df, 1, req) is False


def test_check_entry_blocks_tiny_range(orb):
    """OR range below min_range_pct filter should block."""
    rows = [
        {"time_str": "09:30", "Close": 530.0, "or_high": 530.001, "or_low": 530.000},
        # Close > or_high → bull break, but range is tiny
        {"time_str": "09:35", "Close": 530.1, "or_high": 530.001, "or_low": 530.000},
    ]
    df = _make_df(rows, weekday=0)
    req = _make_req(current_vix=20.0)
    assert orb.check_entry(df, 1, req) is False


# ── check_entry: direction filter ─────────────────────────────────────────────

def test_check_entry_bear_preset_blocks_bull_signal(orb, bull_df_monday):
    """Bull breakout with a bear_put preset — should not signal."""
    req = _make_req(strategy_type="bear_put", current_vix=20.0)
    assert orb.check_entry(bull_df_monday, 1, req) is False


def test_check_entry_bear_signal_with_bear_preset(orb):
    """Bear breakout with bear_put preset — should signal."""
    rows = [
        {"time_str": "09:30", "Close": 530.0, "or_high": 530.5, "or_low": 529.5},
        {"time_str": "09:35", "Close": 529.0, "or_high": 530.5, "or_low": 529.5},
    ]
    df = _make_df(rows, weekday=0)
    req = _make_req(strategy_type="bear_put", current_vix=20.0)
    assert orb.check_entry(df, 1, req) is True


# ── check_exit ────────────────────────────────────────────────────────────────

def test_check_exit_fires_at_1530(orb):
    """Time-based exit must fire at 15:30 ET."""
    rows = [{"time_str": "15:30", "Close": 530.0, "or_high": 530.5, "or_low": 529.5}]
    df = _make_df(rows, weekday=0)
    should_exit, reason = orb.check_exit(df, 0, {}, _make_req())
    assert should_exit is True
    assert "15:30" in reason


def test_check_exit_fires_after_1530(orb):
    """Exit should also trigger past 15:30."""
    rows = [{"time_str": "15:35", "Close": 530.0, "or_high": 530.5, "or_low": 529.5}]
    df = _make_df(rows, weekday=0)
    should_exit, _ = orb.check_exit(df, 0, {}, _make_req())
    assert should_exit is True


def test_check_exit_no_exit_mid_day(orb):
    """No exit in the middle of the trading session."""
    rows = [{"time_str": "11:00", "Close": 530.0, "or_high": 530.5, "or_low": 529.5}]
    df = _make_df(rows, weekday=0)
    should_exit, reason = orb.check_exit(df, 0, {}, _make_req())
    assert should_exit is False
    assert reason == ""


# ── pick_bull_call_strikes (chain.py) ─────────────────────────────────────────

def _declining_prices(strikes: list[int], base_bid: float = 5.0, step: float = 0.3) -> dict:
    """Simulate realistic call bid/ask that declines as strike rises (ITM → OTM)."""
    prices = {}
    for i, k in enumerate(strikes):
        bid = max(0.05, base_bid - i * step)
        ask = bid + 0.10
        prices[k] = (round(bid, 2), round(ask, 2))
    return prices


def test_pick_bull_call_strikes_offset_150():
    """With offset=1.50 and underlying=530, K_long should be 531 (nearest to 531.5)."""
    strikes = list(range(525, 545))
    prices = _declining_prices(strikes)
    result = pick_bull_call_strikes(
        strike_grid=strikes,
        underlying=530.0,
        call_prices=prices,
        target_debit=250.0,
        otm_offset=1.5,
    )
    assert result is not None
    # nearest whole-dollar strike to 530+1.5=531.5 → either 531 or 532
    assert result["K_long"] in (531, 532)
    assert result["K_short"] > result["K_long"]


def test_pick_bull_call_strikes_zero_offset_is_atm():
    """With offset=0, K_long should land on the ATM strike (530)."""
    strikes = list(range(525, 545))
    prices = _declining_prices(strikes)
    result = pick_bull_call_strikes(
        strike_grid=strikes,
        underlying=530.0,
        call_prices=prices,
        target_debit=250.0,
        otm_offset=0.0,
    )
    assert result is not None
    assert result["K_long"] == 530


def test_pick_bull_call_strikes_no_strikes_returns_none():
    """Empty strike grid should return None gracefully."""
    result = pick_bull_call_strikes(
        strike_grid=[],
        underlying=530.0,
        call_prices={},
        target_debit=250.0,
        otm_offset=1.5,
    )
    assert result is None


def test_pick_bull_call_strikes_missing_quote_returns_none():
    """If the target long strike has no valid quote, return None."""
    strikes = [531, 532, 535]
    prices = {532: (1.0, 1.5), 535: (0.5, 0.8)}  # 531 missing
    result = pick_bull_call_strikes(
        strike_grid=strikes,
        underlying=530.0,
        call_prices=prices,
        target_debit=250.0,
        otm_offset=1.0,
    )
    # 530+1=531 is nearest; no valid quote for 531 → None
    assert result is None


# ── compute_indicators ────────────────────────────────────────────────────────

def test_compute_indicators_sets_or_levels(orb):
    """OR high/low should be set from the 9:30 bar on trading days."""
    rows = [
        {"time_str": "09:30", "Open": 529.0, "High": 530.8, "Low": 529.0, "Close": 530.0},
        {"time_str": "09:35", "Open": 530.0, "High": 531.0, "Low": 530.0, "Close": 530.5},
        {"time_str": "09:40", "Open": 530.5, "High": 532.0, "Low": 530.0, "Close": 531.0},
    ]
    from datetime import timedelta
    day = date(2026, 4, 27)  # Monday
    timestamps = [pd.Timestamp(f"{day} {r['time_str']}") for r in rows]
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps))
    req = _make_req()
    out = orb.compute_indicators(df, req)
    # OR window is 9:30 only (< 9:35); high=530.8, low=529.0
    assert out.loc[timestamps[0], "or_high"] == pytest.approx(530.8)
    assert out.loc[timestamps[0], "or_low"] == pytest.approx(529.0)
    # OR levels propagate to all bars of the same day
    assert out.loc[timestamps[1], "or_high"] == pytest.approx(530.8)
    assert out.loc[timestamps[2], "or_high"] == pytest.approx(530.8)


def test_compute_indicators_non_datetime_index_returns_unchanged(orb):
    """compute_indicators should return the DataFrame unmodified for non-DT index."""
    df = pd.DataFrame({"Close": [530.0]}, index=[0])
    req = _make_req()
    out = orb.compute_indicators(df, req)
    assert list(out.index) == [0]
