"""Load environment settings for the nautilus sub-project.

Reads from the parent project's config/.env (dotenv).
All FUTU_* vars are read here; other vars (IBKR, Alpaca) are ignored.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load parent project's .env (two levels up from nautilus/)
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / "config" / ".env"
load_dotenv(_ENV_PATH, override=False)


@dataclass(frozen=True)
class FutuSettings:
    host: str
    port: int
    trd_env: int           # 0=simulate, 1=real
    unlock_pwd_md5: str    # MD5 of trade PIN
    acc_id: int            # 0 = auto-discover first margin account
    leg_fill_timeout_s: int


def load_settings() -> FutuSettings:
    return FutuSettings(
        host=os.getenv("FUTU_HOST", "127.0.0.1"),
        port=int(os.getenv("FUTU_PORT", "11111")),
        trd_env=int(os.getenv("FUTU_TRD_ENV", "0")),
        unlock_pwd_md5=os.getenv("FUTU_UNLOCK_PWD_MD5", ""),
        acc_id=int(os.getenv("FUTU_ACC_ID", "0")),
        leg_fill_timeout_s=int(os.getenv("FUTU_LEG_FILL_TIMEOUT_S", "30")),
    )
