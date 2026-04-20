"""Tests for scanner-log persistence (I11).

The scanner log table keeps a bounded history of every scan tick so the UI
and operators can see *why* a signal did or didn't trigger even after the
server restarts. We verify:

* ``record_scan_log`` round-trips all fields (including nested JSON).
* ``list_scan_logs`` returns newest-first and respects the limit.
* Empty tables yield an empty list rather than raising.
* The ``signal`` boolean survives the int <-> bool conversion.
* Legacy tolerance: ``None`` for price/rsi is accepted.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from core.journal import Journal


@pytest.fixture
def journal(tmp_path):
    j = Journal(str(tmp_path / "scan.db"))
    yield j
    j.close()


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


class TestRecordAndList:
    def test_empty_table_returns_empty_list(self, journal):
        assert journal.list_scan_logs() == []

    def test_single_row_roundtrip(self, journal):
        now = _iso(datetime.now(timezone.utc))
        row_id = journal.record_scan_log(
            time=now,
            signal=True,
            price=415.25,
            rsi=42.7,
            msg="Signal: YES",
            details={"ema_ok": True, "bars": 3},
        )
        assert row_id >= 1

        rows = journal.list_scan_logs()
        assert len(rows) == 1
        r = rows[0]
        assert r["time"] == now
        assert r["signal"] is True
        assert r["price"] == pytest.approx(415.25)
        assert r["rsi"] == pytest.approx(42.7)
        assert r["msg"] == "Signal: YES"
        assert r["details"] == {"ema_ok": True, "bars": 3}

    def test_signal_false_survives(self, journal):
        journal.record_scan_log(
            time=_iso(datetime.now(timezone.utc)),
            signal=False,
            price=400.0,
            rsi=55.0,
            msg="Signal: NO",
        )
        rows = journal.list_scan_logs()
        assert rows[0]["signal"] is False

    def test_none_price_and_rsi_allowed(self, journal):
        journal.record_scan_log(
            time=_iso(datetime.now(timezone.utc)),
            signal=False,
            price=None,
            rsi=None,
            msg="Error: upstream offline",
            details={"error": "timeout"},
        )
        r = journal.list_scan_logs()[0]
        assert r["price"] is None
        assert r["rsi"] is None
        assert r["details"] == {"error": "timeout"}

    def test_default_details_is_empty_dict(self, journal):
        journal.record_scan_log(
            time=_iso(datetime.now(timezone.utc)),
            signal=True,
            price=1.0,
            rsi=1.0,
            msg="ok",
        )
        assert journal.list_scan_logs()[0]["details"] == {}


class TestOrderingAndLimit:
    def test_newest_first(self, journal):
        """Returned rows are newest-first (descending id)."""
        for i in range(5):
            journal.record_scan_log(
                time=f"2026-04-16T12:00:0{i}+00:00",
                signal=i % 2 == 0,
                price=400.0 + i,
                rsi=50.0 + i,
                msg=f"scan {i}",
            )

        rows = journal.list_scan_logs()
        assert len(rows) == 5
        msgs = [r["msg"] for r in rows]
        assert msgs == ["scan 4", "scan 3", "scan 2", "scan 1", "scan 0"]

    def test_limit_respected(self, journal):
        for i in range(10):
            journal.record_scan_log(
                time=f"2026-04-16T12:00:{i:02d}+00:00",
                signal=False,
                price=400.0,
                rsi=50.0,
                msg=f"scan {i}",
            )

        rows = journal.list_scan_logs(limit=3)
        assert len(rows) == 3
        assert [r["msg"] for r in rows] == ["scan 9", "scan 8", "scan 7"]

    def test_limit_greater_than_rows_ok(self, journal):
        journal.record_scan_log(
            time="2026-04-16T12:00:00+00:00",
            signal=True, price=1.0, rsi=1.0, msg="only",
        )
        rows = journal.list_scan_logs(limit=999)
        assert len(rows) == 1


class TestPersistenceAcrossInstances:
    def test_new_instance_sees_prior_rows(self, tmp_path):
        """Two Journal instances against the same file share history."""
        db = str(tmp_path / "shared.db")

        j1 = Journal(db)
        try:
            j1.record_scan_log(
                time="2026-04-16T12:00:00+00:00",
                signal=True, price=415.0, rsi=40.0, msg="first",
                details={"foo": "bar"},
            )
        finally:
            j1.close()

        j2 = Journal(db)
        try:
            rows = j2.list_scan_logs()
            assert len(rows) == 1
            assert rows[0]["msg"] == "first"
            assert rows[0]["details"] == {"foo": "bar"}
        finally:
            j2.close()


class TestRawStorage:
    def test_details_stored_as_valid_json(self, journal):
        """The underlying column should be JSON, not a Python repr."""
        journal.record_scan_log(
            time="2026-04-16T12:00:00+00:00",
            signal=True, price=1.0, rsi=1.0, msg="j",
            details={"list": [1, 2, 3], "nested": {"a": True}},
        )
        raw = journal._conn.execute(
            "SELECT details_json FROM scanner_logs"
        ).fetchone()["details_json"]
        parsed = json.loads(raw)
        assert parsed == {"list": [1, 2, 3], "nested": {"a": True}}

    def test_signal_column_is_integer(self, journal):
        """We store 0/1 for boolean — confirms schema choice."""
        journal.record_scan_log(
            time="t", signal=True, price=1.0, rsi=1.0, msg="a",
        )
        journal.record_scan_log(
            time="t", signal=False, price=1.0, rsi=1.0, msg="b",
        )
        rows = journal._conn.execute(
            "SELECT signal FROM scanner_logs ORDER BY id"
        ).fetchall()
        assert [r["signal"] for r in rows] == [1, 0]
