"""Hardened yfinance wrappers.

yfinance keeps a SQLite cache (timezones, cookies) in
``~/Library/Caches/py-yfinance`` (macOS) or ``~/.cache/py-yfinance``. When the
process is killed mid-write or two processes contend for it, the cache ends up
with stale ``-shm`` / ``-wal`` files and every call raises::

    OperationalError('unable to open database file')

Once that happens, ``yf.download`` and ``yf.Ticker(...).fast_info`` fail forever
until the cache is reset. These helpers catch that error, wipe the cache, and
retry — and also expose ``reset_cache()`` so the FastAPI lifespan can clean up
known-bad state on startup.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_CANDIDATES = [
    Path.home() / "Library" / "Caches" / "py-yfinance",  # macOS
    Path.home() / ".cache" / "py-yfinance",              # Linux
]


def cache_dirs() -> list[Path]:
    return [p for p in _CACHE_CANDIDATES if p.exists()]


def reset_cache() -> list[str]:
    """Delete WAL/SHM remnants that cause SQLite ``unable to open database file``.

    Conservative: only removes ``*-wal`` / ``*-shm`` sidecar files (which are
    safe to drop — SQLite recreates them) plus ``*.db`` files that are 0 bytes
    (corrupt/empty). The main ``tkr-tz.db`` content is preserved when valid.
    """
    removed: list[str] = []
    for cache_dir in cache_dirs():
        try:
            for entry in cache_dir.iterdir():
                if not entry.is_file():
                    continue
                name = entry.name
                try:
                    if name.endswith("-wal") or name.endswith("-shm"):
                        entry.unlink()
                        removed.append(str(entry))
                    elif name.endswith(".db") and entry.stat().st_size == 0:
                        entry.unlink()
                        removed.append(str(entry))
                except OSError as exc:
                    logger.warning("yf cache: could not remove %s: %s", entry, exc)
        except OSError as exc:
            logger.warning("yf cache: could not list %s: %s", cache_dir, exc)
    if removed:
        logger.info("yf cache: reset %d stale file(s): %s", len(removed), removed)
    return removed


def _hard_reset_cache() -> None:
    """Last-resort: nuke entire cache directory. yfinance recreates it."""
    for cache_dir in cache_dirs():
        try:
            shutil.rmtree(cache_dir, ignore_errors=True)
            logger.warning("yf cache: hard-reset %s", cache_dir)
        except OSError as exc:
            logger.warning("yf cache: hard-reset failed for %s: %s", cache_dir, exc)


def _is_sqlite_cache_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "unable to open database file" in msg
        or "database is locked" in msg
        or "database disk image is malformed" in msg
    )


def safe_download(symbol: str, **kwargs: Any):
    """``yf.download(symbol, **kwargs)`` with cache-corruption recovery."""
    import yfinance as yf
    try:
        return yf.download(symbol, **kwargs)
    except Exception as exc:  # noqa: BLE001
        if not _is_sqlite_cache_error(exc):
            raise
        logger.warning("yf.download(%s) hit cache error: %s — resetting cache and retrying", symbol, exc)
        reset_cache()
        try:
            return yf.download(symbol, **kwargs)
        except Exception as exc2:  # noqa: BLE001
            if not _is_sqlite_cache_error(exc2):
                raise
            _hard_reset_cache()
            return yf.download(symbol, **kwargs)


def safe_fast_info(symbol: str) -> dict:
    """``yf.Ticker(symbol).fast_info`` with cache-corruption recovery."""
    import yfinance as yf
    try:
        info = yf.Ticker(symbol).fast_info
        return dict(info) if hasattr(info, "keys") else info
    except Exception as exc:  # noqa: BLE001
        if not _is_sqlite_cache_error(exc):
            raise
        logger.warning("yf.fast_info(%s) hit cache error: %s — resetting cache and retrying", symbol, exc)
        reset_cache()
        try:
            info = yf.Ticker(symbol).fast_info
            return dict(info) if hasattr(info, "keys") else info
        except Exception as exc2:  # noqa: BLE001
            if not _is_sqlite_cache_error(exc2):
                raise
            _hard_reset_cache()
            info = yf.Ticker(symbol).fast_info
            return dict(info) if hasattr(info, "keys") else info


def raise_fd_limit(target: int = 4096) -> tuple[int, int]:
    """Raise the soft RLIMIT_NOFILE so we don't hit ``Too many open files``.

    Returns ``(old_soft, new_soft)``. Best-effort; logs and returns current
    values on any failure (Windows, sandboxed environments).
    """
    try:
        import resource  # POSIX only
    except ImportError:
        return (0, 0)
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        new_soft = min(target, hard) if hard != resource.RLIM_INFINITY else target
        if new_soft > soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))
            logger.info("RLIMIT_NOFILE raised: %d -> %d (hard=%s)", soft, new_soft, hard)
            return (soft, new_soft)
        return (soft, soft)
    except (ValueError, OSError) as exc:
        logger.warning("could not raise RLIMIT_NOFILE: %s", exc)
        return (0, 0)
