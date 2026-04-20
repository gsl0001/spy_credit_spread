"""Scanner preset persistence (use_request.md §4).

A preset is a saved bundle of:
  - ticker + strategy + strategy params
  - entry filters
  - position-sizing config
  - scan interval

Stored as JSON at ``config/presets.json``. Atomic writes via tempfile +
``os.replace`` so the scanner never reads a half-flushed file.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Optional


DEFAULT_PRESETS_PATH = Path("config/presets.json")


@dataclass(frozen=True)
class ScannerPreset:
    name: str
    ticker: str = "SPY"
    strategy_name: str = "consecutive_days"
    strategy_params: dict = field(default_factory=dict)
    entry_filters: dict = field(default_factory=dict)
    position_size_method: str = "fixed"  # fixed | dynamic_risk | targeted_spread
    sizing_params: dict = field(default_factory=dict)
    timing_mode: str = "interval"
    timing_value: int = 60
    notes: str = ""
    auto_execute: bool = False
    fetch_only_live: bool = False

    # Options trading parameters (parity with BacktestRequest)
    topology: str = "vertical_spread"
    direction: str = "bull"
    strategy_type: str = "bull_call"
    strike_width: float = 5.0
    target_dte: int = 14
    spread_cost_target: float = 250.0
    stop_loss_pct: float = 50.0
    take_profit_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    use_mark_to_market: bool = True
    commission_per_contract: float = 0.65
    realism_factor: float = 1.15

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScannerPreset":
        if "name" not in data or not data["name"]:
            raise ValueError("preset 'name' is required")
        return cls(
            name=str(data["name"]),
            ticker=str(data.get("ticker", "SPY")),
            strategy_name=str(data.get("strategy_name", "consecutive_days")),
            strategy_params=dict(data.get("strategy_params") or {}),
            entry_filters=dict(data.get("entry_filters") or {}),
            position_size_method=str(data.get("position_size_method", "fixed")),
            sizing_params=dict(data.get("sizing_params") or {}),
            timing_mode=str(data.get("timing_mode", "interval")),
            timing_value=int(data.get("timing_value", data.get("scan_interval_seconds", 60))),
            notes=str(data.get("notes", "")),
            auto_execute=bool(data.get("auto_execute", False)),
            fetch_only_live=bool(data.get("fetch_only_live", False)),
            topology=str(data.get("topology", "vertical_spread")),
            direction=str(data.get("direction", "bull")),
            strategy_type=str(data.get("strategy_type", "bull_call")),
            strike_width=float(data.get("strike_width", 5.0)),
            target_dte=int(data.get("target_dte", 14)),
            spread_cost_target=float(data.get("spread_cost_target", 250.0)),
            stop_loss_pct=float(data.get("stop_loss_pct", 50.0)),
            take_profit_pct=float(data.get("take_profit_pct", 0.0)),
            trailing_stop_pct=float(data.get("trailing_stop_pct", 0.0)),
            use_mark_to_market=bool(data.get("use_mark_to_market", True)),
            commission_per_contract=float(data.get("commission_per_contract", 0.65)),
            realism_factor=float(data.get("realism_factor", 1.15)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PresetStore:
    """Thin JSON-file repository for ScannerPreset objects."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_PRESETS_PATH

    def _read_all(self) -> list[ScannerPreset]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        out: list[ScannerPreset] = []
        for item in raw or []:
            try:
                out.append(ScannerPreset.from_dict(item))
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def _write_all(self, presets: list[ScannerPreset]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([p.to_dict() for p in presets], indent=2)
        # Atomic write via temp file + os.replace.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".presets_", suffix=".json",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_path, self.path)
        except Exception:
            # Best-effort cleanup if replace failed.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list(self) -> list[ScannerPreset]:
        return self._read_all()

    def get(self, name: str) -> Optional[ScannerPreset]:
        for p in self._read_all():
            if p.name == name:
                return p
        return None

    def save(self, preset: ScannerPreset) -> ScannerPreset:
        """Insert or replace by name."""
        presets = [p for p in self._read_all() if p.name != preset.name]
        presets.append(preset)
        self._write_all(presets)
        return preset

    def delete(self, name: str) -> bool:
        presets = self._read_all()
        kept = [p for p in presets if p.name != name]
        if len(kept) == len(presets):
            return False
        self._write_all(kept)
        return True


__all__ = ["ScannerPreset", "PresetStore", "DEFAULT_PRESETS_PATH"]
