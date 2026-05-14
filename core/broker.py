"""Broker protocol + in-process registry.

All system components (scanner, monitor, fill_watcher) use `get_broker()` /
`register_broker()` rather than importing broker modules directly.  Both IBKR
and moomoo implement `BrokerProtocol`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


# ── Data transfer types ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class LegSpec:
    expiry: str        # YYYYMMDD
    strike: float
    right: str         # "C" | "P"
    price: float       # limit price per share


@dataclass(frozen=True)
class SpreadRequest:
    symbol: str
    long_leg: LegSpec
    short_leg: LegSpec
    qty: int
    net_debit_limit: float   # max total debit in dollars
    position_id: str         # for journaling
    client_order_id: str     # idempotency key
    # Per-leg NBBO width tolerance for the broker-side preflight check.
    # Mirrors the higher-level chain-quality gate's ``chain_max_bid_ask_pct``
    # so a preset that opens the gate to 1.0 (moomoo OpenD wide quotes)
    # isn't silently re-blocked by a hardcoded 0.50 at the broker layer.
    max_bid_ask_pct: float = 0.50


# ── Protocol ──────────────────────────────────────────────────────────────────

@runtime_checkable
class BrokerProtocol(Protocol):
    def is_alive(self) -> bool: ...
    async def get_account_summary(self) -> dict[str, Any]: ...
    async def get_positions(self) -> list[dict[str, Any]]: ...
    async def get_live_price(self, symbol: str) -> dict[str, Any]: ...
    async def place_spread(self, req: SpreadRequest) -> dict[str, Any]: ...
    async def close_position(self, position_id: str, legs: list[dict]) -> dict[str, Any]: ...
    async def cancel_order(self, order_id: str) -> dict[str, Any]: ...


# ── Registry ──────────────────────────────────────────────────────────────────

class BrokerNotConnected(RuntimeError):
    pass


_registry: dict[str, BrokerProtocol] = {}


def register_broker(name: str, instance: BrokerProtocol) -> None:
    """Register a connected broker instance under `name`."""
    _registry[name] = instance


def unregister_broker(name: str) -> None:
    """Remove a broker from the registry (called on disconnect)."""
    _registry.pop(name, None)


def get_broker(name: str) -> BrokerProtocol:
    """Return a registered broker or raise `BrokerNotConnected`."""
    broker = _registry.get(name)
    if broker is None:
        raise BrokerNotConnected(f"Broker '{name}' is not connected.")
    return broker


def broker_is_connected(name: str) -> bool:
    """Return True if broker is registered and reports alive."""
    broker = _registry.get(name)
    return broker is not None and broker.is_alive()
