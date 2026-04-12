"""
start.py — Single-command launcher for the SPY Options Backtesting Dashboard.

Usage:
    python start.py

Starts:
  • FastAPI backend  →  http://127.0.0.1:8000
  • React frontend   →  http://localhost:5173
  • Opens the dashboard in your default browser automatically.

Press Ctrl+C to shut down both servers cleanly.
"""

import shutil
import subprocess
import sys
import os
import time
import signal
import webbrowser
import threading
from pathlib import Path


def _resolve_npm() -> str:
    """Locate npm binary in a cross-platform way (handles `npm.cmd` on Windows)."""
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise FileNotFoundError("npm not found in PATH — install Node.js first.")
    return npm

BASE_DIR    = Path(__file__).parent.resolve()
FRONTEND_DIR = BASE_DIR / "frontend"

# ── Colour helpers (work on Windows ≥ 10 and all Unix) ─────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RED    = "\033[91m"
DIM    = "\033[2m"

def log(tag: str, colour: str, msg: str):
    print(f"  {colour}{BOLD}[{tag}]{RESET} {msg}")


# ── Pre-flight checks ───────────────────────────────────────────────────────
def preflight():
    # Check frontend dependencies exist
    if not (FRONTEND_DIR / "node_modules").exists():
        log("SETUP", YELLOW, "node_modules not found — running npm install…")
        try:
            npm = _resolve_npm()
        except FileNotFoundError as e:
            log("ERROR", RED, str(e))
            sys.exit(1)
        result = subprocess.run([npm, "install"], cwd=FRONTEND_DIR)
        if result.returncode != 0:
            log("ERROR", RED, "npm install failed. Make sure Node.js is installed.")
            sys.exit(1)
        log("SETUP", GREEN, "npm install complete.")

    # Check Python deps
    missing = []
    for pkg in ["fastapi", "uvicorn", "yfinance", "scipy", "pandas"]:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            missing.append(pkg)
    if missing:
        log("SETUP", YELLOW, f"Installing missing packages: {', '.join(missing)}")
        subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True)
        log("SETUP", GREEN, "Python packages installed.")


# ── Process launchers ───────────────────────────────────────────────────────
def start_backend() -> subprocess.Popen:
    log("BACKEND", GREEN, f"Starting FastAPI  ->  http://127.0.0.1:8000")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", "8000", "--reload"],
        cwd=BASE_DIR,
        # Prefix every backend line with a dim label
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def start_frontend() -> subprocess.Popen:
    log("FRONTEND", CYAN, f"Starting React     ->  http://localhost:5173")
    npm = _resolve_npm()
    return subprocess.Popen(
        [npm, "run", "dev"],
        cwd=FRONTEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def stream_output(proc: subprocess.Popen, tag: str, colour: str):
    """Read a process's stdout line-by-line and print with a coloured prefix."""
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            print(f"  {colour}{DIM}[{tag}]{RESET} {DIM}{line}{RESET}")


def open_browser_when_ready(url: str, delay: float = 4.0):
    """Wait a few seconds then open the browser."""
    def _open():
        time.sleep(delay)
        log("BROWSER", GREEN, f"Opening {url}")
        webbrowser.open(url)
    threading.Thread(target=_open, daemon=True).start()


# ── Entry point ─────────────────────────────────────────────────────────────
def main():
    # Enable ANSI colours on Windows
    if sys.platform == "win32":
        os.system("color")

    print(f"  {BOLD}{CYAN}------------------------------------------------{RESET}")
    print(f"  {BOLD}{CYAN}  SPY Options Backtesting Dashboard          {RESET}")
    print(f"  {BOLD}{CYAN}------------------------------------------------{RESET}")

    preflight()
    print()

    backend  = start_backend()
    frontend = start_frontend()

    # Stream both outputs in background threads so they don't block each other
    threading.Thread(target=stream_output, args=(backend,  "API",  GREEN), daemon=True).start()
    threading.Thread(target=stream_output, args=(frontend, "UI",   CYAN),  daemon=True).start()

    open_browser_when_ready("http://localhost:5173", delay=5.0)

    print()
    log("INFO", YELLOW, "Both servers are starting up…")
    log("INFO", YELLOW, "Dashboard will open automatically in your browser.")
    log("INFO", YELLOW, "Press Ctrl+C to stop everything.\n")

    # ── Wait for Ctrl+C ─────────────────────────────────────────────────────
    try:
        while True:
            # If either process dies unexpectedly, exit
            if backend.poll() is not None:
                log("ERROR", RED, "Backend process exited unexpectedly.")
                break
            if frontend.poll() is not None:
                log("ERROR", RED, "Frontend process exited unexpectedly.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        log("INFO", YELLOW, "Shutting down…")
    finally:
        for proc, name in [(frontend, "Frontend"), (backend, "Backend")]:
            if proc.poll() is None:
                if sys.platform == "win32":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                log("INFO", DIM, f"{name} stopped.")
        print()
        log("INFO", GREEN, "All done. Goodbye!")
        print()


if __name__ == "__main__":
    main()
