"""Telegram bot — outgoing notifications + polling-loop command dispatch.

Uses ``unittest.mock`` to stub Telegram's HTTP API so no real network
calls happen. Verifies:
  * Bot is dormant when env vars aren't set (no surprise outbound calls).
  * Authorization gate: messages from unconfigured chats are dropped.
  * Command parsing handles bot mentions (``/cmd@MyBot``) and arguments.
  * Each registered command returns a sensible reply (no crashes on
    empty journal).
  * The two-step ``/flatten`` confirmation is enforced.
  * Notification helpers format the right text + emoji for each event type.
"""
from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def reset_bot():
    """Reset the polling offset and any stale module state between tests."""
    from core.telegram_bot import reset_polling_offset
    reset_polling_offset()
    yield
    reset_polling_offset()


def _fake_settings(token: str = "TEST_TOKEN", chat: str = "CHAT_42"):
    """Replace ``SETTINGS`` with a fresh frozen dataclass carrying the
    requested Telegram config. Patching individual fields fails because
    ``Settings`` is frozen — we rebind the whole singleton instead."""
    from core import settings as _settings_module
    from core.settings import TelegramSettings, Settings
    base = _settings_module.SETTINGS
    new = Settings(
        ibkr=base.ibkr,
        alpaca=base.alpaca,
        risk=base.risk,
        telegram=TelegramSettings(
            bot_token=token, chat_id=chat, poll_interval_seconds=3,
        ),
        journal_db_path=base.journal_db_path,
        log_dir=base.log_dir,
        log_level=base.log_level,
        event_calendar_file=base.event_calendar_file,
        notify_webhook_url=base.notify_webhook_url,
    )
    return patch.object(_settings_module, "SETTINGS", new)


def _http_response(payload: dict, status: int = 200):
    """Return a fake context-manager-style response object that ``urlopen`` yields."""
    body = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.read = MagicMock(return_value=body)
    return resp


# ── configured() ────────────────────────────────────────────────────────


def test_configured_false_without_env_vars():
    """When the token/chat env vars aren't set, the bot is dormant —
    notify() and poll_once() must short-circuit without any HTTP."""
    from core.telegram_bot import configured, notify, poll_once
    with _fake_settings(token="", chat=""):
        assert configured() is False
        # No HTTP calls should happen — patch urlopen so we'd see one if it did.
        with patch("urllib.request.urlopen") as mock_open:
            assert notify("hi") is False
            assert poll_once() == 0
            mock_open.assert_not_called()


def test_configured_true_with_env_vars():
    from core.telegram_bot import configured
    with _fake_settings():
        assert configured() is True


# ── notify() / send_message ─────────────────────────────────────────────


def test_notify_posts_to_telegram_send_message():
    from core.telegram_bot import notify
    with _fake_settings(), patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = _http_response({"ok": True}, status=200)
        assert notify("hello world") is True

    # One HTTP POST happened, to sendMessage, with our payload.
    assert mock_open.call_count == 1
    req = mock_open.call_args.args[0]
    assert "sendMessage" in req.full_url
    body = json.loads(req.data.decode("utf-8"))
    assert body["chat_id"] == "CHAT_42"
    assert body["text"] == "hello world"
    assert body["parse_mode"] == "Markdown"


def test_notify_retries_without_markdown_on_400():
    """Telegram returns 400 when markdown parsing fails (e.g. unescaped
    underscore in an order id). The bot should retry without parse_mode
    so the message still gets through."""
    from core.telegram_bot import notify
    import urllib.error

    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {}, BytesIO(b"")
            )
        return _http_response({"ok": True}, status=200)

    with _fake_settings(), patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert notify("text with _bad_ markdown") is True
    # First call had Markdown; second retry omitted it.
    assert call_count["n"] == 2


def test_notify_returns_false_on_persistent_failure():
    from core.telegram_bot import notify
    with _fake_settings(), patch(
        "urllib.request.urlopen", side_effect=Exception("network down")
    ):
        assert notify("oops") is False


# ── Authorization gate on incoming updates ──────────────────────────────


def test_poll_drops_messages_from_unauthorized_chat(reset_bot):
    """Anyone can DM a Telegram bot; only our configured chat_id may
    issue commands. Strangers must be silently ignored — no reply, no
    state change."""
    from core.telegram_bot import poll_once
    sent_messages = []

    def fake_urlopen(req_or_url, timeout=None):
        url = req_or_url.full_url if hasattr(req_or_url, "full_url") else req_or_url
        if "getUpdates" in url:
            return _http_response({
                "ok": True,
                "result": [{
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 999_999_999},  # NOT our chat
                        "text": "/status",
                    },
                }],
            })
        if "sendMessage" in url:
            sent_messages.append(json.loads(req_or_url.data.decode("utf-8")))
            return _http_response({"ok": True})
        return _http_response({"ok": True})

    with _fake_settings(chat="CHAT_42"), patch(
        "urllib.request.urlopen", side_effect=fake_urlopen
    ):
        processed = poll_once()

    # The update was consumed (offset advanced) but not acted on — no reply.
    assert processed == 0
    assert sent_messages == [], "must not send replies to unauthorized chats"


def test_poll_dispatches_to_registered_command(reset_bot):
    from core.telegram_bot import poll_once, register_command

    @register_command("__test_echo")
    def _echo(args):
        return f"echo: {' '.join(args)}"

    sent = []

    def fake_urlopen(req_or_url, timeout=None):
        url = req_or_url.full_url if hasattr(req_or_url, "full_url") else req_or_url
        if "getUpdates" in url:
            return _http_response({
                "ok": True,
                "result": [{
                    "update_id": 2,
                    "message": {
                        "chat": {"id": 42},
                        "text": "/__test_echo hello world",
                    },
                }],
            })
        if "sendMessage" in url:
            sent.append(json.loads(req_or_url.data.decode("utf-8")))
            return _http_response({"ok": True})
        return _http_response({"ok": True})

    with _fake_settings(chat="42"), patch(
        "urllib.request.urlopen", side_effect=fake_urlopen
    ):
        assert poll_once() == 1

    assert len(sent) == 1
    assert sent[0]["text"] == "echo: hello world"


def test_poll_handles_bot_mention_suffix(reset_bot):
    """Telegram appends ``@BotName`` when a command is sent in a group
    chat — strip it before dispatching."""
    from core.telegram_bot import poll_once, register_command

    @register_command("__test_mention")
    def _h(_args):
        return "ok"

    sent = []

    def fake_urlopen(req_or_url, timeout=None):
        url = req_or_url.full_url if hasattr(req_or_url, "full_url") else req_or_url
        if "getUpdates" in url:
            return _http_response({
                "ok": True,
                "result": [{
                    "update_id": 3,
                    "message": {"chat": {"id": 42}, "text": "/__test_mention@MyTradingBot"},
                }],
            })
        if "sendMessage" in url:
            sent.append(json.loads(req_or_url.data.decode("utf-8")))
        return _http_response({"ok": True})

    with _fake_settings(chat="42"), patch(
        "urllib.request.urlopen", side_effect=fake_urlopen
    ):
        poll_once()

    assert sent and sent[0]["text"] == "ok"


def test_poll_ignores_non_slash_messages(reset_bot):
    from core.telegram_bot import poll_once
    sent = []

    def fake_urlopen(req_or_url, timeout=None):
        url = req_or_url.full_url if hasattr(req_or_url, "full_url") else req_or_url
        if "getUpdates" in url:
            return _http_response({
                "ok": True,
                "result": [{
                    "update_id": 4,
                    "message": {"chat": {"id": 42}, "text": "hello bot"},
                }],
            })
        if "sendMessage" in url:
            sent.append(req_or_url)
        return _http_response({"ok": True})

    with _fake_settings(chat="42"), patch(
        "urllib.request.urlopen", side_effect=fake_urlopen
    ):
        poll_once()

    assert sent == []


def test_poll_replies_to_unknown_command(reset_bot):
    from core.telegram_bot import poll_once
    sent = []

    def fake_urlopen(req_or_url, timeout=None):
        url = req_or_url.full_url if hasattr(req_or_url, "full_url") else req_or_url
        if "getUpdates" in url:
            return _http_response({
                "ok": True,
                "result": [{
                    "update_id": 5,
                    "message": {"chat": {"id": 42}, "text": "/nonsense"},
                }],
            })
        if "sendMessage" in url:
            sent.append(json.loads(req_or_url.data.decode("utf-8")))
        return _http_response({"ok": True})

    with _fake_settings(chat="42"), patch(
        "urllib.request.urlopen", side_effect=fake_urlopen
    ):
        poll_once()

    assert sent and "Unknown command" in sent[0]["text"]
    assert "/help" in sent[0]["text"]


def test_offset_advances_so_same_update_isnt_processed_twice(reset_bot):
    from core.telegram_bot import poll_once
    seen_offsets = []

    def fake_urlopen(req_or_url, timeout=None):
        url = req_or_url.full_url if hasattr(req_or_url, "full_url") else req_or_url
        if "getUpdates" in url:
            seen_offsets.append(url)
            return _http_response({
                "ok": True,
                "result": [{
                    "update_id": 100,
                    "message": {"chat": {"id": 42}, "text": "/help"},
                }],
            })
        return _http_response({"ok": True})

    with _fake_settings(chat="42"), patch(
        "urllib.request.urlopen", side_effect=fake_urlopen
    ):
        poll_once()
        poll_once()

    # Second call must include offset=101 (last_id + 1) so update_id=100 isn't replayed.
    assert "offset=101" in seen_offsets[1]


# ── Built-in commands ──────────────────────────────────────────────────


def test_help_command_lists_built_in_commands():
    from core.telegram_bot import _COMMANDS
    reply = _COMMANDS["help"]([])
    for must_appear in ("/status", "/positions", "/pnl", "/flatten"):
        assert must_appear in reply


def test_status_command_handles_empty_journal(tmp_path):
    """The /status handler must not crash when the journal is fresh —
    it should report zeros, not raise."""
    from core.telegram_bot import _COMMANDS
    from core.journal import Journal
    j = Journal(str(tmp_path / "tg.db"))
    with patch("core.journal.get_journal", return_value=j):
        reply = _COMMANDS["status"]([])
    assert "Status" in reply
    assert "0" in reply  # open positions / trades / pnl all zero
    j.close()


def test_positions_command_empty():
    from core.telegram_bot import _COMMANDS
    from unittest.mock import MagicMock
    fake_j = MagicMock()
    fake_j.list_open.return_value = []
    with patch("core.journal.get_journal", return_value=fake_j):
        reply = _COMMANDS["positions"]([])
    assert "No open positions" in reply


def test_pnl_command_returns_today_and_history():
    from core.telegram_bot import _COMMANDS
    from unittest.mock import MagicMock
    fake_j = MagicMock()
    fake_j.today_realized_pnl.return_value = 123.45
    fake_j.today_trade_count.return_value = 3
    fake_j.history_pnl.return_value = [
        {"date": "2026-04-25", "pnl": 50.0, "trades": 2},
        {"date": "2026-04-24", "pnl": -30.0, "trades": 1},
    ]
    with patch("core.journal.get_journal", return_value=fake_j):
        reply = _COMMANDS["pnl"]([])
    assert "$123.45" in reply
    assert "2026-04-25" in reply
    assert "🟢" in reply or "🔴" in reply  # P&L emoji


def test_flatten_command_requires_confirmation():
    """First call must show a confirm prompt; only ``/flatten confirm``
    actually fires the kill switch. Two-step UX prevents fat-finger
    panic close from a phone notification."""
    from core.telegram_bot import _COMMANDS
    from unittest.mock import MagicMock
    fake_j = MagicMock()
    fake_pos = MagicMock()
    fake_pos.id = "p1"
    fake_j.list_open.return_value = [fake_pos]
    with patch("core.journal.get_journal", return_value=fake_j):
        reply = _COMMANDS["flatten"]([])
    assert "Confirm" in reply
    assert "/flatten confirm" in reply


def test_flatten_command_no_open_positions():
    from core.telegram_bot import _COMMANDS
    from unittest.mock import MagicMock
    fake_j = MagicMock()
    fake_j.list_open.return_value = []
    with patch("core.journal.get_journal", return_value=fake_j):
        reply = _COMMANDS["flatten"]([])
    assert "No open positions" in reply


# ── Notification formatters ────────────────────────────────────────────


def test_notify_entry_submitted_formats_correctly():
    from core import telegram_bot
    sent = []
    with patch.object(telegram_bot, "notify", side_effect=lambda t, **k: sent.append(t) or True):
        telegram_bot.notify_entry_submitted(
            "SPY", "BUY", 2, 2.50, idem_key="key123", preset="Conservative",
        )
    assert sent
    msg = sent[0]
    assert "SPY" in msg
    assert "BUY" in msg
    assert "×2" in msg
    assert "$2.50" in msg
    assert "Conservative" in msg
    assert "🟢" in msg


def test_notify_exit_filled_uses_red_for_loss():
    from core import telegram_bot
    sent = []
    with patch.object(telegram_bot, "notify", side_effect=lambda t, **k: sent.append(t) or True):
        telegram_bot.notify_exit_filled("SPY", -55.20, "stop_loss", position_id="p1")
    assert sent
    assert "🔴" in sent[0]
    assert "-$55.20" in sent[0]
    assert "stop_loss" in sent[0]


def test_notify_exit_filled_uses_green_for_profit():
    from core import telegram_bot
    sent = []
    with patch.object(telegram_bot, "notify", side_effect=lambda t, **k: sent.append(t) or True):
        telegram_bot.notify_exit_filled("SPY", 87.30, "take_profit", position_id="p1")
    assert sent
    assert "🟢" in sent[0]
    assert "+$87.30" in sent[0]


def test_notify_alert_uses_critical_emoji():
    from core import telegram_bot
    sent = []
    with patch.object(telegram_bot, "notify", side_effect=lambda t, **k: sent.append(t) or True):
        telegram_bot.notify_alert("critical", "IBKR socket dropped")
    assert sent and "🚨" in sent[0]
    assert "CRITICAL" in sent[0]
