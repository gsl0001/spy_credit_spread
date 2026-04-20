"""Fill reconciliation tests.

Pure layer: exercise :func:`next_action` across status + elapsed combos.
Integration layer: ``reconcile_once`` with a fake trader + real journal
                   proves state transitions land in SQLite correctly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.fill_watcher import (
    FillAction,
    finalize_cancelled,
    finalize_filled,
    next_action,
    reconcile_once,
)
from core.journal import Journal, Order, Position


def _submitted_iso(seconds_ago: int) -> str:
    t = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return t.isoformat(timespec="seconds")


# ── next_action (pure) ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_waiting_when_status_submitted_and_fresh():
    a = next_action(
        {"status": "Submitted", "filled": 0, "remaining": 2,
         "avgFillPrice": 0.0, "commission": 0.0},
        _submitted_iso(5), datetime.now(timezone.utc),
        timeout_seconds=30, total_qty=2,
    )
    assert a.kind == "waiting"


@pytest.mark.unit
def test_filled_when_status_is_filled():
    a = next_action(
        {"status": "Filled", "filled": 2, "remaining": 0,
         "avgFillPrice": 2.50, "commission": 1.20},
        _submitted_iso(2), datetime.now(timezone.utc),
        timeout_seconds=30, total_qty=2,
    )
    assert a.kind == "filled"
    assert a.details["avg_fill_price"] == 2.50
    assert a.details["commission"] == 1.20


@pytest.mark.unit
def test_rejected_is_terminal():
    a = next_action(
        {"status": "Rejected", "filled": 0, "remaining": 2,
         "avgFillPrice": 0.0, "commission": 0.0},
        _submitted_iso(1), datetime.now(timezone.utc),
        timeout_seconds=30, total_qty=2,
    )
    assert a.kind == "rejected"


@pytest.mark.unit
def test_cancel_timeout_when_still_working_past_window():
    a = next_action(
        {"status": "Submitted", "filled": 0, "remaining": 2,
         "avgFillPrice": 0.0, "commission": 0.0},
        _submitted_iso(60), datetime.now(timezone.utc),
        timeout_seconds=30, total_qty=2,
    )
    assert a.kind == "cancel_timeout"


@pytest.mark.unit
def test_cancel_timeout_finalizes_as_filled_when_race_completes():
    # Broker says Submitted but also reports filled==total. Finalize.
    a = next_action(
        {"status": "Submitted", "filled": 2, "remaining": 0,
         "avgFillPrice": 2.30, "commission": 0.5},
        _submitted_iso(60), datetime.now(timezone.utc),
        timeout_seconds=30, total_qty=2,
    )
    assert a.kind == "filled"


@pytest.mark.unit
def test_partial_reported_while_fresh():
    a = next_action(
        {"status": "Submitted", "filled": 1, "remaining": 1,
         "avgFillPrice": 2.48, "commission": 0.25},
        _submitted_iso(3), datetime.now(timezone.utc),
        timeout_seconds=30, total_qty=2,
    )
    assert a.kind == "partial"
    assert a.details["filled"] == 1
    assert a.details["remaining"] == 1


@pytest.mark.unit
def test_no_broker_record_still_fresh_waits():
    a = next_action(
        None, _submitted_iso(5), datetime.now(timezone.utc),
        timeout_seconds=30,
    )
    assert a.kind == "waiting"


@pytest.mark.unit
def test_no_broker_record_past_timeout_cancels():
    a = next_action(
        None, _submitted_iso(60), datetime.now(timezone.utc),
        timeout_seconds=30,
    )
    assert a.kind == "cancel_timeout"
    assert a.details["reason"] == "no_broker_record"


@pytest.mark.unit
def test_broker_cancelled_is_terminal():
    a = next_action(
        {"status": "Cancelled", "filled": 0, "remaining": 2,
         "avgFillPrice": 0.0, "commission": 0.0},
        _submitted_iso(5), datetime.now(timezone.utc),
        timeout_seconds=30,
    )
    assert a.kind == "cancelled"


# ── Finalizers ────────────────────────────────────────────────────────────

def _pending_pos(**over) -> Position:
    base = dict(
        id="p-entry",
        symbol="SPY",
        topology="vertical_spread",
        direction="bull_call",
        contracts=2,
        entry_cost=500.0,  # provisional (will be overwritten by finalize_filled)
        entry_time=_submitted_iso(10),
        expiry="2026-04-28",
        legs=(
            {"type": "call", "strike": 500, "side": "long",  "expiry": "20260428"},
            {"type": "call", "strike": 505, "side": "short", "expiry": "20260428"},
        ),
        state="pending",
        broker="ibkr",
        meta={"combo_type": "debit"},
    )
    base.update(over)
    return Position(**base)


def _order(**over) -> Order:
    base = dict(
        id="o-1",
        position_id="p-entry",
        broker="ibkr",
        broker_order_id="1234",
        side="BUY",
        limit_price=2.55,
        status="submitted",
        submitted_at=_submitted_iso(10),
        kind="entry",
        idempotency_key="entry:p-entry",
    )
    base.update(over)
    return Order(**base)


@pytest.mark.unit
def test_finalize_entry_filled_opens_position_and_rewrites_cost(tmp_path: Path):
    j = Journal(str(tmp_path / "fw.db"))
    pos = _pending_pos()
    j.open_position(pos)
    j.record_order(_order())
    finalize_filled(
        j, _order(), pos,
        filled_qty=2, avg_fill_price=2.40, commission=1.5,
    )
    out = j.get_position("p-entry")
    assert out is not None
    assert out.state == "open"
    assert out.entry_cost == pytest.approx(2.40 * 100 * 2)
    orders = j.list_orders_for_position("p-entry")
    assert orders[0].status == "filled"
    assert orders[0].fill_price == pytest.approx(2.40)
    assert j.list_fills("o-1")[0].qty == 2
    j.close()


@pytest.mark.unit
def test_finalize_entry_cancelled_kills_position(tmp_path: Path):
    j = Journal(str(tmp_path / "fw.db"))
    pos = _pending_pos()
    j.open_position(pos)
    j.record_order(_order())
    finalize_cancelled(j, _order(), pos, reason="timeout_cancel")
    out = j.get_position("p-entry")
    assert out is not None
    assert out.state == "cancelled"
    j.close()


@pytest.mark.unit
def test_finalize_exit_filled_closes_position_with_realized_pnl(tmp_path: Path):
    j = Journal(str(tmp_path / "fw.db"))
    pos = _pending_pos(state="closing", exit_reason="stop_loss")
    j.open_position(pos)
    exit_order = _order(id="o-exit", side="SELL", kind="exit",
                       idempotency_key="exit:p-entry:stop_loss",
                       limit_price=1.20)
    j.record_order(exit_order)
    # Sold at 1.25 per share, 2 contracts → 250; entry was 500 → realized -250
    finalize_filled(j, exit_order, pos,
                    filled_qty=2, avg_fill_price=1.25, commission=0.0)
    out = j.get_position("p-entry")
    assert out is not None
    assert out.state == "closed"
    assert out.realized_pnl == pytest.approx(-250.0)
    assert out.exit_reason == "stop_loss"
    j.close()


# ── Integration: reconcile_once ───────────────────────────────────────────

class FakeTrader:
    """Minimal trader with get_order_status + cancel_order."""
    def __init__(self):
        self.status_map: dict[int, dict] = {}
        self.cancelled: list[int] = []

    async def get_order_status(self, broker_order_id):
        oid = int(broker_order_id)
        return self.status_map.get(oid)

    async def cancel_order(self, oid: int):
        self.cancelled.append(int(oid))
        return {"success": True, "msg": "cancelled"}


async def test_reconcile_finalizes_filled_entry(tmp_path: Path):
    j = Journal(str(tmp_path / "fw.db"))
    pos = _pending_pos()
    j.open_position(pos)
    j.record_order(_order())
    trader = FakeTrader()
    trader.status_map[1234] = {
        "status": "Filled", "filled": 2, "remaining": 0,
        "avgFillPrice": 2.45, "commission": 0.6,
    }
    results = await reconcile_once(trader, j, timeout_seconds=30)
    assert len(results) == 1
    assert results[0]["action"] == "filled"
    assert j.get_position("p-entry").state == "open"
    j.close()


async def test_reconcile_cancels_stale_entry_and_kills_position(tmp_path: Path):
    j = Journal(str(tmp_path / "fw.db"))
    pos = _pending_pos()
    j.open_position(pos)
    j.record_order(_order(submitted_at=_submitted_iso(120)))
    trader = FakeTrader()
    trader.status_map[1234] = {
        "status": "Submitted", "filled": 0, "remaining": 2,
        "avgFillPrice": 0.0, "commission": 0.0,
    }
    results = await reconcile_once(trader, j, timeout_seconds=30)
    assert results[0]["action"] == "cancel_timeout"
    assert 1234 in trader.cancelled
    assert j.get_position("p-entry").state == "cancelled"
    j.close()


async def test_reconcile_reopens_position_when_exit_cancelled(tmp_path: Path):
    """If the CLOSING exit order gets cancelled, position returns to open."""
    j = Journal(str(tmp_path / "fw.db"))
    pos = _pending_pos(state="closing")
    j.open_position(pos)
    exit_order = _order(id="o-exit", side="SELL", kind="exit",
                       idempotency_key="exit:p-entry:stop_loss",
                       submitted_at=_submitted_iso(120))
    j.record_order(exit_order)
    trader = FakeTrader()
    trader.status_map[1234] = {
        "status": "Cancelled", "filled": 0, "remaining": 2,
        "avgFillPrice": 0.0, "commission": 0.0,
    }
    await reconcile_once(trader, j, timeout_seconds=30)
    assert j.get_position("p-entry").state == "open"
    j.close()


async def test_reconcile_noop_when_no_open_orders(tmp_path: Path):
    j = Journal(str(tmp_path / "fw.db"))
    trader = FakeTrader()
    assert await reconcile_once(trader, j, timeout_seconds=30) == []
    j.close()


async def test_reconcile_survives_status_fetch_failure(tmp_path: Path):
    j = Journal(str(tmp_path / "fw.db"))
    pos = _pending_pos()
    j.open_position(pos)
    j.record_order(_order(submitted_at=_submitted_iso(5)))

    class ExplodingTrader(FakeTrader):
        async def get_order_status(self, broker_order_id):
            raise RuntimeError("IBKR timed out")

    results = await reconcile_once(ExplodingTrader(), j, timeout_seconds=30)
    assert results[0]["action"] == "waiting"
    # Position should still be pending; error was logged
    assert j.get_position("p-entry").state == "pending"
    events = j.recent_events(5)
    assert any(e["kind"] == "status_fetch_failed" for e in events)
    j.close()
