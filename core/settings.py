"""Environment-backed settings.

Loads a `.env` file (either at project root or under `config/.env`) so
IBKR/Alpaca credentials and risk defaults don't have to live in request
bodies. Endpoints can keep accepting explicit credentials for now; this
layer just provides the fallback so the server can run headless.

NEVER log the raw secrets. The `__repr__` on :class:`Settings` deliberately
masks them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _load_dotenv() -> None:
    """Load `.env` into os.environ. Tries python-dotenv, falls back to manual."""
    candidates = [
        Path(".env"),
        Path("config") / ".env",
    ]
    # Prefer python-dotenv when available for proper quoting/escaping
    try:
        from dotenv import load_dotenv  # type: ignore
        for p in candidates:
            if p.exists():
                load_dotenv(p, override=False)
        return
    except ImportError:
        pass
    # Manual fallback: tolerate `KEY=value` lines (no quoting, no export).
    for p in candidates:
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
        except OSError:
            continue


_load_dotenv()


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key, default)
    return val if val is not None else default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class IBKRSettings:
    host: str = field(default_factory=lambda: _env("IBKR_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("IBKR_PORT", 7497))
    client_id: int = field(default_factory=lambda: _env_int("IBKR_CLIENT_ID", 1))

    def as_dict(self) -> dict:
        return {"host": self.host, "port": self.port, "client_id": self.client_id}


@dataclass(frozen=True)
class AlpacaSettings:
    api_key: str = field(default_factory=lambda: _env("ALPACA_API_KEY"))
    api_secret: str = field(default_factory=lambda: _env("ALPACA_API_SECRET"))
    base_url: str = field(
        default_factory=lambda: _env(
            "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
        )
    )

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        masked = lambda s: (s[:4] + "…" + s[-2:]) if s else ""
        return (
            f"AlpacaSettings(api_key={masked(self.api_key)!r}, "
            f"api_secret={masked(self.api_secret)!r}, base_url={self.base_url!r})"
        )


@dataclass(frozen=True)
class RiskSettings:
    max_concurrent_positions: int = field(
        default_factory=lambda: _env_int("MAX_CONCURRENT_POSITIONS", 2)
    )
    daily_loss_limit_pct: float = field(
        default_factory=lambda: _env_float("DAILY_LOSS_LIMIT_PCT", 2.0)
    )
    default_stop_loss_pct: float = field(
        default_factory=lambda: _env_float("DEFAULT_STOP_LOSS_PCT", 50.0)
    )
    default_take_profit_pct: float = field(
        default_factory=lambda: _env_float("DEFAULT_TAKE_PROFIT_PCT", 50.0)
    )
    default_trailing_stop_pct: float = field(
        default_factory=lambda: _env_float("DEFAULT_TRAILING_STOP_PCT", 0.0)
    )
    fill_timeout_seconds: int = field(
        default_factory=lambda: _env_int("FILL_TIMEOUT_SECONDS", 30)
    )
    monitor_interval_seconds: int = field(
        default_factory=lambda: _env_int("MONITOR_INTERVAL_SECONDS", 15)
    )
    limit_price_haircut: float = field(
        default_factory=lambda: _env_float("LIMIT_PRICE_HAIRCUT", 0.05)
    )


@dataclass(frozen=True)
class Settings:
    ibkr: IBKRSettings = field(default_factory=IBKRSettings)
    alpaca: AlpacaSettings = field(default_factory=AlpacaSettings)
    risk: RiskSettings = field(default_factory=RiskSettings)
    journal_db_path: str = field(
        default_factory=lambda: _env("JOURNAL_DB_PATH", "data/trades.db")
    )
    log_dir: str = field(default_factory=lambda: _env("LOG_DIR", "logs"))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    event_calendar_file: str = field(
        default_factory=lambda: _env("EVENT_CALENDAR_FILE", "config/events_2026.json")
    )
    notify_webhook_url: str = field(
        default_factory=lambda: _env("NOTIFY_WEBHOOK_URL", "")
    )


# Module-level singleton — cheap to construct, immutable.
SETTINGS: Settings = Settings()


def resolve_ibkr_creds(request_creds: Optional[dict] = None) -> dict:
    """Merge request-body creds with env defaults. Request body wins when provided."""
    base = SETTINGS.ibkr.as_dict()
    if request_creds:
        for k, v in request_creds.items():
            if v not in (None, "", 0):
                base[k] = v
    return base


def resolve_alpaca_creds(api_key: str = "", api_secret: str = "") -> tuple[str, str]:
    """Return (key, secret) falling back to env when the request omits them."""
    k = api_key or SETTINGS.alpaca.api_key
    s = api_secret or SETTINGS.alpaca.api_secret
    return k, s
