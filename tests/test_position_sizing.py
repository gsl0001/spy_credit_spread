"""Tests for the position-sizing dropdown (use_request.md §2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.risk import size_position, sizing_mode_from_request


# ── size_position ────────────────────────────────────────────────────────


def test_fixed_mode_returns_fixed_contracts() -> None:
    n = size_position(
        equity=10_000, debit_per_contract=200, margin_per_contract=200,
        mode="fixed", fixed_contracts=3,
    )
    assert n == 3


def test_dynamic_mode_uses_risk_percent() -> None:
    # 10k equity, 5% risk = $500; / $100 margin/contract => 5
    n = size_position(
        equity=10_000, debit_per_contract=100, margin_per_contract=100,
        mode="dynamic", risk_percent=5.0,
    )
    assert n == 5


def test_dynamic_risk_alias_normalises_to_dynamic() -> None:
    n = size_position(
        equity=10_000, debit_per_contract=100, margin_per_contract=100,
        mode="dynamic_risk", risk_percent=5.0,
    )
    assert n == 5


def test_targeted_spread_within_cap_uses_pct() -> None:
    # 10k * 2% = $200 budget, cap=$500 (not exceeded), $100/contract => 2
    n = size_position(
        equity=10_000, debit_per_contract=100, margin_per_contract=100,
        mode="targeted_spread", target_spread_pct=2.0, max_allocation_cap=500.0,
        fixed_contracts=99,
    )
    assert n == 2


def test_targeted_spread_over_cap_falls_back_to_fixed_contracts() -> None:
    # 10k * 50% = $5000 budget WAY over $500 cap -> fall back to fixed_contracts=7
    n = size_position(
        equity=10_000, debit_per_contract=100, margin_per_contract=100,
        mode="targeted_spread", target_spread_pct=50.0, max_allocation_cap=500.0,
        fixed_contracts=7,
    )
    assert n == 7


def test_zero_risk_returns_zero() -> None:
    n = size_position(equity=10_000, debit_per_contract=0, margin_per_contract=0,
                      mode="fixed", fixed_contracts=10)
    assert n == 0


# ── sizing_mode_from_request ─────────────────────────────────────────────


def test_explicit_method_takes_precedence_over_legacy_booleans() -> None:
    req = SimpleNamespace(
        position_size_method="targeted_spread",
        use_dynamic_sizing=True,  # would be "dynamic" under legacy
        use_targeted_spread=False,
    )
    assert sizing_mode_from_request(req) == "targeted_spread"


def test_dynamic_risk_alias_through_request() -> None:
    req = SimpleNamespace(position_size_method="dynamic_risk",
                          use_dynamic_sizing=False, use_targeted_spread=False)
    assert sizing_mode_from_request(req) == "dynamic"


def test_legacy_booleans_when_method_absent() -> None:
    req = SimpleNamespace(position_size_method="",
                          use_dynamic_sizing=True, use_targeted_spread=False)
    assert sizing_mode_from_request(req) == "dynamic"
    req2 = SimpleNamespace(position_size_method="",
                           use_dynamic_sizing=False, use_targeted_spread=True)
    assert sizing_mode_from_request(req2) == "targeted_spread"


def test_default_is_fixed() -> None:
    req = SimpleNamespace(position_size_method="",
                          use_dynamic_sizing=False, use_targeted_spread=False)
    assert sizing_mode_from_request(req) == "fixed"
