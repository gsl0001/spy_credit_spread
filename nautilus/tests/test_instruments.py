"""Tests for FutuOptionsInstrumentProvider helpers."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.futu_options.providers import (
    _futu_code_to_instrument_id,
    _build_option_contract,
    FutuOptionsInstrumentProvider,
)
from nautilus_trader.model.enums import OptionKind


def test_futu_code_to_instrument_id():
    iid = _futu_code_to_instrument_id("US.SPY260428C580000")
    assert iid.symbol.value == "US.SPY260428C580000"
    assert iid.venue.value == "FUTU"


def test_build_option_contract_call():
    contract = _build_option_contract(
        futu_code="US.SPY260428C530000",
        strike=530.0,
        option_type="CALL",
        expiry=date(2026, 4, 28),
    )
    assert contract.option_kind == OptionKind.CALL
    assert float(contract.strike_price) == pytest.approx(530.0)
    assert contract.instrument_id.symbol.value == "US.SPY260428C530000"


def test_build_option_contract_put():
    contract = _build_option_contract(
        futu_code="US.SPY260428P525000",
        strike=525.0,
        option_type="PUT",
        expiry=date(2026, 4, 28),
    )
    assert contract.option_kind == OptionKind.PUT
    assert float(contract.strike_price) == pytest.approx(525.0)


def test_instrument_provider_find_by_strike_returns_none_when_empty():
    mock_cache = MagicMock()
    mock_cache.instruments.return_value = []
    provider = FutuOptionsInstrumentProvider(quote_ctx=MagicMock(), cache=mock_cache)
    result = provider.find_by_strike(530.0, OptionKind.CALL, date(2026, 4, 28))
    assert result is None


def test_instrument_provider_find_by_strike_matches():
    contract = _build_option_contract(
        futu_code="US.SPY260428C530000",
        strike=530.0,
        option_type="CALL",
        expiry=date(2026, 4, 28),
    )
    mock_cache = MagicMock()
    mock_cache.instruments.return_value = [contract]
    provider = FutuOptionsInstrumentProvider(quote_ctx=MagicMock(), cache=mock_cache)
    found = provider.find_by_strike(530.0, OptionKind.CALL, date(2026, 4, 28))
    assert found is not None
    assert float(found.strike_price) == pytest.approx(530.0)


def test_instrument_provider_find_by_strike_wrong_kind():
    contract = _build_option_contract(
        futu_code="US.SPY260428C530000",
        strike=530.0,
        option_type="CALL",
        expiry=date(2026, 4, 28),
    )
    mock_cache = MagicMock()
    mock_cache.instruments.return_value = [contract]
    provider = FutuOptionsInstrumentProvider(quote_ctx=MagicMock(), cache=mock_cache)
    # Looking for a PUT — should not match the CALL
    found = provider.find_by_strike(530.0, OptionKind.PUT, date(2026, 4, 28))
    assert found is None
