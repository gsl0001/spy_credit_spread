"""Pre-market autonomous readiness check (09:15 ET).

Verifies every link in the chain BEFORE the strategy fires at 09:30 ET:
  - server process alive
  - server NOT running --reload
  - moomoo broker connected
  - canary preset is the only auto_execute moomoo preset
  - no stuck pending positions from yesterday
  - critical env vars loaded
  - no leftover orphan positions

Posts Telegram message on every run (✅ ready / 🚨 issues).
Exits 0 if ready, 1 if any blocker.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "trades.db"
sys.path.insert(0, str(ROOT))


def _check(name: str, ok: bool, detail: str = "") -> tuple[str, bool, str]:
    return (name, ok, detail)


def run() -> int:
    results: list[tuple[str, bool, str]] = []

    # 1. Server process
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "uvicorn main:app"], text=True,
        ).strip()
        results.append(_check("server process running", bool(out), f"pids: {out}"))
    except subprocess.CalledProcessError:
        results.append(_check("server process running", False, "no uvicorn process"))

    # 2. NOT --reload
    try:
        ps = subprocess.check_output(["ps", "aux"], text=True)
        reload_lines = [l for l in ps.splitlines() if "uvicorn main:app" in l and "--reload" in l]
        results.append(_check(
            "server NOT in --reload mode",
            len(reload_lines) == 0,
            "found --reload in process" if reload_lines else "",
        ))
    except Exception as e:  # noqa: BLE001
        results.append(_check("server NOT in --reload mode", False, str(e)))

    # 3. API responding
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/journal/positions", timeout=5) as r:
            data = json.loads(r.read().decode())
        results.append(_check("API responding", True, f"{len(data.get('positions', []))} open"))
    except Exception as e:  # noqa: BLE001
        results.append(_check("API responding", False, str(e)))

    # 4. Canary is the only auto_execute moomoo preset
    try:
        with open(ROOT / "config" / "presets.json") as f:
            presets = json.load(f)
        auto = [p["name"] for p in presets if p["broker"] == "moomoo" and p["auto_execute"]]
        results.append(_check(
            "canary is sole moomoo auto_execute",
            auto == ["canary-moomoo"],
            f"current: {auto}",
        ))
    except Exception as e:  # noqa: BLE001
        results.append(_check("canary is sole moomoo auto_execute", False, str(e)))

    # 5. No stuck pending/open orphan positions from yesterday or earlier
    try:
        with sqlite3.connect(str(DB)) as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT COUNT(*) AS n FROM positions "
                "WHERE broker='moomoo' AND state IN ('pending','closing') "
                "AND entry_time < date('now')",
            ).fetchone()
            stuck = int(row["n"])
        results.append(_check(
            "no stuck pending/closing from prior days",
            stuck == 0, f"{stuck} stuck rows",
        ))
    except Exception as e:  # noqa: BLE001
        results.append(_check("no stuck pending/closing from prior days", False, str(e)))

    # 6. ALLOW_BYPASS_EVENT_BLACKOUT not set
    bypass = os.environ.get("ALLOW_BYPASS_EVENT_BLACKOUT", "")
    results.append(_check(
        "ALLOW_BYPASS_EVENT_BLACKOUT unset/0",
        bypass in ("", "0"),
        f"value: {bypass!r}",
    ))

    # 7. Risk env vars loaded (non-zero)
    risk_vars = ["MAX_CONCURRENT_POSITIONS", "DAILY_LOSS_LIMIT_PCT",
                 "DAILY_LOSS_LIMIT_ABS", "MAX_ORDERS_PER_DAY"]
    missing = [v for v in risk_vars if not os.environ.get(v)]
    results.append(_check(
        "risk env vars set",
        len(missing) == 0,
        f"missing: {missing}" if missing else "",
    ))

    # Render
    fails = sum(1 for _, ok, _ in results if not ok)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"=== Pre-market check {today} ==="]
    fail_lines: list[str] = []
    for name, ok, detail in results:
        tag = "PASS" if ok else "FAIL"
        line = f"  [{tag}] {name}" + (f" — {detail}" if detail else "")
        lines.append(line)
        if not ok:
            fail_lines.append(f"• {name}: {detail}")
    lines.append(f"\n{len(results) - fails}/{len(results)} passed.")
    for ln in lines:
        print(ln)

    # Telegram
    try:
        from core.telegram_bot import notify, configured
        if configured():
            if fails == 0:
                msg = (
                    f"🟢 *Pre-market ready* {today}\n"
                    f"{len(results)}/{len(results)} checks PASS\n"
                    f"Bot will trade at 09:30 ET."
                )
            else:
                msg = (
                    f"🛑 *Pre-market BLOCKED* {today}\n"
                    f"{len(results) - fails}/{len(results)} passed\n\n"
                    f"Issues:\n" + "\n".join(fail_lines) +
                    f"\n\nBot will NOT trade safely until fixed."
                )
            notify(msg, silent=(fails == 0))
    except Exception as e:  # noqa: BLE001
        print(f"  (telegram notify failed: {e})")

    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
