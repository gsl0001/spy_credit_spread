"""Runtime flags controlling broker auto-reconnect behavior.

Set via the header toggles in the UI (`POST /api/connection/auto`). When a flag
is False, the corresponding broker's auto-connect / auto-reconnect paths must
short-circuit instead of dialing the broker.

Persisted to ``data/connection_flags.json`` so uvicorn ``--reload`` doesn't
silently re-enable a broker the user explicitly turned off.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, bool] = {"ibkr": True, "moomoo": True}
_PATH = Path(__file__).resolve().parent.parent / "data" / "connection_flags.json"

_lock = threading.Lock()


def _load() -> dict[str, bool]:
    try:
        raw = json.loads(_PATH.read_text())
        return {k: bool(raw.get(k, v)) for k, v in _DEFAULTS.items()}
    except FileNotFoundError:
        return dict(_DEFAULTS)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("connection_flags: could not load %s: %s", _PATH, exc)
        return dict(_DEFAULTS)


def _save(flags: dict[str, bool]) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(flags, indent=2))
    except OSError as exc:
        logger.warning("connection_flags: could not save %s: %s", _PATH, exc)


_flags: dict[str, bool] = _load()


def is_auto_enabled(broker: str) -> bool:
    with _lock:
        return _flags.get(broker, True)


def set_auto_enabled(broker: str, enabled: bool) -> bool:
    if broker not in _DEFAULTS:
        raise ValueError(f"unknown broker: {broker}")
    with _lock:
        _flags[broker] = bool(enabled)
        _save(_flags)
        return _flags[broker]


def snapshot() -> dict[str, bool]:
    with _lock:
        return dict(_flags)
