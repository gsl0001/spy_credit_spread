"""Pydantic config classes for the FutuOptions data and exec clients."""
from __future__ import annotations

from nautilus_trader.config import NautilusConfig


class FutuOptionsDataClientConfig(NautilusConfig, frozen=True):
    host: str = "127.0.0.1"
    port: int = 11111
    market: str = "US"
    spy_bar_spec: str = "5-MINUTE"


class FutuOptionsExecClientConfig(NautilusConfig, frozen=True):
    host: str = "127.0.0.1"
    port: int = 11111
    trd_env: int = 0              # 0=simulate, 1=real
    unlock_pwd_md5: str = ""
    acc_id: int = 0               # 0 = auto-discover first margin account
    leg_fill_timeout_s: int = 30
