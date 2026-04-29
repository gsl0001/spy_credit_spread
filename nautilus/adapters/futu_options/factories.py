"""Factory classes for TradingNode registration."""
from __future__ import annotations

from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory

from adapters.futu_options.config import (
    FutuOptionsDataClientConfig,
    FutuOptionsExecClientConfig,
)
from adapters.futu_options.data import FutuOptionsDataClient
from adapters.futu_options.execution import FutuOptionsExecClient


class FutuOptionsDataClientFactory(LiveDataClientFactory):
    @staticmethod
    def create(
        loop,
        name: str,
        config: FutuOptionsDataClientConfig,
        msgbus,
        cache,
        clock,
    ) -> FutuOptionsDataClient:
        return FutuOptionsDataClient(
            loop=loop,
            name=name,
            config=config,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )


class FutuOptionsExecClientFactory(LiveExecClientFactory):
    @staticmethod
    def create(
        loop,
        name: str,
        config: FutuOptionsExecClientConfig,
        msgbus,
        cache,
        clock,
    ) -> FutuOptionsExecClient:
        return FutuOptionsExecClient(
            loop=loop,
            name=name,
            config=config,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )
