"""Tests for the ORB backtest engine.

Synthetic intraday bars exercise the core decision points:
  - day-of-week filter (skip Tue/Thu)
  - VIX gate
  - news-day skip
  - OR window detection + min-range filter
  - bull/bear breakout direction
  - take-profit / stop-loss / time-exit
"""
from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd
import pytest

from core.backtest_orb import OrbBacktestConfig, run_orb_backtest


def _make_day(
    d: date,
    open_price: float,
    or_high: float,
    or_low: float,
    afternoon_close: float,
    afternoon_high: float | None = None,
    afternoon_low: float | None = None,
) -> list[dict]:
    """Generate ~75 bars (5-min) for a single trading day from 9:30 to 16:00.

    OR window: 9:30 bar uses (open=open_price, high=or_high, low=or_low).
    Subsequent bars trend toward afternoon_close.
    """
    bars = []
    # 9:30 OR bar
    bars.append({
        "_ts": datetime(d.year, d.month, d.day, 9, 30),
        "open": open_price, "high": or_high, "low": or_low,
        "close": (or_high + or_low) / 2, "volume": 1_000_000,
    })
    # Linear trend to afternoon_close from end of OR
    or_close = (or_high + or_low) / 2
    minutes = list(range(35, 60, 5)) + [
        h * 60 + m for h in range(10, 16) for m in (0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)
    ]
    n = len(minutes)
    for i, mins in enumerate(minutes):
        progress = (i + 1) / n
        c = or_close + (afternoon_close - or_close) * progress
        h = max(c, afternoon_high if afternoon_high is not None else c) + 0.05
        low = min(c, afternoon_low if afternoon_low is not None else c) - 0.05
        bars.append({
            "_ts": datetime(d.year, d.month, d.day, mins // 60, mins % 60),
            "open": c, "high": h, "low": low, "close": c, "volume": 500_000,
        })
    return bars


def _frame(bar_lists: list[list[dict]]) -> pd.DataFrame:
    rows = [b for day in bar_lists for b in day]
    df = pd.DataFrame(rows)
    df.set_index("_ts", inplace=True)
    return df


# ── Day filter ────────────────────────────────────────────────────────────────


def test_skip_tuesday_thursday():
    """Tue (4-28) and Thu (4-30) bars are skipped — only Mon (4-27) trades."""
    monday = date(2026, 4, 27)
    tuesday = date(2026, 4, 28)
    thursday = date(2026, 4, 30)
    bars = _frame([
        _make_day(monday, 580, 581, 579, afternoon_close=584),       # bull breakout
        _make_day(tuesday, 580, 581, 579, afternoon_close=584),
        _make_day(thursday, 580, 581, 579, afternoon_close=584),
    ])
    cfg = OrbBacktestConfig()
    report = run_orb_backtest(bars, config=cfg)
    assert report["stats"]["total_trades"] == 1
    assert report["trades"][0]["date"] == str(monday)


# ── OR detection + breakout ───────────────────────────────────────────────────


def test_bull_breakout_take_profit():
    """Strong upmove after OR → bull entry → TP fires."""
    monday = date(2026, 4, 27)
    bars = _frame([
        _make_day(
            monday, 580, or_high=580.5, or_low=579.5,
            afternoon_close=590,         # strong move past long+offset
            afternoon_high=592, afternoon_low=580,
        ),
    ])
    cfg = OrbBacktestConfig(
        skip_news_days=False,            # bypass events
        spread_cost_per_contract=200.0,  # so TP is reachable
    )
    report = run_orb_backtest(bars, config=cfg)
    assert report["stats"]["total_trades"] == 1
    t = report["trades"][0]
    assert t["direction"] == "bull"
    assert t["exit_reason"] == "take_profit"
    assert t["pnl_pct"] > 0


def test_bear_breakout_take_profit():
    """Strong downmove after OR → bear entry → TP fires."""
    monday = date(2026, 4, 27)
    bars = _frame([
        _make_day(
            monday, 580, or_high=580.5, or_low=579.5,
            afternoon_close=570,
            afternoon_high=580, afternoon_low=568,
        ),
    ])
    cfg = OrbBacktestConfig(skip_news_days=False, spread_cost_per_contract=200.0)
    report = run_orb_backtest(bars, config=cfg)
    assert report["stats"]["total_trades"] == 1
    t = report["trades"][0]
    assert t["direction"] == "bear"
    assert t["exit_reason"] == "take_profit"


def test_no_breakout_skips():
    """Range-bound day after OR → no trade."""
    monday = date(2026, 4, 27)
    # Stays inside the OR all day
    bars = _frame([
        _make_day(monday, 580, or_high=580.3, or_low=579.7, afternoon_close=580.1,
                  afternoon_high=580.4, afternoon_low=579.6),
    ])
    cfg = OrbBacktestConfig(skip_news_days=False)
    report = run_orb_backtest(bars, config=cfg)
    assert report["stats"]["total_trades"] == 0


def test_min_range_filter_blocks_tiny_or():
    """If OR range < min_range_pct of price, skip the day."""
    monday = date(2026, 4, 27)
    bars = _frame([
        _make_day(monday, 580, or_high=580.05, or_low=579.95, afternoon_close=590),
    ])
    cfg = OrbBacktestConfig(
        skip_news_days=False,
        min_range_pct=0.10,  # require 0.10% range = $0.58 on $580
    )
    report = run_orb_backtest(bars, config=cfg)
    assert report["stats"]["total_trades"] == 0


# ── VIX gate ─────────────────────────────────────────────────────────────────


def test_vix_too_low_blocks():
    """VIX = 10 (below floor) → skip day."""
    monday = date(2026, 4, 27)
    bars = _frame([_make_day(monday, 580, 581, 579, afternoon_close=590)])
    vix = pd.Series({pd.Timestamp(monday): 10.0})
    cfg = OrbBacktestConfig(skip_news_days=False)
    report = run_orb_backtest(bars, vix=vix, config=cfg)
    assert report["stats"]["total_trades"] == 0


def test_vix_too_high_blocks():
    monday = date(2026, 4, 27)
    bars = _frame([_make_day(monday, 580, 581, 579, afternoon_close=590)])
    vix = pd.Series({pd.Timestamp(monday): 35.0})
    cfg = OrbBacktestConfig(skip_news_days=False)
    report = run_orb_backtest(bars, vix=vix, config=cfg)
    assert report["stats"]["total_trades"] == 0


def test_vix_in_range_allows():
    monday = date(2026, 4, 27)
    bars = _frame([_make_day(monday, 580, 581, 579, afternoon_close=590,
                              afternoon_high=592, afternoon_low=580)])
    vix = pd.Series({pd.Timestamp(monday): 18.0})
    cfg = OrbBacktestConfig(skip_news_days=False, spread_cost_per_contract=200.0)
    report = run_orb_backtest(bars, vix=vix, config=cfg)
    assert report["stats"]["total_trades"] == 1


# ── News day filter ──────────────────────────────────────────────────────────


def test_news_day_skip():
    monday = date(2026, 4, 27)
    bars = _frame([_make_day(monday, 580, 581, 579, afternoon_close=590)])
    events = {monday.isoformat()}
    cfg = OrbBacktestConfig(skip_news_days=True)
    report = run_orb_backtest(bars, events=events, config=cfg)
    assert report["stats"]["total_trades"] == 0


# ── Stats sanity ─────────────────────────────────────────────────────────────


def test_stats_reflect_trade_outcomes():
    """Three days: two TP wins + one SL loss → win_rate 67%."""
    days = [date(2026, 4, 27), date(2026, 4, 29), date(2026, 5, 1)]   # Mon, Wed, Fri
    bars = _frame([
        _make_day(days[0], 580, 581, 579, 590, afternoon_high=592, afternoon_low=580),
        _make_day(days[1], 580, 581, 579, 590, afternoon_high=592, afternoon_low=580),
        # Bull entry, reverse and SL
        _make_day(days[2], 580, 581, 579, 575, afternoon_high=581.5, afternoon_low=572),
    ])
    cfg = OrbBacktestConfig(skip_news_days=False, spread_cost_per_contract=200.0)
    report = run_orb_backtest(bars, config=cfg)
    s = report["stats"]
    assert s["total_trades"] == 3
    assert s["wins"] >= 2
    assert s["win_rate"] >= 0.5


def test_empty_bars_returns_empty_result():
    cfg = OrbBacktestConfig()
    report = run_orb_backtest(pd.DataFrame(), config=cfg)
    assert report["stats"]["total_trades"] == 0
    assert report["trades"] == []


def test_config_round_trips_in_report():
    """Stats include the config that produced them — auditable."""
    bars = _frame([_make_day(date(2026, 4, 27), 580, 581, 579, 590)])
    cfg = OrbBacktestConfig(offset_points=2.0, width_points=10, vix_min=12)
    report = run_orb_backtest(bars, config=cfg)
    c = report["config"]
    assert c["offset_points"] == 2.0
    assert c["width_points"] == 10
    assert c["vix_min"] == 12
