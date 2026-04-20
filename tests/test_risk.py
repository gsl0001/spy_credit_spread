"""Pre-trade risk gate + position sizer tests.

These run without IBKR — we stub the calendar helpers so tests are
deterministic regardless of what day pytest is invoked.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from core.risk import (
    AccountSnapshot,
    Decision,
    RiskContext,
    RiskLimits,
    evaluate_pre_trade,
    size_position,
    sizing_mode_from_request,
)


def _ctx(**over):
    """Build a RiskContext with sensible defaults and optional overrides."""
    base = dict(
        account=AccountSnapshot(equity=100_000.0, buying_power=50_000.0,
                                excess_liquidity=50_000.0),
        open_positions=0,
        today_realized_pnl=0.0,
        debit_per_contract=250.0,
        margin_per_contract=250.0,
        contracts=1,
        target_dte=14,
        limits=RiskLimits(require_market_open=False),
        today=date(2026, 4, 14),
        events=[],
    )
    base.update(over)
    return RiskContext(**base)


# ── evaluate_pre_trade ────────────────────────────────────────────────────

@pytest.mark.unit
def test_allows_clean_trade():
    d = evaluate_pre_trade(_ctx())
    assert d.allowed is True
    assert d.reason == "ok"


@pytest.mark.unit
def test_blocks_market_closed():
    with patch("core.calendar.is_market_open", return_value=(False, "weekend")):
        ctx = _ctx(limits=RiskLimits(require_market_open=True))
        d = evaluate_pre_trade(ctx)
    assert d.allowed is False
    assert d.reason == "market_closed"


@pytest.mark.unit
def test_blocks_too_close_to_close():
    with patch("core.calendar.is_market_open", return_value=(True, "open")), \
         patch("core.calendar.minutes_to_close", return_value=2):
        ctx = _ctx(limits=RiskLimits(require_market_open=True,
                                     min_minutes_before_close=5))
        d = evaluate_pre_trade(ctx)
    assert d.allowed is False
    assert d.reason == "too_close_to_close"
    assert d.details["minutes_to_close"] == 2


@pytest.mark.unit
def test_blocks_max_concurrent_positions():
    ctx = _ctx(open_positions=2, limits=RiskLimits(
        max_concurrent_positions=2, require_market_open=False))
    d = evaluate_pre_trade(ctx)
    assert d.allowed is False
    assert d.reason == "max_concurrent_positions"


@pytest.mark.unit
def test_blocks_daily_loss_pct():
    # -2500 on 100k equity = 2.5% loss, limit at 2%
    ctx = _ctx(today_realized_pnl=-2500.0,
               limits=RiskLimits(daily_loss_limit_pct=2.0,
                                 require_market_open=False))
    d = evaluate_pre_trade(ctx)
    assert d.allowed is False
    assert d.reason == "daily_loss_limit"
    assert d.details["pct_loss"] == 2.5


@pytest.mark.unit
def test_blocks_daily_loss_abs():
    ctx = _ctx(today_realized_pnl=-1200.0,
               limits=RiskLimits(daily_loss_limit_pct=0.0,
                                 daily_loss_limit_abs=1000.0,
                                 require_market_open=False))
    d = evaluate_pre_trade(ctx)
    assert d.allowed is False
    assert d.reason == "daily_loss_limit_abs"


@pytest.mark.unit
def test_allows_profit_never_hits_loss_gate():
    ctx = _ctx(today_realized_pnl=5_000.0,
               limits=RiskLimits(daily_loss_limit_pct=2.0,
                                 require_market_open=False))
    d = evaluate_pre_trade(ctx)
    assert d.allowed is True


@pytest.mark.unit
def test_blocks_insufficient_buying_power():
    # Need 250 * 500 = 125_000 but only 50_000 available
    ctx = _ctx(contracts=500)
    d = evaluate_pre_trade(ctx)
    assert d.allowed is False
    assert d.reason == "insufficient_buying_power"
    assert d.details["required"] == 125_000.0


@pytest.mark.unit
def test_uses_buying_power_when_excess_liq_missing():
    ctx = _ctx(account=AccountSnapshot(equity=100_000, buying_power=400,
                                       excess_liquidity=0),
               contracts=2)  # need 500
    d = evaluate_pre_trade(ctx)
    assert d.allowed is False
    assert d.reason == "insufficient_buying_power"


@pytest.mark.unit
def test_blocks_event_blackout_today():
    from core.calendar import MarketEvent
    events = [MarketEvent(date="2026-04-14", name="FOMC", severity="high")]
    ctx = _ctx(events=events, target_dte=0,
               limits=RiskLimits(require_market_open=False,
                                 blackout_window_before=0,
                                 blackout_window_after=0))
    d = evaluate_pre_trade(ctx)
    assert d.allowed is False
    assert d.reason == "event_blackout"
    assert d.details["event"] == "FOMC"


@pytest.mark.unit
def test_blocks_event_inside_dte_window():
    # Today is 4/14, target DTE 14 → expiry window reaches 4/28.
    # CPI on 4/20 should block.
    from core.calendar import MarketEvent
    events = [MarketEvent(date="2026-04-20", name="CPI", severity="medium")]
    ctx = _ctx(events=events, target_dte=14,
               limits=RiskLimits(require_market_open=False))
    d = evaluate_pre_trade(ctx)
    assert d.allowed is False
    assert d.reason == "event_blackout"


@pytest.mark.unit
def test_event_far_out_does_not_block():
    # DTE 7 → window ends 4/21. Event 5/1 is beyond window.
    from core.calendar import MarketEvent
    events = [MarketEvent(date="2026-05-01", name="NFP", severity="medium")]
    ctx = _ctx(events=events, target_dte=7,
               limits=RiskLimits(require_market_open=False))
    d = evaluate_pre_trade(ctx)
    assert d.allowed is True


# ── size_position ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_fixed_mode_returns_exact():
    assert size_position(10_000, 250, 250, mode="fixed",
                         fixed_contracts=3) == 3


@pytest.mark.unit
def test_dynamic_uses_risk_percent():
    # 10_000 * 5% = 500 budget; margin 250 → 2 contracts
    assert size_position(10_000, 250, 250, mode="dynamic",
                         risk_percent=5.0) == 2


@pytest.mark.unit
def test_dynamic_respects_cap():
    # 100k * 10% = 10_000 budget, cap 500 → 2 contracts
    assert size_position(100_000, 250, 250, mode="dynamic",
                         risk_percent=10.0, max_trade_cap=500) == 2


@pytest.mark.unit
def test_targeted_spread_under_cap_uses_pct():
    # 100k * 2% = $2000 budget, $250/contract -> 8 contracts
    assert size_position(100_000, 250, 250, mode="targeted_spread",
                         target_spread_pct=2.0,
                         max_allocation_cap=2500) == 8


@pytest.mark.unit
def test_targeted_spread_over_cap_falls_back_to_fixed():
    # use_request §2③: budget exceeds cap -> use fixed_contracts as fallback
    assert size_position(1_000_000, 250, 250, mode="targeted_spread",
                         target_spread_pct=2.0,
                         max_allocation_cap=2500,
                         fixed_contracts=4) == 4


@pytest.mark.unit
def test_excess_liquidity_clamps_contracts():
    # Fixed mode would give 10, but excess_liq only supports 4
    n = size_position(10_000, 250, 250, mode="fixed", fixed_contracts=10,
                      excess_liquidity=1050)
    assert n == 4


@pytest.mark.unit
def test_zero_margin_returns_zero():
    assert size_position(10_000, 0, 0, mode="fixed", fixed_contracts=5) == 0


@pytest.mark.unit
def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        size_position(10_000, 250, 250, mode="bogus")


# ── sizing_mode_from_request ───────────────────────────────────────────────

class _FakeReq:
    def __init__(self, **kw):
        self.use_dynamic_sizing = False
        self.use_targeted_spread = False
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.mark.unit
def test_sizing_mode_default_fixed():
    assert sizing_mode_from_request(_FakeReq()) == "fixed"


@pytest.mark.unit
def test_sizing_mode_dynamic():
    assert sizing_mode_from_request(_FakeReq(use_dynamic_sizing=True)) == "dynamic"


@pytest.mark.unit
def test_sizing_mode_targeted_wins_over_dynamic():
    req = _FakeReq(use_dynamic_sizing=True, use_targeted_spread=True)
    assert sizing_mode_from_request(req) == "targeted_spread"
