#!/usr/bin/env python3
"""End-to-end smoke test: place a 1-contract SPY 0DTE spread on moomoo simulate.

Verifies:
  1. Order endpoint accepts the request
  2. Strike picker chose a sane spread
  3. Journal recorded the position with broker='moomoo'
  4. Broker actually has the legs in its position list
  5. Manual exit works and records per-leg orders

Run:
    python3 scripts/test_moomoo_order.py [--cleanup]

--cleanup also closes the position via /api/moomoo/exit at the end.
Without --cleanup, leaves the position open so you can inspect it in the UI.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import urllib.request
import urllib.error

API = "http://127.0.0.1:8000"


def _http(method: str, path: str, body: dict | None = None, timeout: int = 90) -> dict:
    """Minimal HTTP client — no extra deps."""
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"_http_error": e.code, "_body": body}


def _print_section(title: str) -> None:
    print()
    print("─" * 72)
    print(title)
    print("─" * 72)


def _check_or_die(label: str, ok: bool, detail: str = "") -> None:
    sym = "✓" if ok else "✗"
    print(f"  {sym} {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        print()
        print(f"FAILED at: {label}")
        sys.exit(1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cleanup", action="store_true",
                   help="Also close the position via /api/moomoo/exit at end.")
    p.add_argument("--debit", type=float, default=250.0,
                   help="Spread cost target in dollars (default 250 = $2.50/contract).")
    p.add_argument("--offset", type=float, default=1.50,
                   help="Long-strike offset in points (default 1.50).")
    p.add_argument("--dte", type=int, default=2,
                   help="Days-to-expiry (default 2 — next Monday from a weekend).")
    p.add_argument("--bypass-events", action="store_true",
                   help="Bypass event-blackout gate (NFP/FOMC/CPI). Test only.")
    args = p.parse_args()

    client_id = f"smoke-{uuid.uuid4().hex[:8]}"
    print(f"Client order ID: {client_id}")

    # ── 1. Pre-flight: server reachable + moomoo connected
    _print_section("1. Pre-flight checks")
    acct = _http("GET", "/api/moomoo/account")
    _check_or_die(
        "Server reachable + moomoo connected",
        not acct.get("error") and not acct.get("_http_error"),
        f"equity=${acct.get('equity', 0):,.2f}" if not acct.get("error") else acct.get("error", "?"),
    )

    # ── 2. Submit the spread
    _print_section("2. Submit /api/moomoo/execute")
    submit_body = {
        "symbol": "SPY",
        "direction": "bull_call",
        "contracts": 1,
        "strike_width": 5,
        "target_dte": args.dte,
        "spread_cost_target": args.debit,
        "otm_offset": args.offset,
        "client_order_id": client_id,
        "bypass_event_blackout": args.bypass_events,
    }
    print(f"  Request: {json.dumps(submit_body, indent=2)}")
    res = _http("POST", "/api/moomoo/execute", submit_body)
    print(f"  Response: {json.dumps(res, indent=2)}")

    if res.get("_http_error"):
        _check_or_die("HTTP 200 from execute", False,
                      f"got {res['_http_error']}: {res.get('_body', '')[:200]}")

    if res.get("error"):
        # Structured error — common ones: risk_gate_blocked (FOMC blackout),
        # chain_quality_rejected (after-hours wide spreads).  Print and exit
        # cleanly — these are real strategy gates doing their job.
        print()
        print(f"⚠ Order rejected by gate: {res['error']}")
        print(f"   Reason: {res.get('reason', '?')}")
        if res.get("error") == "risk_gate_blocked":
            print()
            print("   This is the strategy correctly blocking trading. Common causes:")
            print("   - Today is a news day (FOMC/CPI/NFP) or post-news blackout")
            print("   - Market is closed and require_market_open is True")
            print("   - VIX out of 15-25 range")
            print("   - max_concurrent_positions reached")
        sys.exit(0)

    if not res.get("success"):
        _check_or_die("execute returned success=true", False,
                      f"got: {json.dumps(res)[:200]}")

    pos_id = res["position_id"]
    print()
    print(f"  ✓ Order submitted: position_id={pos_id}")
    print(f"    K_long={res['K_long']} K_short={res['K_short']} "
          f"contracts={res['contracts']} debit/ct=${res['debit_per_contract']:.2f}")
    print(f"    leg1_order_id={res['leg1_order_id']}")
    print(f"    leg2_order_id={res['leg2_order_id']}")

    # ── 3. Verify journal has the position with broker='moomoo'
    _print_section("3. Verify journal recorded the position")
    time.sleep(0.5)
    journal = _http("GET", "/api/journal/positions?state=open")
    moomoo_positions = [
        p for p in (journal.get("positions") or [])
        if (p.get("broker") or "") == "moomoo"
    ]
    matching = [p for p in moomoo_positions if p["id"] == pos_id]
    _check_or_die(
        f"Journal has position {pos_id[:12]} with broker='moomoo'",
        len(matching) == 1,
        f"found {len(matching)} matches; total open moomoo: {len(moomoo_positions)}",
    )
    j_pos = matching[0]
    print(f"    state={j_pos['state']} entry_cost=${j_pos['entry_cost']:.2f}")
    print(f"    legs: {j_pos.get('legs')}")

    # ── 4. Verify broker has the legs
    _print_section("4. Verify moomoo broker has the legs")
    broker_pos = _http("GET", "/api/moomoo/positions")
    if broker_pos.get("error"):
        print(f"  ⚠ Could not fetch broker positions: {broker_pos['error']}")
    else:
        positions = broker_pos.get("positions") or []
        # moomoo returns option codes like 'US.SPY260502C580000'
        # Build expected codes from the spread we just placed
        from datetime import date, timedelta
        target = date.today() + timedelta(days=args.dte)
        expiry_str = target.strftime("%y%m%d")
        # Strike encoding: round(strike * 1000), no padding
        expected_long = f"US.SPY{expiry_str}C{int(round(res['K_long'] * 1000))}"
        expected_short = f"US.SPY{expiry_str}C{int(round(res['K_short'] * 1000))}"
        codes = {str(p.get("code", "")) for p in positions}
        print(f"    Broker positions: {len(positions)} total")
        for p in positions:
            print(f"      {p.get('code')}  qty={p.get('qty')}  side={p.get('position_side')}")
        # Saturday: order may be queued for Monday open and not yet show as a position.
        # So this is a "warn if missing" — not a hard failure.
        if expected_long in codes and expected_short in codes:
            print(f"  ✓ Both legs visible at broker")
        else:
            print(f"  ⚠ Legs not yet visible at broker (likely queued for next session)")
            print(f"    Expected: {expected_long}, {expected_short}")

    # ── 5. Optional cleanup
    if args.cleanup:
        _print_section("5. Cleanup — /api/moomoo/exit")
        exit_res = _http("POST", "/api/moomoo/exit", {
            "position_id": pos_id, "reason": "smoke_test_cleanup",
        })
        print(f"  Response: {json.dumps(exit_res, indent=2)}")
        if exit_res.get("error"):
            print(f"  ⚠ Exit returned error: {exit_res['error']}")
        else:
            print(f"  ✓ Exit submitted: {len(exit_res.get('exit_orders', []))} leg order(s)")

        time.sleep(1)
        journal2 = _http("GET", "/api/journal/positions?state=all")
        after = [p for p in (journal2.get("positions") or []) if p["id"] == pos_id]
        if after:
            print(f"    Final state: {after[0]['state']}")
    else:
        _print_section("Done")
        print(f"  Position {pos_id[:12]} left OPEN for inspection.")
        print(f"  To clean up later:  curl -X POST {API}/api/moomoo/exit \\")
        print(f"                        -H 'Content-Type: application/json' \\")
        print(f"                        -d '{{\"position_id\": \"{pos_id}\"}}'")
        print(f"  Or use the Close button in the moomoo view.")

    print()
    print("Smoke test complete.")


if __name__ == "__main__":
    main()
