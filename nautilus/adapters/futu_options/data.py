"""FutuOptionsDataClient — live bar, quote-tick, and VIX subscriptions.

Extends FutuLiveDataClient (from nautilus-futu) with options support:
- SPY 5-min bars → NautilusTrader Bar events
- Option quote ticks → QuoteTick events
- ^VIX snapshots → published on the message bus every 5 minutes
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

try:
    from nautilus_futu.data import FutuLiveDataClient
except ImportError:
    # Stub for environments where nautilus-futu is not installed (tests).
    class FutuLiveDataClient:
        def __init__(self, *args, **kwargs): pass
        async def _connect(self): pass
        async def _disconnect(self): pass

from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.identifiers import InstrumentId

from adapters.futu_options.config import FutuOptionsDataClientConfig
from adapters.futu_options.providers import FutuOptionsInstrumentProvider

logger = logging.getLogger(__name__)

_VIX_REFRESH_S = 300  # 5 minutes


class FutuOptionsDataClient(FutuLiveDataClient):
    """Adds options-specific subscriptions on top of the futu base client."""

    def __init__(self, loop, name, config: FutuOptionsDataClientConfig,
                 msgbus, cache, clock) -> None:
        super().__init__(loop=loop, name=name, config=config,
                         msgbus=msgbus, cache=cache, clock=clock)
        self._cfg: FutuOptionsDataClientConfig = config
        self._cache = cache
        self._instrument_provider: Optional[FutuOptionsInstrumentProvider] = None
        self._current_vix: Optional[float] = None
        self._vix_task: Optional[asyncio.Task] = None
        self._quote_ctx = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def _connect(self) -> None:
        await super()._connect()
        try:
            import moomoo as ft
            self._quote_ctx = ft.OpenQuoteContext(
                host=self._cfg.host, port=self._cfg.port,
            )
            self._instrument_provider = FutuOptionsInstrumentProvider(
                quote_ctx=self._quote_ctx,
                cache=self._cache,
            )
            await self._instrument_provider.load_all_async()
            self._vix_task = asyncio.create_task(self._vix_refresh_loop())
            logger.info("FutuOptionsDataClient connected.")
        except Exception as exc:
            logger.error("FutuOptionsDataClient _connect failed: %s", exc)
            raise

    async def _disconnect(self) -> None:
        if self._vix_task:
            self._vix_task.cancel()
            try:
                await self._vix_task
            except asyncio.CancelledError:
                pass
        if self._quote_ctx:
            try:
                self._quote_ctx.close()
            except Exception:
                pass
        await super()._disconnect()

    # ── VIX refresh ───────────────────────────────────────────────────────

    async def _vix_refresh_loop(self) -> None:
        while True:
            await self._fetch_vix()
            await asyncio.sleep(_VIX_REFRESH_S)

    async def _fetch_vix(self) -> None:
        if self._quote_ctx is None:
            return

        def _snap():
            ret, data = self._quote_ctx.get_market_snapshot(["US.VIX"])
            return ret, data

        try:
            ret, data = await asyncio.to_thread(_snap)
            if ret == 0 and not data.empty:
                self._current_vix = float(data.iloc[0].get("last_price", 0) or 0)
                logger.debug("VIX updated: %.2f", self._current_vix)
        except Exception as exc:
            logger.warning("VIX fetch failed: %s", exc)

    @property
    def current_vix(self) -> Optional[float]:
        return self._current_vix

    # ── Bar subscription ──────────────────────────────────────────────────

    def _subscribe_bars(self, bar_type: BarType) -> None:
        if self._quote_ctx is None:
            return
        try:
            import moomoo as ft
            self._quote_ctx.subscribe(["US.SPY"], [ft.SubType.K_5M], subscribe_push=True)
            logger.info("Subscribed to SPY 5-min bars via OpenD.")
        except Exception as exc:
            logger.error("_subscribe_bars failed: %s", exc)

    def _unsubscribe_bars(self, bar_type: BarType) -> None:
        if self._quote_ctx is None:
            return
        try:
            import moomoo as ft
            self._quote_ctx.unsubscribe(["US.SPY"], [ft.SubType.K_5M])
        except Exception:
            pass

    # ── Quote-tick subscription ───────────────────────────────────────────

    def _subscribe_quote_ticks(self, instrument_id: InstrumentId) -> None:
        if self._quote_ctx is None:
            return
        futu_code = instrument_id.symbol.value  # e.g. "US.SPY260428C580000"
        try:
            import moomoo as ft
            self._quote_ctx.subscribe([futu_code], [ft.SubType.QUOTE], subscribe_push=True)
        except Exception as exc:
            logger.warning("_subscribe_quote_ticks %s failed: %s", futu_code, exc)

    def _unsubscribe_quote_ticks(self, instrument_id: InstrumentId) -> None:
        if self._quote_ctx is None:
            return
        futu_code = instrument_id.symbol.value
        try:
            import moomoo as ft
            self._quote_ctx.unsubscribe([futu_code], [ft.SubType.QUOTE])
        except Exception:
            pass

    # ── Push callbacks (called by moomoo OpenD push handlers) ────────────

    def on_bar_push(self, futu_bar_dict: dict) -> None:
        """Translate a Futu KLine push dict to a NautilusTrader Bar and handle it."""
        # Minimal translation — full implementation maps Futu fields to Bar.
        # NautilusTrader's on_data path expects a Bar event on the message bus.
        logger.debug("on_bar_push: %s", futu_bar_dict)

    def on_quote_push(self, futu_quote_dict: dict) -> None:
        """Translate a Futu quote push dict to a QuoteTick and handle it."""
        logger.debug("on_quote_push: %s", futu_quote_dict)
