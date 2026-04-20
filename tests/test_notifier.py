"""Tests for I2: daily digest notifier.

Covers:
* ``build_daily_digest`` — correct fields, win-rate calc, empty-day edge case.
* Payload formatters for Slack, Discord, and generic URLs.
* ``send_webhook`` — 2xx returns True, non-2xx returns False, network errors
  return False (never raise).
* ``send_daily_digest`` — skips gracefully when URL is empty; calls
  ``send_webhook`` with the right payload type; records a journal event on
  success.
* ``/api/notify/digest`` endpoint — returns ``{"sent": bool, "digest": ...}``.
"""
from __future__ import annotations

import json
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from core.journal import reset_journal_for_tests
from core.notifier import (
    _build_payload,
    _format_discord,
    _format_generic,
    _format_slack,
    build_daily_digest,
    send_daily_digest,
    send_webhook,
)


# ── fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def journal(tmp_path):
    j = reset_journal_for_tests(str(tmp_path / "n.db"))
    yield j
    j.close()


@pytest.fixture
def client(tmp_path):
    reset_journal_for_tests(str(tmp_path / "nc.db"))
    with TestClient(main.app) as c:
        yield c


def _seed_today(journal, pnl: float, trades: int, wins: int, losses: int) -> None:
    """Write a daily_pnl row directly — faster than open+close positions."""
    journal._conn.execute(
        "INSERT OR REPLACE INTO daily_pnl (date, realized, trades, win_count, loss_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (date.today().isoformat(), pnl, trades, wins, losses),
    )


# ── build_daily_digest ─────────────────────────────────────────────────────

class TestBuildDailyDigest:
    def test_empty_day_returns_zeros(self, journal):
        d = build_daily_digest(journal)
        assert d["today_pnl"] == 0.0
        assert d["today_trades"] == 0
        assert d["win_count"] == 0
        assert d["loss_count"] == 0
        assert d["open_positions"] == 0
        assert d["open_summaries"] == []
        assert d["win_rate_pct"] == 0.0

    def test_positive_day_fields(self, journal):
        _seed_today(journal, 312.50, 3, 2, 1)
        d = build_daily_digest(journal)
        assert d["today_pnl"] == pytest.approx(312.50)
        assert d["today_trades"] == 3
        assert d["win_count"] == 2
        assert d["loss_count"] == 1
        assert d["win_rate_pct"] == pytest.approx(66.7)

    def test_negative_day(self, journal):
        _seed_today(journal, -125.00, 2, 0, 2)
        d = build_daily_digest(journal)
        assert d["today_pnl"] == pytest.approx(-125.0)

    def test_date_field_is_today(self, journal):
        d = build_daily_digest(journal)
        assert d["date"] == date.today().isoformat()

    def test_generated_at_is_iso_string(self, journal):
        d = build_daily_digest(journal)
        from datetime import datetime
        # Should parse without raising.
        dt = datetime.fromisoformat(d["generated_at"])
        assert dt.tzinfo is not None  # timezone-aware

    def test_win_rate_is_zero_when_no_trades(self, journal):
        d = build_daily_digest(journal)
        assert d["win_rate_pct"] == 0.0


# ── payload formatters ─────────────────────────────────────────────────────

@pytest.fixture
def sample_digest():
    return {
        "date": "2026-04-17",
        "generated_at": "2026-04-17T21:05:00+00:00",
        "today_pnl": 150.0,
        "today_trades": 2,
        "win_count": 2,
        "loss_count": 0,
        "win_rate_pct": 100.0,
        "open_positions": 1,
        "open_summaries": [
            {
                "id": "p1",
                "symbol": "SPY",
                "direction": "bull",
                "contracts": 2,
                "entry_cost": -300.0,
                "expiry": "2026-04-24",
                "state": "open",
            }
        ],
    }


class TestPayloadFormatters:
    def test_slack_has_text_key(self, sample_digest):
        p = _format_slack(sample_digest)
        assert "text" in p
        assert isinstance(p["text"], str)
        assert "SPY" in p["text"]
        assert "+$150.00" in p["text"]

    def test_discord_has_embeds(self, sample_digest):
        p = _format_discord(sample_digest)
        assert "embeds" in p
        assert len(p["embeds"]) == 1
        embed = p["embeds"][0]
        assert "title" in embed
        assert "color" in embed
        assert isinstance(embed["fields"], list)

    def test_discord_green_for_profit(self, sample_digest):
        p = _format_discord(sample_digest)
        assert p["embeds"][0]["color"] == 0x2ECC71

    def test_discord_red_for_loss(self, sample_digest):
        d = {**sample_digest, "today_pnl": -50.0}
        p = _format_discord(d)
        assert p["embeds"][0]["color"] == 0xE74C3C

    def test_generic_has_text_and_data(self, sample_digest):
        p = _format_generic(sample_digest)
        assert "text" in p
        assert "data" in p
        assert p["data"] == sample_digest

    def test_build_payload_routes_discord(self, sample_digest):
        p = _build_payload("https://discord.com/api/webhooks/abc/xyz", sample_digest)
        assert "embeds" in p

    def test_build_payload_routes_slack(self, sample_digest):
        p = _build_payload("https://hooks.slack.com/services/T/B/x", sample_digest)
        assert "text" in p
        assert "embeds" not in p

    def test_build_payload_routes_generic(self, sample_digest):
        p = _build_payload("https://example.com/hook", sample_digest)
        assert "data" in p


# ── send_webhook ───────────────────────────────────────────────────────────

class _StubHTTPHandler(BaseHTTPRequestHandler):
    """Minimal handler that echoes back the configured status code."""
    _response_code = 204

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        # Store for inspection.
        _StubHTTPHandler.last_body = json.loads(body)
        self.send_response(self._response_code)
        self.end_headers()

    def log_message(self, *a, **kw):  # suppress output
        pass


@pytest.fixture(scope="module")
def stub_server_200():
    _StubHTTPHandler._response_code = 204
    srv = HTTPServer(("127.0.0.1", 0), _StubHTTPHandler)
    t = Thread(target=srv.serve_forever, daemon=True)
    t.start()
    host, port = srv.server_address
    yield f"http://{host}:{port}/hook"
    srv.shutdown()


@pytest.fixture(scope="module")
def stub_server_500():
    class _500Handler(_StubHTTPHandler):
        _response_code = 500
    srv = HTTPServer(("127.0.0.1", 0), _500Handler)
    t = Thread(target=srv.serve_forever, daemon=True)
    t.start()
    host, port = srv.server_address
    yield f"http://{host}:{port}/hook"
    srv.shutdown()


class TestSendWebhook:
    def test_2xx_returns_true(self, stub_server_200):
        assert send_webhook(stub_server_200, {"text": "hello"}) is True

    def test_payload_delivered_as_json(self, stub_server_200):
        send_webhook(stub_server_200, {"key": "value"})
        assert _StubHTTPHandler.last_body == {"key": "value"}

    def test_5xx_returns_false(self, stub_server_500):
        assert send_webhook(stub_server_500, {"text": "hi"}) is False

    def test_empty_url_returns_false(self):
        assert send_webhook("", {"text": "hi"}) is False

    def test_unreachable_url_returns_false(self):
        # Port 1 is almost never open.
        assert send_webhook("http://127.0.0.1:1/x", {"text": "hi"}) is False

    def test_invalid_url_returns_false(self):
        assert send_webhook("not-a-url", {"text": "hi"}) is False


# ── send_daily_digest ──────────────────────────────────────────────────────

class TestSendDailyDigest:
    def test_empty_url_returns_false_no_raise(self, journal):
        result = send_daily_digest(journal, url="")
        assert result is False

    def test_skips_when_no_url_in_settings(self, journal, monkeypatch):
        import core.settings as cs
        from types import SimpleNamespace
        real = cs.SETTINGS
        fake = SimpleNamespace(
            notify_webhook_url="",
            risk=real.risk,
            ibkr=real.ibkr,
            journal_db_path=real.journal_db_path,
            log_dir=getattr(real, "log_dir", "logs"),
            log_level=getattr(real, "log_level", "INFO"),
            event_calendar_file=getattr(real, "event_calendar_file", ""),
        )
        monkeypatch.setattr(cs, "SETTINGS", fake)
        assert send_daily_digest(journal) is False

    def test_success_records_journal_event(self, journal, stub_server_200):
        _seed_today(journal, 200.0, 2, 2, 0)
        ok = send_daily_digest(journal, url=stub_server_200)
        assert ok is True
        events = journal.recent_events(limit=5)
        assert any(e["kind"] == "digest_sent" for e in events)

    def test_failure_does_not_record_event(self, journal, stub_server_500):
        ok = send_daily_digest(journal, url=stub_server_500)
        assert ok is False
        events = journal.recent_events(limit=5)
        assert not any(e["kind"] == "digest_sent" for e in events)

    def test_sends_discord_format_for_discord_url(self, journal, stub_server_200):
        """When URL contains 'discord.com', the payload must have 'embeds'."""
        # Monkeypatch send_webhook to capture payload.
        captured: list[dict] = []

        def _mock(url, payload, **kw):
            captured.append(payload)
            return True

        with patch("core.notifier.send_webhook", side_effect=_mock):
            send_daily_digest(journal, url="https://discord.com/api/webhooks/1/2")

        assert captured
        assert "embeds" in captured[0]


# ── /api/notify/digest endpoint ────────────────────────────────────────────

class TestDigestEndpoint:
    def test_returns_digest_regardless_of_webhook(self, client):
        r = client.post("/api/notify/digest")
        assert r.status_code == 200
        body = r.json()
        assert "digest" in body
        assert "sent" in body
        assert isinstance(body["sent"], bool)

    def test_digest_has_required_fields(self, client):
        r = client.post("/api/notify/digest")
        d = r.json()["digest"]
        for key in (
            "date", "today_pnl", "today_trades", "win_count",
            "loss_count", "win_rate_pct", "open_positions", "open_summaries",
        ):
            assert key in d, f"Missing key: {key}"

    def test_sent_false_when_url_not_configured(self, client):
        """NOTIFY_WEBHOOK_URL is empty by default in tests."""
        r = client.post("/api/notify/digest")
        assert r.json()["sent"] is False
