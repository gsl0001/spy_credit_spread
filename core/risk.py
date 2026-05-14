"""Pre-trade risk check + position sizer.

This is the single gate in front of every live order. If the plan calls
for a layered defence, this is the outer ring — before ``place_order``
runs, :func:`evaluate_pre_trade` must return `allowed=True`.

Usage
-----
>>> from core.risk import evaluate_pre_trade, size_position
>>> ctx = RiskContext(account=acct, journal=j, spec=spec, request=req)
>>> decision = evaluate_pre_trade(ctx)
>>> if not decision.allowed:
...     return {"error": "risk_rejected", "reason": decision.reason}

Every rejection reason is a stable string so the UI and journal can key
off it without parsing human text.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float
    buying_power: float
    excess_liquidity: float
    daily_pnl: float = 0.0          # broker-reported
    currency: str = "USD"


@dataclass(frozen=True)
class RiskLimits:
    max_concurrent_positions: int = 2
    daily_loss_limit_pct: float = 2.0       # % of equity
    daily_loss_limit_abs: float = 0.0       # absolute $ cap (0 = disabled)
    max_orders_per_day: int = 0             # 0 = unlimited; daily entry cap
    min_minutes_before_close: int = 5       # no new entries inside this window
    blackout_window_before: int = 0
    blackout_window_after: int = 2
    block_on_events: bool = True
    require_market_open: bool = True

    @classmethod
    def from_settings(cls, risk_settings=None) -> "RiskLimits":
        if risk_settings is None:
            from core.settings import SETTINGS
            risk_settings = SETTINGS.risk
        return cls(
            max_concurrent_positions=risk_settings.max_concurrent_positions,
            daily_loss_limit_pct=risk_settings.daily_loss_limit_pct,
            daily_loss_limit_abs=getattr(risk_settings, "daily_loss_limit_abs", 0.0),
            max_orders_per_day=getattr(risk_settings, "max_orders_per_day", 0),
        )


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RiskContext:
    account: AccountSnapshot
    open_positions: int
    today_realized_pnl: float
    debit_per_contract: float    # e.g. SpreadSpec.net_debit
    margin_per_contract: float
    contracts: int
    target_dte: int
    today_trade_count: int = 0   # number of entries today; for max_orders_per_day
    limits: RiskLimits = field(default_factory=RiskLimits.from_settings)
    today: Optional[date] = None
    events: list = field(default_factory=list)


# ── Pre-trade gate ─────────────────────────────────────────────────────────

def evaluate_pre_trade(ctx: RiskContext) -> Decision:
    """Run every risk check in order, short-circuit on first failure.

    Checks (in order):
        1. market hours
        2. max concurrent positions
        3. daily loss limit
        4. buying power
        5. event blackout
    """
    from core.calendar import in_blackout, is_market_open, minutes_to_close

    # 1. Market hours — only when the limit is enabled.
    if ctx.limits.require_market_open:
        is_open, why = is_market_open()
        if not is_open:
            return Decision(False, "market_closed", {"why": why})
        mtc = minutes_to_close()
        if 0 <= mtc < ctx.limits.min_minutes_before_close:
            return Decision(False, "too_close_to_close", {"minutes_to_close": mtc})

    # 2. Concurrent-positions cap.
    if ctx.open_positions >= ctx.limits.max_concurrent_positions:
        return Decision(False, "max_concurrent_positions", {
            "open": ctx.open_positions,
            "cap": ctx.limits.max_concurrent_positions,
        })

    # 3. Daily loss circuit breaker.
    # NOTE: today_realized_pnl is CLOSED-only; an open losing position
    # bleeding all day does not count toward this limit until it closes.
    # For autonomous live trading, complement this with monitor-side
    # stop_loss_pct on every preset.
    eq = max(ctx.account.equity, 1.0)
    pct_loss = (-ctx.today_realized_pnl / eq) * 100 if ctx.today_realized_pnl < 0 else 0.0
    if ctx.limits.daily_loss_limit_pct > 0 and pct_loss >= ctx.limits.daily_loss_limit_pct:
        return Decision(False, "daily_loss_limit", {
            "realized_pnl": ctx.today_realized_pnl,
            "pct_loss": round(pct_loss, 2),
            "limit_pct": ctx.limits.daily_loss_limit_pct,
        })
    if ctx.limits.daily_loss_limit_abs > 0 and ctx.today_realized_pnl <= -abs(ctx.limits.daily_loss_limit_abs):
        return Decision(False, "daily_loss_limit_abs", {
            "realized_pnl": ctx.today_realized_pnl,
            "limit_abs": ctx.limits.daily_loss_limit_abs,
        })

    # 3b. Daily entry cap — guardrail against a runaway scanner firing all day.
    # max_orders_per_day=0 means unlimited (legacy default). For autonomous
    # live trading set MAX_ORDERS_PER_DAY=N in env. today_trade_count should
    # be the count of POSITIONS ENTERED today (open + closed), not the closed
    # subset — callers pass journal.today_entry_count(broker=...).
    if ctx.limits.max_orders_per_day > 0 and ctx.today_trade_count >= ctx.limits.max_orders_per_day:
        return Decision(False, "max_orders_per_day", {
            "today_count": ctx.today_trade_count,
            "limit": ctx.limits.max_orders_per_day,
        })

    # 4. Buying power — debit spread margin = debit; credit spread = width*100.
    cost = abs(ctx.debit_per_contract * ctx.contracts) if ctx.debit_per_contract > 0 else 0
    margin = abs(ctx.margin_per_contract * ctx.contracts)
    required = max(cost, margin)
    bp = ctx.account.excess_liquidity if ctx.account.excess_liquidity > 0 else ctx.account.buying_power
    if required > bp:
        return Decision(False, "insufficient_buying_power", {
            "required": round(required, 2),
            "available": round(bp, 2),
        })

    # 5. Event blackout — both today and inside the DTE window.
    if ctx.limits.block_on_events and ctx.events:
        today = ctx.today or date.today()
        window_end = today + timedelta(days=max(ctx.target_dte, 0))
        cursor = today
        while cursor <= window_end:
            in_bl, event_name = in_blackout(
                cursor,
                events=ctx.events,
                window_days_before=ctx.limits.blackout_window_before,
                window_days_after=ctx.limits.blackout_window_after,
            )
            if in_bl:
                return Decision(False, "event_blackout", {
                    "date": cursor.isoformat(),
                    "event": event_name,
                })
            cursor += timedelta(days=1)

    return Decision(True, "ok")


# ── Position sizing ────────────────────────────────────────────────────────

def size_position(
    equity: float,
    debit_per_contract: float,
    margin_per_contract: float,
    *,
    mode: str = "fixed",
    fixed_contracts: int = 1,
    risk_percent: float = 5.0,
    max_trade_cap: float = 0.0,
    target_spread_pct: float = 2.0,
    max_allocation_cap: float = 2500.0,
    excess_liquidity: Optional[float] = None,
) -> int:
    """Return the number of contracts to submit given live inputs.

    ``mode`` — one of:
        "fixed":            ``fixed_contracts`` (ignores everything else)
        "dynamic":          contracts = floor(equity * risk% / margin_per_contract)
                            capped by max_trade_cap
        "targeted_spread":  contracts = floor(min(equity * pct%, max_allocation_cap)
                                              / margin_per_contract)

    In every mode the final answer is also clamped by ``excess_liquidity``
    when provided, so we never submit more than we can actually afford.
    """
    risk_per_contract = margin_per_contract if margin_per_contract > 0 else abs(debit_per_contract)
    if risk_per_contract <= 0:
        return 0

    # Aliases — UI dropdown uses "dynamic_risk"; canonical is "dynamic".
    canonical = {"dynamic_risk": "dynamic"}.get(mode, mode)

    if canonical == "fixed":
        contracts = max(0, int(fixed_contracts))
    elif canonical == "dynamic":
        budget = equity * (risk_percent / 100.0)
        # Both caps clamp the budget. max_trade_cap is the legacy IBKR knob;
        # max_allocation_cap is the moomoo/preset knob. Apply whichever
        # is positive so a preset can't escape its dollar ceiling.
        if max_trade_cap > 0:
            budget = min(budget, max_trade_cap)
        if max_allocation_cap > 0:
            budget = min(budget, max_allocation_cap)
        contracts = max(1, int(math.floor(budget / risk_per_contract)))
    elif canonical == "targeted_spread":
        # use_request §2③: try targeted % first; if it exceeds the cap,
        # fall back to fixed_contracts.
        raw_budget = equity * (target_spread_pct / 100.0)
        if max_allocation_cap > 0 and raw_budget > max_allocation_cap:
            contracts = max(0, int(fixed_contracts))
        else:
            budget = raw_budget if max_allocation_cap <= 0 else min(raw_budget, max_allocation_cap)
            contracts = max(1, int(math.floor(budget / risk_per_contract)))
    else:
        raise ValueError(f"Unknown sizing mode: {mode!r}")

    if excess_liquidity is not None and excess_liquidity > 0:
        cap = int(math.floor(excess_liquidity / risk_per_contract))
        contracts = min(contracts, max(0, cap))

    return max(0, contracts)


def sizing_mode_from_request(req) -> str:
    """Map a request to a canonical sizing mode string.

    Prefers the explicit ``position_size_method`` field (use_request §2),
    falling back to the legacy boolean toggles for backward compatibility.
    """
    explicit = getattr(req, "position_size_method", "") or ""
    if explicit:
        return {"dynamic_risk": "dynamic"}.get(explicit, explicit)
    if getattr(req, "use_targeted_spread", False):
        return "targeted_spread"
    if getattr(req, "use_dynamic_sizing", False):
        return "dynamic"
    return "fixed"


__all__ = [
    "AccountSnapshot",
    "RiskLimits",
    "Decision",
    "RiskContext",
    "evaluate_pre_trade",
    "size_position",
    "sizing_mode_from_request",
]
