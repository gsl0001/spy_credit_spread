"""Tests for ``core.logger`` (I1).

Covers
------
* JSON-line shape (time, level, logger, message, event_type always present)
* ``log_event`` merges extras and preserves schema
* Sensitive keys are redacted
* Daily file name is correct
* ``get_logger`` is idempotent
* Exception serialisation is safe
* Unicode / non-JSON-serialisable values don't crash the handler
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.logger import (
    JsonFormatter,
    configure_root_logging,
    get_logger,
    log_event,
    reset_logging,
)


@pytest.fixture(autouse=True)
def _clean_logging():
    """Ensure no handler leakage between tests."""
    reset_logging()
    yield
    reset_logging()


def _read_log(tmp_path: Path) -> list[dict]:
    """Read every `.jsonl` file in tmp_path and return parsed records."""
    records: list[dict] = []
    for p in sorted(tmp_path.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


class TestJsonFormatter:
    def test_basic_shape(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="p", lineno=1,
            msg="hello", args=(), exc_info=None,
        )
        out = formatter.format(record)
        parsed = json.loads(out)
        assert parsed["message"] == "hello"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test"
        assert "time" in parsed
        assert parsed["event_type"] == "log"

    def test_extra_fields_merged(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="p", lineno=1,
            msg="m", args=(), exc_info=None,
        )
        record.order_id = "42"
        record.symbol = "SPY"
        parsed = json.loads(formatter.format(record))
        assert parsed["order_id"] == "42"
        assert parsed["symbol"] == "SPY"

    def test_exception_serialised(self):
        formatter = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="p", lineno=1,
                msg="err", args=(), exc_info=sys.exc_info(),
            )
            parsed = json.loads(formatter.format(record))
            assert "exception" in parsed
            assert "ValueError" in parsed["exception"]


class TestGetLogger:
    def test_creates_file_in_target_dir(self, tmp_path):
        log = get_logger("test_create", log_dir=tmp_path, console=False)
        log.info("file handler test")
        for h in log.handlers:
            h.flush()
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert files[0].name == f"{today}.jsonl"

    def test_is_idempotent(self, tmp_path):
        log1 = get_logger("test_idem", log_dir=tmp_path, console=False)
        n_handlers_before = len(log1.handlers)
        log2 = get_logger("test_idem", log_dir=tmp_path, console=False)
        assert log1 is log2
        assert len(log2.handlers) == n_handlers_before

    def test_json_line_shape(self, tmp_path):
        log = get_logger("test_shape", log_dir=tmp_path, console=False)
        log.info("scanner started")
        for h in log.handlers:
            h.flush()
        records = _read_log(tmp_path)
        assert len(records) == 1
        r = records[0]
        assert r["message"] == "scanner started"
        assert r["level"] == "INFO"
        assert r["logger"] == "test_shape"
        assert r["event_type"] == "log"
        # ISO-8601 timestamp
        assert datetime.fromisoformat(r["time"].replace("Z", "+00:00"))

    def test_level_filter(self, tmp_path):
        log = get_logger(
            "test_level", log_dir=tmp_path, level=logging.WARNING, console=False,
        )
        log.info("ignored")
        log.warning("kept")
        for h in log.handlers:
            h.flush()
        records = _read_log(tmp_path)
        messages = [r["message"] for r in records]
        assert "ignored" not in messages
        assert "kept" in messages


class TestLogEvent:
    def test_event_type_present(self, tmp_path):
        log = get_logger("test_event", log_dir=tmp_path, console=False)
        log_event(log, "order_submitted", order_id="42", symbol="SPY", limit=3.45)
        for h in log.handlers:
            h.flush()
        records = _read_log(tmp_path)
        assert len(records) == 1
        r = records[0]
        assert r["event_type"] == "order_submitted"
        assert r["order_id"] == "42"
        assert r["symbol"] == "SPY"
        assert r["limit"] == 3.45

    def test_custom_message(self, tmp_path):
        log = get_logger("test_msg", log_dir=tmp_path, console=False)
        log_event(log, "risk_rejected", message="daily loss cap hit", pct=-2.1)
        for h in log.handlers:
            h.flush()
        records = _read_log(tmp_path)
        r = records[0]
        assert r["message"] == "daily loss cap hit"
        assert r["event_type"] == "risk_rejected"
        assert r["pct"] == -2.1

    def test_warning_level(self, tmp_path):
        log = get_logger("test_warn", log_dir=tmp_path, console=False)
        log_event(log, "stale_data", level=logging.WARNING, source="yf")
        for h in log.handlers:
            h.flush()
        records = _read_log(tmp_path)
        assert records[0]["level"] == "WARNING"


class TestRedaction:
    def test_api_key_masked(self, tmp_path):
        log = get_logger("test_redact", log_dir=tmp_path, console=False)
        log_event(
            log, "connect",
            api_key="SECRET_KEY_1234",
            api_secret="hunter2",
            password="p@ss",
            token="tok123",
        )
        for h in log.handlers:
            h.flush()
        records = _read_log(tmp_path)
        r = records[0]
        assert r["api_key"] == "***"
        assert r["api_secret"] == "***"
        assert r["password"] == "***"
        assert r["token"] == "***"
        assert "SECRET_KEY_1234" not in json.dumps(r)

    def test_non_sensitive_unchanged(self, tmp_path):
        log = get_logger("test_nonsens", log_dir=tmp_path, console=False)
        log_event(log, "order", symbol="SPY", contracts=3, price=4.25)
        for h in log.handlers:
            h.flush()
        records = _read_log(tmp_path)
        r = records[0]
        assert r["symbol"] == "SPY"
        assert r["contracts"] == 3
        assert r["price"] == 4.25


class TestRobustness:
    def test_unicode_message(self, tmp_path):
        log = get_logger("test_unicode", log_dir=tmp_path, console=False)
        log.info("启动 — 扫描 🚀")
        for h in log.handlers:
            h.flush()
        records = _read_log(tmp_path)
        assert records[0]["message"] == "启动 — 扫描 🚀"

    def test_non_serialisable_extra_coerced(self, tmp_path):
        """Non-JSON values (like datetime) are coerced via str() — no crash."""
        log = get_logger("test_coerce", log_dir=tmp_path, console=False)
        now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
        log_event(log, "tick", when=now, obj=object())
        for h in log.handlers:
            h.flush()
        records = _read_log(tmp_path)
        r = records[0]
        assert "when" in r
        assert "2026-04-16" in r["when"]

    def test_exception_captured(self, tmp_path):
        log = get_logger("test_exc", log_dir=tmp_path, console=False)
        try:
            raise RuntimeError("kapow")
        except RuntimeError:
            log.exception("broker_error")
        for h in log.handlers:
            h.flush()
        records = _read_log(tmp_path)
        r = records[0]
        assert r["level"] == "ERROR"
        assert "exception" in r
        assert "RuntimeError" in r["exception"]


class TestRetention:
    def test_backup_count_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOG_BACKUP_COUNT", "7")
        log = get_logger("test_retain", log_dir=tmp_path, console=False)
        # find the file handler
        file_handler = next(
            h for h in log.handlers if isinstance(h, logging.handlers.TimedRotatingFileHandler)
        )
        assert file_handler.backupCount == 7


# Needed for TimedRotatingFileHandler isinstance check above.
import logging.handlers  # noqa: E402


class TestConfigureRootLogging:
    def test_root_propagation_catches_module_loggers(self, tmp_path):
        """A `logging.getLogger("core.foo")` call after configure_root_logging
        should produce JSON output because logs propagate to root."""
        configure_root_logging(log_dir=tmp_path, console=False)

        module_logger = logging.getLogger("core.some_module")
        module_logger.info("propagated")
        for h in logging.getLogger().handlers:
            h.flush()

        records = _read_log(tmp_path)
        assert any(
            r["logger"] == "core.some_module" and r["message"] == "propagated"
            for r in records
        ), f"no propagated record found in {records}"

    def test_configure_is_idempotent(self, tmp_path):
        root1 = configure_root_logging(log_dir=tmp_path, console=False)
        n1 = len(root1.handlers)
        root2 = configure_root_logging(log_dir=tmp_path, console=False)
        assert len(root2.handlers) == n1
        assert root1 is root2

    def test_event_type_via_log_event_on_module_logger(self, tmp_path):
        configure_root_logging(log_dir=tmp_path, console=False)
        module_logger = logging.getLogger("core.order")
        log_event(module_logger, "order_filled", order_id="42", price=4.10)
        for h in logging.getLogger().handlers:
            h.flush()
        records = _read_log(tmp_path)
        match = [r for r in records if r.get("event_type") == "order_filled"]
        assert match
        assert match[0]["order_id"] == "42"
        assert match[0]["price"] == 4.10
