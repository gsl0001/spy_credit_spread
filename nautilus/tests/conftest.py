"""Stub nautilus_trader + nautilus_futu before any test imports them.

The nautilus/ sub-project requires its own isolated Python 3.12 + venv.
These stubs let the *pure-logic* unit tests run in the parent project's
Python environment without installing those heavy packages.
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

# ── Ensure nautilus/ is on sys.path ──────────────────────────────────────────
NAUTILUS_ROOT = Path(__file__).resolve().parent.parent
if str(NAUTILUS_ROOT) not in sys.path:
    sys.path.insert(0, str(NAUTILUS_ROOT))


def _mod(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


# ── Enums ─────────────────────────────────────────────────────────────────────

class OptionKind(IntEnum):
    CALL = 1
    PUT = 2

class OrderSide(IntEnum):
    BUY = 1
    SELL = 2

class BarAggregation(IntEnum):
    MINUTE = 1

class PriceType(IntEnum):
    LAST = 1

class AggregationSource(IntEnum):
    EXTERNAL = 1

class OmsType(IntEnum):
    NETTING = 1

class AccountType(IntEnum):
    CASH = 1

class TimeInForce(IntEnum):
    DAY = 1

# ── Lightweight value types ───────────────────────────────────────────────────

class _Numeric:
    def __init__(self, v): self._v = float(v)
    def __float__(self): return self._v
    def __repr__(self): return str(self._v)
    @classmethod
    def from_str(cls, s): return cls(s)

class Price(_Numeric): pass
class Quantity(_Numeric): pass
class Money:
    def __init__(self, amount, currency): self.amount = amount; self.currency = currency

class _USD:
    value = "USD"
USD = _USD()

# ── Identifiers ───────────────────────────────────────────────────────────────

class Symbol:
    def __init__(self, value): self.value = str(value)
    def __repr__(self): return self.value

class Venue:
    def __init__(self, value): self.value = str(value)
    def __repr__(self): return self.value

class InstrumentId:
    def __init__(self, symbol, venue):
        self.symbol = symbol
        self.venue = venue
    def __repr__(self): return f"{self.symbol.value}.{self.venue.value}"

# ── Instruments ───────────────────────────────────────────────────────────────

class _Instrument:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        if not hasattr(self, "info"):
            self.info = {}

class OptionContract(_Instrument):
    pass

class Equity(_Instrument):
    pass

# ── Config base classes ───────────────────────────────────────────────────────

class _FrozenMeta(type):
    def __new__(mcs, name, bases, ns, frozen=False, **kw):
        return super().__new__(mcs, name, bases, ns)
    def __init__(cls, name, bases, ns, frozen=False, **kw):
        super().__init__(name, bases, ns)

class NautilusConfig(metaclass=_FrozenMeta):
    def __init_subclass__(cls, frozen=False, **kw):
        super().__init_subclass__(**kw)

class StrategyConfig(NautilusConfig):
    pass

class _DummyConfig:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

TradingNodeConfig = _DummyConfig
LiveDataEngineConfig = _DummyConfig
LiveRiskEngineConfig = _DummyConfig
LiveExecEngineConfig = _DummyConfig
LoggingConfig = _DummyConfig

# ── Data types ────────────────────────────────────────────────────────────────

class BarSpecification:
    def __init__(self, step, agg, price_type):
        self.step = step; self.aggregation = agg; self.price_type = price_type

class BarType:
    def __init__(self, instrument_id, bar_spec, aggregation_source=None):
        self.instrument_id = instrument_id
        self.bar_spec = bar_spec

class Bar:
    def __init__(self, bar_type, open, high, low, close, volume, ts_event, ts_init):
        self.bar_type = bar_type
        self.open = open; self.high = high; self.low = low; self.close = close
        self.volume = volume; self.ts_event = ts_event; self.ts_init = ts_init

class QuoteTick: pass

# ── Strategy base ─────────────────────────────────────────────────────────────

class Strategy:
    def __init__(self, config=None):
        self.config = config
    def subscribe_bars(self, bar_type): pass
    def subscribe_quote_ticks(self, instrument_id): pass
    def submit_order_list(self, order_list): pass
    def close_position(self, instrument_id, reason=""): pass

# ── Execution messages ────────────────────────────────────────────────────────

class SubmitOrder:
    def __init__(self, **kw):
        for k, v in kw.items(): setattr(self, k, v)

class SubmitOrderList:
    def __init__(self, **kw):
        for k, v in kw.items(): setattr(self, k, v)

# ── Backtest stubs ────────────────────────────────────────────────────────────

class BacktestEngineConfig:
    def __init__(self, **kw): pass

class BacktestEngine:
    def __init__(self, config=None): pass
    def add_instrument(self, i): pass
    def add_data(self, d): pass
    def add_venue(self, **kw): pass
    def add_strategy(self, s): pass
    def run(self): pass
    class trader:
        @staticmethod
        def generate_account_report(venue): return "—"

# ── Live node stubs ───────────────────────────────────────────────────────────

class TradingNode:
    def __init__(self, config=None): self.trader = self
    def add_data_client_factory(self, *a): pass
    def add_exec_client_factory(self, *a): pass
    def add_strategy(self, *a): pass
    def build(self): pass
    def run(self): pass
    def dispose(self): pass

# ── Factory bases ─────────────────────────────────────────────────────────────

class LiveDataClientFactory: pass
class LiveExecClientFactory: pass

# ── Install all stubs into sys.modules ────────────────────────────────────────

def _install(module_name: str, attrs: dict):
    m = _mod(module_name)
    for k, v in attrs.items():
        setattr(m, k, v)

_install("nautilus_trader", {})
_install("nautilus_trader.config", {
    "NautilusConfig": NautilusConfig,
    "StrategyConfig": StrategyConfig,
    "TradingNodeConfig": TradingNodeConfig,
    "LiveDataEngineConfig": LiveDataEngineConfig,
    "LiveRiskEngineConfig": LiveRiskEngineConfig,
    "LiveExecEngineConfig": LiveExecEngineConfig,
    "LoggingConfig": LoggingConfig,
})
_install("nautilus_trader.common", {})
_install("nautilus_trader.common.component", {"LiveClock": object})
_install("nautilus_trader.model", {})
_install("nautilus_trader.model.currencies", {"USD": USD})
_install("nautilus_trader.model.enums", {
    "OptionKind": OptionKind,
    "OrderSide": OrderSide,
    "BarAggregation": BarAggregation,
    "PriceType": PriceType,
    "AggregationSource": AggregationSource,
    "OmsType": OmsType,
    "AccountType": AccountType,
    "TimeInForce": TimeInForce,
})
_install("nautilus_trader.model.identifiers", {
    "InstrumentId": InstrumentId,
    "Symbol": Symbol,
    "Venue": Venue,
})
_install("nautilus_trader.model.instruments", {
    "OptionContract": OptionContract,
    "Equity": Equity,
})
_install("nautilus_trader.model.objects", {
    "Price": Price,
    "Quantity": Quantity,
    "Money": Money,
})
_install("nautilus_trader.model.data", {
    "Bar": Bar,
    "BarType": BarType,
    "BarSpecification": BarSpecification,
    "QuoteTick": QuoteTick,
})
_install("nautilus_trader.trading", {})
_install("nautilus_trader.trading.strategy", {"Strategy": Strategy})
_install("nautilus_trader.execution", {})
_install("nautilus_trader.execution.messages", {
    "SubmitOrder": SubmitOrder,
    "SubmitOrderList": SubmitOrderList,
})
_install("nautilus_trader.live", {})
_install("nautilus_trader.live.node", {"TradingNode": TradingNode})
_install("nautilus_trader.live.factories", {
    "LiveDataClientFactory": LiveDataClientFactory,
    "LiveExecClientFactory": LiveExecClientFactory,
})
_install("nautilus_trader.backtest", {})
_install("nautilus_trader.backtest.engine", {
    "BacktestEngine": BacktestEngine,
    "BacktestEngineConfig": BacktestEngineConfig,
})
_install("nautilus_futu", {})
_install("nautilus_futu.data", {"FutuLiveDataClient": object})
_install("nautilus_futu.execution", {"FutuLiveExecClient": object})
