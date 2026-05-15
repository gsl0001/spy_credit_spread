"""Server liveness watchdog (every 5 min during market hours).

Posts a Telegram alert IF the FastAPI server dies and auto-restarts it from
run-live.sh. Silent on success. Tracks consecutive restart attempts to
avoid restart-loop spam — bails after 3 in a row.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "logs" / ".watchdog_state.json"
sys.path.insert(0, str(ROOT))


def _state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_state(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d))


def _alive() -> bool:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8000/api/journal/positions", timeout=3,
        ) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def run() -> int:
    env = os.environ.copy()
    for env_file in (ROOT / "config" / ".env", ROOT / ".env.live"):
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    s = _state()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if _alive():
        if s.get("down_since"):
            # We recovered — notify
            try:
                from core.telegram_bot import notify, configured
                if configured():
                    notify(
                        f"♻️ *Server recovered* {now}\n"
                        f"was down since {s['down_since']}",
                        silent=False,
                    )
            except Exception:  # noqa: BLE001
                pass
        _save_state({"restart_count": 0})
        return 0

    # Server is down
    restart_count = int(s.get("restart_count", 0))
    down_since = s.get("down_since") or now

    if restart_count >= 3:
        # Bail — too many restart attempts; manual intervention needed
        try:
            from core.telegram_bot import notify, configured
            if configured() and not s.get("bailout_sent"):
                notify(
                    f"🆘 *Watchdog GIVING UP* {now}\n"
                    f"server has failed 3 consecutive restart attempts. "
                    f"Manual intervention required.\n"
                    f"Bot is NOT trading.",
                    silent=False,
                )
                _save_state({**s, "bailout_sent": True,
                             "down_since": down_since, "restart_count": restart_count})
        except Exception:  # noqa: BLE001
            pass
        return 1

    # Attempt restart
    print(f"[{now}] server down, attempting restart #{restart_count + 1}")
    try:
        out = open(ROOT / "logs" / "run-live.out", "a")
        if platform.system().lower().startswith("win"):
            subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "scripts" / "run-live.ps1"),
                ],
                cwd=str(ROOT),
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            subprocess.Popen(
                ["./run-live.sh"],
                cwd=str(ROOT),
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except Exception as e:  # noqa: BLE001
        print(f"  restart failed: {e}")

    _save_state({
        "down_since": down_since,
        "restart_count": restart_count + 1,
        "last_attempt": now,
    })

    try:
        from core.telegram_bot import notify, configured
        if configured():
            notify(
                f"🔴 *Server crashed* {now}\n"
                f"watchdog attempt {restart_count + 1}/3 — restarting...",
                silent=False,
            )
    except Exception:  # noqa: BLE001
        pass
    return 1


if __name__ == "__main__":
    sys.exit(run())
