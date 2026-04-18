"""Tests for ``core.leader`` — file-based leader election (I6)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from core import leader


@pytest.fixture(autouse=True)
def _reset_leader():
    """Ensure each test starts with no lock held."""
    leader.release_leadership()
    yield
    leader.release_leadership()


class TestBasicAcquire:
    def test_acquire_returns_true(self, tmp_path):
        lock_path = tmp_path / "monitor.lock"
        ok = leader.try_acquire_leadership(str(lock_path))
        assert ok is True
        assert leader.is_leader()

    def test_acquire_is_idempotent_in_process(self, tmp_path):
        """Second call from the same process should return True without
        re-locking (we are already the leader)."""
        lock_path = tmp_path / "monitor.lock"
        assert leader.try_acquire_leadership(str(lock_path))
        assert leader.try_acquire_leadership(str(lock_path))  # idempotent
        assert leader.is_leader()

    def test_metadata_written(self, tmp_path):
        lock_path = tmp_path / "monitor.lock"
        assert leader.try_acquire_leadership(str(lock_path))
        # Need to re-read the file from disk to see what was written.
        content = lock_path.read_text(encoding="utf-8").strip()
        assert content, "lock file should have JSON metadata"
        meta = json.loads(content)
        assert meta["pid"] == os.getpid()
        assert "host" in meta
        assert meta["acquired_at"] > 0

    def test_info_object_populated(self, tmp_path):
        lock_path = tmp_path / "monitor.lock"
        assert leader.try_acquire_leadership(str(lock_path))
        info = leader.current_leader_info()
        assert info is not None
        assert info.pid == os.getpid()
        assert info.lock_path == str(lock_path)


class TestRelease:
    def test_release_clears_state(self, tmp_path):
        lock_path = tmp_path / "monitor.lock"
        leader.try_acquire_leadership(str(lock_path))
        leader.release_leadership()
        assert not leader.is_leader()
        assert leader.current_leader_info() is None

    def test_release_without_acquire_is_safe(self):
        # Should not raise
        leader.release_leadership()
        assert not leader.is_leader()

    def test_reacquire_after_release(self, tmp_path):
        lock_path = tmp_path / "monitor.lock"
        leader.try_acquire_leadership(str(lock_path))
        leader.release_leadership()
        assert leader.try_acquire_leadership(str(lock_path))
        assert leader.is_leader()


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl required")
class TestMutualExclusion:
    def test_second_process_blocked(self, tmp_path):
        """Spawn a subprocess that holds the lock, then verify this process
        cannot acquire."""
        lock_path = tmp_path / "monitor.lock"

        # Child process holds the lock for 3 seconds.
        child_script = textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
            from core import leader
            ok = leader.try_acquire_leadership({str(lock_path)!r})
            print("OK" if ok else "FAIL", flush=True)
            time.sleep(3)
        """)
        child = subprocess.Popen(
            [sys.executable, "-c", child_script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            # Wait for child to confirm it has the lock.
            first = child.stdout.readline().strip()
            assert first == "OK", (
                f"child did not acquire lock: {first!r} / "
                f"stderr={child.stderr.read() if child.stderr else ''!r}"
            )

            # Now from this (parent) process, acquire must fail.
            ok = leader.try_acquire_leadership(str(lock_path))
            assert ok is False, "parent process acquired a lock already held by child"
            assert not leader.is_leader()
        finally:
            child.terminate()
            child.wait(timeout=5)

    def test_reacquire_after_child_dies(self, tmp_path):
        """Once the holder exits, the next acquire succeeds."""
        lock_path = tmp_path / "monitor.lock"

        child_script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
            from core import leader
            ok = leader.try_acquire_leadership({str(lock_path)!r})
            print("OK" if ok else "FAIL", flush=True)
            # Exit immediately — kernel releases the flock.
        """)
        child = subprocess.run(
            [sys.executable, "-c", child_script],
            capture_output=True, text=True, timeout=10,
        )
        assert "OK" in child.stdout

        # Parent must now be able to acquire.
        ok = leader.try_acquire_leadership(str(lock_path))
        assert ok is True
        assert leader.is_leader()


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl required")
class TestLockFileInspection:
    def test_peek_when_another_holds_lock(self, tmp_path):
        """When acquire fails, the lock file still contains the holder's metadata."""
        lock_path = tmp_path / "monitor.lock"

        child_script = textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
            from core import leader
            leader.try_acquire_leadership({str(lock_path)!r})
            print("OK", flush=True)
            time.sleep(3)
        """)
        child = subprocess.Popen(
            [sys.executable, "-c", child_script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            assert child.stdout.readline().strip() == "OK"
            # This parent cannot acquire — but CAN read the metadata.
            meta = leader._peek_lock_file(lock_path)
            assert "pid" in meta
            assert meta["pid"] == child.pid
        finally:
            child.terminate()
            child.wait(timeout=5)


class TestDirCreation:
    def test_creates_parent_dir(self, tmp_path):
        """Missing parent directory is auto-created."""
        lock_path = tmp_path / "deeply" / "nested" / "monitor.lock"
        assert not lock_path.parent.exists()
        ok = leader.try_acquire_leadership(str(lock_path))
        assert ok is True
        assert lock_path.parent.is_dir()
        assert lock_path.exists()
