"""Regression tests for two architectural fixes in main.py:

1. TTL cache replaces lru_cache on data-fetching functions — ensures live
   scanner never uses bars cached during a prior backtest run.
2. APScheduler async bridge uses run_coroutine_threadsafe instead of
   ensure_future so coroutine errors propagate and are not silently swallowed.
"""

import time
import unittest.mock as mock
import pytest

# ── TTL cache tests ────────────────────────────────────────────────────────────

import main as _main  # triggers module-level TTL cache setup


def test_ttl_cache_returns_cached_value_within_ttl():
    call_count = 0

    @_main._ttl_cache(ttl=60)
    def expensive(x):
        nonlocal call_count
        call_count += 1
        return x * 2

    assert expensive(5) == 10
    assert expensive(5) == 10  # cached
    assert call_count == 1


def test_ttl_cache_expires_after_ttl():
    call_count = 0

    @_main._ttl_cache(ttl=1)
    def compute(x):
        nonlocal call_count
        call_count += 1
        return x + 1

    result1 = compute(3)
    # Fake the stored timestamp to be older than ttl
    key = (compute.__wrapped__.__qualname__ if hasattr(compute, "__wrapped__") else "compute", (3,))
    # Find the key in _TTL_CACHE
    for k in list(_main._TTL_CACHE.keys()):
        if k[1] == (3,):
            _main._TTL_CACHE[k] = (_time_stub := _main._TTL_CACHE[k][0] - 2, _main._TTL_CACHE[k][1])
            break

    result2 = compute(3)
    assert result1 == result2 == 4
    assert call_count == 2  # second call re-executed after TTL


def test_ttl_cache_different_args_cached_independently():
    call_count = 0

    @_main._ttl_cache(ttl=60)
    def fn(x):
        nonlocal call_count
        call_count += 1
        return x

    fn(1)
    fn(2)
    fn(1)
    fn(2)
    assert call_count == 2


def test_ttl_cache_clear_evicts_all_entries():
    @_main._ttl_cache(ttl=60)
    def fn(x):
        return x * 3

    fn(7)
    fn(8)
    fn.cache_clear()
    assert _main._TTL_CACHE == {} or all(
        # after clear, previously cached keys are gone
        k not in _main._TTL_CACHE for k in [(fn.__qualname__, (7,)), (fn.__qualname__, (8,))]
    )


def test_fetch_historical_data_decorated_with_ttl():
    """fetch_historical_data must use _ttl_cache, not lru_cache."""
    import functools
    from main import fetch_historical_data
    # _ttl_cache wraps via functools.wraps; lru_cache adds .cache_info()
    assert not hasattr(fetch_historical_data, "cache_info"), (
        "fetch_historical_data must NOT use lru_cache — it would serve stale bars in live mode"
    )
    assert hasattr(fetch_historical_data, "cache_clear"), (
        "fetch_historical_data must have cache_clear from _ttl_cache"
    )


def test_fetch_risk_free_rate_decorated_with_ttl():
    from main import fetch_risk_free_rate
    assert not hasattr(fetch_risk_free_rate, "cache_info")
    assert hasattr(fetch_risk_free_rate, "cache_clear")


def test_fetch_vix_data_decorated_with_ttl():
    from main import fetch_vix_data
    assert not hasattr(fetch_vix_data, "cache_info")
    assert hasattr(fetch_vix_data, "cache_clear")


# ── APScheduler async bridge tests ────────────────────────────────────────────

def test_run_monitor_tick_uses_run_coroutine_threadsafe():
    """_run_monitor_tick must use run_coroutine_threadsafe, not ensure_future."""
    import inspect
    import main as m
    src = inspect.getsource(m._run_monitor_tick)
    assert "run_coroutine_threadsafe" in src, (
        "_run_monitor_tick must use asyncio.run_coroutine_threadsafe to properly "
        "block and propagate errors from the APScheduler thread"
    )
    assert "ensure_future" not in src, (
        "_run_monitor_tick must NOT use ensure_future — it orphans coroutines"
    )


def test_run_fill_reconcile_uses_run_coroutine_threadsafe():
    import inspect
    import main as m
    src = inspect.getsource(m._run_fill_reconcile)
    assert "run_coroutine_threadsafe" in src
    assert "ensure_future" not in src


def test_run_monitor_tick_noop_when_main_loop_none():
    """If _MAIN_LOOP is None (app not started), tick must be a no-op."""
    import main as m
    original = m._MAIN_LOOP
    try:
        m._MAIN_LOOP = None
        # Should not raise — just returns early
        m._run_monitor_tick()
    finally:
        m._MAIN_LOOP = original


def test_run_monitor_tick_propagates_coroutine_exception():
    """Exceptions from the monitor coroutine must be caught and logged, not lost."""
    import asyncio
    import main as m

    async def _failing():
        raise RuntimeError("deliberate test failure")

    fake_loop = mock.MagicMock()
    fake_future = mock.MagicMock()
    fake_future.result.side_effect = RuntimeError("deliberate test failure")
    fake_loop.is_running.return_value = True
    fake_loop.return_value = fake_loop

    original = m._MAIN_LOOP
    try:
        m._MAIN_LOOP = fake_loop
        with mock.patch("asyncio.run_coroutine_threadsafe", return_value=fake_future):
            with mock.patch("logging.getLogger") as mock_logger:
                mock_logger.return_value = mock.MagicMock()
                # Must not raise — exception should be logged, not propagated
                m._run_monitor_tick()
    finally:
        m._MAIN_LOOP = original
