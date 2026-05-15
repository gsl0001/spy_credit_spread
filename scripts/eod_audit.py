"""End-of-day autonomous audit script.

Run from cron at 16:30 ET each test day. Produces a PASS/FAIL line per
invariant the autonomous trading system must satisfy. Designed for the
pre-live test week — failures here block going live.

Usage:
    python3 scripts/eod_audit.py [YYYY-MM-DD]
    # default: today (UTC date)
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


DB = Path(__file__).resolve().parent.parent / "data" / "trades.db"


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    return c


class Audit:
    def __init__(self, day: str):
        self.day = day
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok, detail))

    def render(self) -> int:
        fails = 0
        lines = [f"=== EOD audit for {self.day} ==="]
        fail_lines: list[str] = []
        for name, ok, detail in self.results:
            tag = "PASS" if ok else "FAIL"
            line = f"  [{tag}] {name}" + (f" — {detail}" if detail else "")
            lines.append(line)
            if not ok:
                fails += 1
                fail_lines.append(f"• {name}: {detail}")
        summary = f"\n{len(self.results) - fails}/{len(self.results)} passed."
        lines.append(summary)
        for ln in lines:
            print(ln)

        # Post to Telegram (always — PASS days too, for the test week)
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from core.telegram_bot import notify, configured
            if configured():
                if fails == 0:
                    msg = f"✅ *EOD audit {self.day}*\n{len(self.results)}/{len(self.results)} PASS"
                else:
                    msg = (
                        f"🚨 *EOD audit {self.day} FAILED*\n"
                        f"{len(self.results) - fails}/{len(self.results)} passed\n\n"
                        f"Failures:\n" + "\n".join(fail_lines)
                    )
                notify(msg, silent=(fails == 0))
        except Exception as exc:  # noqa: BLE001
            print(f"  (telegram notify failed: {exc})")

        return 0 if fails == 0 else 1


def run(day: str) -> int:
    a = Audit(day)
    with _conn() as c:
        # ── 1. No open orphan positions left from today ─────────────────
        rows = c.execute(
            "SELECT id, topology FROM positions "
            "WHERE broker='moomoo' AND state IN ('pending','open','closing') "
            "AND entry_time >= ?", (day,)
        ).fetchall()
        orphans = [r for r in rows if "orphan" in (r["topology"] or "")]
        a.check(
            "no orphan positions left open at EOD",
            len(orphans) == 0,
            f"{len(orphans)} found: {[r['id'] for r in orphans]}",
        )

        # ── 2. No pending positions stranded ─────────────────────────────
        pending = c.execute(
            "SELECT id, entry_time FROM positions "
            "WHERE broker='moomoo' AND state='pending' AND entry_time >= ?",
            (day,),
        ).fetchall()
        a.check(
            "no positions stranded in 'pending'",
            len(pending) == 0,
            f"{[r['id'] for r in pending]}",
        )

        # ── 3. Every closed entry has matching Fill rows ────────────────
        closed = c.execute(
            "SELECT id FROM positions "
            "WHERE broker='moomoo' AND state='closed' AND entry_time >= ?",
            (day,),
        ).fetchall()
        missing_fills: list[str] = []
        for pos in closed:
            n = c.execute(
                "SELECT COUNT(*) AS n FROM fills f "
                "JOIN orders o ON o.id = f.order_id "
                "WHERE o.position_id = ? AND o.kind='entry'", (pos["id"],),
            ).fetchone()["n"]
            if n < 2:
                missing_fills.append(pos["id"])
        a.check(
            "every closed position has ≥2 entry fills journaled",
            len(missing_fills) == 0,
            f"missing: {missing_fills}",
        )

        # ── 4. No risk_rejected with reason 'max_concurrent_positions' AND
        #       open count > 0 at the time (i.e. orphan-blocking pattern) ─
        rej = c.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE kind='risk_rejected' AND time >= ? "
            "AND json_extract(payload_json,'$.reason')='max_concurrent_positions'",
            (day,),
        ).fetchone()["n"]
        # informational — flag if extremely high (likely orphan-block)
        a.check(
            "risk_rejected (max_concurrent_positions) frequency sane",
            rej < 100,
            f"{rej} rejections today; if you weren't trading, you may have a stuck orphan",
        )

        # ── 5. No order_failed events (broker-side hard errors) ──────────
        failed = c.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE kind='order_failed' AND time >= ?", (day,),
        ).fetchone()["n"]
        a.check("zero order_failed events", failed == 0, f"{failed} failures")

        # ── 6. No broken_spread events (leg2 dropped after leg1 filled) ──
        broken = c.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE kind='broken_spread' AND time >= ?", (day,),
        ).fetchone()["n"]
        a.check("zero broken_spread events", broken == 0,
                f"{broken} broken_spreads (leg2 failed after leg1 filled)")

        # ── 7. No reconcile_orphan_recorded today (system should journal
        #       its own trades — orphans mean a crash/skipped journal) ────
        rec = c.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE kind='reconcile_orphan_recorded' AND time >= ?", (day,),
        ).fetchone()["n"]
        a.check("zero reconcile_orphan_recorded events", rec == 0,
                f"{rec} orphans recorded by reconciler — crash mid-spread?")

        # ── 8. Every Order has a fill_price that matches Fill.price ─────
        mismatches = c.execute(
            "SELECT o.id, o.fill_price, f.price "
            "FROM orders o JOIN fills f ON f.order_id = o.id "
            "WHERE o.broker='moomoo' AND o.submitted_at >= ? "
            "AND ABS(COALESCE(o.fill_price,0) - COALESCE(f.price,0)) > 0.01",
            (day,),
        ).fetchall()
        a.check(
            "Order.fill_price matches Fill.price for every leg",
            len(mismatches) == 0,
            f"{len(mismatches)} mismatches",
        )

        # ── 9. server_startup count today (uvicorn --reload would show
        #       many; live mode should show 0 unless restarted) ──────────
        starts = c.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE kind='server_startup' AND time >= ?", (day,),
        ).fetchone()["n"]
        a.check("server_startup count ≤ 2", starts <= 2,
                f"{starts} restarts — --reload still on?")

        # ── 10. reload_mode_warning never fired ─────────────────────────
        reload_warn = c.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE kind='reload_mode_warning' AND time >= ?", (day,),
        ).fetchone()["n"]
        a.check("no reload_mode_warning event", reload_warn == 0,
                f"{reload_warn} warnings — using --reload, NOT safe for live")

        # ── 11. Daily P&L row matches sum of closed positions ───────────
        try:
            d = c.execute(
                "SELECT realized, trades FROM daily_pnl WHERE date = ?",
                (day,),
            ).fetchone()
            if d is None:
                a.check("daily_pnl row exists for today",
                        len(closed) == 0,  # OK if no trades
                        f"no row but {len(closed)} closed positions")
            else:
                pos_sum = c.execute(
                    "SELECT COALESCE(SUM(realized_pnl), 0) AS s FROM positions "
                    "WHERE broker='moomoo' AND state='closed' AND entry_time >= ?",
                    (day,),
                ).fetchone()["s"]
                a.check(
                    "daily_pnl.realized == SUM(positions.realized_pnl)",
                    abs((d["realized"] or 0) - (pos_sum or 0)) < 0.01,
                    f"daily={d['realized']:.2f} vs sum={pos_sum:.2f}",
                )
        except Exception as exc:
            a.check("daily_pnl audit", False, str(exc))

        # ── 12. Bypass-event-blackout never used ─────────────────────────
        bypass = c.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE kind='bypass_event_blackout' AND time >= ?", (day,),
        ).fetchone()["n"]
        a.check("bypass_event_blackout never invoked", bypass == 0,
                f"{bypass} bypasses today — production must not allow this")

    return a.render()


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else _today_utc()
    sys.exit(run(day))
