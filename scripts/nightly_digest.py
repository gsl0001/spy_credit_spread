"""Nightly Telegram digest (21:00 ET).

Comprehensive recap of the trading day for the pre-live test week. Designed
to be the single message you read each evening to know if the system is
on track for Mon 2026-05-18.

Sections:
  1. Day summary (entered, closed, PnL, audit verdict)
  2. Each trade — entry, exit, PnL, exit reason
  3. Rejection breakdown (risk, preflight, chain quality)
  4. System health (restarts, errors, warnings)
  5. Open positions carrying overnight
  6. Test-week scorecard (cumulative PASS/FAIL across days)
  7. Tomorrow's go/no-go
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "trades.db"
sys.path.insert(0, str(ROOT))


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterdays_audit() -> tuple[int, int]:
    """Read today's eod_audit.log entry; return (passed, total). (0,0) if absent."""
    log = ROOT / "logs" / "eod_audit.log"
    if not log.exists():
        return 0, 0
    # Find the most recent "N/M passed." line from today
    today = _today()
    last_pass = None
    with open(log) as f:
        for line in f:
            if today in line and "passed." in line:
                # next pattern: "N/M passed."
                import re
                m = re.search(r"(\d+)/(\d+) passed\.", line)
                if m:
                    last_pass = (int(m.group(1)), int(m.group(2)))
    return last_pass or (0, 0)


def _scorecard_so_far() -> str:
    """Read all of eod_audit.log; return a compact PASS/FAIL string per day."""
    log = ROOT / "logs" / "eod_audit.log"
    if not log.exists():
        return "(no audit log yet)"
    import re
    per_day: dict[str, tuple[int, int]] = {}
    with open(log) as f:
        for line in f:
            m = re.search(r"=== EOD audit for (\d{4}-\d{2}-\d{2}) ===", line)
            if m:
                cur_day = m.group(1)
                continue
            m = re.search(r"(\d+)/(\d+) passed\.", line)
            if m and "cur_day" in dir():
                per_day[cur_day] = (int(m.group(1)), int(m.group(2)))
    if not per_day:
        return "(no audit results yet)"
    parts = []
    for d in sorted(per_day):
        p, t = per_day[d]
        parts.append(f"{d[5:]}: {'✅' if p == t else '🛑'}{p}/{t}")
    return " · ".join(parts)


def run() -> int:
    today = _today()

    with sqlite3.connect(str(DB)) as c:
        c.row_factory = sqlite3.Row

        # 1. Day summary
        entered = c.execute(
            "SELECT id, symbol, topology, state, entry_cost, realized_pnl, "
            "       exit_reason, entry_time, exit_time "
            "FROM positions WHERE broker='moomoo' AND entry_time >= ? "
            "ORDER BY entry_time", (today,),
        ).fetchall()
        closed = [p for p in entered if p["state"] == "closed"]
        open_now = [p for p in entered if p["state"] in ("pending", "open", "closing")]
        total_pnl = sum((p["realized_pnl"] or 0) for p in closed)

        # 2. Rejections breakdown
        rej = c.execute(
            "SELECT kind, COUNT(*) AS n FROM events "
            "WHERE kind IN ('risk_rejected','order_rejected','chain_quality_rejected',"
            "'broken_spread','order_failed') AND time >= ? GROUP BY kind", (today,),
        ).fetchall()
        rej_by_kind = {r["kind"]: r["n"] for r in rej}

        # 3. System health
        startups = c.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind='server_startup' AND time >= ?",
            (today,),
        ).fetchone()["n"]
        reload_warns = c.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind='reload_mode_warning' AND time >= ?",
            (today,),
        ).fetchone()["n"]
        recon_orphans = c.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind='reconcile_orphan_recorded' AND time >= ?",
            (today,),
        ).fetchone()["n"]

    audit_pass, audit_total = _yesterdays_audit()
    scorecard = _scorecard_so_far()

    # Build Telegram message
    icon = "✅" if (audit_total > 0 and audit_pass == audit_total) else (
        "⚠️" if audit_total > 0 else "ℹ️"
    )
    pnl_str = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"

    lines = [
        f"{icon} *Nightly digest* — {today}",
        "",
        f"*Trades today:* {len(entered)} entered · {len(closed)} closed · {len(open_now)} open",
        f"*Realized P&L:* {pnl_str}",
        f"*Audit:* {audit_pass}/{audit_total}" + (" ✅" if audit_pass == audit_total and audit_total > 0 else (" 🛑" if audit_total else " (not run)")),
        "",
    ]

    if closed:
        lines.append("*Closed positions:*")
        for p in closed:
            sym = p["symbol"]
            pnl = p["realized_pnl"] or 0
            sign = "+" if pnl >= 0 else "-"
            lines.append(
                f"  {sym} {p['topology']} → {sign}${abs(pnl):.2f} "
                f"({p['exit_reason'] or 'manual'})"
            )
        lines.append("")

    if open_now:
        lines.append("*Carrying overnight:*")
        for p in open_now:
            lines.append(f"  {p['symbol']} {p['topology']} (state={p['state']})")
        lines.append("")

    if rej_by_kind:
        lines.append("*Blocks:*")
        for k, n in sorted(rej_by_kind.items(), key=lambda x: -x[1]):
            lines.append(f"  {k}: {n}")
        lines.append("")

    if startups > 1 or reload_warns > 0 or recon_orphans > 0:
        lines.append("*System health:*")
        if startups > 1:
            lines.append(f"  ⚠️ {startups} server restarts today")
        if reload_warns:
            lines.append(f"  🛑 {reload_warns} reload_mode_warning — --reload still on")
        if recon_orphans:
            lines.append(f"  🛑 {recon_orphans} reconciler orphans — crash mid-spread?")
        lines.append("")

    lines.append("*Test-week scorecard:*")
    lines.append(scorecard)
    lines.append("")

    # Tomorrow gate
    weekday = datetime.now(timezone.utc).strftime("%a")
    if weekday == "Fri":
        lines.append("_Tomorrow: weekend freeze. Review + commit pre-live-v1._")
    elif weekday == "Sun":
        lines.append("_Tomorrow: GO LIVE_ 🚀")
    else:
        lines.append("_Tomorrow: continue autonomous testing._")

    msg = "\n".join(lines)
    print(msg)

    try:
        from core.telegram_bot import notify, configured
        if configured():
            notify(msg, silent=False)
        else:
            print("  (telegram NOT configured — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)")
    except Exception as e:  # noqa: BLE001
        print(f"  (telegram notify failed: {e})")

    return 0


if __name__ == "__main__":
    sys.exit(run())
