"""Assemble TradingNodeConfig for the live ORB system.

Called by run_live.py. Returns a fully-configured TradingNodeConfig
that wires the FUTU data client, exec client, and trader together.
"""
from __future__ import annotations

from nautilus_trader.config import (
    TradingNodeConfig,
    LiveDataEngineConfig,
    LiveRiskEngineConfig,
    LiveExecEngineConfig,
    LoggingConfig,
)

from adapters.futu_options.config import (
    FutuOptionsDataClientConfig,
    FutuOptionsExecClientConfig,
)
from config.settings import FutuSettings


def build_config(settings: FutuSettings) -> TradingNodeConfig:
    data_cfg = FutuOptionsDataClientConfig(
        host=settings.host,
        port=settings.port,
    )
    exec_cfg = FutuOptionsExecClientConfig(
        host=settings.host,
        port=settings.port,
        trd_env=settings.trd_env,
        unlock_pwd_md5=settings.unlock_pwd_md5,
        acc_id=settings.acc_id,
        leg_fill_timeout_s=settings.leg_fill_timeout_s,
    )
    return TradingNodeConfig(
        trader_id="ORB-FUTU-001",
        data_clients={"FUTU": data_cfg},
        exec_clients={"FUTU": exec_cfg},
        data_engine=LiveDataEngineConfig(debug=False),
        risk_engine=LiveRiskEngineConfig(bypass=False),
        exec_engine=LiveExecEngineConfig(reconciliation=True),
        logging=LoggingConfig(log_level="INFO"),
    )
