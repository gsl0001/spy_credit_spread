"""Leader election via advisory file lock (I6).

If a user accidentally starts two server instances against the same data
directory (common: one `python main.py` in a shell, another in an IDE run
configuration), **both** monitors would try to place exit orders for the
same open positions. On a win they race to fill; on a fail they double-
cancel. Either outcome leaks money.

This module provides a best-effort *advisory* lock so only one process at
a time is the "leader" that runs the monitor/fill-watcher tick. Others
continue to serve HTTP but their monitor ticks become no-ops.

Design
------
* POSIX-only: uses ``fcntl.flock(LOCK_EX | LOCK_NB)``. Falls back to a
  *noop* lock on Windows (warns once).
* **Advisory** — a third-party process that doesn't honour the lock can
  still clobber state. Within this codebase every scheduler-driven tick
  checks ``is_leader()`` before doing work.
* Lock file records the owner process (pid, host, timestamp) so the UI
  can show *who* is the leader.
* Lock is released automatically on process exit (kernel closes the FD).

Usage
-----
>>> from core.leader import try_acquire_leadership, is_leader
>>> if try_acquire_leadership("data/monitor.lock"):
...     start_monitor()
... else:
...     log.warning("another instance is the leader; monitor ticks will no-op")

Or as a tick guard:

>>> def monitor_tick():
...     if not is_leader():
...         return  # a peer is already running the loop
...     ...
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeaderInfo:
    pid: int
    host: str
    acquired_at: float      # unix timestamp
    lock_path: str


# Module-level singleton state.
_lock_fd: Optional[int] = None
_current_info: Optional[LeaderInfo] = None
_fcntl_warned = False


def _have_fcntl() -> bool:
    """Return True if ``fcntl.flock`` is available (POSIX)."""
    global _fcntl_warned
    try:
        import fcntl  # noqa: F401
        return True
    except ImportError:
        if not _fcntl_warned:
            log.warning(
                "fcntl unavailable (Windows?); leader election disabled — "
                "every instance will consider itself leader."
            )
            _fcntl_warned = True
        return False


def try_acquire_leadership(lock_path: str = "data/monitor.lock") -> bool:
    """Attempt to acquire the monitor leader lock.

    Returns
    -------
    True  — this process is now the leader.
    False — another process holds the lock. The caller should skip
            scheduler registration for the monitor loop.

    The lock is released automatically when the process exits. Callers
    may also call :func:`release_leadership` explicitly for clean
    shutdown.
    """
    global _lock_fd, _current_info

    if _current_info is not None:
        # Already the leader in this process.
        return True

    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # On non-POSIX, degrade gracefully — act as leader but warn.
    if not _have_fcntl():
        _current_info = LeaderInfo(
            pid=os.getpid(),
            host=socket.gethostname(),
            acquired_at=time.time(),
            lock_path=str(path),
        )
        return True

    import fcntl  # safe: _have_fcntl() passed

    # Open with O_CREAT so a missing file is created, but don't truncate
    # — we want to preserve any crash-leaked metadata until we overwrite.
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another process holds the lock.
        os.close(fd)
        held_by = _peek_lock_file(path)
        log.info(
            "leader-lock busy: held_by=%s; this instance will not run monitor",
            held_by,
        )
        return False
    except OSError as e:
        os.close(fd)
        log.warning("leader-lock flock failed (%s); treating as leader", e)
        _current_info = LeaderInfo(
            pid=os.getpid(),
            host=socket.gethostname(),
            acquired_at=time.time(),
            lock_path=str(path),
        )
        return True

    info = LeaderInfo(
        pid=os.getpid(),
        host=socket.gethostname(),
        acquired_at=time.time(),
        lock_path=str(path),
    )
    # Write owner metadata so peers can report WHO holds the lock.
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(asdict(info)).encode("utf-8"))
        os.fsync(fd)
    except OSError as e:
        log.warning("leader-lock metadata write failed: %s", e)

    _lock_fd = fd
    _current_info = info
    log.info("leader-lock acquired: pid=%d host=%s", info.pid, info.host)
    return True


def is_leader() -> bool:
    """Return True if this process currently holds the leader lock."""
    return _current_info is not None


def current_leader_info() -> Optional[LeaderInfo]:
    """Return the LeaderInfo for this process when it's the leader, else None."""
    return _current_info


def release_leadership() -> None:
    """Release the leader lock (called at clean shutdown).

    Safe to call when we are not the leader — becomes a no-op.
    """
    global _lock_fd, _current_info

    if _lock_fd is None:
        _current_info = None
        return

    try:
        import fcntl
        fcntl.flock(_lock_fd, fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass

    try:
        os.close(_lock_fd)
    except OSError:
        pass

    _lock_fd = None
    _current_info = None
    log.info("leader-lock released")


def _peek_lock_file(path: Path) -> dict:
    """Read owner metadata from the lock file without trying to lock it.

    Used to report *who* holds the lock when our acquire fails.
    """
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return {"pid": None, "host": None, "note": "lock_file_empty"}
        return json.loads(raw)
    except (OSError, ValueError):
        return {"pid": None, "host": None, "note": "lock_file_unreadable"}


__all__ = [
    "LeaderInfo",
    "try_acquire_leadership",
    "is_leader",
    "current_leader_info",
    "release_leadership",
]
