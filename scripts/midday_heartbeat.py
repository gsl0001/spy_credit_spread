"""Mid-day autonomous heartbeat (12:30 ET).

Light-touch status ping to confirm the bot is alive and what it's done so far.
Silent notification on Telegram (no phone buzz) unless something's wrong.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "trades.db"
sys.path.insert(0, str(ROOT))


def run() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Server alive?
    try:
        subprocess.check_output(["pgrep", "-f", "uvicorn main:app"], text=True)
        alive = True
    except subprocess.CalledProcessError:
        alive = False

    # Today's activity
    with sqlite3.connect(str(DB)) as c:
        c.row_factory = sqlite3.Row
        entered = c.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE broker='moomoo' AND entry_time >= ?",
            (today,),
        ).fetchone()["n"]
        open_now = c.execute(
            "SELECT COUNT(*) AS n FROM positions "
            "WHERE broker='moomoo' AND state IN ('pending','open','closing')",
        ).fetchone()["n"]
        closed_today = c.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(realized_pnl), 0) AS pnl "
            "FROM positions WHERE broker='moomoo' AND state='closed' AND entry_time >= ?",
            (today,),
        ).fetchone()
        risk_blocks = c.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE kind='risk_rejected' AND time >= ?", (today,),
        ).fetchone()["n"]

    print(f"=== Mid-day heartbeat {today} ===")
    print(f"  server alive: {alive}")
    print(f"  entered today: {entered}")
    print(f"  open now: {open_now}")
    print(f"  closed today: {closed_today['n']} (PnL ${closed_today['pnl']:.2f})")
    print(f"  risk_rejected today: {risk_blocks}")

    try:
        from core.telegram_bot import notify, configured
        if configured():
            anomaly = (not alive) or risk_blocks > 50
            icon = "⚠️" if anomaly else "💓"
            msg = (
                f"{icon} *Mid-day heartbeat* {today}\n"
                f"server: {'alive' if alive else '*DOWN*'}\n"
                f"entered: {entered} · open: {open_now}\n"
                f"closed: {closed_today['n']} (PnL ${closed_today['pnl']:.2f})\n"
                f"risk_blocks: {risk_blocks}"
            )
            notify(msg, silent=not anomaly)
    except Exception as e:  # noqa: BLE001
        print(f"  (telegram notify failed: {e})")

    return 0


if __name__ == "__main__":
    sys.exit(run())
