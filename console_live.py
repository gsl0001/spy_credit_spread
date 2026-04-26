"""
console_live.py — Standalone Console Live Mode for SPY Options Trading.

This script runs the live trading lifecycle (scanner, monitor, fill-watcher) 
directly in the terminal with a rich interactive control board.

Usage:
    python console_live.py [--preset PRESET_NAME] [--interval 15]

Commands:
    ls, use <preset>, pos, orders, flatten, scan on/off, help, exit
"""

import asyncio
import logging
import signal
import sys
import os
import time
import threading
from datetime import datetime, timezone
from typing import Optional, List, Any

from apscheduler.schedulers.background import BackgroundScheduler
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from core.journal import get_journal, Journal, Position, Order
from core.settings import SETTINGS
from core.logger import configure_root_logging, log_event
from core.leader import try_acquire_leadership, release_leadership, is_leader
from ibkr_trading import get_ib_connection

from core.monitor import tick as monitor_tick
from core.fill_watcher import reconcile_once
from core.scanner import Scanner, PresetRequired
from core.presets import PresetStore
from core.telegram_bot import (
    configured as tg_configured,
    notify as tg_notify,
    poll_once as tg_poll_once,
    list_commands as tg_list_commands,
)

# ── Configuration & Global State ───────────────────────────────────────────
configure_root_logging()
logger = logging.getLogger("console_live")

console = Console()
_start_time = time.time()
_last_monitor_tick = None
_last_fill_tick = None
_last_scan_tick = None
_status_msg = "Initializing..."
_logs: List[str] = []
_stop_event = threading.Event()

# System Metrics
_spy_price = 0.0
_ibkr_connected = False
_market_status = "Unknown"

# ── Dashboard Rendering ────────────────────────────────────────────────────

def make_header(scanner: Scanner) -> Panel:
    leader = is_leader()
    l_status = Text("LEADER", style="bold green") if leader else Text("FOLLOWER", style="bold red")
    
    # Connection Status
    conn_color = "green" if _ibkr_connected else "red"
    conn_text = Text("● IBKR", style=conn_color)
    
    # Market Status
    m_color = "green" if _market_status == "open" else "bold yellow"
    m_text = Text(f"MKT: {_market_status.upper()}", style=m_color)
    
    # Uptime
    uptime = int(time.time() - _start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours:02}:{minutes:02}:{seconds:02}"
    
    # SPY Price
    spy_display = Text(f"SPY: ${_spy_price:.2f}", style="bold yellow") if _spy_price > 0 else Text("SPY: ---", style="dim")

    # Preset
    preset_name = scanner.active_preset.name if scanner and scanner.active_preset else "None"
    now = datetime.now().strftime("%H:%M:%S")

    # Telegram bot indicator
    if tg_configured():
        tg_text = Text("● TG", style="green")
    else:
        tg_text = Text("○ TG", style="dim")

    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="center", ratio=1)
    grid.add_column(justify="right", ratio=1)

    grid.add_row(
        Text.assemble(("SYSTEM ", "bold cyan"), ("v2.1 ", "dim"), ("| ", "dim"),
                      conn_text, ("  ", ""), tg_text),
        Text(f"PRESET: {preset_name}", style="bold magenta"),
        Text.assemble((f"{now} ", "bold white"), ("| ", "dim"), l_status)
    )

    grid.add_row(
        Text.assemble(("UPTIME: ", "dim"), (uptime_str, "white")),
        spy_display,
        m_text
    )

    return Panel(grid, title="[bold cyan] SPY OPTIONS CONTROL BOARD [/bold cyan]", border_style="cyan", box=box.ROUNDED)

def make_monitor_table(journal: Journal) -> Table:
    table = Table(box=box.SIMPLE, expand=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold white")
    table.add_column("Staleness", justify="right")

    def fmt_tick(t):
        if not t: return "[red]Never[/red]"
        diff = (datetime.now(timezone.utc) - t).total_seconds()
        color = "green" if diff < 60 else ("yellow" if diff < 120 else "red")
        return f"[{color}]{diff:.1f}s[/{color}]"

    try:
        open_pos = journal.list_open()
        pnl = journal.today_realized_pnl()
        trades = journal.today_trade_count()
        
        table.add_row("Open Pos", str(len(open_pos)), fmt_tick(_last_monitor_tick))
        table.add_row("Today P&L", f"${pnl:.2f}", fmt_tick(_last_fill_tick))
        table.add_row("Trades", str(trades), fmt_tick(_last_scan_tick))
    except Exception:
        table.add_row("Status", "Journal Error", "")

    return table

def make_command_ref() -> Panel:
    # Quick ref for the bottom of the screen
    ref = Text.assemble(
        (" ls ", "bold cyan"), ("list presets  ", "dim"),
        (" use <name> ", "bold cyan"), ("switch  ", "dim"),
        (" pos ", "bold cyan"), ("details  ", "dim"),
        (" flatten ", "bold red"), ("panic close  ", "dim"),
        (" tg ", "bold cyan"), ("telegram  ", "dim"),
        (" exit ", "bold yellow"), ("shutdown", "dim")
    )
    return Panel(ref, title="[dim]Quick Commands[/dim]", border_style="dim", box=box.SIMPLE)


# ── Dashboard panels ──────────────────────────────────────────────────────
# These two functions used to be referenced by ``make_layout`` but never
# defined — calling the dashboard would crash on first render. Adding them
# completes the layout contract.


def make_positions_summary(journal: Journal) -> Table:
    """Compact active-positions table for the dashboard's right pane.

    Lists every position in pending / open / closing state with its
    direction, contract count, entry cost, current state, and an estimate
    of unrealised P&L when ``meta['mtm']`` (mark-to-market) is available.
    The monitor populates ``meta['mtm']`` each tick, so for healthy
    positions the panel shows live P&L.
    """
    table = Table(box=box.SIMPLE, expand=True, show_header=True,
                  header_style="bold cyan", padding=(0, 1))
    table.add_column("Symbol", style="bold white", no_wrap=True)
    table.add_column("Dir",    justify="left",  style="dim")
    table.add_column("Qty",    justify="right")
    table.add_column("Entry",  justify="right")
    table.add_column("State",  justify="center")
    table.add_column("P&L",    justify="right")

    try:
        positions = journal.list_open()
    except Exception as e:
        table.add_row(f"[red]journal error: {e}[/red]", "", "", "", "", "")
        return table

    if not positions:
        table.add_row("[dim]— no active positions —[/dim]", "", "", "", "", "")
        return table

    for p in positions:
        # Best-effort live P&L: prefer meta['mtm'] (set by monitor each tick)
        # × 100 × contracts − entry_cost. Fall back to dash if unavailable.
        pnl_str = "—"
        pnl_style = "dim"
        try:
            meta = p.meta or {}
            mtm = meta.get("mtm")
            if mtm is not None and p.contracts:
                pnl = (float(mtm) * 100.0 * p.contracts) - (p.entry_cost or 0.0)
                pnl_style = "green" if pnl >= 0 else "red"
                pnl_str = f"${pnl:+,.2f}"
        except Exception:
            pass

        state_style = {
            "open":    "green",
            "pending": "yellow",
            "closing": "yellow",
        }.get(p.state, "white")

        table.add_row(
            p.symbol,
            (p.direction or "—")[:10],
            str(p.contracts),
            f"${(p.entry_cost or 0):,.2f}",
            f"[{state_style}]{p.state}[/{state_style}]",
            f"[{pnl_style}]{pnl_str}[/{pnl_style}]",
        )
    return table


def make_logs_panel() -> Panel:
    """Most-recent activity log lines, freshest at the bottom.

    Dashboard footer reserves ~9 visible rows after the cmd-ref slice;
    showing the last 8 entries keeps the most relevant context on screen.
    Lines are rendered as plain text — the timestamps in ``add_log`` use
    ``[HH:MM:SS]`` which would otherwise look like Rich style tags and
    error out the markup parser.
    """
    if not _logs:
        return Panel(
            Text("no logs yet", style="dim"),
            title="[dim]Activity Log[/dim]",
            border_style="dim", box=box.SIMPLE,
        )
    body = Text(no_wrap=False)
    for i, line in enumerate(_logs[-8:]):
        if i:
            body.append("\n")
        body.append(line)  # plain text — no markup interpretation
    return Panel(
        body,
        title="[dim]Activity Log[/dim]",
        border_style="dim", box=box.SIMPLE,
    )


def make_layout(journal: Journal, scanner: Scanner) -> Layout:
    layout = Layout()
    layout.split(
        Layout(name="header", size=5),
        Layout(name="main"),
        Layout(name="footer", size=12)
    )
    layout["main"].split_row(
        Layout(name="monitor", ratio=1),
        Layout(name="positions", ratio=2)
    )
    layout["footer"].split_column(
        Layout(name="logs", ratio=3),
        Layout(name="cmd_ref", size=3)
    )
    
    layout["header"].update(make_header(scanner))
    layout["monitor"].update(Panel(make_monitor_table(journal), title="Health", border_style="dim"))
    layout["positions"].update(Panel(make_positions_summary(journal), title="Active Positions", border_style="dim"))
    layout["footer"]["logs"].update(make_logs_panel())
    layout["footer"]["cmd_ref"].update(make_command_ref())
    return layout

# ── Background Task Wrappers ──────────────────────────────────────────────

async def _trader_factory():
    creds = SETTINGS.ibkr.as_dict()
    trader, _ = await get_ib_connection(creds)
    return trader

def run_metrics_job():
    """Lightweight job to update UI-only metrics (Price, Connection, Market)."""
    global _spy_price, _ibkr_connected, _market_status
    
    # 1. Market Status
    from core.calendar import is_market_open
    try:
        is_open, reason = is_market_open()
        _market_status = reason if not is_open else "open"
    except Exception:
        _market_status = "Error"
    
    # 2. IBKR & Price
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async def _update():
            trader = await _trader_factory()
            if trader:
                connected = trader.is_alive()
                price = 0.0
                if connected:
                    res = await trader.get_live_price("SPY")
                    # ``get_live_price`` returns None for ``last`` when the
                    # account has no SPY market-data subscription. Coerce
                    # to 0.0 so ``make_header``'s `_spy_price > 0` check
                    # doesn't throw TypeError.
                    raw = res.get("last") if isinstance(res, dict) else None
                    try:
                        price = float(raw) if raw is not None else 0.0
                    except (TypeError, ValueError):
                        price = 0.0
                return connected, price
            return False, 0.0

        _ibkr_connected, _spy_price = loop.run_until_complete(_update())
    except Exception:
        _ibkr_connected = False
        _spy_price = 0.0

def run_monitor_job(journal: Journal):
    global _last_monitor_tick
    if not is_leader(): return
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(monitor_tick(_trader_factory, journal=journal))
        _last_monitor_tick = datetime.now(timezone.utc)
        if results:
            add_log(f"Monitor: processed {len(results)} positions")
    except Exception as e:
        add_log(f"Monitor Error: {e}")

def run_fill_job(journal: Journal):
    global _last_fill_tick
    if not is_leader(): return
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async def _go():
            trader = await _trader_factory()
            if trader: return await reconcile_once(trader, journal=journal)
        loop.run_until_complete(_go())
        _last_fill_tick = datetime.now(timezone.utc)
    except Exception as e:
        add_log(f"Fill Error: {e}")

def run_scanner_job(scanner: Scanner, journal: Journal):
    global _last_scan_tick
    if not is_leader(): return
    try:
        signals = scanner.tick()
        _last_scan_tick = datetime.now(timezone.utc)
        if signals:
            fired = [s for s in signals if s.fired]
            if fired:
                add_log(f"Scanner: {fired[0].symbol} signal fired!")
            else:
                add_log(f"Scanner: Checked {len(signals)} symbols, no signal.")
    except Exception as e:
        add_log(f"Scanner Error: {e}")


def run_telegram_poll_job():
    """Drain pending Telegram updates and dispatch slash commands.

    Runs in the background thread alongside the monitor / fill / scanner
    jobs. No-op when the bot isn't configured. Exceptions are swallowed
    so a Telegram outage can't take the dashboard down.
    """
    if not tg_configured():
        return
    try:
        n = tg_poll_once()
        if n > 0:
            add_log(f"Telegram: dispatched {n} command(s).")
    except Exception as e:
        add_log(f"Telegram poll error: {e}")


def add_log(msg: str):
    now = datetime.now().strftime("%H:%M:%S")
    _logs.append(f"[{now}] {msg}")
    if len(_logs) > 50: _logs.pop(0)

# ── Commands ───────────────────────────────────────────────────────────────

def cmd_ls(store: PresetStore):
    table = Table(title="Available Presets", box=box.MINIMAL)
    table.add_column("Name", style="bold cyan")
    table.add_column("Mode")
    table.add_column("Topology")
    table.add_column("Auto")
    for p in store.list():
        table.add_row(p.name, p.timing_mode, p.topology, "YES" if p.auto_execute else "no")
    console.print(table)

def cmd_pos(journal: Journal):
    table = Table(title="Detailed Positions", box=box.HEAVY_EDGE)
    table.add_column("ID")
    table.add_column("Symbol")
    table.add_column("Direction")
    table.add_column("Contracts", justify="right")
    table.add_column("Entry Cost", justify="right")
    table.add_column("State")
    table.add_column("P&L", justify="right")
    
    for p in journal.list_all():
        if p.state in ("open", "pending", "closing"):
            pnl = p.realized_pnl or 0.0
            entry_cost = p.entry_cost or 0.0
            color = "green" if pnl >= 0 else "red"
            table.add_row(
                p.id[:8], p.symbol, p.direction or "—", str(p.contracts),
                f"${entry_cost:.2f}", p.state, f"[{color}]${pnl:.2f}[/{color}]"
            )
    console.print(table)

def cmd_orders(journal: Journal):
    table = Table(title="Recent Orders", box=box.SIMPLE)
    table.add_column("Time")
    table.add_column("Sym")
    table.add_column("Side")
    table.add_column("Limit")
    table.add_column("Status")
    
    try:
        orders = journal.list_orders_by_status(("filled", "submitted", "cancelled"))
        for o in orders[:10]:
            # ``submitted_at`` is "YYYY-MM-DDTHH:MM:SS+00:00" — slice the time portion.
            t = (o.submitted_at or "")[11:19] or "—"
            # Market orders have ``limit_price=None`` (used by the panic
            # market-close fallback). Show "MKT" instead of "None".
            limit = "MKT" if o.limit_price is None else f"${o.limit_price:.2f}"
            table.add_row(t, "?", o.side or "—", limit, o.status or "—")
    except Exception as e:
        console.print(f"[red]orders query failed: {e}[/red]")
    console.print(table)

async def cmd_flatten(journal: Journal):
    """Console panic close — uses the same aggressive-haircut + market-fallback
    path as ``/api/ibkr/flatten_all`` so behaviour stays consistent across
    the UI, the FastAPI endpoint, the Telegram bot, and this console."""
    from core.telegram_bot import notify_alert

    open_pos = journal.list_open()
    if not open_pos:
        console.print("[yellow]No open positions to flatten.[/yellow]")
        return

    trader = await _trader_factory()
    if not trader:
        console.print("[red]Error: Could not connect to IBKR[/red]")
        return

    console.print(f"[bold red]FLATTENING {len(open_pos)} POSITIONS...[/bold red]")

    # Reuse main.py's helpers so we get identical behaviour everywhere:
    #   - ``submit_exit_order`` with the panic haircut when we have a quote
    #   - ``_market_close_position`` (market order) when no quote is available
    # Importing main here is a bit ugly but keeps a single source of truth
    # for the close path; refactoring to a shared module is the next step.
    import main as _main_app
    from core.monitor import submit_exit_order

    closed = 0
    failed = 0
    for p in open_pos:
        try:
            legs_list = [dict(leg) for leg in p.legs]
            mid = await trader.get_combo_midpoint(p.symbol, legs_list)
            if mid is None or mid <= 0 or (isinstance(mid, float) and mid != mid):
                # NaN or no quote → market order so it actually closes.
                res = await _main_app._market_close_position(
                    trader, p, journal, reason="console_flatten_market",
                )
            else:
                res = await submit_exit_order(
                    trader, p, float(mid), "console_flatten", journal,
                    haircut_pct=_main_app._EXIT_HAIRCUT_PANIC,
                )
            if res.get("ok"):
                closed += 1
            else:
                failed += 1
                console.print(f"  [red]✗ {p.id[:8]}: {res.get('error', 'failed')}[/red]")
        except Exception as e:
            failed += 1
            console.print(f"  [red]✗ {p.id[:8]}: {e}[/red]")

    add_log(f"Flattened {closed}/{len(open_pos)} positions"
            f"{f' ({failed} failed)' if failed else ''}.")
    # Notify Telegram so the operator sees the panic event on their phone too.
    try:
        notify_alert(
            "critical",
            f"FLATTEN ALL fired from console — {closed}/{len(open_pos)} closing"
            f"{f' ({failed} failed)' if failed else ''}",
        )
    except Exception:
        pass


def cmd_tg(args: list[str]) -> None:
    """Telegram bot command — show status / send a test message."""
    sub = (args[0] if args else "status").lower()

    if sub == "status":
        if not tg_configured():
            console.print(
                "[yellow]Telegram bot:[/yellow] [bold]not configured[/bold]\n"
                "Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in your .env "
                "and restart."
            )
            return
        cmds = ", ".join(f"/{c}" for c in tg_list_commands())
        console.print(
            "[green]Telegram bot:[/green] [bold]active[/bold]\n"
            f"  Polling every {SETTINGS.telegram.poll_interval_seconds}s\n"
            f"  Registered commands: [cyan]{cmds}[/cyan]"
        )
        return

    if sub == "test":
        if not tg_configured():
            console.print("[red]Telegram bot not configured.[/red]")
            return
        text = (
            " ".join(args[1:]).strip()
            or "✅ Test message from SPY console live."
        )
        ok = tg_notify(text)
        console.print(
            f"[green]✓ Sent[/green]" if ok else "[red]✗ Send failed[/red]"
        )
        return

    if sub == "send":
        if not tg_configured():
            console.print("[red]Telegram bot not configured.[/red]")
            return
        text = " ".join(args[1:]).strip()
        if not text:
            console.print("[yellow]Usage: tg send <message>[/yellow]")
            return
        ok = tg_notify(text)
        console.print(
            f"[green]✓ Sent[/green]" if ok else "[red]✗ Send failed[/red]"
        )
        return

    console.print(
        "[yellow]Usage:[/yellow] tg [status|test|send <text>]"
    )

# ── Main Loop ─────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="SPY Console Control Board")
    parser.add_argument("--preset", type=str, help="Initial scanner preset")
    parser.add_argument("--interval", type=int, default=15, help="Monitor interval (s)")
    parser.add_argument("--list-presets", action="store_true", help="List presets and exit")
    args = parser.parse_args()

    store = PresetStore()
    if args.list_presets:
        cmd_ls(store)
        return

    journal = get_journal()
    scheduler = BackgroundScheduler()
    
    if not try_acquire_leadership("data/monitor.lock"):
        add_log("Follower mode: leadership held by another instance.")

    from main import _preset_bars_fetcher
    scanner = Scanner(store=store, bars_fetcher=_preset_bars_fetcher)
    if args.preset:
        try:
            scanner.load_preset(args.preset)
            add_log(f"Loaded preset: {args.preset}")
        except:
            console.print(f"[red]Error loading preset {args.preset}[/red]")

    scheduler.add_job(run_monitor_job, "interval", seconds=args.interval, args=[journal], id="monitor")
    scheduler.add_job(run_fill_job, "interval", seconds=args.interval, args=[journal], id="fills")
    scheduler.add_job(run_metrics_job, "interval", seconds=5, id="metrics")

    # Telegram bot polling — dormant unless TELEGRAM_BOT_TOKEN+CHAT_ID are set.
    # Lets the operator control + monitor the console session from their phone.
    if tg_configured():
        scheduler.add_job(
            run_telegram_poll_job, "interval",
            seconds=max(int(SETTINGS.telegram.poll_interval_seconds), 1),
            id="telegram_poll",
        )
        add_log(f"Telegram bot active — {len(tg_list_commands())} commands registered.")

    def register_scanner():
        if scanner.is_active:
            p = scanner.active_preset
            if p.timing_mode == "interval":
                scheduler.add_job(run_scanner_job, "interval", seconds=p.timing_value, args=[scanner, journal], id="scanner")

    register_scanner()
    scheduler.start()

    add_log("System Online.")
    # Telegram: announce console-mode startup.
    if tg_configured():
        try:
            preset_msg = (
                f" · preset `{scanner.active_preset.name}`"
                if scanner.is_active else ""
            )
            tg_notify(f"📟 *Console Live started*{preset_msg}")
        except Exception:
            pass

    # Dashboard Thread
    def dashboard_loop():
        with Live(make_layout(journal, scanner), refresh_per_second=1, screen=True) as live:
            while not _stop_event.is_set():
                live.update(make_layout(journal, scanner))
                time.sleep(1)

    dash_thread = threading.Thread(target=dashboard_loop, daemon=True)
    dash_thread.start()

    # Command Loop
    try:
        while True:
            # We use a dedicated console for input to not mess with the Live display
            # But in 'screen' mode, we need to handle it carefully.
            # For simplicity, let's just use input() which will suspend the live view on some terminals, 
            # or just print above it.
            cmd = console.input("[bold green]CMD> [/bold green]").strip().lower()
            
            if not cmd: continue
            
            if cmd in ("exit", "quit"):
                break
            elif cmd == "help":
                console.print(
                    "\n[bold]Commands:[/bold]\n"
                    "  ls               List presets\n"
                    "  use <name>       Switch preset\n"
                    "  pos              Show positions\n"
                    "  orders           Show orders\n"
                    "  flatten          Panic close all (notifies Telegram)\n"
                    "  scan on/off      Toggle scanner\n"
                    "  tg [status]      Telegram bot state + commands\n"
                    "  tg test [text]   Send a test message\n"
                    "  tg send <text>   Push an arbitrary message\n"
                    "  clear            Clear logs\n"
                    "  exit             Shutdown\n"
                )
            elif cmd == "ls":
                cmd_ls(store)
            elif cmd == "pos":
                cmd_pos(journal)
            elif cmd == "orders":
                cmd_orders(journal)
            elif cmd.startswith("use "):
                p_name = cmd[4:].strip()
                try:
                    scanner.load_preset(p_name)
                    if scheduler.get_job("scanner"): scheduler.remove_job("scanner")
                    register_scanner()
                    add_log(f"Switched to {p_name}")
                    if tg_configured():
                        try:
                            tg_notify(f"📟 Console preset switched to `{p_name}`")
                        except Exception:
                            pass
                except:
                    console.print(f"[red]Preset {p_name} not found.[/red]")
            elif cmd == "flatten":
                asyncio.run(cmd_flatten(journal))
            elif cmd == "scan off":
                if scheduler.get_job("scanner"): scheduler.remove_job("scanner")
                add_log("Scanner disabled.")
            elif cmd == "scan on":
                register_scanner()
                add_log("Scanner enabled.")
            elif cmd.startswith("tg"):
                # Tokenise: "tg test hello world" → ["test", "hello", "world"]
                tg_args = cmd.split()[1:]
                cmd_tg(tg_args)
            elif cmd == "clear":
                _logs.clear()
            else:
                console.print(f"[yellow]Unknown command: {cmd}[/yellow]")

            time.sleep(0.5)

    except KeyboardInterrupt:
        pass
    finally:
        _stop_event.set()
        scheduler.shutdown()
        release_leadership()
        # Telegram: announce graceful shutdown.
        if tg_configured():
            try:
                tg_notify("🛑 *Console Live stopped*")
            except Exception:
                pass
        console.print("[bold yellow]Shutting down... Goodbye![/bold yellow]")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
