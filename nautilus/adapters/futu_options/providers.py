"""FutuOptionsInstrumentProvider — loads SPY option chain from OpenD.

Called during _connect() before the strategy starts. Registers all
OptionContract instruments in the NautilusTrader cache so the strategy
can resolve InstrumentIds by strike and expiry.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Optional

from nautilus_trader.common.component import LiveClock
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OptionKind
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import OptionContract
from nautilus_trader.model.objects import Price, Quantity

logger = logging.getLogger(__name__)

FUTU_VENUE = Venue("FUTU")


def _futu_code_to_instrument_id(futu_code: str) -> InstrumentId:
    """'US.SPY260428C580000' → InstrumentId('US.SPY260428C580000.FUTU')"""
    return InstrumentId(Symbol(futu_code), FUTU_VENUE)


def _build_option_contract(
    futu_code: str,
    strike: float,
    option_type: str,   # "CALL" | "PUT"
    expiry: date,
) -> OptionContract:
    kind = OptionKind.CALL if option_type.upper() == "CALL" else OptionKind.PUT
    return OptionContract(
        instrument_id=_futu_code_to_instrument_id(futu_code),
        raw_symbol=Symbol(futu_code),
        asset_class=None,
        underlying=InstrumentId(Symbol("SPY"), FUTU_VENUE),
        option_kind=kind,
        activation_ns=0,
        expiration_ns=int(
            (expiry - date(1970, 1, 1)).total_seconds() * 1_000_000_000
        ),
        strike_price=Price.from_str(f"{strike:.2f}"),
        currency=USD,
        multiplier=Quantity.from_str("100"),
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_str("1"),
        margin_init=None,
        margin_maint=None,
        max_quantity=None,
        min_quantity=None,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        info={},
        ts_event=0,
        ts_init=0,
    )


class FutuOptionsInstrumentProvider:
    """Loads today's SPY option chain from moomoo OpenD.

    Instruments are cached in the NautilusTrader cache keyed by InstrumentId
    so the strategy can resolve strikes via cache.instrument().
    """

    def __init__(self, quote_ctx, cache) -> None:
        self._quote_ctx = quote_ctx
        self._cache = cache
        self._loaded = False

    async def load_all_async(self, filters: Optional[dict] = None) -> None:
        today = (filters or {}).get("expiry", date.today())
        today_str = today.strftime("%Y-%m-%d") if isinstance(today, date) else str(today)

        def _fetch():
            ret, data = self._quote_ctx.get_option_chain(
                code="US.SPY",
                start=today_str,
                end=today_str,
            )
            return ret, data

        ret, data = await asyncio.to_thread(_fetch)
        if ret != 0:
            logger.error("FutuOptionsInstrumentProvider: get_option_chain failed: %s", data)
            return

        loaded = 0
        for _, row in data.iterrows():
            try:
                futu_code = str(row.get("option_id") or row.get("code", ""))
                strike = float(row.get("strike_price", 0))
                opt_type = str(row.get("option_type", "CALL")).upper()
                exp_str = str(row.get("strike_time", today_str))[:10]
                expiry = date.fromisoformat(exp_str)
                if not futu_code or strike <= 0:
                    continue
                contract = _build_option_contract(futu_code, strike, opt_type, expiry)
                self._cache.add_instrument(contract)
                loaded += 1
            except Exception as exc:
                logger.warning("FutuOptionsInstrumentProvider: skip row: %s", exc)

        self._loaded = True
        logger.info("FutuOptionsInstrumentProvider: loaded %d option contracts", loaded)

    def find_by_strike(
        self,
        strike: float,
        option_kind: OptionKind,
        expiry: date,
    ) -> Optional[OptionContract]:
        """Convenience: find a cached OptionContract by strike + kind + expiry."""
        for inst in self._cache.instruments():
            if not isinstance(inst, OptionContract):
                continue
            if (
                inst.option_kind == option_kind
                and abs(float(inst.strike_price) - strike) < 0.01
                and datetime.fromtimestamp(inst.expiration_ns // 1_000_000_000, tz=timezone.utc).date() == expiry
            ):
                return inst
        return None
