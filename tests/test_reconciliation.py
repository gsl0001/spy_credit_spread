"""Tests for Journal.daily_reconciliation_report (task I4).

Covers:
    1. Empty day — no positions closed on the requested date.
    2. Single closed trade with commission — all fields correct.
    3. Multiple trades on the same day — totals aggregate correctly.
    4. Date filtering — only positions closed on the requested date are included.
    5. Gross vs net P&L relationship — net_pnl == gross_pnl - commissions.
    6. Slippage estimate equals the commission drag.
    7. Positions that are still open are excluded.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

import pytest

from core.journal import Fill, Journal, Order, Position


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_position(
    *,
    symbol: str = "SPY",
    entry_cost: float = 200.0,
    state: str = "open",
) -> Position:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return Position(
        id=str(uuid.uuid4()),
        symbol=symbol,
        topology="vertical_spread",
        direction="bear_call",
        contracts=1,
        entry_cost=entry_cost,
        entry_time=now,
        expiry="2026-06-20",
        legs=(
            {"type": "call", "strike": 500, "side": "short", "price": 3.0},
            {"type": "call", "strike": 505, "side": "long", "price": 1.0},
        ),
        state=state,
    )


def _make_order(
    pos_id: str,
    commission: float = 0.0,
    kind: str = "entry",
    status: str = "filled",
) -> Order:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return Order(
        id=str(uuid.uuid4()),
        position_id=pos_id,
        broker="ibkr",
        broker_order_id=str(uuid.uuid4()),
        side="SELL" if kind == "entry" else "BUY",
        limit_price=2.0,
        status=status,
        submitted_at=now,
        filled_at=now,
        fill_price=2.0,
        commission=commission,
        kind=kind,
    )


def _close(
    journal: Journal,
    pos_id: str,
    *,
    exit_cost: float,
    realized_pnl: float,
    exit_time: str,
) -> None:
    """Close a position with an explicit exit_time for date-filter tests."""
    with journal._tx() as c:
        c.execute(
            """
            UPDATE positions
               SET state        = 'closed',
                   exit_cost    = ?,
                   exit_reason  = 'take_profit',
                   realized_pnl = ?,
                   exit_time    = ?
             WHERE id = ?
            """,
            (exit_cost, realized_pnl, exit_time, pos_id),
        )


# ── Tests ──────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_empty_day_returns_zero_totals(tmp_path):
    """No closed positions on a day → all totals are 0 and list is empty."""
    j = Journal(str(tmp_path / "t.db"))
    report = j.daily_reconciliation_report("2024-01-01")

    assert report["date"] == "2024-01-01"
    assert report["positions_closed"] == []
    assert report["total_commissions"] == pytest.approx(0.0)
    assert report["total_gross_pnl"] == pytest.approx(0.0)
    assert report["total_net_pnl"] == pytest.approx(0.0)


@pytest.mark.unit
def test_single_closed_trade_with_commission(tmp_path):
    """A single closed trade surfaces all fields correctly."""
    j = Journal(str(tmp_path / "t.db"))
    pos = _make_position(symbol="SPY", entry_cost=200.0)
    j.open_position(pos)

    # Record entry and exit orders with commissions.
    entry_order = _make_order(pos.id, commission=1.30, kind="entry")
    exit_order = _make_order(pos.id, commission=1.30, kind="exit")
    j.record_order(entry_order)
    j.record_order(exit_order)

    target_date = "2025-03-10"
    exit_time = f"{target_date}T15:55:00+00:00"
    _close(j, pos.id, exit_cost=240.0, realized_pnl=40.0, exit_time=exit_time)

    report = j.daily_reconciliation_report(target_date)

    assert report["date"] == target_date
    assert len(report["positions_closed"]) == 1

    rec = report["positions_closed"][0]
    assert rec["id"] == pos.id
    assert rec["symbol"] == "SPY"
    assert rec["entry_cost"] == pytest.approx(200.0)
    assert rec["exit_proceeds"] == pytest.approx(240.0)
    assert rec["commissions"] == pytest.approx(2.60)          # 1.30 + 1.30
    assert rec["gross_pnl"] == pytest.approx(40.0)
    assert rec["net_pnl"] == pytest.approx(40.0 - 2.60)
    assert rec["slippage_est"] == pytest.approx(2.60)

    assert report["total_commissions"] == pytest.approx(2.60)
    assert report["total_gross_pnl"] == pytest.approx(40.0)
    assert report["total_net_pnl"] == pytest.approx(40.0 - 2.60)


@pytest.mark.unit
def test_multiple_trades_totals_aggregate(tmp_path):
    """Multiple positions closed on the same day produce correct aggregates."""
    j = Journal(str(tmp_path / "t.db"))
    target_date = "2025-04-15"
    exit_time = f"{target_date}T16:00:00+00:00"

    # Trade A: gross +100, commissions 2.00 → net +98
    pos_a = _make_position(symbol="SPY", entry_cost=200.0)
    j.open_position(pos_a)
    j.record_order(_make_order(pos_a.id, commission=1.00, kind="entry"))
    j.record_order(_make_order(pos_a.id, commission=1.00, kind="exit"))
    _close(j, pos_a.id, exit_cost=300.0, realized_pnl=100.0, exit_time=exit_time)

    # Trade B: gross -30, commissions 1.50 → net -31.50
    pos_b = _make_position(symbol="QQQ", entry_cost=150.0)
    j.open_position(pos_b)
    j.record_order(_make_order(pos_b.id, commission=0.75, kind="entry"))
    j.record_order(_make_order(pos_b.id, commission=0.75, kind="exit"))
    _close(j, pos_b.id, exit_cost=120.0, realized_pnl=-30.0, exit_time=exit_time)

    report = j.daily_reconciliation_report(target_date)

    assert len(report["positions_closed"]) == 2
    assert report["total_commissions"] == pytest.approx(3.50)
    assert report["total_gross_pnl"] == pytest.approx(70.0)
    assert report["total_net_pnl"] == pytest.approx(70.0 - 3.50)


@pytest.mark.unit
def test_date_filtering_excludes_other_days(tmp_path):
    """Only positions closed on the requested date are returned."""
    j = Journal(str(tmp_path / "t.db"))

    # Position closed on 2025-04-01
    pos_a = _make_position(symbol="SPY", entry_cost=100.0)
    j.open_position(pos_a)
    _close(j, pos_a.id, exit_cost=150.0, realized_pnl=50.0,
           exit_time="2025-04-01T15:00:00+00:00")

    # Position closed on 2025-04-02
    pos_b = _make_position(symbol="IWM", entry_cost=80.0)
    j.open_position(pos_b)
    _close(j, pos_b.id, exit_cost=90.0, realized_pnl=10.0,
           exit_time="2025-04-02T15:00:00+00:00")

    report_01 = j.daily_reconciliation_report("2025-04-01")
    assert len(report_01["positions_closed"]) == 1
    assert report_01["positions_closed"][0]["id"] == pos_a.id

    report_02 = j.daily_reconciliation_report("2025-04-02")
    assert len(report_02["positions_closed"]) == 1
    assert report_02["positions_closed"][0]["id"] == pos_b.id

    # A day with nothing closed should be empty.
    report_03 = j.daily_reconciliation_report("2025-04-03")
    assert report_03["positions_closed"] == []


@pytest.mark.unit
def test_gross_vs_net_pnl_relationship(tmp_path):
    """net_pnl == gross_pnl - commissions for every position in the report."""
    j = Journal(str(tmp_path / "t.db"))
    target_date = "2025-05-01"

    scenarios = [
        ("SPY", 300.0, 380.0, 80.0, 3.00),   # winner
        ("QQQ", 200.0, 160.0, -40.0, 1.50),  # loser
        ("IWM", 50.0, 50.0, 0.0, 0.65),      # break-even (ignoring comm)
    ]
    for symbol, entry, exit_p, gross, comm in scenarios:
        pos = _make_position(symbol=symbol, entry_cost=entry)
        j.open_position(pos)
        j.record_order(_make_order(pos.id, commission=comm, kind="entry"))
        _close(j, pos.id, exit_cost=exit_p, realized_pnl=gross,
               exit_time=f"{target_date}T16:00:00+00:00")

    report = j.daily_reconciliation_report(target_date)
    assert len(report["positions_closed"]) == 3

    for rec in report["positions_closed"]:
        assert rec["net_pnl"] == pytest.approx(rec["gross_pnl"] - rec["commissions"])

    # Aggregate relationship also holds.
    assert report["total_net_pnl"] == pytest.approx(
        report["total_gross_pnl"] - report["total_commissions"]
    )


@pytest.mark.unit
def test_open_positions_excluded(tmp_path):
    """Positions that are still open are not included in the reconciliation."""
    j = Journal(str(tmp_path / "t.db"))
    target_date = "2025-06-01"

    # Closed position.
    pos_closed = _make_position(symbol="SPY", entry_cost=200.0)
    j.open_position(pos_closed)
    _close(j, pos_closed.id, exit_cost=250.0, realized_pnl=50.0,
           exit_time=f"{target_date}T15:30:00+00:00")

    # Open position (still in flight).
    pos_open = _make_position(symbol="QQQ", entry_cost=100.0, state="open")
    j.open_position(pos_open)

    report = j.daily_reconciliation_report(target_date)
    ids = [r["id"] for r in report["positions_closed"]]
    assert pos_closed.id in ids
    assert pos_open.id not in ids


@pytest.mark.unit
def test_default_date_is_today(tmp_path):
    """Calling with date=None uses today's date."""
    j = Journal(str(tmp_path / "t.db"))
    today = date.today().isoformat()

    pos = _make_position(symbol="SPY", entry_cost=100.0)
    j.open_position(pos)
    exit_time = f"{today}T14:00:00+00:00"
    _close(j, pos.id, exit_cost=130.0, realized_pnl=30.0, exit_time=exit_time)

    # date=None should default to today.
    report = j.daily_reconciliation_report()
    assert report["date"] == today
    assert len(report["positions_closed"]) == 1
    assert report["positions_closed"][0]["id"] == pos.id
