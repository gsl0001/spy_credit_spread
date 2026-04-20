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
    scan_interval_seconds: int = 30
    notes: str = ""

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
            scan_interval_seconds=int(data.get("scan_interval_seconds", 30)),
            notes=str(data.get("notes", "")),
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
