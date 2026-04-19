"""Live position monitor.

Runs every ``monitor_interval_seconds`` (default 15 s during RTH).
For each open position in the journal:

    1. Pull the live combo midpoint from IBKR.
    2. Roll the high-water-mark forward.
    3. Evaluate stop-loss / take-profit / trailing-stop / DTE-based exits.
    4. If an exit fires, submit an opposite-side combo LMT with a small
       haircut and mark the position ``closing`` in the journal.

The exit-decision math is factored into :func:`evaluate_exit`, which is
pure and accepts the position, current mid, and a config dict. That
function is what tests drive directly.

Defensive programming
---------------------
The monitor is always-on. It must NOT raise from inside the tick —
anything uncaught would kill the APScheduler worker. Every broker call
is wrapped, and failure paths log an event to the journal so the
operator can see *why* a position wasn't managed.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from core.journal import Journal, Order, Position, get_journal


logger = logging.getLogger(__name__)


# ── Exit decision ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str
    details: dict


_CONTRACT_MULTIPLIER = 100.0


def _entry_debit_unit_price(pos: Position) -> float:
    """Per-share (a.k.a. "unit price") entry debit — matches IBKR's mid scale.

    ``Position.entry_cost`` is the TOTAL $ paid; IBKR's combo mid is
    quoted per share. Divide by ``contracts * 100`` so both operands in
    the exit math live on the same scale.
    """
    if pos.contracts <= 0:
        return 0.0
    return abs(pos.entry_cost) / (pos.contracts * _CONTRACT_MULTIPLIER)


def _dte_for(expiry: str, today: Optional[date] = None) -> int:
    """Parse position expiry (YYYY-MM-DD or YYYYMMDD) and return days to expiry."""
    ref = today or date.today()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            d = datetime.strptime(expiry, fmt).date()
        except ValueError:
            continue
        return (d - ref).days
    return 0


def evaluate_exit(
    pos: Position,
    current_mid: float,
    *,
    stop_loss_pct: float = 50.0,       # close if unreal pnl <= -this% of debit
    take_profit_pct: float = 50.0,     # close if unreal pnl >= +this% of debit
    trailing_stop_pct: float = 0.0,    # 0 disables; else % drawdown from HWM
    dte_exit_at: int = 0,              # close when DTE reaches this (inclusive)
    force_exit: bool = False,
    high_water_mark: Optional[float] = None,
    today: Optional[date] = None,
) -> ExitDecision:
    """Pure evaluation of a position's exit conditions.

    ``current_mid`` is the LIVE combo mid in per-share dollars (e.g.
    2.55 for a $2.55 spread) — the scale IBKR reports. Internally
    converted to the same scale as the stored entry debit.

    Returns the first-firing exit condition, or
    ``ExitDecision(False, "ok", {...})`` when none trigger.
    """
    entry_debit = _entry_debit_unit_price(pos)
    if entry_debit <= 0:
        return ExitDecision(False, "no_entry_cost", {})

    # Manual override beats everything else.
    if force_exit:
        return ExitDecision(True, "manual_flatten", {"mid": current_mid})

    # DTE-based forced exit.
    dte = _dte_for(pos.expiry, today=today)
    if dte <= dte_exit_at:
        return ExitDecision(True, "dte_exit", {
            "dte": dte, "threshold": dte_exit_at,
        })

    pnl_unit = current_mid - entry_debit
    pnl_pct = (pnl_unit / entry_debit) * 100.0

    # Stop loss.
    if stop_loss_pct > 0 and pnl_pct <= -abs(stop_loss_pct):
        return ExitDecision(True, "stop_loss", {
            "pnl_pct": round(pnl_pct, 2), "limit_pct": stop_loss_pct,
            "entry_debit": entry_debit, "current_mid": current_mid,
        })

    # Take profit.
    if take_profit_pct > 0 and pnl_pct >= abs(take_profit_pct):
        return ExitDecision(True, "take_profit", {
            "pnl_pct": round(pnl_pct, 2), "target_pct": take_profit_pct,
            "entry_debit": entry_debit, "current_mid": current_mid,
        })

    # Trailing stop — only meaningful once we've been in profit.
    if trailing_stop_pct > 0 and high_water_mark is not None and high_water_mark > entry_debit:
        # Only trail after we've banked some gain. Measure drawdown as a
        # % of the HWM's excess over entry.
        hwm_gain = high_water_mark - entry_debit
        current_gain = current_mid - entry_debit
        if hwm_gain > 0:
            give_back_pct = ((hwm_gain - current_gain) / hwm_gain) * 100.0
            if give_back_pct >= trailing_stop_pct:
                return ExitDecision(True, "trailing_stop", {
                    "give_back_pct": round(give_back_pct, 2),
                    "trail_pct": trailing_stop_pct,
                    "hwm": high_water_mark,
                    "current_mid": current_mid,
                })

    return ExitDecision(False, "ok", {
        "pnl_pct": round(pnl_pct, 2),
        "dte": dte,
        "current_mid": current_mid,
    })


def exit_config_for(pos: Position, defaults: dict[str, Any]) -> dict[str, Any]:
    """Merge a position's meta-stored exit config with system defaults.

    The position's ``meta`` dict may carry per-trade overrides under keys
    ``stop_loss_pct``, ``take_profit_pct``, ``trailing_stop_pct``,
    ``dte_exit_at``. Missing keys fall back to ``defaults``.
    """
    out = dict(defaults)
    for k in ("stop_loss_pct", "take_profit_pct",
              "trailing_stop_pct", "dte_exit_at"):
        if k in pos.meta and pos.meta[k] is not None:
            out[k] = pos.meta[k]
    return out


# ── Exit order submission (async, uses IBKR) ──────────────────────────────

def _reverse_legs(legs: tuple[dict, ...]) -> list[dict]:
    """Flip each leg's side to build the closing order."""
    out = []
    for leg in legs:
        flipped = dict(leg)
        side = leg.get("side", "long")
        flipped["side"] = "short" if side == "long" else "long"
        out.append(flipped)
    return out


def _closing_side(pos: Position) -> str:
    """Side used in ``place_combo_order`` to close a position.

    A bull-call debit spread was opened BUY; the close is SELL at mid.
    """
    # For now, debit spreads close with SELL. Credit spreads would close
    # with BUY. The combo_type flag on meta lets us override.
    combo_type = pos.meta.get("combo_type", "debit")
    return "SELL" if combo_type == "debit" else "BUY"


async def submit_exit_order(
    trader,
    pos: Position,
    current_mid: float,
    reason: str,
    journal: Journal,
    *,
    haircut_pct: float = 0.05,
) -> dict[str, Any]:
    """Fire an opposite-side combo LMT to close ``pos`` and journal it.

    Returns a dict describing what happened. Never raises.
    """
    try:
        close_side = _closing_side(pos)
        # Aggressive-to-fill price: hit the mid minus a small haircut when
        # selling, plus haircut when buying, so the order clears quickly.
        hc = max(0.0, haircut_pct) * abs(current_mid)
        if close_side == "SELL":
            limit = max(0.01, current_mid - hc)
        else:
            limit = current_mid + hc
        limit = round(limit, 2)

        # Reverse the legs so place_combo_order interprets the combo
        # correctly when we pass side=close_side.
        legs = _reverse_legs(pos.legs)
        res = await trader.place_combo_order(
            pos.symbol, legs, int(pos.contracts), side=close_side,
            lmtPrice=limit,
        )
        broker_order_id = str(res.get("orderId", "")) if isinstance(res, dict) else ""
        order_id = str(uuid.uuid4())
        journal.record_order(Order(
            id=order_id,
            position_id=pos.id,
            broker=pos.broker,
            broker_order_id=broker_order_id or None,
            side=close_side,
            limit_price=limit,
            status="submitted",
            submitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            kind="exit",
            idempotency_key=f"exit:{pos.id}:{reason}",
        ))
        journal.update_position(pos.id, state="closing")
        journal.log_event("exit_submitted", subject=pos.id, payload={
            "reason": reason, "limit": limit, "mid": current_mid,
            "broker_order_id": broker_order_id,
        })
        return {"ok": True, "order_id": order_id, "reason": reason,
                "limit": limit, "mid": current_mid}
    except Exception as e:  # noqa: BLE001
        journal.log_event("exit_failed", subject=pos.id, payload={
            "reason": reason, "error": f"{type(e).__name__}: {e}",
        })
        return {"ok": False, "error": str(e), "reason": reason}


# ── Per-position tick ─────────────────────────────────────────────────────

async def _process_position(
    pos: Position,
    trader,
    journal: Journal,
    defaults: dict[str, Any],
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Fetch current mid, update HWM, evaluate exits, fire close if needed."""
    if pos.state not in ("open",):
        # Pending / closing / closed — not our job.
        return {"position_id": pos.id, "skipped": True, "state": pos.state}
    try:
        legs_list = [dict(leg) for leg in pos.legs]
        mid = await trader.get_combo_midpoint(pos.symbol, legs_list)
    except Exception as e:  # noqa: BLE001
        journal.log_event("quote_failed", subject=pos.id, payload={
            "error": f"{type(e).__name__}: {e}",
        })
        return {"position_id": pos.id, "error": "quote_failed"}

    if mid is None or (isinstance(mid, float) and math.isnan(mid)) or mid <= 0:
        return {"position_id": pos.id, "error": "no_quote"}

    # Roll the high-water mark forward.
    prev_hwm = pos.high_water_mark if pos.high_water_mark is not None else 0.0
    new_hwm = max(prev_hwm, float(mid))
    if new_hwm > prev_hwm:
        journal.update_position(pos.id, high_water_mark=new_hwm)

    cfg = exit_config_for(pos, defaults)
    decision = evaluate_exit(
        pos,
        float(mid),
        stop_loss_pct=float(cfg.get("stop_loss_pct", 50.0)),
        take_profit_pct=float(cfg.get("take_profit_pct", 50.0)),
        trailing_stop_pct=float(cfg.get("trailing_stop_pct", 0.0)),
        dte_exit_at=int(cfg.get("dte_exit_at", 0)),
        force_exit=bool(cfg.get("force_exit", False)),
        high_water_mark=new_hwm,
        today=today,
    )
    if not decision.should_exit:
        return {"position_id": pos.id, "mid": float(mid), "status": "holding",
                "details": decision.details}

    haircut = float(cfg.get("haircut_pct", 0.05))
    result = await submit_exit_order(
        trader, pos, float(mid), decision.reason, journal,
        haircut_pct=haircut,
    )
    return {"position_id": pos.id, "mid": float(mid),
            "exit_reason": decision.reason,
            "submitted": result.get("ok", False), "details": result}


# ── Tick entry point (called by scheduler) ───────────────────────────────

# Exact callable shape: () -> Awaitable[trader]  (or dict with error).
TraderFactory = Callable[[], Awaitable[Any]]


async def tick(
    trader_factory: TraderFactory,
    *,
    journal: Optional[Journal] = None,
    defaults: Optional[dict[str, Any]] = None,
    today: Optional[date] = None,
) -> list[dict]:
    """Run one pass of the monitor.

    Always returns a list of per-position result dicts. Never raises.
    """
    j = journal or get_journal()
    try:
        open_positions = j.list_open()
    except Exception as e:  # noqa: BLE001
        logger.exception("monitor: list_open failed: %s", e)
        return []

    # Only 'open' positions are actionable here; 'pending' / 'closing'
    # are the fill-watcher's job.
    active = [p for p in open_positions if p.state == "open"]
    if not active:
        return []

    try:
        trader = await trader_factory()
    except Exception as e:  # noqa: BLE001
        j.log_event("monitor_no_trader", payload={
            "error": f"{type(e).__name__}: {e}",
        })
        return []
    if trader is None:
        j.log_event("monitor_no_trader", payload={"error": "factory returned None"})
        return []

    if defaults is None:
        try:
            from core.settings import SETTINGS
            defaults = {
                "stop_loss_pct": SETTINGS.risk.default_stop_loss_pct,
                "take_profit_pct": SETTINGS.risk.default_take_profit_pct,
                "trailing_stop_pct": SETTINGS.risk.default_trailing_stop_pct,
                "dte_exit_at": 0,
                "haircut_pct": SETTINGS.risk.limit_price_haircut,
            }
        except Exception:  # noqa: BLE001
            defaults = {}

    results: list[dict] = []
    for pos in active:
        try:
            res = await _process_position(pos, trader, j, defaults, today=today)
        except Exception as e:  # noqa: BLE001
            j.log_event("monitor_error", subject=pos.id, payload={
                "error": f"{type(e).__name__}: {e}",
            })
            res = {"position_id": pos.id, "error": str(e)}
        results.append(res)
    return results


__all__ = [
    "ExitDecision",
    "evaluate_exit",
    "exit_config_for",
    "submit_exit_order",
    "tick",
]
