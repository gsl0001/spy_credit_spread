"""Tests for core.presets.PresetStore (use_request.md §4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.presets import (
    PresetStore,
    ScannerPreset,
    moomoo_auto_execute_presets,
    single_moomoo_auto_execute_preset,
)


@pytest.fixture
def store(tmp_path: Path) -> PresetStore:
    return PresetStore(path=tmp_path / "presets.json")


def test_empty_store_returns_empty_list(store: PresetStore) -> None:
    assert store.list() == []
    assert store.get("missing") is None
    assert store.delete("missing") is False


def test_save_then_get(store: PresetStore) -> None:
    p = ScannerPreset(name="bull-spy", ticker="SPY",
                      strategy_params={"entry_red_days": 3})
    store.save(p)
    got = store.get("bull-spy")
    assert got is not None
    assert got.strategy_params == {"entry_red_days": 3}


def test_save_replaces_by_name(store: PresetStore) -> None:
    store.save(ScannerPreset(name="x", timing_value=10))
    store.save(ScannerPreset(name="x", timing_value=60))
    assert len(store.list()) == 1
    assert store.get("x").timing_value == 60


def test_delete_removes_preset(store: PresetStore) -> None:
    store.save(ScannerPreset(name="kill-me"))
    assert store.delete("kill-me") is True
    assert store.get("kill-me") is None


def test_from_dict_requires_name() -> None:
    with pytest.raises(ValueError):
        ScannerPreset.from_dict({"ticker": "QQQ"})


def test_round_trip_persistence(tmp_path: Path) -> None:
    path = tmp_path / "presets.json"
    s1 = PresetStore(path=path)
    s1.save(ScannerPreset(name="persist-test", ticker="QQQ"))
    s2 = PresetStore(path=path)  # fresh handle, same file
    assert s2.get("persist-test").ticker == "QQQ"


def test_moomoo_auto_execute_presets_only_returns_enabled_moomoo(store: PresetStore) -> None:
    store.save(ScannerPreset(name="paper-live", broker="moomoo", auto_execute=True))
    store.save(ScannerPreset(name="manual-moomoo", broker="moomoo", auto_execute=False))
    store.save(ScannerPreset(name="ibkr-auto", broker="ibkr", auto_execute=True))

    presets = moomoo_auto_execute_presets(store.path)

    assert [p.name for p in presets] == ["paper-live"]


def test_single_moomoo_auto_execute_preset_requires_exactly_one(store: PresetStore) -> None:
    preset, names = single_moomoo_auto_execute_preset(store.path)
    assert preset is None
    assert names == []

    store.save(ScannerPreset(name="one", broker="moomoo", auto_execute=True))
    preset, names = single_moomoo_auto_execute_preset(store.path)
    assert preset is not None
    assert preset.name == "one"
    assert names == ["one"]

    store.save(ScannerPreset(name="two", broker="moomoo", auto_execute=True))
    preset, names = single_moomoo_auto_execute_preset(store.path)
    assert preset is None
    assert names == ["one", "two"]
