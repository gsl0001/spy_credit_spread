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
    broker: str = "ibkr"  # "ibkr" | "moomoo"

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

    # Smoke-test escape hatch: skip the event-blackout gate. Production presets
    # MUST leave this False — CPI/FOMC/NFP filters exist to avoid event risk.
    bypass_event_blackout: bool = False

    # Stored for self-description / drift detection only. The scanner's
    # bar fetcher reads BAR_SIZE from the strategy class — this field
    # is a redundancy check enforced at save time (see PresetStore.save).
    bar_size: str = "1 day"

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
            broker=str(data.get("broker", "ibkr")),
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
            bypass_event_blackout=bool(data.get("bypass_event_blackout", False)),
            bar_size=str(data.get("bar_size", "1 day")),
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
        """Insert or replace by name.

        Enforces two soft invariants at save time:
          - bar_size drift: preset.bar_size must match the strategy
            class's BAR_SIZE. Mismatch raises ValueError because the
            scanner reads BAR_SIZE from the class — a stale preset
            would silently route to the wrong harness.
          - vetting gate: refuse to save a preset whose strategy is
            marked VETTING_RESULT='rejected' on the class. Skill bar
            says: "may not become a moomoo preset until its backtest
            stats clear an explicit bar" — this enforces it.
        """
        import logging
        log = logging.getLogger(__name__)
        try:
            from core.scanner import resolve_strategy_class
            cls = resolve_strategy_class(preset.strategy_name)
        except Exception:
            cls = None
        if cls is not None:
            actual_bar = getattr(cls, "BAR_SIZE", "1 day")
            if preset.bar_size and preset.bar_size != actual_bar:
                raise ValueError(
                    f"preset {preset.name!r} bar_size={preset.bar_size!r} "
                    f"drifted from strategy {preset.strategy_name!r} "
                    f"class BAR_SIZE={actual_bar!r}. Update the preset or "
                    f"the strategy class so they agree."
                )
            verdict = getattr(cls, "VETTING_RESULT", "pending")
            if verdict == "rejected":
                raise ValueError(
                    f"strategy {preset.strategy_name!r} is marked "
                    f"VETTING_RESULT='rejected'. Cannot create a preset "
                    f"for a rejected strategy — re-vet first."
                )
            if verdict == "pending":
                log.warning(
                    "preset %s saved for VETTING_RESULT='pending' strategy %s "
                    "— ensure backtest cleared the skill bar before live use.",
                    preset.name, preset.strategy_name,
                )
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


def moomoo_auto_execute_presets(path: Optional[Path] = None) -> list[ScannerPreset]:
    """Return moomoo presets currently allowed to auto-fire."""
    return [
        p for p in PresetStore(path).list()
        if p.broker == "moomoo" and p.auto_execute
    ]


def single_moomoo_auto_execute_preset(
    path: Optional[Path] = None,
) -> tuple[Optional[ScannerPreset], list[str]]:
    """Return the sole moomoo auto preset, or names causing the gate to fail."""
    presets = moomoo_auto_execute_presets(path)
    names = [p.name for p in presets]
    if len(presets) != 1:
        return None, names
    return presets[0], names


__all__ = [
    "ScannerPreset",
    "PresetStore",
    "DEFAULT_PRESETS_PATH",
    "moomoo_auto_execute_presets",
    "single_moomoo_auto_execute_preset",
]
