"""Pre-market autonomous readiness check (09:15 ET).

Verifies every link in the chain BEFORE the strategy fires at 09:30 ET:
  - server process alive
  - server NOT running --reload
  - moomoo broker connected
  - exactly one moomoo preset is marked auto_execute
  - no stuck pending positions from yesterday
  - critical env vars loaded
  - no leftover orphan positions

Posts Telegram message on every run (✅ ready / 🚨 issues).
Exits 0 if ready, 1 if any blocker.
"""
from __future__ import annotations

import json
import os
import platform
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


def _load_env_file(path: Path, *, override: bool = False) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value


def _uvicorn_processes() -> list[dict[str, str]]:
    """Return uvicorn main:app processes on Unix or Windows."""
    if platform.system().lower().startswith("win"):
        ps_cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'uvicorn main:app' } | "
                "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
            ),
        ]
        try:
            raw = subprocess.check_output(ps_cmd, text=True).strip()
        except Exception:
            return []
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            data = [data]
        return [
            {"pid": str(item.get("ProcessId", "")), "cmd": str(item.get("CommandLine", ""))}
            for item in data
        ]

    try:
        out = subprocess.check_output(["pgrep", "-f", "uvicorn main:app"], text=True).strip()
        pids = [p for p in out.splitlines() if p.strip()]
    except subprocess.CalledProcessError:
        pids = []
    try:
        ps = subprocess.check_output(["ps", "aux"], text=True)
    except Exception:
        ps = ""
    procs = []
    for pid in pids:
        line = next((l for l in ps.splitlines() if pid in l and "uvicorn main:app" in l), "")
        procs.append({"pid": pid, "cmd": line})
    return procs


def run() -> int:
    _load_env_file(ROOT / "config" / ".env", override=False)
    _load_env_file(ROOT / ".env.live", override=True)

    results: list[tuple[str, bool, str]] = []

    # 1. Server process
    procs = _uvicorn_processes()
    pids = ",".join(p["pid"] for p in procs if p.get("pid"))
    results.append(_check("server process running", bool(procs), f"pids: {pids}" if pids else "no uvicorn process"))

    # 2. NOT --reload
    reload_lines = [p["cmd"] for p in procs if "--reload" in p.get("cmd", "")]
    results.append(_check(
        "server NOT in --reload mode",
        len(reload_lines) == 0,
        "found --reload in process" if reload_lines else "",
    ))

    # 3. API responding
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/journal/positions", timeout=5) as r:
            data = json.loads(r.read().decode())
        results.append(_check("API responding", True, f"{len(data.get('positions', []))} open"))
    except Exception as e:  # noqa: BLE001
        results.append(_check("API responding", False, str(e)))

    # 3b. moomoo broker connected
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/moomoo/account", timeout=8) as r:
            account = json.loads(r.read().decode())
        err = account.get("error") if isinstance(account, dict) else None
        results.append(_check(
            "moomoo broker connected",
            not err,
            str(err or "account reachable"),
        ))
    except Exception as e:  # noqa: BLE001
        results.append(_check("moomoo broker connected", False, str(e)))

    # 4. Exactly one moomoo preset is armed for unattended auto_execute
    try:
        with open(ROOT / "config" / "presets.json") as f:
            presets = json.load(f)
        auto = [p["name"] for p in presets if p["broker"] == "moomoo" and p["auto_execute"]]
        results.append(_check(
            "exactly one moomoo auto_execute preset",
            len(auto) == 1,
            f"current: {auto}",
        ))
    except Exception as e:  # noqa: BLE001
        results.append(_check("exactly one moomoo auto_execute preset", False, str(e)))

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
