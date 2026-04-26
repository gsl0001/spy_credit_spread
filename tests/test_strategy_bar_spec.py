"""Verify Option B: each strategy class declares the bar size + history
period it needs, and ``main._resolve_bar_spec`` picks them up so the
preset bars-fetcher requests the right yfinance interval / IBKR
barSizeSetting.

Why these tests matter:
  - DryRunStrategy is the only intraday strategy; if the resolver picks
    daily bars for it, ``check_entry`` (which compares wall-clock times)
    can never fire.
  - ConsecutiveDays / ComboSpread are daily-bar strategies; if the
    resolver gave them 5m bars, ``days_held = i - entry_idx`` would
    silently mean "5-minute intervals since entry".
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.base import BaseStrategy
from strategies.dryrun import DryRunStrategy
from strategies.consecutive_days import ConsecutiveDaysStrategy
from strategies.combo_spread import ComboSpreadStrategy


# ── Class-level contract ────────────────────────────────────────────────


def test_base_strategy_default_bar_spec_is_daily():
    """The base class default keeps existing daily strategies working."""
    assert BaseStrategy.BAR_SIZE == "1 day"
    assert BaseStrategy.HISTORY_PERIOD == "1mo"


def test_dryrun_overrides_to_intraday():
    """Dryrun must declare 5m bars or its ts.time() entries never match."""
    assert DryRunStrategy.BAR_SIZE == "5 mins"
    assert DryRunStrategy.HISTORY_PERIOD == "5d"


@pytest.mark.parametrize(
    "cls",
    [ConsecutiveDaysStrategy, ComboSpreadStrategy],
)
def test_daily_strategies_inherit_default(cls):
    """Daily strategies must keep daily bars — ``days_held`` semantics
    in ``check_exit`` rely on bar count == day count."""
    assert cls.BAR_SIZE == "1 day"


# ── Resolver wiring ─────────────────────────────────────────────────────


def test_resolve_bar_spec_dryrun_returns_5min(monkeypatch):
    """The fetcher's _resolve_bar_spec helper picks up dryrun's override."""
    import main

    preset = MagicMock()
    preset.strategy_name = "dryrun"
    bar_size, period = main._resolve_bar_spec(preset)
    assert bar_size == "5 mins"
    assert period == "5d"


def test_resolve_bar_spec_consecutive_days_is_daily():
    import main

    preset = MagicMock()
    preset.strategy_name = "consecutive_days"
    bar_size, period = main._resolve_bar_spec(preset)
    assert bar_size == "1 day"
    assert period == "1mo"


def test_resolve_bar_spec_unknown_strategy_falls_back_to_daily():
    """If the strategy can't be resolved, daily is the safe default —
    daily strategies dominate the codebase."""
    import main

    preset = MagicMock()
    preset.strategy_name = "no_such_strategy"
    bar_size, period = main._resolve_bar_spec(preset)
    assert bar_size == "1 day"
    assert period == "1mo"


def test_resolve_bar_spec_handles_none_preset():
    """When no preset is active, the resolver returns daily defaults
    so the fetcher can still answer ad-hoc calls."""
    import main

    bar_size, period = main._resolve_bar_spec(None)
    assert bar_size == "1 day"
    assert period == "1mo"


# ── Yfinance interval mapping ───────────────────────────────────────────


def test_yf_interval_map_round_trips_known_bar_sizes():
    """All canonical IBKR bar sizes must map to a valid yfinance interval."""
    import main

    expected = {
        "1 min":   "1m",
        "5 mins":  "5m",
        "15 mins": "15m",
        "30 mins": "30m",
        "1 hour":  "60m",
        "1 day":   "1d",
    }
    for ib, yf in expected.items():
        assert main._YF_INTERVAL_MAP[ib] == yf


# ── Integration: bars-fetcher dispatches the right yfinance call ───────


@patch("yfinance.download")
def test_preset_fetcher_uses_5m_for_dryrun(mock_yf_download):
    """End-to-end: when dryrun is the active preset, the fetcher should
    invoke ``yf.download`` with ``interval='5m'`` — proving the wiring
    from BaseStrategy.BAR_SIZE all the way through to the data call.
    """
    import pandas as pd
    import main

    # Empty result is fine; we only assert on the call args.
    mock_yf_download.return_value = pd.DataFrame()

    preset = MagicMock()
    preset.strategy_name = "dryrun"
    preset.ticker = "SPY"
    preset.fetch_only_live = False

    with patch.object(main._preset_scanner, "_active_preset", preset):
        main._preset_bars_fetcher("SPY")

    assert mock_yf_download.called
    _, kwargs = mock_yf_download.call_args
    assert kwargs.get("interval") == "5m", (
        f"dryrun should request 5m bars, got {kwargs.get('interval')!r}"
    )
    # 5m intraday is capped at 60d by yfinance — anything wider would
    # raise. The fetcher should request a bounded period.
    assert kwargs.get("period") in {"5d", "60d"}


@patch("yfinance.download")
def test_preset_fetcher_uses_1d_for_consecutive_days(mock_yf_download):
    """Daily strategies must continue to receive ``interval='1d'`` —
    regression guard against accidentally promoting all presets to intraday.
    """
    import pandas as pd
    import main

    mock_yf_download.return_value = pd.DataFrame()

    preset = MagicMock()
    preset.strategy_name = "consecutive_days"
    preset.ticker = "SPY"
    preset.fetch_only_live = False

    with patch.object(main._preset_scanner, "_active_preset", preset):
        main._preset_bars_fetcher("SPY")

    _, kwargs = mock_yf_download.call_args
    assert kwargs.get("interval") == "1d"
    assert kwargs.get("period") == "1mo"
