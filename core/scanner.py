"""Dedicated live scanner (use_request.md §4).

The scanner is the only component that decides "fire entry now" or
"fire exit now" on live data. It is intentionally decoupled from the
backtester. A preset is REQUIRED before any tick — there is no
implicit default.

Public surface
--------------
- ``ScannerSignal``  — frozen dataclass describing one entry/exit signal
- ``PresetRequired`` — raised when ``Scanner.tick()`` is called with no
  active preset (the API layer translates this into a 400)
- ``Scanner``        — orchestrator that resolves a preset, fetches
  bars, runs strategy ``check_entry`` / ``check_exit``, and dispatches
  signals + ticket data to consumers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from core.presets import PresetStore, ScannerPreset


logger = logging.getLogger(__name__)


def list_strategy_classes() -> dict:
    """Return ``{strategy_id: cls}`` for every known strategy.

    Single source of truth — Scanner, monitor's strategy-exit resolver,
    main's StrategyFactory, and the bars fetcher all read this. Adding
    a new strategy means editing one dict, not four.
    """
    try:
        from strategies.consecutive_days import ConsecutiveDaysStrategy
        from strategies.combo_spread import ComboSpreadStrategy
        from strategies.dryrun import DryRunStrategy
        from strategies.orb import OrbStrategy
    except Exception:  # noqa: BLE001
        return {}
    return {
        "consecutive_days": ConsecutiveDaysStrategy,
        "combo_spread": ComboSpreadStrategy,
        "dryrun": DryRunStrategy,
        "orb": OrbStrategy,
    }


def resolve_strategy_class(name: str):
    """Module-level strategy resolver shared by Scanner and the bars fetcher.

    Returns the strategy class for ``name`` (e.g. "dryrun"), or ``None`` if
    the name is unknown or the strategies package failed to import.
    Callers can read ``cls.BAR_SIZE`` / ``cls.HISTORY_PERIOD`` to pick the
    right historical-data request without instantiating the strategy.
    """
    return list_strategy_classes().get(name)


class PresetRequired(RuntimeError):
    """Raised when the scanner ticks without an active preset."""


@dataclass(frozen=True)
class ScannerSignal:
    """One signal produced by a scan tick.

    ``ticket`` carries the trade-ticket bundle the order desk needs
    (symbol/side/contracts/max_risk/preset_name) so downstream Live or
    Paper consumers can build an order without re-deriving anything.
    """
    time: str
    preset_name: str
    symbol: str
    signal_type: str          # "entry" | "exit"
    side: str                 # "buy" | "sell"
    fired: bool               # False = scanned, no signal
    reason: str = ""
    price: Optional[float] = None
    contracts: int = 0
    max_risk: float = 0.0
    ticket: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "preset_name": self.preset_name,
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "side": self.side,
            "fired": self.fired,
            "reason": self.reason,
            "price": self.price,
            "contracts": self.contracts,
            "max_risk": self.max_risk,
            "ticket": dict(self.ticket),
        }


# Type aliases for the pluggable callables that keep this module testable.
BarsFetcher = Callable[[str], Any]                     # symbol -> DataFrame
StrategyResolver = Callable[[str], Any]                # name -> BaseStrategy class
OpenPositionsProvider = Callable[[str], list[Any]]     # symbol -> [Position]
SignalConsumer = Callable[[ScannerSignal], None]       # downstream sink


class Scanner:
    """Stateful scanner. Singleton-friendly but accepts injected deps."""

    def __init__(
        self,
        *,
        store: Optional[PresetStore] = None,
        bars_fetcher: Optional[BarsFetcher] = None,
        strategy_resolver: Optional[StrategyResolver] = None,
        open_positions_provider: Optional[OpenPositionsProvider] = None,
        history_size: int = 100,
    ) -> None:
        self._store = store or PresetStore()
        self._bars_fetcher = bars_fetcher
        self._strategy_resolver = strategy_resolver or self._default_strategy_resolver
        self._open_positions_provider = open_positions_provider
        self._active_preset: Optional[ScannerPreset] = None
        self._history: list[ScannerSignal] = []
        self._history_size = max(10, int(history_size))
        self._consumers: list[SignalConsumer] = []

    # ── lifecycle ────────────────────────────────────────────────────

    @property
    def active_preset(self) -> Optional[ScannerPreset]:
        return self._active_preset

    @property
    def is_active(self) -> bool:
        return self._active_preset is not None

    def load_preset(self, name: str) -> ScannerPreset:
        preset = self._store.get(name)
        if preset is None:
            raise KeyError(f"preset not found: {name!r}")
        self._active_preset = preset
        return preset

    def stop(self) -> None:
        self._active_preset = None

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._history[:limit]]

    def add_consumer(self, fn: SignalConsumer) -> None:
        self._consumers.append(fn)

    # ── core tick ────────────────────────────────────────────────────

    def tick(self, *, now: Optional[datetime] = None) -> list[ScannerSignal]:
        """Run one scan pass for the active preset.

        Returns the list of signals emitted (entry + exit). Always
        records to history. Raises :class:`PresetRequired` if no
        preset is loaded — the UI must select one first.
        """
        if self._active_preset is None:
            raise PresetRequired("scanner has no active preset; load one first")
        preset = self._active_preset
        ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        out: list[ScannerSignal] = []

        if self._bars_fetcher is None:
            logger.debug("scanner.tick: no bars_fetcher injected; skipping")
            return out
        try:
            df = self._bars_fetcher(preset.ticker)
        except Exception as e:  # noqa: BLE001
            logger.warning("scanner bars_fetcher failed: %s", e)
            return out
        if df is None or len(df) == 0:
            return out

        strat_cls = self._strategy_resolver(preset.strategy_name)
        if strat_cls is None:
            logger.warning("scanner: unknown strategy %r", preset.strategy_name)
            return out
        strat = strat_cls()
        req = _ParamShim(preset)
        try:
            df = strat.compute_indicators(df, req)
        except Exception as e:  # noqa: BLE001
            logger.warning("scanner.compute_indicators failed: %s", e)
            return out
        i = len(df) - 1

        # ── entry signal ──
        try:
            entered = bool(strat.check_entry(df, i, req))
        except Exception as e:  # noqa: BLE001
            logger.warning("scanner.check_entry failed: %s", e)
            entered = False
        out.append(self._make_signal(
            ts, preset, df, i, signal_type="entry",
            side="buy", fired=entered,
            reason="strategy_entry" if entered else "no_signal",
        ))

        # ── exit signals — one per open position on this symbol ──
        if self._open_positions_provider is not None:
            try:
                positions = self._open_positions_provider(preset.ticker) or []
            except Exception as e:  # noqa: BLE001
                logger.warning("scanner.open_positions_provider failed: %s", e)
                positions = []
            for pos in positions:
                try:
                    entry_state = (pos.meta or {}).get("entry_state", {})
                    should_exit, reason = strat.check_exit(df, i, entry_state, req)
                except Exception as e:  # noqa: BLE001
                    logger.warning("scanner.check_exit failed: %s", e)
                    continue
                if should_exit:
                    out.append(self._make_signal(
                        ts, preset, df, i, signal_type="exit",
                        side="sell", fired=True,
                        reason=f"strategy:{reason or 'signal'}",
                        position_id=pos.id,
                    ))

        for sig in out:
            self._history.insert(0, sig)
        del self._history[self._history_size:]
        for sig in out:
            if sig.fired:
                self._dispatch(sig)
        return out

    # ── helpers ──────────────────────────────────────────────────────

    def _make_signal(
        self,
        ts: str,
        preset: ScannerPreset,
        df,
        i: int,
        *,
        signal_type: str,
        side: str,
        fired: bool,
        reason: str,
        position_id: Optional[str] = None,
    ) -> ScannerSignal:
        try:
            price = float(df["Close"].iloc[i]) if "Close" in df.columns else None
        except Exception:  # noqa: BLE001
            price = None
        contracts = int(preset.sizing_params.get("fixed_contracts", 1) or 1)
        max_risk = float(preset.sizing_params.get("max_allocation_cap", 0.0) or 0.0)
        ticket = {
            "symbol": preset.ticker,
            "side": side,
            "signal_type": signal_type,
            "preset_name": preset.name,
            "contracts": contracts,
            "max_risk": max_risk,
            "position_size_method": preset.position_size_method,
            "strategy_name": preset.strategy_name,
            "topology": preset.topology,
            "direction": preset.direction,
            "strategy_type": preset.strategy_type,
            "strike_width": preset.strike_width,
            "target_dte": preset.target_dte,
            "spread_cost_target": preset.spread_cost_target,
            "stop_loss_pct": preset.stop_loss_pct,
            "take_profit_pct": preset.take_profit_pct,
            "trailing_stop_pct": preset.trailing_stop_pct,
            "use_mark_to_market": preset.use_mark_to_market,
            "commission_per_contract": preset.commission_per_contract,
            "realism_factor": preset.realism_factor,
        }
        if position_id is not None:
            ticket["position_id"] = position_id
        return ScannerSignal(
            time=ts, preset_name=preset.name, symbol=preset.ticker,
            signal_type=signal_type, side=side, fired=fired,
            reason=reason, price=price, contracts=contracts,
            max_risk=max_risk, ticket=ticket,
        )

    def _dispatch(self, sig: ScannerSignal) -> None:
        for fn in self._consumers:
            try:
                fn(sig)
            except Exception as e:  # noqa: BLE001
                logger.warning("scanner consumer failed: %s", e)

    @staticmethod
    def _default_strategy_resolver(name: str):
        return resolve_strategy_class(name)


class _ParamShim:
    """Attribute-access wrapper around a ScannerPreset for BaseStrategy.req params.

    Provides a flat view over strategy_params, entry_filters, sizing_params,
    and top-level preset fields to match the BacktestRequest interface.
    """

    def __init__(self, preset: ScannerPreset) -> None:
        self._preset = preset

    def __getattr__(self, name: str) -> Any:
        # 1. Strategy-specific params (e.g. entry_red_days)
        if name in self._preset.strategy_params:
            return self._preset.strategy_params[name]

        # 2. Shared entry filters (e.g. ema_length, rsi_threshold)
        if name in self._preset.entry_filters:
            return self._preset.entry_filters[name]

        # 3. Sizing params (e.g. contracts_per_trade)
        # Note: mapping keys from sizing_params to backtest-style names if needed
        if name == "contracts_per_trade":
            return self._preset.sizing_params.get("fixed_contracts", 1)
        if name == "max_trade_cap":
            return self._preset.sizing_params.get("max_allocation_cap", 0.0)
        if name in self._preset.sizing_params:
            return self._preset.sizing_params[name]

        # 4. Top-level fields (e.g. ticker, direction, topology, strike_width)
        if hasattr(self._preset, name):
            return getattr(self._preset, name)

        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


__all__ = ["Scanner", "ScannerSignal", "PresetRequired"]
