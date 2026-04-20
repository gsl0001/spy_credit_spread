"""Tests for core.scanner.Scanner (use_request.md §4)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.presets import PresetStore, ScannerPreset
from core.scanner import PresetRequired, Scanner, ScannerSignal


class _FakeStrategy:
    """Mimics BaseStrategy interface; entry/exit driven by class flags."""

    entry_signal = False
    exit_signal = False
    exit_reason = "fake"

    def compute_indicators(self, df, req):
        return df

    def check_entry(self, df, i, req):
        return self.entry_signal

    def check_exit(self, df, i, state, req):
        return (self.exit_signal, self.exit_reason)


class _FakePosition:
    def __init__(self, pid: str = "p1", meta: dict | None = None) -> None:
        self.id = pid
        self.meta = meta or {}


@pytest.fixture
def store(tmp_path: Path) -> PresetStore:
    s = PresetStore(path=tmp_path / "presets.json")
    s.save(ScannerPreset(
        name="test", ticker="SPY", strategy_name="fake",
        sizing_params={"fixed_contracts": 2, "max_allocation_cap": 500.0},
    ))
    return s


def _bars() -> pd.DataFrame:
    return pd.DataFrame({"Close": [100.0, 101.0, 102.0]})


def test_tick_without_preset_raises(store) -> None:
    sc = Scanner(store=store, bars_fetcher=lambda s: _bars(),
                 strategy_resolver=lambda n: _FakeStrategy)
    with pytest.raises(PresetRequired):
        sc.tick()


def test_entry_signal_emitted_and_dispatched(store) -> None:
    _FakeStrategy.entry_signal = True
    _FakeStrategy.exit_signal = False
    sc = Scanner(store=store, bars_fetcher=lambda s: _bars(),
                 strategy_resolver=lambda n: _FakeStrategy)
    sc.load_preset("test")
    received: list[ScannerSignal] = []
    sc.add_consumer(received.append)
    out = sc.tick()
    entries = [s for s in out if s.signal_type == "entry"]
    assert len(entries) == 1
    assert entries[0].fired is True
    assert entries[0].ticket["contracts"] == 2
    assert entries[0].ticket["preset_name"] == "test"
    assert received and received[0].fired is True


def test_exit_signal_per_open_position(store) -> None:
    _FakeStrategy.entry_signal = False
    _FakeStrategy.exit_signal = True
    _FakeStrategy.exit_reason = "ema_cross"
    sc = Scanner(
        store=store, bars_fetcher=lambda s: _bars(),
        strategy_resolver=lambda n: _FakeStrategy,
        open_positions_provider=lambda sym: [_FakePosition("p1"), _FakePosition("p2")],
    )
    sc.load_preset("test")
    out = sc.tick()
    exits = [s for s in out if s.signal_type == "exit"]
    assert len(exits) == 2
    assert all(s.reason == "strategy:ema_cross" for s in exits)
    assert {s.ticket["position_id"] for s in exits} == {"p1", "p2"}


def test_history_records_unfired_scans(store) -> None:
    _FakeStrategy.entry_signal = False
    _FakeStrategy.exit_signal = False
    sc = Scanner(store=store, bars_fetcher=lambda s: _bars(),
                 strategy_resolver=lambda n: _FakeStrategy)
    sc.load_preset("test")
    sc.tick()
    hist = sc.history()
    assert len(hist) == 1
    assert hist[0]["fired"] is False


def test_load_unknown_preset_raises(store) -> None:
    sc = Scanner(store=store)
    with pytest.raises(KeyError):
        sc.load_preset("nope")


def test_stop_clears_active_preset(store) -> None:
    sc = Scanner(store=store)
    sc.load_preset("test")
    assert sc.is_active
    sc.stop()
    assert not sc.is_active
