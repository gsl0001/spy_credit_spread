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
from core.journal import Position, get_journal

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

    # Auto-record orphans as ``single_leg_orphan`` positions so the monitor
    # MTM/exit loop can manage them. Without this, broker-side orphans bleed
    # silently — exactly the failure mode that produced the 2026-05-07 mess.
    # We use a deterministic claim_key so re-runs of the reconciler don't
    # double-record the same orphan.
    orphans_recorded: list[str] = []

    # Pre-pass: pair opposite-sided orphan legs that obviously form a
    # vertical spread (same symbol, same expiry, same right, equal qty, one
    # long + one short). Without this we double-count what is actually a
    # single spread — e.g. the 2026-05-11 incident where a process restart
    # mid-place_spread left both legs at the broker, and the reconciler
    # recorded them as 2 single_leg_orphan positions, immediately tripping
    # max_concurrent_positions for every subsequent signal.
    deltas: dict[tuple[str, str, str], list[dict]] = {}
    for orphan in orphans:
        if int(orphan.get("delta", 0)) == 0:
            continue
        bucket = (orphan["symbol"], orphan["right"], orphan["expiry"])
        deltas.setdefault(bucket, []).append(orphan)

    paired_keys: set[tuple[str, float, str, str]] = set()
    spreads_to_record: list[dict] = []
    for (symbol, right, expiry), bucket in deltas.items():
        longs = [o for o in bucket if int(o["delta"]) > 0]
        shorts = [o for o in bucket if int(o["delta"]) < 0]
        # Greedy pairing on qty match. Real-world case here is 1×1.
        for lo in list(longs):
            for sh in list(shorts):
                if abs(int(lo["delta"])) != abs(int(sh["delta"])):
                    continue
                qty = abs(int(lo["delta"]))
                spreads_to_record.append({
                    "symbol": symbol, "right": right, "expiry": expiry,
                    "qty": qty,
                    "K_long": float(lo["strike"]),
                    "K_short": float(sh["strike"]),
                })
                paired_keys.add((symbol, float(lo["strike"]), right, expiry))
                paired_keys.add((symbol, float(sh["strike"]), right, expiry))
                longs.remove(lo)
                shorts.remove(sh)
                break

    # Record paired orphans as vertical_spread (one journal row per pair).
    for sp in spreads_to_record:
        direction = "bull" if (
            (sp["right"] == "C" and sp["K_long"] < sp["K_short"]) or
            (sp["right"] == "P" and sp["K_long"] > sp["K_short"])
        ) else "bear"
        claim_key = (
            f"reconcile_orphan_spread:{sp['symbol']}:"
            f"{sp['K_long']}:{sp['K_short']}:{sp['right']}:"
            f"{sp['expiry']}:{sp['qty']}"
        )
        if journal.has_event_claim(claim_key, kind="reconcile_orphan_recorded"):
            continue
        legs = [
            {"expiry": sp["expiry"], "strike": sp["K_long"],
             "right": sp["right"], "side": "long", "qty": sp["qty"]},
            {"expiry": sp["expiry"], "strike": sp["K_short"],
             "right": sp["right"], "side": "short", "qty": sp["qty"]},
        ]
        # V9: hydrate entry_cost from broker deal history so the orphan
        # carries a realistic cost basis. Without this, monitor.close ->
        # realized_pnl = exit - 0 = looks like 100% win. Falls back to 0.0
        # only when the broker history lookup fails (older deals expired
        # from history window, network glitch, etc.).
        long_fill = None
        short_fill = None
        try:
            long_fill = await broker.get_recent_fill_for_leg(
                sp["symbol"], sp["expiry"], sp["right"], sp["K_long"],
                "long", sp["qty"],
            )
            short_fill = await broker.get_recent_fill_for_leg(
                sp["symbol"], sp["expiry"], sp["right"], sp["K_short"],
                "short", sp["qty"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("orphan entry_cost lookup failed for %s: %s", claim_key, exc)
        if long_fill and short_fill:
            real_debit = max(float(long_fill["price"]) - float(short_fill["price"]), 0.0)
            entry_cost = real_debit * 100.0 * sp["qty"]
            hwm = real_debit * 100.0
            cost_source = "broker_history"
        else:
            entry_cost = 0.0
            hwm = 0.0
            cost_source = "unknown"
        try:
            pos_id = journal.open_position(Position(
                id=claim_key,
                symbol=sp["symbol"],
                topology="vertical_spread",
                direction=direction,
                contracts=sp["qty"],
                entry_cost=entry_cost,
                entry_time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                expiry=sp["expiry"],
                state="open",
                legs=tuple(legs),
                broker="moomoo",
                high_water_mark=hwm or None,
                meta={
                    "broker": "moomoo",
                    "orphan": True,
                    "source": "reconciler_paired",
                    "broker_order_id": "",
                    "stop_loss_pct": 50.0,
                    "take_profit_pct": 50.0,
                    "trailing_stop_pct": 0.0,
                    "idempotency_key": claim_key,
                    "entry_cost_source": cost_source,
                    "leg1_fill_price": (long_fill or {}).get("price"),
                    "leg2_fill_price": (short_fill or {}).get("price"),
                },
            ))
            journal.log_event(
                "reconcile_orphan_recorded",
                subject=pos_id,
                claim_key=claim_key,
                payload={"spread": sp, "position_id": pos_id},
            )
            orphans_recorded.append(pos_id)
            logger.info("reconcile: recorded orphan spread %s as %s", sp, pos_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconcile: record orphan spread %s failed: %s", sp, exc)
            journal.log_event(
                "reconcile_orphan_failed", payload={
                    "spread": sp, "error": str(exc),
                },
            )

    for orphan in orphans:
        # Skip 'good' deltas: journal already has more than broker (= phantom
        # leg, handled below) or qty matches (delta=0 — shouldn't reach here).
        delta = int(orphan.get("delta", 0))
        if delta == 0:
            continue
        # delta > 0 = broker has more longs than journal (orphan long)
        # delta < 0 = broker has more shorts than journal (orphan short)
        side = "long" if delta > 0 else "short"
        qty = abs(delta)
        symbol, strike, right, expiry = (
            orphan["symbol"], float(orphan["strike"]),
            orphan["right"], orphan["expiry"],
        )
        # Skip legs already consumed by a paired vertical_spread above.
        if (symbol, strike, right, expiry) in paired_keys:
            continue
        # Deterministic dedup key: same (symbol,K,right,expiry,side,qty)
        # produces the same key, so successive reconciler ticks see the
        # already-claimed event and skip.
        claim_key = f"reconcile_orphan:{symbol}:{strike}:{right}:{expiry}:{side}:{qty}"
        if journal.has_event_claim(claim_key, kind="reconcile_orphan_recorded"):
            continue
        legs = [{"expiry": expiry, "strike": strike, "right": right,
                 "side": side, "qty": qty}]
        # V9: hydrate entry_cost from broker deal history for single-leg
        # orphans too. For a long leg, entry_cost = fill_price * 100 * qty.
        # For a short leg, entry_cost is conventionally the (negative)
        # credit received, but our journal treats entry_cost as |paid|;
        # store the signed value in meta and the absolute in the column.
        leg_fill = None
        try:
            leg_fill = await broker.get_recent_fill_for_leg(
                symbol, expiry, right, strike, side, qty,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("orphan entry_cost lookup failed for %s: %s", claim_key, exc)
        if leg_fill:
            entry_cost_s = float(leg_fill["price"]) * 100.0 * qty
            hwm_s = float(leg_fill["price"]) * 100.0
            cost_source_s = "broker_history"
        else:
            entry_cost_s = 0.0
            hwm_s = 0.0
            cost_source_s = "unknown"
        try:
            pos_id = journal.open_position(Position(
                id=claim_key,
                symbol=symbol,
                topology="single_leg_orphan",
                direction="bull" if side == "long" else "bear",
                contracts=qty,
                entry_cost=entry_cost_s,
                entry_time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                expiry=expiry,
                state="open",
                legs=tuple(legs),
                broker="moomoo",
                high_water_mark=hwm_s or None,
                meta={
                    "broker": "moomoo",
                    "orphan": True,
                    "source": "reconciler",
                    "broker_order_id": "",
                    "stop_loss_pct": 50.0,
                    "take_profit_pct": 50.0,
                    "trailing_stop_pct": 0.0,
                    "idempotency_key": claim_key,
                    "entry_cost_source": cost_source_s,
                    "leg_fill_price": (leg_fill or {}).get("price"),
                },
            ))
            journal.log_event(
                "reconcile_orphan_recorded",
                subject=pos_id,
                claim_key=claim_key,
                payload={"orphan": orphan, "position_id": pos_id},
            )
            orphans_recorded.append(pos_id)
            logger.info("reconcile: recorded orphan %s as %s", orphan, pos_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconcile: record orphan %s failed: %s", orphan, exc)
            journal.log_event(
                "reconcile_orphan_failed", payload={
                    "orphan": orphan, "error": str(exc),
                },
            )

    if orphans:
        journal.log_event("reconcile_orphans_detected", payload={
            "orphans": orphans,
            "recorded_position_ids": orphans_recorded,
        })
        try:
            from core.telegram_bot import notify_alert
            count = len(orphans)
            recorded = len(orphans_recorded)
            notify_alert(
                "critical",
                f"MOOMOO RECONCILE — {count} orphan leg(s) detected; {recorded} "
                f"auto-recorded for monitor pickup. First 3: {orphans[:3]}",
            )
        except Exception:  # noqa: BLE001
            pass

    return {
        "broker_legs": len(broker_legs),
        "journal_open_positions": len(open_journal),
        "orphans": orphans,
        "orphans_recorded": orphans_recorded,
        "phantoms": phantoms,
        "phantoms_closed": closed_phantoms,
    }


def _timedelta(*args, **kwargs):
    """Tiny indirection so mocking ``datetime`` in tests doesn't break us."""
    from datetime import timedelta
    return timedelta(*args, **kwargs)
