"""Daily digest notifier (I2).

Sends an end-of-day summary to a configured HTTP webhook (Discord, Slack,
or any JSON-accepting endpoint). Stdlib only — uses urllib.request so no
extra dependencies are needed.

Usage:
    from core.notifier import send_daily_digest
    from core.journal import get_journal

    ok = send_daily_digest(get_journal(), url="https://...")
    # or omit url to read from SETTINGS.notify_webhook_url

Design:
    - ``build_daily_digest`` assembles the payload from Journal data.
    - ``send_webhook`` does the HTTP POST; returns True on 2xx.
    - ``send_daily_digest`` composes both.
    - Discord and Slack webhook URLs are auto-detected by URL substring;
      all others receive a generic ``{"text": ..., "data": ...}`` payload.
    - Failures are logged as warnings but never re-raised — this is
      best-effort notification, not a hard dependency.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from typing import Optional

_log = logging.getLogger(__name__)

# ── Digest builder ─────────────────────────────────────────────────────────


def build_daily_digest(journal) -> dict:
    """Assemble today's performance summary from the journal.

    Returns a plain ``dict`` (serialisable to JSON) with the fields the
    webhook payload needs. Pulls everything from SQLite — no live broker
    calls, so safe to invoke after market close.
    """
    today = date.today().isoformat()
    today_pnl = journal.today_realized_pnl()
    today_trades = journal.today_trade_count()

    # Win / loss breakdown from history.
    win_count = loss_count = 0
    history = journal.history_pnl(days=1)
    if history:
        row = history[0]
        if row.get("date") == today:
            win_count = int(row.get("win_count") or 0)
            loss_count = int(row.get("loss_count") or 0)

    # Open positions summary.
    open_positions = journal.list_open()
    open_summaries = [
        {
            "id": p.id,
            "symbol": p.symbol,
            "direction": p.direction,
            "contracts": p.contracts,
            "entry_cost": p.entry_cost,
            "expiry": p.expiry,
            "state": p.state,
        }
        for p in open_positions
    ]

    return {
        "date": today,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "today_pnl": round(today_pnl, 2),
        "today_trades": today_trades,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate_pct": (
            round(win_count / today_trades * 100, 1) if today_trades > 0 else 0.0
        ),
        "open_positions": len(open_positions),
        "open_summaries": open_summaries,
    }


# ── Payload formatters ─────────────────────────────────────────────────────


def _format_slack(digest: dict) -> dict:
    """Slack Incoming Webhook payload (``{"text": ...}``)."""
    pnl = digest["today_pnl"]
    sign = "+" if pnl >= 0 else ""
    emoji = ":white_check_mark:" if pnl >= 0 else ":warning:"
    lines = [
        f"{emoji} *SPY Spread Bot — Daily Digest {digest['date']}*",
        f"Realized PnL: `{sign}${pnl:.2f}`",
        f"Trades: {digest['today_trades']}  "
        f"(W {digest['win_count']} / L {digest['loss_count']}, "
        f"{digest['win_rate_pct']}% win rate)",
        f"Open positions: {digest['open_positions']}",
    ]
    if digest["open_summaries"]:
        lines.append("Open:")
        for s in digest["open_summaries"]:
            lines.append(
                f"  • {s['symbol']} {s['direction']} ×{s['contracts']} "
                f"exp {s['expiry']} [{s['state']}]"
            )
    return {"text": "\n".join(lines)}


def _format_discord(digest: dict) -> dict:
    """Discord webhook payload (``{"embeds": [...]}`` style)."""
    pnl = digest["today_pnl"]
    sign = "+" if pnl >= 0 else ""
    colour = 0x2ECC71 if pnl >= 0 else 0xE74C3C  # green / red

    fields = [
        {"name": "Realized PnL", "value": f"`{sign}${pnl:.2f}`", "inline": True},
        {"name": "Trades", "value": str(digest["today_trades"]), "inline": True},
        {
            "name": "Win / Loss",
            "value": f"{digest['win_count']} / {digest['loss_count']} "
                     f"({digest['win_rate_pct']}%)",
            "inline": True,
        },
        {
            "name": "Open positions",
            "value": str(digest["open_positions"]),
            "inline": True,
        },
    ]
    if digest["open_summaries"]:
        body = "\n".join(
            f"• {s['symbol']} {s['direction']} ×{s['contracts']} "
            f"exp {s['expiry']} [{s['state']}]"
            for s in digest["open_summaries"]
        )
        fields.append({"name": "Open detail", "value": body, "inline": False})

    return {
        "embeds": [
            {
                "title": f"SPY Spread Bot — Daily Digest {digest['date']}",
                "color": colour,
                "fields": fields,
                "footer": {"text": f"Generated at {digest['generated_at']} UTC"},
            }
        ]
    }


def _format_generic(digest: dict) -> dict:
    """Generic JSON webhook — includes both a ``text`` summary and raw ``data``."""
    pnl = digest["today_pnl"]
    sign = "+" if pnl >= 0 else ""
    text = (
        f"SPY Spread Bot | {digest['date']} | "
        f"PnL {sign}${pnl:.2f} | "
        f"{digest['today_trades']} trades ({digest['win_count']}W/{digest['loss_count']}L) | "
        f"{digest['open_positions']} open"
    )
    return {"text": text, "content": text, "data": digest}


def _build_payload(url: str, digest: dict) -> dict:
    """Choose the right format based on the webhook URL."""
    lower = url.lower()
    if "discord.com" in lower or "discordapp.com" in lower:
        return _format_discord(digest)
    if "slack.com" in lower or "hooks.slack.com" in lower:
        return _format_slack(digest)
    return _format_generic(digest)


# ── HTTP sender ────────────────────────────────────────────────────────────


def send_webhook(
    url: str,
    payload: dict,
    *,
    timeout: int = 10,
) -> bool:
    """POST ``payload`` as JSON to ``url``.

    Returns ``True`` on HTTP 2xx, ``False`` on any error. Errors are
    logged as warnings — callers should not treat False as fatal.
    """
    if not url:
        _log.debug("send_webhook called with empty URL — skipped")
        return False
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            if 200 <= status < 300:
                _log.info(
                    "webhook delivered (HTTP %s)", status,
                    extra={"event_type": "webhook_ok", "status": status, "url": url},
                )
                return True
            _log.warning(
                "webhook returned non-2xx HTTP %s for %s", status, url,
                extra={"event_type": "webhook_error", "status": status},
            )
            return False
    except urllib.error.HTTPError as exc:
        _log.warning(
            "webhook HTTP error %s for %s: %s", exc.code, url, exc.reason,
            extra={"event_type": "webhook_error", "status": exc.code},
        )
        return False
    except Exception as exc:
        _log.warning(
            "webhook delivery failed for %s: %s", url, exc,
            extra={"event_type": "webhook_error"},
        )
        return False


# ── Public API ─────────────────────────────────────────────────────────────


def send_daily_digest(journal, url: Optional[str] = None) -> bool:
    """Build and send the daily digest. Returns True if delivery succeeded.

    ``url`` defaults to ``SETTINGS.notify_webhook_url`` when omitted.
    """
    if url is None:
        try:
            from core.settings import SETTINGS
            url = SETTINGS.notify_webhook_url
        except Exception:
            url = ""

    if not url:
        _log.info(
            "NOTIFY_WEBHOOK_URL not configured — daily digest skipped",
            extra={"event_type": "webhook_skipped"},
        )
        return False

    digest = build_daily_digest(journal)
    payload = _build_payload(url, digest)
    ok = send_webhook(url, payload)
    if ok:
        try:
            journal.log_event(
                "digest_sent",
                subject="daily",
                payload={
                    "url_prefix": url[:40],
                    "today_pnl": digest["today_pnl"],
                    "today_trades": digest["today_trades"],
                },
            )
        except Exception:
            pass
    return ok


__all__ = [
    "build_daily_digest",
    "send_webhook",
    "send_daily_digest",
]
