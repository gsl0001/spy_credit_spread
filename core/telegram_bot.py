"""Telegram bot — outgoing notifications + incoming control commands.

Architecture
------------
- **Outgoing**: any module can call ``notify(text)`` to push a message to
  the configured chat. Best-effort — failures are logged, never raised.
- **Incoming**: a polling loop calls Telegram's ``getUpdates`` with a
  short timeout (default every 3s) and dispatches recognised slash
  commands. Polling avoids needing a public webhook URL — everything
  runs on the user's own machine.
- **Authorization**: only messages from the configured ``chat_id`` are
  acted on. Anything else is silently ignored.

The bot is dormant unless both ``TELEGRAM_BOT_TOKEN`` and
``TELEGRAM_CHAT_ID`` env vars are set; ``configured()`` reports state.

Stdlib-only (urllib), matching the rest of the codebase. No new deps.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

_log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Module-level state — last update_id we've processed (for offset polling).
_last_update_id: int = 0
_lock = threading.Lock()

# Command registry: name (without leading /) → handler(args: list[str]) -> str
_COMMANDS: dict[str, Callable[[list[str]], str]] = {}


# ── Public surface ──────────────────────────────────────────────────────


def configured() -> bool:
    """True when both ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` are set."""
    try:
        from core.settings import SETTINGS
        return SETTINGS.telegram.configured
    except Exception:  # noqa: BLE001
        return False


def notify(text: str, *, parse_mode: str = "Markdown", silent: bool = False) -> bool:
    """Send a message to the configured chat. Best-effort, never raises.

    ``silent=True`` uses Telegram's ``disable_notification`` flag — useful
    for routine status pings that shouldn't ping the phone.
    """
    if not configured():
        return False
    from core.settings import SETTINGS
    return _send_message(
        SETTINGS.telegram.bot_token,
        SETTINGS.telegram.chat_id,
        text,
        parse_mode=parse_mode,
        silent=silent,
    )


def poll_once() -> int:
    """Run a single ``getUpdates`` pass and dispatch any new commands.

    Returns the number of updates processed. Safe to call from a sync
    APScheduler job — never raises.
    """
    if not configured():
        return 0
    from core.settings import SETTINGS
    token = SETTINGS.telegram.bot_token
    chat_id = SETTINGS.telegram.chat_id

    global _last_update_id
    with _lock:
        offset = _last_update_id + 1 if _last_update_id else None

    try:
        updates = _get_updates(token, offset=offset)
    except Exception as e:  # noqa: BLE001
        _log.warning("telegram getUpdates failed: %s", e)
        return 0

    processed = 0
    for upd in updates:
        try:
            uid = int(upd.get("update_id") or 0)
        except (TypeError, ValueError):
            uid = 0
        if uid:
            with _lock:
                if uid > _last_update_id:
                    _last_update_id = uid

        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        sender_chat_id = str(chat.get("id") or "")
        if sender_chat_id != str(chat_id):
            # Authorization gate — silently ignore strangers.
            _log.info(
                "ignored telegram message from unauthorized chat %s",
                sender_chat_id,
            )
            continue

        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue

        try:
            reply = _dispatch_command(text)
        except Exception as e:  # noqa: BLE001
            _log.warning("telegram command dispatch failed: %s", e)
            reply = f"⚠️ Command error: `{e}`"

        if reply:
            _send_message(token, chat_id, reply, parse_mode="Markdown")
        processed += 1

    return processed


def reset_polling_offset() -> None:
    """Reset the update-id cursor — useful for tests."""
    global _last_update_id
    with _lock:
        _last_update_id = 0


# ── Command registry ────────────────────────────────────────────────────


def register_command(name: str):
    """Decorator: register a function as a slash-command handler.

    The handler signature is ``handler(args: list[str]) -> str``. Name is
    case-insensitive and stored without the leading slash.
    """
    def deco(fn):
        _COMMANDS[name.lower().lstrip("/")] = fn
        return fn
    return deco


def _dispatch_command(text: str) -> str:
    """Parse ``/cmd a b c`` and call the registered handler. Returns the
    reply text. Unknown commands return a help hint."""
    # Strip leading slash, optional bot mention (`/cmd@MyBot`).
    parts = text.split()
    head = parts[0].lstrip("/")
    if "@" in head:
        head = head.split("@", 1)[0]
    args = parts[1:]
    cmd = head.lower()
    handler = _COMMANDS.get(cmd)
    if handler is None:
        return f"Unknown command `/{cmd}`. Send /help for the command list."
    return handler(args)


def list_commands() -> list[str]:
    """Return registered command names — used by tests + /help builder."""
    return sorted(_COMMANDS.keys())


# ── Telegram HTTP layer (stdlib) ────────────────────────────────────────


def _api_url(token: str, method: str) -> str:
    return _API_BASE.format(token=token, method=method)


def _send_message(
    token: str, chat_id: str, text: str,
    *, parse_mode: str = "Markdown", silent: bool = False,
    timeout: int = 10,
) -> bool:
    """POST sendMessage. Returns True on success."""
    if not token or not chat_id:
        return False
    payload = {
        "chat_id": str(chat_id),
        "text": text[:4000],  # Telegram caps at 4096; leave headroom for safety.
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if silent:
        payload["disable_notification"] = True
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _api_url(token, "sendMessage"),
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        # If markdown parsing failed (common: unescaped underscores in IDs),
        # retry without parse_mode so the message still gets through.
        if parse_mode and exc.code == 400:
            return _send_message(
                token, chat_id, text,
                parse_mode="", silent=silent, timeout=timeout,
            )
        _log.warning(
            "telegram sendMessage HTTP %s: %s", exc.code, exc.reason,
        )
        return False
    except Exception as e:  # noqa: BLE001
        _log.warning("telegram sendMessage failed: %s", e)
        return False


def _get_updates(token: str, *, offset: Optional[int] = None,
                 timeout: int = 0) -> list[dict]:
    """GET /getUpdates with offset cursor.

    ``timeout=0`` makes this a non-blocking poll — Telegram returns any
    pending updates immediately. Long-poll (e.g. timeout=25) holds the
    connection open server-side, but APScheduler-driven polling at 3s
    intervals doesn't need that.
    """
    params: dict[str, Any] = {"timeout": timeout, "limit": 50}
    if offset:
        params["offset"] = offset
    qs = urllib.parse.urlencode(params)
    url = _api_url(token, "getUpdates") + "?" + qs
    try:
        with urllib.request.urlopen(url, timeout=max(timeout + 5, 10)) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        if not data.get("ok"):
            _log.warning("telegram getUpdates returned ok=false: %s", data)
            return []
        return list(data.get("result") or [])
    except urllib.error.HTTPError as exc:
        _log.warning("telegram getUpdates HTTP %s: %s", exc.code, exc.reason)
        return []
    except Exception as e:  # noqa: BLE001
        _log.warning("telegram getUpdates failed: %s", e)
        return []


# ── Built-in commands ───────────────────────────────────────────────────


def _fmt_usd(n: float, *, sign: bool = False) -> str:
    v = float(n or 0)
    s = ("+" if sign and v > 0 else ("-" if v < 0 else "")) + f"${abs(v):,.2f}"
    return s


@register_command("help")
def _cmd_help(_args):
    cmds = list_commands()
    lines = [
        "*SPY Spread Bot — commands*",
        "",
        "_Read-only:_",
        "/status — server + IBKR + moomoo + market state",
        "/moomoo — moomoo broker connection + equity",
        "/positions — open positions and live P&L",
        "/pnl — today's realized P&L + recent history",
        "/presets — list saved scanner presets",
        "/scanner — current scanner state",
        "",
        "_Control:_",
        "/connect-moomoo — connect moomoo broker with default settings",
        "/flatten confirm — close every open position (all brokers)",
        "/flatten ibkr confirm — close only IBKR positions",
        "/flatten moomoo confirm — close only moomoo positions",
        "/preset\\_start <name> — start a preset scanner",
        "/preset\\_stop — stop the active preset scanner",
        "",
        f"_{len(cmds)} commands registered._",
    ]
    return "\n".join(lines)


@register_command("status")
def _cmd_status(_args):
    try:
        from core.journal import get_journal
        from brokers.ibkr_trading import _ib_instances
        from core.settings import SETTINGS
        from core.calendar import is_market_open
        j = get_journal()
        all_open = j.list_open()
        open_ibkr = sum(1 for p in all_open if p.broker == "ibkr")
        open_moomoo = sum(1 for p in all_open if p.broker == "moomoo")
        today_pnl = j.today_realized_pnl()
        today_trades = j.today_trade_count()

        ib_creds = SETTINGS.ibkr.as_dict()
        key = f"{ib_creds['host']}:{ib_creds['port']}:{ib_creds['client_id']}"
        trader = _ib_instances.get(key)
        ib_state = "live" if (trader and trader.is_alive()) else "off"

        # Moomoo connection from registry
        try:
            from core.broker import _registry
            moomoo = _registry.get("moomoo")
            if moomoo and moomoo.is_alive():
                env = "REAL" if getattr(moomoo, "_trd_env_int", 0) == 1 else "SIM"
                moomoo_state = f"live ({env})"
            else:
                moomoo_state = "off"
        except Exception:  # noqa: BLE001
            moomoo_state = "off"

        is_open, why = is_market_open()
        mkt = "OPEN" if is_open else f"CLOSED ({why})"
    except Exception as e:  # noqa: BLE001
        return f"⚠️ status query failed: `{e}`"

    return (
        "*Status*\n"
        f"IBKR:    `{ib_state}`\n"
        f"Moomoo:  `{moomoo_state}`\n"
        f"Market:  `{mkt}`\n"
        f"Open:    *{open_ibkr}* IBKR · *{open_moomoo}* moomoo\n"
        f"Today P&L: *{_fmt_usd(today_pnl, sign=True)}*  ({today_trades} trades)"
    )


@register_command("positions")
def _cmd_positions(_args):
    try:
        from core.journal import get_journal
        positions = get_journal().list_open()
    except Exception as e:  # noqa: BLE001
        return f"⚠️ positions query failed: `{e}`"

    if not positions:
        return "_No open positions._"

    lines = [f"*Open positions ({len(positions)})*"]
    for p in positions:
        legs = " / ".join(
            f"{('+' if leg.get('side') == 'long' else '-')}{leg.get('strike')}{(leg.get('type') or '')[:1].upper()}"
            for leg in p.legs
        ) if p.legs else "—"
        meta = p.meta or {}
        pnl = (meta.get("mtm") or p.entry_cost or 0) - (p.entry_cost or 0)
        lines.append(
            f"`{p.id[:8]}` {p.symbol} {legs} ×{p.contracts} "
            f"exp `{p.expiry}` [{p.state}] "
            f"PnL {_fmt_usd(pnl, sign=True)}"
        )
    return "\n".join(lines)


@register_command("pnl")
def _cmd_pnl(_args):
    try:
        from core.journal import get_journal
        j = get_journal()
        today_pnl = j.today_realized_pnl()
        today_trades = j.today_trade_count()
        history = j.history_pnl(days=7)
    except Exception as e:  # noqa: BLE001
        return f"⚠️ pnl query failed: `{e}`"

    lines = [
        "*P&L*",
        f"Today: *{_fmt_usd(today_pnl, sign=True)}*  ({today_trades} trades)",
    ]
    if history:
        lines.append("")
        lines.append("_Last 7 days:_")
        for row in history[:7]:
            d = row.get("date") or "—"
            v = float(row.get("pnl") or 0)
            t = int(row.get("trades") or 0)
            emoji = "🟢" if v >= 0 else "🔴"
            lines.append(f"{emoji} `{d}` {_fmt_usd(v, sign=True)}  ({t}t)")
    return "\n".join(lines)


@register_command("presets")
def _cmd_presets(_args):
    try:
        from core.presets import PresetStore
        names = [p.name for p in PresetStore().list()]
    except Exception as e:  # noqa: BLE001
        return f"⚠️ presets query failed: `{e}`"
    if not names:
        return "_No saved presets._ Save one in the UI first."
    body = "\n".join(f"• `{n}`" for n in sorted(names))
    return f"*Saved presets*\n{body}\n\nStart one with `/preset_start <name>`"


@register_command("scanner")
def _cmd_scanner(_args):
    try:
        # Lazy import to avoid pulling main.py at module load.
        import main
        scanner = main._preset_scanner
        active = scanner.active_preset
    except Exception as e:  # noqa: BLE001
        return f"⚠️ scanner query failed: `{e}`"
    if active is None:
        return "_Scanner is idle._ Start one with `/preset_start <name>`"
    auto = "ON 🟢" if active.auto_execute else "OFF"
    return (
        f"*Scanner: {active.name}*\n"
        f"Strategy: `{active.strategy_name}`\n"
        f"Cadence: `{active.timing_mode} ({active.timing_value})`\n"
        f"Auto-execute: {auto}\n"
        f"Stop: {active.stop_loss_pct}% · TP: {active.take_profit_pct}%"
    )


@register_command("flatten")
def _cmd_flatten(args):
    """Multi-broker flatten with two-step confirmation.

    Usage:
        /flatten                  — show position counts (no action)
        /flatten confirm          — flatten ALL brokers
        /flatten ibkr confirm     — flatten only IBKR
        /flatten moomoo confirm   — flatten only moomoo
    """
    args = [a.lower() for a in (args or [])]
    if "ibkr" in args:
        broker_filter = "ibkr"
        args = [a for a in args if a != "ibkr"]
    elif "moomoo" in args:
        broker_filter = "moomoo"
        args = [a for a in args if a != "moomoo"]
    else:
        broker_filter = None  # all

    try:
        from core.journal import get_journal
        positions = get_journal().list_open()
        if broker_filter:
            positions = [p for p in positions if p.broker == broker_filter]
    except Exception as e:  # noqa: BLE001
        return f"⚠️ flatten query failed: `{e}`"

    if not positions:
        scope = broker_filter or "any broker"
        return f"_No open positions on {scope} to flatten._"

    if not args or args[0] != "confirm":
        scope = f"*{broker_filter.upper()}*" if broker_filter else "*ALL brokers*"
        cmd_suffix = f"{broker_filter} confirm" if broker_filter else "confirm"
        return (
            f"⚠️ *Confirm flatten on {scope}?*\n"
            f"This will market-close *{len(positions)}* position(s).\n\n"
            f"Send `/flatten {cmd_suffix}` to proceed."
        )

    # Confirmed — schedule per broker on the main loop.
    try:
        import asyncio
        import main
        loop = main._MAIN_LOOP
        if loop is None or not loop.is_running():
            return "⚠️ Server not ready — cannot flatten right now."

        results = []
        if broker_filter in (None, "ibkr"):
            from core.settings import SETTINGS
            ib_creds = SETTINGS.ibkr.as_dict()
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    main.ibkr_flatten_all(main.IBKRConnectRequest(**ib_creds)), loop,
                )
                r = fut.result(timeout=60)
                results.append(("IBKR", r))
            except Exception as e:  # noqa: BLE001
                results.append(("IBKR", {"error": str(e)}))
        if broker_filter in (None, "moomoo"):
            try:
                fut = asyncio.run_coroutine_threadsafe(main.moomoo_flatten_all(), loop)
                r = fut.result(timeout=60)
                results.append(("MOOMOO", r))
            except Exception as e:  # noqa: BLE001
                results.append(("MOOMOO", {"error": str(e)}))
    except Exception as e:  # noqa: BLE001
        return f"⚠️ flatten failed: `{e}`"

    lines = ["✅ *Flatten submitted*"]
    for name, r in results:
        if r.get("error"):
            lines.append(f"  {name}: ⚠️ `{r['error']}`")
        else:
            lines.append(f"  {name}: {r.get('closed', 0)} closing")
    return "\n".join(lines)


@register_command("moomoo")
def _cmd_moomoo(_args):
    """Moomoo broker status: connection, account equity, last healthy."""
    try:
        from core.broker import _registry
        moomoo = _registry.get("moomoo")
        if not moomoo or not moomoo.is_alive():
            return "*Moomoo*: `not connected`"

        env = "REAL" if getattr(moomoo, "_trd_env_int", 0) == 1 else "SIMULATE"
        last_iso = getattr(moomoo, "last_healthy_iso", None) or "—"
        acc_id = getattr(moomoo, "_acc_id", None) or "—"

        # Best-effort live account snapshot
        equity_str = "—"
        try:
            import asyncio, main
            loop = main._MAIN_LOOP
            if loop is not None and loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(moomoo.get_account_summary(), loop)
                acct = fut.result(timeout=5)
                equity_str = _fmt_usd(acct.get("equity", 0))
        except Exception:  # noqa: BLE001
            pass

        return (
            "*Moomoo*\n"
            f"Env:    `{env}`\n"
            f"Acct:   `{acc_id}`\n"
            f"Equity: *{equity_str}*\n"
            f"Last healthy: `{last_iso}`"
        )
    except Exception as e:  # noqa: BLE001
        return f"⚠️ moomoo query failed: `{e}`"


@register_command("connect-moomoo")
def _cmd_connect_moomoo(_args):
    """Connect moomoo broker via the existing FastAPI connect path."""
    try:
        import asyncio
        import main

        loop = main._MAIN_LOOP
        if loop is None or not loop.is_running():
            return "⚠️ Server not ready — cannot connect moomoo right now."

        req = main.MoomooConnectRequest()
        fut = asyncio.run_coroutine_threadsafe(main.moomoo_connect(req), loop)
        result = fut.result(timeout=45)
    except Exception as e:  # noqa: BLE001
        return f"⚠️ moomoo connect failed: `{e}`"

    if not isinstance(result, dict):
        return "⚠️ moomoo connect failed: `unexpected response`"
    if not result.get("connected"):
        return f"⚠️ moomoo connect failed: `{result.get('error') or 'unknown error'}`"

    env = str(result.get("trd_env") or ("REAL" if getattr(req, "trd_env", 0) == 1 else "SIMULATE"))
    acc_id = result.get("acc_id", "—")
    return f"✅ *Moomoo connected*\nEnv: `{env}`\nAcct: `{acc_id}`"


@register_command("preset_start")
def _cmd_preset_start(args):
    if not args:
        return "Usage: `/preset_start <name>`. Use /presets to list."
    name = " ".join(args).strip()
    try:
        import main
        result = main.start_preset_scanner({"name": name})
    except Exception as e:  # noqa: BLE001
        return f"⚠️ preset_start failed: `{e}`"
    if result.get("error"):
        return f"⚠️ {result['error']}: `{result.get('detail', name)}`"
    return f"✅ Scanner started: `{name}`"


@register_command("preset_stop")
def _cmd_preset_stop(_args):
    try:
        import main
        main.stop_preset_scanner()
    except Exception as e:  # noqa: BLE001
        return f"⚠️ preset_stop failed: `{e}`"
    return "✅ Scanner stopped."


# ── Notification formatters — called from main.py event sites ───────────


def notify_entry_submitted(symbol: str, side: str, contracts: int,
                            limit: float, *, idem_key: str = "",
                            preset: str = "") -> None:
    pre = f" via `{preset}`" if preset else ""
    notify(
        f"🟢 *Entry submitted{pre}*\n"
        f"{symbol} {side} ×{contracts} @ {_fmt_usd(limit)}\n"
        f"key: `{idem_key[:24]}`"
    )


def notify_entry_filled(symbol: str, contracts: int, fill_price: float,
                        position_id: str = "") -> None:
    notify(
        f"✅ *Entry filled*\n"
        f"{symbol} ×{contracts} @ {_fmt_usd(fill_price)}\n"
        f"pos: `{position_id[:8]}`"
    )


def notify_entry_rejected(symbol: str, reason: str, detail: str = "") -> None:
    body = f"\n_{detail}_" if detail else ""
    notify(f"🚫 *Entry rejected* — {symbol}\nreason: `{reason}`{body}")


def notify_exit_submitted(symbol: str, reason: str, position_id: str = "") -> None:
    notify(
        f"🟡 *Exit submitted* — {symbol}\n"
        f"reason: `{reason}` · pos `{position_id[:8]}`"
    )


def notify_exit_filled(symbol: str, realized_pnl: float, reason: str,
                       position_id: str = "") -> None:
    emoji = "🟢" if realized_pnl >= 0 else "🔴"
    notify(
        f"{emoji} *Exit filled* — {symbol}\n"
        f"P&L: *{_fmt_usd(realized_pnl, sign=True)}*  ·  reason: `{reason}`\n"
        f"pos: `{position_id[:8]}`"
    )


def notify_alert(level: str, message: str) -> None:
    emoji = {"critical": "🚨", "warning": "⚠️"}.get(level, "ℹ️")
    notify(f"{emoji} *{level.upper()}* — {message}", silent=(level == "info"))


__all__ = [
    "configured",
    "notify",
    "poll_once",
    "register_command",
    "list_commands",
    "reset_polling_offset",
    "notify_entry_submitted",
    "notify_entry_filled",
    "notify_entry_rejected",
    "notify_exit_submitted",
    "notify_exit_filled",
    "notify_alert",
]
