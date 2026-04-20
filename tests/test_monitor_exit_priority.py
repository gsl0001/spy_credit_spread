"""Tests for the priority-ordered exit logic in core.monitor.evaluate_exit.

Priority (per use_request.md §1):
    0. force_exit
    1. strategy_exit (TOP)
    2. expiration_close
    3. dte_exit
    4. take_profit / stop_loss / trailing_stop
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.journal import Position
from core.monitor import ExitDecision, evaluate_exit


def _make_position(
    *,
    expiry: str = "2026-12-31",
    contracts: int = 1,
    entry_cost: float = 100.0,  # total $; unit = 1.00
) -> Position:
    return Position(
        id="p-1",
        symbol="SPY",
        topology="vertical_spread",
        direction="long",
        contracts=contracts,
        entry_cost=entry_cost,
        entry_time="2026-01-01T15:00:00Z",
        expiry=expiry,
        legs=({"strike": 500, "side": "long", "right": "C"},),
    )


def test_force_exit_beats_everything() -> None:
    pos = _make_position()
    d = evaluate_exit(
        pos, current_mid=2.0,
        force_exit=True,
        strategy_exit=(True, "ema_cross"),
        is_expiration_close=True,
        take_profit_pct=10.0,
    )
    assert d.should_exit is True
    assert d.reason == "manual_flatten"


def test_strategy_exit_beats_expiration_and_tp_sl() -> None:
    pos = _make_position()
    d = evaluate_exit(
        pos, current_mid=5.0,
        strategy_exit=(True, "ema_cross"),
        is_expiration_close=True,
        take_profit_pct=10.0,  # would also fire (5.0 vs 1.0 unit)
        stop_loss_pct=10.0,
    )
    assert d.should_exit is True
    assert d.reason == "strategy:ema_cross"


def test_strategy_exit_false_falls_through_to_next_priority() -> None:
    pos = _make_position(expiry=date.today().isoformat())
    d = evaluate_exit(
        pos, current_mid=1.0,
        strategy_exit=(False, ""),
        is_expiration_close=True,
    )
    assert d.should_exit is True
    assert d.reason == "expiration_close"


def test_expiration_close_only_fires_when_dte_le_zero() -> None:
    pos = _make_position(expiry=(date.today() + timedelta(days=5)).isoformat())
    d = evaluate_exit(pos, current_mid=1.0, is_expiration_close=True)
    assert d.should_exit is False  # not expiration day yet
    # Now flip to today
    pos_today = _make_position(expiry=date.today().isoformat())
    d2 = evaluate_exit(pos_today, current_mid=1.0, is_expiration_close=True)
    assert d2.should_exit is True
    assert d2.reason == "expiration_close"


def test_take_profit_fires_when_no_higher_priority_signal() -> None:
    pos = _make_position()
    d = evaluate_exit(
        pos, current_mid=1.6,  # +60% on $1.00 entry
        take_profit_pct=50.0,
    )
    assert d.should_exit is True
    assert d.reason == "take_profit"


def test_stop_loss_fires_when_no_higher_priority_signal() -> None:
    pos = _make_position()
    d = evaluate_exit(
        pos, current_mid=0.4,  # -60% on $1.00 entry
        stop_loss_pct=50.0,
    )
    assert d.should_exit is True
    assert d.reason == "stop_loss"


def test_no_exit_when_all_quiet() -> None:
    pos = _make_position()
    d = evaluate_exit(
        pos, current_mid=1.05,
        strategy_exit=(False, ""),
        is_expiration_close=False,
        take_profit_pct=50.0,
        stop_loss_pct=50.0,
    )
    assert d.should_exit is False
    assert d.reason == "ok"
