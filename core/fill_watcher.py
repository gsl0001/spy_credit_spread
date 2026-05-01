"""Entry/exit fill reconciliation + cancel-on-timeout.

Why this exists
---------------
:func:`~core.monitor.tick` manages the open-position lifecycle, but it
assumes a position is actually ``open`` — i.e., the entry order filled.
Between ``submitted`` and ``filled`` live a handful of failure modes
that must be handled or the system ends up with orphaned broker orders:

    * The LMT sits unfilled past our patience window → cancel it.
    * The broker partially fills and stalls → decide what to do.
    * The broker rejects → flip position to ``cancelled``.
    * The broker confirms the fill → flip position to ``open`` and
      update the journaled entry cost to the real fill price.

This module owns that reconciliation. It is scheduler-friendly: call
:func:`reconcile_once` every few seconds and it will converge on a
consistent state.

All decision math is factored into :func:`next_action`, which is pure.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from core.journal import Fill, Journal, Order, Position, get_journal


logger = logging.getLogger(__name__)


# IBKR status → canonical state
_FILLED = {"Filled"}
_CANCELLED = {"Cancelled", "ApiCancelled", "Inactive"}
_REJECTED = {"Rejected"}
_WORKING = {"PendingSubmit", "PreSubmitted", "Submitted"}


@dataclass(frozen=True)
class FillAction:
    kind: str                # "waiting" | "filled" | "partial" | "cancel_timeout" | "cancelled" | "rejected"
    details: dict


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _elapsed(submitted_at: str, now: datetime) -> float:
    t = _parse_iso(submitted_at)
    if t is None:
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t).total_seconds())


def next_action(
    broker_status: Optional[dict],
    submitted_at: str,
    now: datetime,
    *,
    timeout_seconds: int = 30,
    total_qty: int = 0,
) -> FillAction:
    """Decide what to do next for an outstanding order.

    ``broker_status`` is what IBKR returned (see
    :meth:`IBKRTrader.get_order_status`). ``None`` means the broker has
    no record of the order — treat as 'waiting' until timeout, then give
    up.
    """
    elapsed = _elapsed(submitted_at, now)

    if broker_status is None:
        if elapsed >= timeout_seconds:
            return FillAction("cancel_timeout", {"elapsed": elapsed,
                                                 "reason": "no_broker_record"})
        return FillAction("waiting", {"elapsed": elapsed})

    status = broker_status.get("status", "")
    filled = int(broker_status.get("filled", 0) or 0)
    remaining = int(broker_status.get("remaining", 0) or 0)
    avg_fill = float(broker_status.get("avgFillPrice", 0.0) or 0.0)
    commission = float(broker_status.get("commission", 0.0) or 0.0)

    if status in _FILLED:
        return FillAction("filled", {
            "filled": filled, "avg_fill_price": avg_fill,
            "commission": commission, "elapsed": elapsed,
        })

    if status in _REJECTED:
        return FillAction("rejected", {"status": status, "elapsed": elapsed})

    if status in _CANCELLED:
        return FillAction("cancelled", {
            "status": status, "filled": filled, "avg_fill_price": avg_fill,
            "elapsed": elapsed,
        })

    # Working status: either timeout or keep waiting (with a partial note).
    if elapsed >= timeout_seconds:
        if filled > 0 and remaining == 0:
            # Race: broker says filled-but-not-status-updated yet. Finalize.
            return FillAction("filled", {
                "filled": filled, "avg_fill_price": avg_fill,
                "commission": commission, "elapsed": elapsed,
            })
        return FillAction("cancel_timeout", {
            "elapsed": elapsed, "status": status,
            "filled": filled, "remaining": remaining,
        })

    # Partial fill reported — keep waiting but tell callers.
    if filled > 0 and remaining > 0:
        return FillAction("partial", {
            "filled": filled, "remaining": remaining,
            "avg_fill_price": avg_fill, "elapsed": elapsed,
        })

    return FillAction("waiting", {"elapsed": elapsed, "status": status})


# ── Finalizers ─────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finalize_filled(
    journal: Journal,
    order: Order,
    position: Optional[Position],
    *,
    filled_qty: int,
    avg_fill_price: float,
    commission: float,
    now_iso: Optional[str] = None,
) -> None:
    """Mark an entry/exit order as filled and update the parent position."""
    t = now_iso or _utc_now_iso()
    journal.record_order(Order(
        id=order.id,
        position_id=order.position_id,
        broker=order.broker,
        broker_order_id=order.broker_order_id,
        side=order.side,
        limit_price=order.limit_price,
        status="filled",
        submitted_at=order.submitted_at,
        filled_at=t,
        fill_price=avg_fill_price,
        commission=commission,
        kind=order.kind,
        idempotency_key=order.idempotency_key,
    ))
    journal.record_fill(Fill(
        id=None, order_id=order.id, qty=filled_qty, price=avg_fill_price,
        time=t, exec_id=None, commission=commission,
    ))
    if position is None:
        return

    if order.kind == "entry":
        # Rewrite entry_cost from the real fill (avg_fill_price is per share,
        # multiply by 100 * qty for total debit paid).
        real_entry = avg_fill_price * 100.0 * filled_qty
        journal.update_position(
            position.id,
            state="open",
            entry_cost=real_entry,
        )
        journal.log_event("entry_filled", subject=position.id, payload={
            "avg_fill_price": avg_fill_price, "qty": filled_qty,
            "commission": commission,
        })
        try:
            from core.telegram_bot import notify_entry_filled
            notify_entry_filled(
                position.symbol, filled_qty, avg_fill_price,
                position_id=position.id,
            )
        except Exception:  # noqa: BLE001
            pass
    elif order.kind == "exit":
        # Aggregate ALL exit-order fills for this position before closing.
        # Multi-leg spread exits (moomoo) fire one order per leg — closing
        # on the first fill would lose the second leg's proceeds and
        # produce a wildly wrong realized_pnl.
        try:
            all_orders = journal.list_orders_for_position(position.id)
        except Exception:  # noqa: BLE001
            all_orders = [order]

        exit_orders = [o for o in all_orders if o.kind == "exit"]
        # Include the order we just filled (in case the journal read above
        # raced and missed our own update).
        if not any(o.id == order.id for o in exit_orders):
            exit_orders.append(order)

        # Are all exit orders for this position now in a terminal state?
        unfinished = [
            o for o in exit_orders
            if (o.status or "") not in ("filled", "cancelled")
        ]
        if unfinished:
            # Wait for the other leg(s).  Just record the fill row above
            # and emit a leg-fill event so the user sees progress.
            journal.log_event("exit_leg_filled", subject=position.id, payload={
                "order_id": order.id, "broker_order_id": order.broker_order_id,
                "avg_fill_price": avg_fill_price, "qty": filled_qty,
                "remaining_legs": [o.id for o in unfinished],
            })
            return

        # All exit orders done — aggregate net cash and close.
        # For long legs: SELL fill → +cash * qty * 100
        # For short legs: BUY-to-close fill → -cash * qty * 100
        # Cancelled orders contribute zero cash.
        exit_total_net = 0.0
        for o in exit_orders:
            if (o.status or "") != "filled":
                continue
            fill_price = float(o.fill_price or 0.0)
            qty = 0
            try:
                fills = journal.list_fills(o.id)
                qty = sum(f.qty for f in fills) or 0
            except Exception:  # noqa: BLE001
                pass
            if qty == 0:
                qty = filled_qty if o.id == order.id else 0
            cash = fill_price * 100.0 * qty
            sign = +1 if (o.side or "").upper() == "SELL" else -1
            exit_total_net += sign * cash

        # Sum every commission paid on this position (entry + exits).
        total_commission = sum(o.commission or 0.0 for o in all_orders) + (
            commission if not any(o.id == order.id and o.commission for o in all_orders) else 0.0
        )

        realized = exit_total_net - abs(position.entry_cost) - total_commission
        reason = "exit_filled"
        if position.state == "closing" and position.exit_reason:
            reason = position.exit_reason
        journal.close_position(
            position.id,
            exit_cost=exit_total_net,
            reason=reason,
            realized_pnl=realized,
            time=t,
        )
        journal.log_event("exit_filled", subject=position.id, payload={
            "exit_total_net": exit_total_net,
            "realized_pnl": realized,
            "total_commission": total_commission,
            "leg_count": len(exit_orders),
        })
        # Telegram: ping the operator with the closing P&L. Best-effort,
        # never let a notify failure break the journal write above.
        try:
            from core.telegram_bot import notify_exit_filled
            notify_exit_filled(
                position.symbol, realized, reason,
                position_id=position.id,
            )
        except Exception:  # noqa: BLE001
            pass


def finalize_cancelled(
    journal: Journal,
    order: Order,
    position: Optional[Position],
    *,
    reason: str,
    now_iso: Optional[str] = None,
) -> None:
    """Mark an order cancelled and roll its parent position."""
    t = now_iso or _utc_now_iso()
    journal.record_order(Order(
        id=order.id,
        position_id=order.position_id,
        broker=order.broker,
        broker_order_id=order.broker_order_id,
        side=order.side,
        limit_price=order.limit_price,
        status="cancelled",
        submitted_at=order.submitted_at,
        filled_at=order.filled_at,
        fill_price=order.fill_price,
        commission=order.commission,
        kind=order.kind,
        idempotency_key=order.idempotency_key,
    ))
    if position is None:
        return
    if order.kind == "entry":
        # Entry never landed → position dies.
        journal.update_position(position.id, state="cancelled",
                                exit_reason=reason)
        journal.log_event("entry_cancelled", subject=position.id,
                          payload={"reason": reason})
    elif order.kind == "exit":
        # Exit vanished → position still open, needs a retry next tick.
        journal.update_position(position.id, state="open")
        journal.log_event("exit_cancelled", subject=position.id,
                          payload={"reason": reason})


def finalize_rejected(
    journal: Journal,
    order: Order,
    position: Optional[Position],
    *,
    now_iso: Optional[str] = None,
) -> None:
    finalize_cancelled(journal, order, position,
                       reason="broker_rejected", now_iso=now_iso)


# ── Async reconciler ───────────────────────────────────────────────────────

StatusFetcher = Callable[[Any], Awaitable[Optional[dict]]]


async def _try_cancel(trader, broker_order_id) -> bool:
    """Submit a cancel to the broker. Returns True on success (best-effort)."""
    if broker_order_id is None:
        return False
    try:
        try:
            oid = int(broker_order_id)
        except (TypeError, ValueError):
            return False
        res = await trader.cancel_order(oid)
        return bool(isinstance(res, dict) and res.get("success"))
    except Exception as e:  # noqa: BLE001
        logger.warning("cancel_order failed for %s: %s", broker_order_id, e)
        return False


async def reconcile_once(
    trader,
    journal: Optional[Journal] = None,
    *,
    timeout_seconds: int = 30,
    now: Optional[datetime] = None,
) -> list[dict]:
    """One pass of fill reconciliation. Scheduler-safe: never raises.

    Iterates open entry/exit orders and advances each toward a terminal
    state. Returns a list of per-order action dicts for logging.
    """
    j = journal or get_journal()
    ts_now = now or datetime.now(timezone.utc)

    try:
        open_orders = j.list_orders_by_status(("submitted", "partial"))
    except Exception as e:  # noqa: BLE001
        logger.exception("reconcile_once list_orders failed: %s", e)
        return []
    if not open_orders:
        return []

    results: list[dict] = []
    for order in open_orders:
        try:
            effective_trader = _resolve_trader_for_order(order, trader)
            result = await _reconcile_order(
                order, effective_trader, j, timeout_seconds=timeout_seconds, now=ts_now,
            )
        except Exception as e:  # noqa: BLE001
            j.log_event("fill_watcher_error", subject=order.id, payload={
                "error": f"{type(e).__name__}: {e}",
            })
            result = {"order_id": order.id, "error": str(e)}
        results.append(result)
    return results


def _resolve_trader_for_order(order: "Order", default_trader):
    """Return the appropriate trader for an order based on order.broker.

    For moomoo orders, returns the registered MoomooTrader (which exposes
    get_order_status and cancel_order in the same interface shape as IBKRTrader).
    Falls back to default_trader if moomoo is not connected.
    """
    broker_name = order.broker or "ibkr"
    if broker_name == "moomoo":
        try:
            from core.broker import get_broker
            return get_broker("moomoo")
        except Exception:
            pass
    return default_trader


async def _reconcile_order(
    order: Order,
    trader,
    journal: Journal,
    *,
    timeout_seconds: int,
    now: datetime,
) -> dict:
    position = journal.get_position(order.position_id) if order.position_id else None

    broker_status: Optional[dict] = None
    if order.broker_order_id:
        try:
            broker_status = await trader.get_order_status(order.broker_order_id)
        except Exception as e:  # noqa: BLE001
            journal.log_event("status_fetch_failed", subject=order.id, payload={
                "error": f"{type(e).__name__}: {e}",
            })
            broker_status = None

    action = next_action(
        broker_status, order.submitted_at, now,
        timeout_seconds=timeout_seconds,
        total_qty=position.contracts if position else 0,
    )

    if action.kind == "waiting" or action.kind == "partial":
        return {"order_id": order.id, "action": action.kind,
                "details": action.details}

    if action.kind == "filled":
        finalize_filled(
            journal, order, position,
            filled_qty=int(action.details.get("filled", 0)),
            avg_fill_price=float(action.details.get("avg_fill_price", 0.0)),
            commission=float(action.details.get("commission", 0.0)),
        )
        return {"order_id": order.id, "action": "filled",
                "details": action.details}

    if action.kind == "cancel_timeout":
        await _try_cancel(trader, order.broker_order_id)
        finalize_cancelled(journal, order, position, reason="timeout_cancel")
        return {"order_id": order.id, "action": "cancel_timeout",
                "details": action.details}

    if action.kind == "cancelled":
        finalize_cancelled(journal, order, position, reason="broker_cancelled")
        return {"order_id": order.id, "action": "cancelled",
                "details": action.details}

    if action.kind == "rejected":
        finalize_rejected(journal, order, position)
        return {"order_id": order.id, "action": "rejected",
                "details": action.details}

    return {"order_id": order.id, "action": "noop",
            "details": action.details}


__all__ = [
    "FillAction",
    "next_action",
    "finalize_filled",
    "finalize_cancelled",
    "finalize_rejected",
    "reconcile_once",
]
