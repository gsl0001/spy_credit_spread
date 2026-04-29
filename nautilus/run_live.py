"""Live trading entry point — TradingNode + moomoo OpenD.

Usage:
    cd nautilus/
    python run_live.py

Prerequisites:
  - moomoo OpenD v8.3+ running on FUTU_HOST:FUTU_PORT
  - FUTU_* env vars set in parent project's config/.env
  - Python 3.12+, nautilus_trader>=1.225.0, nautilus-futu>=0.4.2 installed
"""
import sys
from pathlib import Path

# Add nautilus/ to sys.path so sibling packages resolve without install.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import load_settings
from config.live_config import build_config
from adapters.futu_options.factories import (
    FutuOptionsDataClientFactory,
    FutuOptionsExecClientFactory,
)
from strategies.orb_spread import OrbSpreadStrategy, OrbSpreadConfig

from nautilus_trader.live.node import TradingNode

settings = load_settings()
config = build_config(settings)

node = TradingNode(config=config)
node.add_data_client_factory("FUTU", FutuOptionsDataClientFactory)
node.add_exec_client_factory("FUTU", FutuOptionsExecClientFactory)
node.trader.add_strategy(OrbSpreadStrategy(config=OrbSpreadConfig()))
node.build()

if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()
