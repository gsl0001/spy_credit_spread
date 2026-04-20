"""Journal persistence tests.

Covers: open/close round-trip, restart loads open positions, daily P&L
rollup excludes open positions, idempotency key, event audit log.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from core.journal import Fill, Journal, Order, Position


def _sample_position(symbol: str = "SPY") -> Position:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return Position(
        id=str(uuid.uuid4()),
        symbol=symbol,
        topology="vertical_spread",
        direction="bull_call",
        contracts=2,
        entry_cost=250.0,
        entry_time=now,
        expiry="2026-05-01",
        legs=(
            {"type": "call", "strike": 500, "side": "long", "price": 5.0},
            {"type": "call", "strike": 505, "side": "short", "price": 2.5},
        ),
        state="open",
        high_water_mark=250.0,
    )


@pytest.mark.unit
def test_open_and_close_roundtrip(tmp_path):
    j = Journal(str(tmp_path / "t.db"))
    pos = _sample_position()
    j.open_position(pos)

    reloaded = j.get_position(pos.id)
    assert reloaded is not None
    assert reloaded.symbol == "SPY"
    assert reloaded.legs[0]["strike"] == 500
    assert reloaded.state == "open"

    j.close_position(pos.id, exit_cost=350.0, reason="take_profit",
                     realized_pnl=100.0)
    closed = j.get_position(pos.id)
    assert closed.state == "closed"
    assert closed.exit_reason == "take_profit"
    assert closed.realized_pnl == 100.0


@pytest.mark.unit
def test_restart_loads_open_positions(tmp_path):
    db = str(tmp_path / "restart.db")
    j1 = Journal(db)
    p1 = _sample_position("SPY")
    p2 = _sample_position("QQQ")
    j1.open_position(p1)
    j1.open_position(p2)
    j1.close_position(p2.id, exit_cost=0, reason="cancelled", realized_pnl=0)
    j1.close()

    # Fresh journal instance simulating server restart
    j2 = Journal(db)
    open_positions = j2.list_open()
    assert len(open_positions) == 1
    assert open_positions[0].id == p1.id


@pytest.mark.unit
def test_today_realized_pnl_excludes_open(tmp_path):
    j = Journal(str(tmp_path / "pnl.db"))
    p = _sample_position()
    j.open_position(p)

    assert j.today_realized_pnl() == 0.0
    assert j.today_trade_count() == 0

    j.close_position(p.id, exit_cost=400, reason="take_profit", realized_pnl=150.0)
    assert j.today_realized_pnl() == pytest.approx(150.0)
    assert j.today_trade_count() == 1

    # Losing trade on same day aggregates
    p2 = _sample_position()
    j.open_position(p2)
    j.close_position(p2.id, exit_cost=100, reason="stop_loss", realized_pnl=-50.0)
    assert j.today_realized_pnl() == pytest.approx(100.0)
    assert j.today_trade_count() == 2


@pytest.mark.unit
def test_order_idempotency(tmp_path):
    j = Journal(str(tmp_path / "idem.db"))
    key = "signal-123"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    o1 = Order(
        id=str(uuid.uuid4()),
        position_id=None,
        broker="ibkr",
        broker_order_id="1001",
        side="BUY",
        limit_price=2.50,
        status="submitted",
        submitted_at=now,
        idempotency_key=key,
    )
    j.record_order(o1)
    hit = j.get_order_by_idempotency(key)
    assert hit is not None and hit.id == o1.id


@pytest.mark.unit
def test_fill_recording(tmp_path):
    j = Journal(str(tmp_path / "fills.db"))
    pos = _sample_position()
    j.open_position(pos)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    order = Order(
        id=str(uuid.uuid4()),
        position_id=pos.id,
        broker="ibkr",
        broker_order_id="2001",
        side="BUY",
        limit_price=2.5,
        status="submitted",
        submitted_at=now,
    )
    j.record_order(order)
    j.record_fill(Fill(id=None, order_id=order.id, qty=1, price=2.47, time=now))
    j.record_fill(Fill(id=None, order_id=order.id, qty=1, price=2.49, time=now))
    fills = j.list_fills(order.id)
    assert len(fills) == 2
    assert fills[0].qty == 1


@pytest.mark.unit
def test_event_audit_log(tmp_path):
    j = Journal(str(tmp_path / "ev.db"))
    j.log_event("monitor_tick", subject="SPY", payload={"mid": 2.50, "pnl_pct": -5.0})
    j.log_event("risk_reject", subject="daily_loss_limit")
    ev = j.recent_events(limit=10)
    assert len(ev) == 2
    assert ev[0]["kind"] == "risk_reject"
    assert ev[1]["payload"]["mid"] == 2.50
