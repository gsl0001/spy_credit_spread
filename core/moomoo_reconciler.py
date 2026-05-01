"""Periodic reconciler that detects mismatches between moomoo broker state and the journal.

Runs from APScheduler alongside the monitor + fill_watcher.  The two
problems it catches:

  1. **Orphan broker positions** — moomoo has a position the journal
     doesn't know about.  Happens when uvicorn dies between leg1 fill
     and journal write, or when manual broker activity occurs.  Logged
     as a CRITICAL alert; we do NOT auto-flatten because the operator
     may have placed the trade intentionally.

  2. **Phantom journal positions** — the journal says a position is
     'open' but moomoo has no matching position.  Happens when the
     broker auto-cancelled an order, or after a flatten that didn't
     reach the journal.  Auto-marked closed with reason
     'reconcile_phantom' so it stops blocking new entries.

The reconciler is **read-only against the broker** — never sends orders.
Side-effects are limited to journal updates + Telegram alerts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.broker import BrokerNotConnected, get_broker
from core.journal import get_journal

logger = logging.getLogger(__name__)


def _option_code_to_legs_signature(code: str) -> tuple[str, float, str, str]:
    """Parse 'US.SPY260428C580000' → (symbol, strike, right, expiry).

    Returns (symbol, strike_float, right, expiry_yyyymmdd).
    Returns ('', 0.0, '', '') on parse failure.
    """
    try:
        # 'US.SPY260428C580000'
        body = code.split(".", 1)[1] if "." in code else code
        # Find C/P boundary (after symbol+date)
        for i, ch in enumerate(body):
            if ch in ("C", "P") and body[i - 1].isdigit():
                symbol = body[:i - 6]
                yymmdd = body[i - 6:i]
                right = ch
                strike_thousandths = int(body[i + 1:])
                strike = strike_thousandths / 1000.0
                expiry = "20" + yymmdd
                return symbol, strike, right, expiry
        return "", 0.0, "", ""
    except Exception:  # noqa: BLE001
        return "", 0.0, "", ""


def _journal_legs_signature(pos) -> set[tuple[str, float, str, str]]:
    """Return set of (symbol, strike, right, expiry) tuples for a journal position."""
    sig = set()
    for leg in (pos.legs or []):
        sig.add((
            pos.symbol,
            float(leg.get("strike", 0)),
            (leg.get("right") or "").upper(),
            str(leg.get("expiry", "")),
        ))
    return sig


async def reconcile_once() -> dict[str, Any]:
    """Compare moomoo broker positions vs journal open positions.

    Returns a dict with counts + list of detected issues.  Safe to call
    even when broker isn't connected (returns ``{"skipped": "broker_not_connected"}``).
    """
    journal = get_journal()
    try:
        broker = get_broker("moomoo")
    except BrokerNotConnected:
        return {"skipped": "broker_not_connected"}

    if not broker.is_alive():
        return {"skipped": "broker_not_alive"}

    try:
        broker_positions = await broker.get_positions()
    except Exception as exc:  # noqa: BLE001
        logger.warning("reconcile: get_positions failed: %s", exc)
        return {"skipped": "get_positions_failed", "error": str(exc)}

    # Build broker-side signature: which (symbol, strike, right, expiry, side)
    # legs does the broker think it has?
    broker_legs: dict[tuple[str, float, str, str], int] = {}
    for row in broker_positions or []:
        code = str(row.get("code", ""))
        symbol, strike, right, expiry = _option_code_to_legs_signature(code)
        if not symbol or strike <= 0:
            continue
        qty = int(float(row.get("qty", 0) or 0))
        # qty<0 = short, qty>0 = long
        if qty == 0:
            continue
        key = (symbol, strike, right, expiry)
        broker_legs[key] = broker_legs.get(key, 0) + qty

    # Journal side: which legs do open moomoo positions claim?
    open_journal = [p for p in journal.list_open() if p.broker == "moomoo"]
    journal_legs: dict[tuple[str, float, str, str], int] = {}
    journal_pos_for_key: dict[tuple[str, float, str, str], list[str]] = {}
    for pos in open_journal:
        for leg in (pos.legs or []):
            key = (
                pos.symbol,
                float(leg.get("strike", 0)),
                (leg.get("right") or "").upper(),
                str(leg.get("expiry", "")),
            )
            qty = int(leg.get("qty", pos.contracts) or pos.contracts)
            sign = +1 if leg.get("side") == "long" else -1
            journal_legs[key] = journal_legs.get(key, 0) + sign * qty
            journal_pos_for_key.setdefault(key, []).append(pos.id)

    # Detect orphans: legs in broker but not (or not enough) in journal.
    orphans = []
    for key, broker_qty in broker_legs.items():
        journal_qty = journal_legs.get(key, 0)
        if broker_qty != journal_qty:
            orphans.append({
                "symbol": key[0], "strike": key[1], "right": key[2],
                "expiry": key[3],
                "broker_qty": broker_qty,
                "journal_qty": journal_qty,
                "delta": broker_qty - journal_qty,
            })

    # Detect phantoms: positions in journal whose ALL legs are missing
    # from broker.  (Partial matches → orphan list above; we don't auto-
    # close partials — operator must intervene.)
    phantoms = []
    for pos in open_journal:
        all_missing = True
        for leg in (pos.legs or []):
            key = (
                pos.symbol,
                float(leg.get("strike", 0)),
                (leg.get("right") or "").upper(),
                str(leg.get("expiry", "")),
            )
            if broker_legs.get(key, 0) != 0:
                all_missing = False
                break
        if all_missing:
            phantoms.append({
                "position_id": pos.id, "symbol": pos.symbol,
                "entry_time": pos.entry_time, "state": pos.state,
            })

    # Auto-resolve phantoms: mark journal closed with reason='reconcile_phantom'.
    # Only positions older than 60s — avoids racing with /api/moomoo/execute
    # mid-flight where journal write hasn't landed yet.
    closed_phantoms = []
    cutoff_iso = (datetime.now(timezone.utc) - _timedelta(seconds=60)).isoformat(timespec="seconds")
    for ph in phantoms:
        entry_time = ph.get("entry_time") or ""
        if entry_time and entry_time > cutoff_iso:
            continue  # too fresh, may be in-flight
        try:
            journal.close_position(
                ph["position_id"],
                exit_cost=0.0,
                reason="reconcile_phantom",
                realized_pnl=0.0,
            )
            journal.log_event("reconcile_phantom_closed", subject=ph["position_id"], payload=ph)
            closed_phantoms.append(ph["position_id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconcile: close phantom %s failed: %s", ph["position_id"], exc)

    if orphans:
        journal.log_event("reconcile_orphans_detected", payload={"orphans": orphans})
        try:
            from core.telegram_bot import notify_alert
            count = len(orphans)
            notify_alert(
                "critical",
                f"MOOMOO RECONCILE — {count} orphan leg(s) at broker not in journal. "
                f"Investigate: {orphans[:3]}",
            )
        except Exception:  # noqa: BLE001
            pass

    return {
        "broker_legs": len(broker_legs),
        "journal_open_positions": len(open_journal),
        "orphans": orphans,
        "phantoms": phantoms,
        "phantoms_closed": closed_phantoms,
    }


def _timedelta(*args, **kwargs):
    """Tiny indirection so mocking ``datetime`` in tests doesn't break us."""
    from datetime import timedelta
    return timedelta(*args, **kwargs)
