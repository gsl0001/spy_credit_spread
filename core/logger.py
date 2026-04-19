"""Structured logging (I1).

Writes JSON-lines to ``logs/YYYY-MM-DD.jsonl`` with daily rotation and a
14-day retention window. Every log line is a single JSON object on its own
line so ``jq``/``grep``/observability pipelines can parse them without
regexes.

Usage
-----
>>> from core.logger import get_logger, log_event
>>> log = get_logger(__name__)
>>> log.info("monitor tick ok")
>>> log_event(log, "order_submitted", order_id="42", symbol="SPY", limit=3.45)

The helper ``log_event`` keeps a stable schema — the *event_type* field is
always present, letting downstream consumers filter cleanly.

Design constraints
------------------
* stdlib only — no runtime dependency on ``python-json-logger``.
* Idempotent — calling ``get_logger`` twice returns the same handler set.
* Deterministic in tests — a ``reset_logging()`` helper clears handlers so
  tests can re-initialise against a temporary directory.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Optional

# ── module-level configuration ─────────────────────────────────────────────
# Sensitive field names — values are replaced with "***" before serialisation.
_REDACT_KEYS: frozenset[str] = frozenset({
    "api_key", "api_secret", "password", "token", "secret",
    "alpaca_api_key", "alpaca_api_secret", "ibkr_password",
})

# Track which logger names we've already wired up, so ``get_logger`` is idempotent.
_CONFIGURED: set[str] = set()

# Retention window — keep 14 daily files by default. Override via env.
_DEFAULT_BACKUP_COUNT = 14


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with sensitive keys masked."""
    if not payload:
        return payload
    clean: dict[str, Any] = {}
    for k, v in payload.items():
        if k.lower() in _REDACT_KEYS:
            clean[k] = "***"
        else:
            clean[k] = v
    return clean


class JsonFormatter(logging.Formatter):
    """Format a ``LogRecord`` as a single JSON object.

    Extra fields attached to the record (via ``logger.info(msg, extra={...})``
    or via ``log_event``) are merged into the top-level object. The reserved
    fields ``time``, ``level``, ``logger``, ``message``, ``event_type`` are
    always present.
    """

    # Fields the stdlib logging framework sets on every record; we don't
    # want them echoed back under their ugly names.
    _STD_ATTRS: frozenset[str] = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge any structured extras attached to the record.
        for key, value in record.__dict__.items():
            if key in self._STD_ATTRS or key.startswith("_"):
                continue
            if key in payload:
                # Never overwrite the reserved fields above.
                continue
            payload[key] = value

        # Guarantee event_type key for downstream filters.
        payload.setdefault("event_type", payload.get("event_type", "log"))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        try:
            return json.dumps(payload, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            # Last-resort: force string fallback so logging never breaks runtime.
            return json.dumps({
                "time": payload["time"],
                "level": payload["level"],
                "logger": payload["logger"],
                "message": str(payload.get("message")),
                "event_type": "log_format_failed",
            })


def _log_dir() -> Path:
    """Resolve the log directory, honouring the live ``SETTINGS`` object."""
    try:
        from core.settings import SETTINGS
        d = Path(SETTINGS.log_dir)
    except Exception:
        d = Path(os.environ.get("LOG_DIR", "logs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_level() -> int:
    """Resolve log level from settings/env — defaults to INFO."""
    try:
        from core.settings import SETTINGS
        name = (SETTINGS.log_level or "INFO").upper()
    except Exception:
        name = os.environ.get("LOG_LEVEL", "INFO").upper()
    return getattr(logging, name, logging.INFO)


def _backup_count() -> int:
    try:
        return int(os.environ.get("LOG_BACKUP_COUNT", _DEFAULT_BACKUP_COUNT))
    except (TypeError, ValueError):
        return _DEFAULT_BACKUP_COUNT


def get_logger(
    name: str = "spy_credit_spread",
    *,
    log_dir: Optional[Path] = None,
    level: Optional[int] = None,
    console: bool = True,
) -> logging.Logger:
    """Return a configured logger. Idempotent by name + file path.

    Parameters
    ----------
    name
        Dotted module name; maps to the file section in logs.
    log_dir
        Override directory (used by tests). Default: ``SETTINGS.log_dir``.
    level
        Override level. Default: ``SETTINGS.log_level``.
    console
        Attach a console handler in addition to the file handler.
    """
    logger = logging.getLogger(name)
    cache_key = f"{name}:{log_dir or ''}"
    if cache_key in _CONFIGURED:
        return logger

    logger.setLevel(level if level is not None else _log_level())
    logger.propagate = False  # don't double-log via root

    formatter = JsonFormatter()

    # File handler — rotates at midnight UTC, keeps N backups.
    target_dir = Path(log_dir) if log_dir is not None else _log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = target_dir / f"{today}.jsonl"
    file_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        utc=True,
        backupCount=_backup_count(),
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler — same JSON format, easier to grep in dev.
    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    _CONFIGURED.add(cache_key)
    return logger


def log_event(
    logger: logging.Logger,
    event_type: str,
    *,
    level: int = logging.INFO,
    message: str = "",
    **extra: Any,
) -> None:
    """Emit a structured event with a stable schema.

    Reserved fields (``event_type``, ``time``, ``level``, ``logger``,
    ``message``) are always present; ``**extra`` is merged into the JSON
    payload after redaction of sensitive keys.

    Examples
    --------
    >>> log_event(log, "order_filled", order_id="42", symbol="SPY", fill=3.40)
    """
    safe_extra = _redact(extra)
    msg = message or event_type
    # ``extra`` must avoid clashing with stdlib LogRecord reserved names.
    safe_extra["event_type"] = event_type
    logger.log(level, msg, extra=safe_extra)


def configure_root_logging(
    *,
    log_dir: Optional[Path] = None,
    level: Optional[int] = None,
    console: bool = True,
) -> logging.Logger:
    """Attach JSON handlers to the **root** logger.

    Modules that use ``logging.getLogger(__name__)`` automatically flow
    through root when ``propagate=True`` (the default). Call this once at
    process start-up (``main.py`` on import) so every existing logger
    acquires the JSON file + console handlers without code changes.

    Idempotent — calling it twice does not stack handlers.
    """
    cache_key = f"__root__:{log_dir or ''}"
    root = logging.getLogger()
    if cache_key in _CONFIGURED:
        return root

    root.setLevel(level if level is not None else _log_level())

    formatter = JsonFormatter()
    target_dir = Path(log_dir) if log_dir is not None else _log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = target_dir / f"{today}.jsonl"

    file_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        utc=True,
        backupCount=_backup_count(),
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    _CONFIGURED.add(cache_key)
    return root


def reset_logging() -> None:
    """Remove every handler from every configured logger.

    Tests use this between cases to avoid handler leakage across instances.
    """
    for name in list(_CONFIGURED):
        if name.startswith("__root__"):
            lg = logging.getLogger()
        else:
            base_name = name.split(":", 1)[0]
            lg = logging.getLogger(base_name)
        for h in list(lg.handlers):
            try:
                h.close()
            except Exception:
                pass
            lg.removeHandler(h)
    _CONFIGURED.clear()


__all__ = [
    "get_logger",
    "log_event",
    "configure_root_logging",
    "reset_logging",
    "JsonFormatter",
]
