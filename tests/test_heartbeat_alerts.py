"""Tests for I10: critical-condition alerts on /api/ibkr/heartbeat.

The heartbeat endpoint must surface three classes of trouble so the UI
can raise banners:

1. **Daily-loss approaching** — realized P&L consumes ≥80% of the limit.
2. **Monitor stalled** — registered but no tick in >30s.
3. **IBKR dropped** — socket was up, now isn't.

These tests build the response dict directly by invoking the endpoint
through FastAPI's ``TestClient`` so routing, pydantic request parsing,
and the live main-module state all participate (no mocking of FastAPI).

Dependencies on external services (IBKR socket) are neutralised by the
``TestClient`` pattern: ``HAS_IBSYNC`` may be False or no trader is
registered for the given ``host:port:client_id``, which is exactly what
we want for an isolated unit test.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from core.journal import reset_journal_for_tests


def _patch_equity(monkeypatch, equity: float, limit_pct: float = 2.0) -> None:
    """Replace ``core.settings.SETTINGS`` with a namespace exposing the
    fields the heartbeat reads. Both ``Settings`` and ``RiskSettings``
    are frozen dataclasses so we can't mutate in place — swap the
    module attribute instead."""
    import core.settings as cs
    real = cs.SETTINGS
    fake_risk = SimpleNamespace(
        assumed_equity_for_alerts=equity,
        daily_loss_limit_pct=limit_pct,
        max_concurrent_positions=real.risk.max_concurrent_positions,
    )
    fake = SimpleNamespace(
        risk=fake_risk,
        ibkr=real.ibkr,
        alpaca=getattr(real, "alpaca", None),
        journal_db_path=real.journal_db_path,
        log_dir=getattr(real, "log_dir", "logs"),
        log_level=getattr(real, "log_level", "INFO"),
        event_calendar_file=getattr(real, "event_calendar_file", ""),
        notify_webhook_url=getattr(real, "notify_webhook_url", ""),
    )
    monkeypatch.setattr(cs, "SETTINGS", fake, raising=True)


# ── helpers ────────────────────────────────────────────────────────────────

def _hb_request() -> dict:
    # Fields accepted by IBKRConnectRequest; values don't matter because no
    # real trader is registered under this key.
    return {
        "host": "127.0.0.1",
        "port": 7497,
        "client_id": 999,
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated TestClient with a fresh journal DB per test."""
    reset_journal_for_tests(str(tmp_path / "hb.db"))
    # Clear monitor-tick tracker between tests.
    monkeypatch.setattr(main, "_last_monitor_tick_iso", None, raising=False)
    with TestClient(main.app) as c:
        yield c


# ── daily-loss warning ─────────────────────────────────────────────────────

class TestDailyLossWarning:
    def test_no_loss_no_warning(self, client):
        r = client.post("/api/ibkr/heartbeat", json=_hb_request())
        assert r.status_code == 200
        body = r.json()
        assert body["daily_loss_warning"] is False
        assert body["daily_loss_pct_used"] == 0.0

    def test_small_loss_below_threshold_no_warning(self, client, monkeypatch):
        """A loss under 80% of the cap should NOT raise an alert."""
        # Inject a small loss into the journal.
        from core.journal import get_journal
        j = get_journal()
        # Use the close_position path to bump daily_pnl with a negative realized.
        # Easier: write directly for test (read-only fixture wants isolation).
        j._conn.execute(
            "INSERT OR REPLACE INTO daily_pnl (date, realized, trades, "
            "win_count, loss_count) VALUES (?, ?, 1, 0, 1)",
            (date.today().isoformat(), -10.0),
        )
        # Provide an equity estimate so pct computation activates.
        _patch_equity(monkeypatch, 10_000.0)
        r = client.post("/api/ibkr/heartbeat", json=_hb_request())
        body = r.json()
        # With a 2% limit ($200 cap) and $10 loss, we're at 5% of the limit.
        assert body["daily_loss_warning"] is False

    def test_loss_at_80_percent_triggers_warning(self, client, monkeypatch):
        """At ≥80% of the daily-loss cap, heartbeat surfaces a warning alert."""
        from core.journal import get_journal
        j = get_journal()
        # 2% of $10000 = $200 cap. 80% of $200 = $160 loss.
        j._conn.execute(
            "INSERT OR REPLACE INTO daily_pnl (date, realized, trades, "
            "win_count, loss_count) VALUES (?, ?, 1, 0, 1)",
            (date.today().isoformat(), -170.0),
        )
        _patch_equity(monkeypatch, 10_000.0)
        r = client.post("/api/ibkr/heartbeat", json=_hb_request())
        body = r.json()
        assert body["daily_loss_warning"] is True
        assert body["daily_loss_pct_used"] >= 80.0
        codes = [a["code"] for a in body["alerts"]]
        assert "daily_loss_approaching" in codes


# ── monitor staleness ──────────────────────────────────────────────────────

class TestMonitorStalled:
    def test_not_registered_not_stalled(self, client):
        """If the monitor job isn't registered, we don't report staleness."""
        r = client.post("/api/ibkr/heartbeat", json=_hb_request())
        body = r.json()
        # Baseline state from a fresh TestClient — no monitor job registered.
        assert body["monitor_stalled"] is False
        assert body["monitor_registered"] is False

    def test_recent_tick_not_stalled(self, client, monkeypatch):
        """A tick within the last 30s is healthy."""
        # Inject a fake monitor job so monitor_registered=True.
        job = type("J", (), {"id": "monitor_tick"})()
        monkeypatch.setattr(main.scheduler, "get_jobs", lambda: [job])

        fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
        monkeypatch.setattr(
            main, "_last_monitor_tick_iso",
            fresh.isoformat(timespec="seconds"),
            raising=False,
        )

        r = client.post("/api/ibkr/heartbeat", json=_hb_request())
        body = r.json()
        assert body["monitor_registered"] is True
        assert body["monitor_stalled"] is False
        assert body["monitor_seconds_since_tick"] is not None
        assert body["monitor_seconds_since_tick"] < 10.0

    def test_stale_tick_triggers_alert(self, client, monkeypatch):
        """A tick >30s old yields a critical 'monitor_stalled' alert."""
        job = type("J", (), {"id": "monitor_tick"})()
        monkeypatch.setattr(main.scheduler, "get_jobs", lambda: [job])

        stale = datetime.now(timezone.utc) - timedelta(seconds=120)
        monkeypatch.setattr(
            main, "_last_monitor_tick_iso",
            stale.isoformat(timespec="seconds"),
            raising=False,
        )

        r = client.post("/api/ibkr/heartbeat", json=_hb_request())
        body = r.json()
        assert body["monitor_stalled"] is True
        codes = [a["code"] for a in body["alerts"]]
        assert "monitor_stalled" in codes
        # Critical severity — UI should render red.
        for a in body["alerts"]:
            if a["code"] == "monitor_stalled":
                assert a["level"] == "critical"

    def test_registered_but_never_ticked_flags_warning(self, client, monkeypatch):
        """Registered with no tick history = warning (not critical)."""
        job = type("J", (), {"id": "monitor_tick"})()
        monkeypatch.setattr(main.scheduler, "get_jobs", lambda: [job])
        monkeypatch.setattr(main, "_last_monitor_tick_iso", None, raising=False)

        r = client.post("/api/ibkr/heartbeat", json=_hb_request())
        body = r.json()
        assert body["monitor_stalled"] is True
        codes = [a["code"] for a in body["alerts"]]
        assert "monitor_never_ticked" in codes


# ── IBKR dropped ───────────────────────────────────────────────────────────

class TestIBKRDropped:
    def test_no_trader_no_dropped_flag(self, client):
        """`not_connected` is different from `dropped` — no trader, no alert."""
        r = client.post("/api/ibkr/heartbeat", json=_hb_request())
        body = r.json()
        assert body["ibkr_dropped"] is False
        assert body["status"] in ("not_connected", "unavailable")

    def test_disconnected_trader_flags_dropped(self, client, monkeypatch):
        """A registered trader whose socket reports False → dropped alert."""
        # Only meaningful when HAS_IBSYNC is True.
        from brokers.ibkr_trading import HAS_IBSYNC, _ib_instances
        if not HAS_IBSYNC:
            pytest.skip("ib_insync not available")

        class _FakeIB:
            def isConnected(self):
                return False

        class _FakeTrader:
            def __init__(self):
                self.ib = _FakeIB()
                self.connected = True

        key = "127.0.0.1:7497:999"
        _ib_instances[key] = _FakeTrader()
        try:
            r = client.post("/api/ibkr/heartbeat", json=_hb_request())
            body = r.json()
            assert body["alive"] is False
            assert body["status"] == "dropped"
            assert body["ibkr_dropped"] is True
            codes = [a["code"] for a in body["alerts"]]
            assert "ibkr_dropped" in codes
        finally:
            _ib_instances.pop(key, None)


# ── alerts list shape ──────────────────────────────────────────────────────

class TestAlertsShape:
    def test_alerts_always_list(self, client):
        r = client.post("/api/ibkr/heartbeat", json=_hb_request())
        body = r.json()
        assert isinstance(body["alerts"], list)

    def test_alert_fields_are_strings(self, client, monkeypatch):
        job = type("J", (), {"id": "monitor_tick"})()
        monkeypatch.setattr(main.scheduler, "get_jobs", lambda: [job])
        stale = datetime.now(timezone.utc) - timedelta(seconds=90)
        monkeypatch.setattr(
            main, "_last_monitor_tick_iso",
            stale.isoformat(timespec="seconds"), raising=False,
        )
        r = client.post("/api/ibkr/heartbeat", json=_hb_request())
        for a in r.json()["alerts"]:
            assert isinstance(a["level"], str)
            assert isinstance(a["code"], str)
            assert isinstance(a["message"], str)
            assert a["level"] in ("warning", "critical", "info")
