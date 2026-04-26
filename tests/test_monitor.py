"""Live monitor tests.

Layer 1: pure exit-decision math.
Layer 2: `tick()` with a FakeTrader + real in-memory journal — verifies
         a stop-loss hit produces an exit order and flips the position
         to ``closing``.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from core.journal import Journal, Position
from core.monitor import (
    ExitDecision,
    evaluate_exit,
    exit_config_for,
    tick,
)


def _pos(**over) -> Position:
    base = dict(
        id="p-1",
        symbol="SPY",
        topology="vertical_spread",
        direction="bull_call",
        contracts=2,
        entry_cost=500.0,       # 2 contracts * $2.50 debit
        entry_time=datetime.now(timezone.utc).isoformat(),
        expiry="2026-04-28",    # 14 days from 2026-04-14
        legs=(
            {"type": "call", "strike": 500, "side": "long",  "expiry": "20260428"},
            {"type": "call", "strike": 505, "side": "short", "expiry": "20260428"},
        ),
        state="open",
        broker="ibkr",
        meta={"combo_type": "debit"},
    )
    base.update(over)
    return Position(**base)


# ── evaluate_exit ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_holds_when_mid_near_entry():
    d = evaluate_exit(_pos(), current_mid=2.55,
                      stop_loss_pct=50, take_profit_pct=50,
                      today=date(2026, 4, 14))
    assert d.should_exit is False
    assert d.reason == "ok"


@pytest.mark.unit
def test_take_profit_fires_at_target():
    # Entry debit 2.50, target +50% → mid 3.75 triggers
    d = evaluate_exit(_pos(), current_mid=3.75,
                      stop_loss_pct=50, take_profit_pct=50,
                      today=date(2026, 4, 14))
    assert d.should_exit is True
    assert d.reason == "take_profit"


@pytest.mark.unit
def test_stop_loss_fires():
    # Mid 1.25 = -50% vs 2.50 entry → stop
    d = evaluate_exit(_pos(), current_mid=1.25,
                      stop_loss_pct=50, take_profit_pct=50,
                      today=date(2026, 4, 14))
    assert d.should_exit is True
    assert d.reason == "stop_loss"


@pytest.mark.unit
def test_dte_exit_triggers_on_expiry_day():
    # today == expiry → DTE 0 → forced exit
    d = evaluate_exit(_pos(), current_mid=2.40,
                      stop_loss_pct=50, take_profit_pct=50,
                      dte_exit_at=0,
                      today=date(2026, 4, 28))
    assert d.should_exit is True
    assert d.reason == "dte_exit"


@pytest.mark.unit
def test_trailing_stop_requires_prior_gain():
    # HWM at entry (no gain yet) → trailing stop disabled
    d = evaluate_exit(_pos(), current_mid=2.30,
                      trailing_stop_pct=20, high_water_mark=2.50,
                      today=date(2026, 4, 14))
    # 2.30 is only -8% of entry, not a stop loss, and HWM == entry so no trail
    assert d.should_exit is False


@pytest.mark.unit
def test_trailing_stop_fires_after_pullback():
    # HWM 3.50 (gain 1.00), current 3.30 → give-back 20% of 1.00 = 20%
    d = evaluate_exit(_pos(), current_mid=3.30,
                      stop_loss_pct=50, take_profit_pct=200,
                      trailing_stop_pct=20, high_water_mark=3.50,
                      today=date(2026, 4, 14))
    assert d.should_exit is True
    assert d.reason == "trailing_stop"


@pytest.mark.unit
def test_force_exit_overrides_everything():
    d = evaluate_exit(_pos(), current_mid=2.50, force_exit=True,
                      today=date(2026, 4, 14))
    assert d.should_exit is True
    assert d.reason == "manual_flatten"


@pytest.mark.unit
def test_zero_entry_returns_no_exit_with_reason():
    p = _pos(entry_cost=0.0)
    d = evaluate_exit(p, current_mid=1.0,
                      today=date(2026, 4, 14))
    assert d.should_exit is False
    assert d.reason == "no_entry_cost"


# ── exit_config_for ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_exit_config_merges_defaults_and_meta():
    p = _pos(meta={"combo_type": "debit",
                   "stop_loss_pct": 30, "take_profit_pct": 60})
    cfg = exit_config_for(p, {"stop_loss_pct": 50, "take_profit_pct": 50,
                              "dte_exit_at": 0})
    assert cfg["stop_loss_pct"] == 30    # overridden by meta
    assert cfg["take_profit_pct"] == 60  # overridden by meta
    assert cfg["dte_exit_at"] == 0       # default survives


# ── tick() with fake trader + real journal ───────────────────────────────

class FakeTrader:
    """Minimal stand-in for IBKRTrader that records what was called."""
    def __init__(self, mid: float):
        self.mid = mid
        self.orders: list[dict] = []
        self.next_order_id = 1000

    async def get_combo_midpoint(self, symbol, legs):
        return self.mid

    async def place_combo_order(self, symbol, legs, qty, *, side="BUY",
                                lmtPrice=None):
        self.next_order_id += 1
        rec = {"symbol": symbol, "legs": list(legs), "qty": qty,
               "side": side, "lmtPrice": lmtPrice,
               "orderId": self.next_order_id}
        self.orders.append(rec)
        return {"orderId": rec["orderId"], "status": "Submitted", "success": True}


@pytest.mark.asyncio
async def test_tick_holds_when_no_exit_triggers(tmp_path: Path):
    db = tmp_path / "mon.db"
    j = Journal(str(db))
    j.open_position(replace(_pos(), state="open"))
    # Mid near entry → no exit
    trader = FakeTrader(mid=2.55)

    async def factory():
        return trader

    results = await tick(factory, journal=j,
                        defaults={"stop_loss_pct": 50, "take_profit_pct": 50,
                                  "trailing_stop_pct": 0, "dte_exit_at": 0,
                                  "haircut_pct": 0.05},
                        today=date(2026, 4, 14))
    assert len(results) == 1
    assert results[0]["status"] == "holding"
    assert trader.orders == []
    pos = j.get_position("p-1")
    assert pos is not None and pos.state == "open"
    j.close()


@pytest.mark.asyncio
async def test_tick_fires_stop_loss_and_marks_closing(tmp_path: Path):
    db = tmp_path / "mon.db"
    j = Journal(str(db))
    j.open_position(replace(_pos(), state="open"))
    trader = FakeTrader(mid=1.20)  # deep loss → stop_loss

    async def factory():
        return trader

    results = await tick(factory, journal=j,
                        defaults={"stop_loss_pct": 50, "take_profit_pct": 50,
                                  "trailing_stop_pct": 0, "dte_exit_at": 0,
                                  "haircut_pct": 0.05},
                        today=date(2026, 4, 14))

    assert len(results) == 1
    assert results[0]["exit_reason"] == "stop_loss"
    assert results[0]["submitted"] is True

    # Verify broker was asked to SELL at a limit slightly below mid
    assert len(trader.orders) == 1
    o = trader.orders[0]
    assert o["side"] == "SELL"
    assert o["qty"] == 2
    assert 0.01 <= o["lmtPrice"] <= 1.20

    # Journal should have flipped state to closing and recorded an exit order
    pos = j.get_position("p-1")
    assert pos is not None and pos.state == "closing"
    orders = j.list_orders_for_position("p-1")
    assert any(o.kind == "exit" for o in orders)
    j.close()


@pytest.mark.asyncio
async def test_tick_skips_pending_positions(tmp_path: Path):
    db = tmp_path / "mon.db"
    j = Journal(str(db))
    # Pending position should not be touched
    j.open_position(replace(_pos(id="p-pending", state="pending")))
    trader = FakeTrader(mid=1.20)

    async def factory():
        return trader

    results = await tick(factory, journal=j,
                        defaults={"stop_loss_pct": 50, "take_profit_pct": 50,
                                  "trailing_stop_pct": 0, "dte_exit_at": 0,
                                  "haircut_pct": 0.05},
                        today=date(2026, 4, 14))
    assert results == []
    assert trader.orders == []
    j.close()


@pytest.mark.asyncio
async def test_tick_survives_trader_factory_failure(tmp_path: Path):
    db = tmp_path / "mon.db"
    j = Journal(str(db))
    j.open_position(replace(_pos(), state="open"))

    async def bad_factory():
        raise RuntimeError("no socket")

    results = await tick(bad_factory, journal=j,
                        defaults={"stop_loss_pct": 50, "take_profit_pct": 50},
                        today=date(2026, 4, 14))
    assert results == []
    # An event should be logged so the operator sees this
    events = j.recent_events(10)
    assert any(e["kind"] == "monitor_no_trader" for e in events)
    j.close()


@pytest.mark.asyncio
async def test_tick_rolls_high_water_mark(tmp_path: Path):
    db = tmp_path / "mon.db"
    j = Journal(str(db))
    j.open_position(replace(_pos(), state="open"))
    trader = FakeTrader(mid=3.00)  # +20% gain, not at TP yet

    async def factory():
        return trader

    await tick(factory, journal=j,
               defaults={"stop_loss_pct": 50, "take_profit_pct": 50,
                         "trailing_stop_pct": 0, "dte_exit_at": 0,
                         "haircut_pct": 0.05},
               today=date(2026, 4, 14))
    pos = j.get_position("p-1")
    assert pos is not None
    assert pos.high_water_mark == 3.00
    j.close()


# ── Strategy-driven exits: regression test for the missing bars_fetcher bug ──
#
# Before the fix, _run_monitor_tick built `defaults` without a bars_fetcher,
# so _resolve_strategy_exit early-returned None on every position — meaning
# the strategy's check_exit() never ran. Stop/profit/trailing/expiry gates
# fired, but a strategy-declared "exit on signal reversal" never did.
#
# These tests prove the wire is live: when defaults includes both a
# bars_fetcher and a strategy_registry, a strategy exit triggers a real
# close — even when none of the dollar-based gates would.


class _AlwaysExitStrategy:
    """Test strategy that always says exit. Mirrors BaseStrategy interface."""

    BAR_SIZE = "1 day"
    HISTORY_PERIOD = "1mo"

    @property
    def name(self):
        return "Always-Exit Test"

    @classmethod
    def get_schema(cls):
        return {}

    def compute_indicators(self, df, req):
        return df

    def check_entry(self, df, i, req):
        return False

    def check_exit(self, df, i, trade_state, req):
        return True, "test_strategy_exit"


class _NeverExitStrategy(_AlwaysExitStrategy):
    """Strategy that never wants to exit — used to confirm the gate is real."""

    def check_exit(self, df, i, trade_state, req):
        return False, ""


@pytest.mark.asyncio
async def test_strategy_exit_fires_when_bars_fetcher_is_wired(tmp_path: Path):
    """Regression for the silent-monitor bug: with bars_fetcher provided,
    the strategy's check_exit() runs and can trigger a close even when
    no dollar gate would."""
    import pandas as pd

    db = tmp_path / "mon.db"
    j = Journal(str(db))
    # Position carries a strategy_name in meta — the fetcher + registry use it.
    pos_with_strat = replace(
        _pos(meta={"combo_type": "debit", "strategy_name": "always_exit"}),
        state="open",
    )
    j.open_position(pos_with_strat)

    # Mid near entry → no stop/profit/trailing trigger. Only the strategy
    # exit can fire.
    trader = FakeTrader(mid=2.55)

    async def factory():
        return trader

    bars_calls: list = []

    def fake_bars_fetcher(symbol, strategy_name=None):
        bars_calls.append((symbol, strategy_name))
        # Minimal frame — content doesn't matter, _AlwaysExitStrategy ignores it.
        return pd.DataFrame({"close": [100.0, 101.0, 102.0]})

    results = await tick(
        factory,
        journal=j,
        defaults={
            "stop_loss_pct": 50, "take_profit_pct": 50,
            "trailing_stop_pct": 0, "dte_exit_at": 0,
            "haircut_pct": 0.05,
            "bars_fetcher": fake_bars_fetcher,
            "strategy_registry": {"always_exit": _AlwaysExitStrategy},
        },
        today=date(2026, 4, 14),
    )

    # 1. The fetcher was called with both the symbol AND the strategy name —
    #    proving _resolve_strategy_exit takes the strategy-aware path.
    assert bars_calls == [("SPY", "always_exit")]

    # 2. The strategy exit actually fired.
    assert len(results) == 1
    assert results[0]["exit_reason"] == "strategy:test_strategy_exit"
    assert results[0]["submitted"] is True

    # 3. A real exit order was sent to the broker.
    assert len(trader.orders) == 1
    assert trader.orders[0]["side"] == "SELL"

    # 4. Position state flipped to closing.
    assert j.get_position("p-1").state == "closing"
    j.close()


@pytest.mark.asyncio
async def test_no_strategy_exit_when_strategy_says_hold(tmp_path: Path):
    """Confirms the strategy gate is real: when check_exit returns False,
    the position holds even with bars_fetcher wired."""
    import pandas as pd

    db = tmp_path / "mon.db"
    j = Journal(str(db))
    j.open_position(replace(
        _pos(meta={"combo_type": "debit", "strategy_name": "never_exit"}),
        state="open",
    ))
    trader = FakeTrader(mid=2.55)  # near entry — no dollar gate

    async def factory():
        return trader

    def fake_bars_fetcher(symbol, strategy_name=None):
        return pd.DataFrame({"close": [100.0, 101.0]})

    results = await tick(
        factory,
        journal=j,
        defaults={
            "stop_loss_pct": 50, "take_profit_pct": 50,
            "trailing_stop_pct": 0, "dte_exit_at": 0,
            "haircut_pct": 0.05,
            "bars_fetcher": fake_bars_fetcher,
            "strategy_registry": {"never_exit": _NeverExitStrategy},
        },
        today=date(2026, 4, 14),
    )

    assert results[0]["status"] == "holding"
    assert trader.orders == []
    assert j.get_position("p-1").state == "open"
    j.close()


@pytest.mark.asyncio
async def test_legacy_single_arg_bars_fetcher_still_works(tmp_path: Path):
    """Backward-compat: callers that pass a 1-arg fetcher (older tests
    or external consumers) still work — _resolve_strategy_exit catches
    the TypeError and falls back to fetcher(symbol)."""
    import pandas as pd

    db = tmp_path / "mon.db"
    j = Journal(str(db))
    j.open_position(replace(
        _pos(meta={"combo_type": "debit", "strategy_name": "always_exit"}),
        state="open",
    ))
    trader = FakeTrader(mid=2.55)

    async def factory():
        return trader

    def legacy_fetcher(symbol):  # only accepts one arg
        return pd.DataFrame({"close": [100.0, 101.0]})

    results = await tick(
        factory,
        journal=j,
        defaults={
            "stop_loss_pct": 50, "take_profit_pct": 50,
            "trailing_stop_pct": 0, "dte_exit_at": 0,
            "haircut_pct": 0.05,
            "bars_fetcher": legacy_fetcher,
            "strategy_registry": {"always_exit": _AlwaysExitStrategy},
        },
        today=date(2026, 4, 14),
    )

    assert results[0]["exit_reason"] == "strategy:test_strategy_exit"
    j.close()
