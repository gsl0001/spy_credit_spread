"""SPY 0DTE Opening Range Breakout strategy for NautilusTrader + moomoo.

Based on SSRN paper 6355218. Identical entry/exit logic to strategies/orb.py
in the parent project, but expressed in NautilusTrader's event-driven API.

Lifecycle:
  on_start   → subscribe SPY 5-min bars + option quotes
  on_bar     → detect OR window; check entry/exit on each post-OR bar
  on_stop    → close any open position with market orders
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Optional

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import (
    AggregationSource,
    BarAggregation,
    OrderSide,
    PriceType,
    TimeInForce,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

logger = logging.getLogger(__name__)

_ALLOWED_WEEKDAYS = {0, 2, 4}   # Mon, Wed, Fri
_OR_OPEN = time(9, 30)
_OR_CLOSE = time(9, 35)
FUTU_VENUE = Venue("FUTU")


@dataclass
class OrbSpreadConfig(StrategyConfig, frozen=True):
    instrument_id: str = "SPY.FUTU"
    venue: str = "FUTU"
    bar_spec: str = "5-MINUTE"
    offset: float = 1.50
    width: int = 5
    min_range_pct: float = 0.05
    vix_min: float = 15.0
    vix_max: float = 25.0
    take_profit_pct: float = 50.0
    stop_loss_pct: float = 50.0
    time_exit_hhmm: str = "15:30"
    events_file: str = "../config/events_2026.json"


class OrbSpreadStrategy(Strategy):
    """ORB 0DTE spread strategy running on NautilusTrader + moomoo OpenD."""

    def __init__(self, config: OrbSpreadConfig) -> None:
        super().__init__(config)
        self._cfg = config

        # Opening range state (reset each day)
        self._or_high: Optional[float] = None
        self._or_low: Optional[float] = None
        self._or_date: Optional[date] = None

        # Position state
        self._in_trade: bool = False
        self._entry_cost: float = 0.0
        self._long_instrument: Optional[InstrumentId] = None
        self._short_instrument: Optional[InstrumentId] = None
        self._long_mid: float = 0.0
        self._short_mid: float = 0.0

        # Loaded on start
        self._news_dates: set[str] = set()
        self._current_vix: Optional[float] = None

        # Time exit parsed
        try:
            h, m = (int(x) for x in config.time_exit_hhmm.split(":"))
        except Exception:
            h, m = 15, 30
        self._time_exit = time(h, m)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def on_start(self) -> None:
        self._load_news_dates()
        self._subscribe_spy_bars()
        self.log.info("OrbSpreadStrategy started.")

    def on_stop(self) -> None:
        if self._in_trade:
            self.log.warning("Stopping with open position — submitting market close.")
            self._close_all("strategy_stop")
        self.log.info("OrbSpreadStrategy stopped.")

    # ── Bar handler ───────────────────────────────────────────────────────

    def on_bar(self, bar: Bar) -> None:
        ts = bar.ts_event
        # Convert nanoseconds to datetime in ET (naive UTC offset approximation)
        from datetime import datetime, timezone, timedelta
        dt_utc = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)
        dt_et = dt_utc - timedelta(hours=4)   # EDT; adjust for EST (-5) in winter
        bar_date = dt_et.date()
        bar_time = dt_et.time()

        close = float(bar.close)

        # Update OR window (9:30 bar)
        if bar_time >= _OR_OPEN and bar_time < _OR_CLOSE:
            if self._or_date != bar_date:
                self._or_high = float(bar.high)
                self._or_low = float(bar.low)
                self._or_date = bar_date
            else:
                self._or_high = max(self._or_high, float(bar.high))
                self._or_low = min(self._or_low, float(bar.low))
            return

        # Only act on post-OR bars
        if bar_time < _OR_CLOSE:
            return

        # Refresh VIX from the data client
        self._current_vix = self._get_vix()

        # Exit check first
        if self._in_trade:
            self._check_exit(bar_date, bar_time, close)
            return

        # Entry check
        self._check_entry(bar_date, bar_time, close)

    # ── Entry ─────────────────────────────────────────────────────────────

    def _check_entry(self, bar_date: date, bar_time: time, close: float) -> None:
        if self._or_high is None or self._or_low is None:
            return
        if self._or_date != bar_date:
            return  # No OR for today
        if bar_time >= self._time_exit:
            return
        if bar_date.weekday() not in _ALLOWED_WEEKDAYS:
            return
        if bar_date.strftime("%Y-%m-%d") in self._news_dates:
            return
        vix = self._current_vix
        if vix is None or not (self._cfg.vix_min <= vix <= self._cfg.vix_max):
            return
        price = close
        if price <= 0:
            return
        range_size = self._or_high - self._or_low
        if range_size < price * self._cfg.min_range_pct / 100.0:
            return

        bull_break = close > self._or_high
        bear_break = close < self._or_low
        if not bull_break and not bear_break:
            return

        self._submit_spread(bull_break, close, bar_date)

    def _submit_spread(self, bull: bool, breakout_price: float, expiry: date) -> None:
        from adapters.futu_options.providers import FutuOptionsInstrumentProvider
        from nautilus_trader.model.enums import OptionKind

        kind = OptionKind.CALL if bull else OptionKind.PUT
        offset = self._cfg.offset
        width = self._cfg.width

        if bull:
            long_strike = round(breakout_price + offset)
            short_strike = long_strike + width
        else:
            long_strike = round(breakout_price - offset)
            short_strike = long_strike - width

        # Find instruments in cache
        provider = self._get_instrument_provider()
        long_inst = provider.find_by_strike(long_strike, kind, expiry) if provider else None
        short_inst = provider.find_by_strike(short_strike, kind, expiry) if provider else None

        if not long_inst or not short_inst:
            self.log.warning(
                "Cannot find option instruments: long=%s short=%s expiry=%s",
                long_strike, short_strike, expiry,
            )
            return

        from nautilus_trader.model.orders import LimitOrder
        import uuid

        long_order = self.order_factory.limit(
            instrument_id=long_inst.id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str("1"),
            price=Price.from_str(f"{long_inst.info.get('ask', 0):.2f}"),
            time_in_force=TimeInForce.DAY,
            client_order_id=f"L1-{uuid.uuid4().hex[:8]}",
        )
        short_order = self.order_factory.limit(
            instrument_id=short_inst.id,
            order_side=OrderSide.SELL,
            quantity=Quantity.from_str("1"),
            price=Price.from_str(f"{short_inst.info.get('bid', 0):.2f}"),
            time_in_force=TimeInForce.DAY,
            client_order_id=f"L2-{uuid.uuid4().hex[:8]}",
        )

        order_list = self.order_factory.order_list(
            orders=[long_order, short_order],
        )
        self.submit_order_list(order_list)
        self._long_instrument = long_inst.id
        self._short_instrument = short_inst.id
        self._in_trade = True
        self.log.info(
            "Spread submitted: %s K_long=%s K_short=%s",
            "BULL CALL" if bull else "BEAR PUT", long_strike, short_strike,
        )

    # ── Exit ──────────────────────────────────────────────────────────────

    def _check_exit(self, bar_date: date, bar_time: time, close: float) -> None:
        # Time exit
        if bar_time >= self._time_exit:
            self._close_all("time_exit_15:30")
            return

        # P&L exit — evaluate mark price of each leg
        if self._entry_cost <= 0:
            return
        long_mid = self._get_option_mid(self._long_instrument)
        short_mid = self._get_option_mid(self._short_instrument)
        if long_mid is None or short_mid is None:
            return
        current_value = (long_mid - short_mid) * 100.0
        pnl_pct = (current_value - self._entry_cost) / self._entry_cost * 100.0
        if pnl_pct >= self._cfg.take_profit_pct:
            self._close_all("take_profit")
        elif pnl_pct <= -self._cfg.stop_loss_pct:
            self._close_all("stop_loss")

    def _close_all(self, reason: str) -> None:
        if self._long_instrument:
            self.close_position(self._long_instrument, reason=reason)
        if self._short_instrument:
            self.close_position(self._short_instrument, reason=reason)
        self._in_trade = False
        self._entry_cost = 0.0
        self.log.info("Position closed: reason=%s", reason)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _subscribe_spy_bars(self) -> None:
        bar_type = BarType(
            instrument_id=InstrumentId(Symbol("SPY"), FUTU_VENUE),
            bar_spec=BarSpecification(5, BarAggregation.MINUTE, PriceType.LAST),
            aggregation_source=AggregationSource.EXTERNAL,
        )
        self.subscribe_bars(bar_type)

    def _load_news_dates(self) -> None:
        events_path = Path(__file__).resolve().parent.parent / self._cfg.events_file
        try:
            raw = json.loads(events_path.read_text(encoding="utf-8"))
            self._news_dates = {
                item["date"] for item in raw
                if item.get("severity") in ("high", "medium")
            }
            self.log.info("Loaded %d news blackout dates.", len(self._news_dates))
        except Exception as exc:
            self.log.warning("Could not load events file: %s", exc)

    def _get_vix(self) -> Optional[float]:
        try:
            data_client = self.cache.data_client("FUTU")
            if hasattr(data_client, "current_vix"):
                return data_client.current_vix
        except Exception:
            pass
        return None

    def _get_option_mid(self, instrument_id: Optional[InstrumentId]) -> Optional[float]:
        if instrument_id is None:
            return None
        try:
            quote = self.cache.quote_tick(instrument_id)
            if quote:
                return (float(quote.bid_price) + float(quote.ask_price)) / 2.0
        except Exception:
            pass
        return None

    def _get_instrument_provider(self):
        try:
            client = self.cache.data_client("FUTU")
            return getattr(client, "_instrument_provider", None)
        except Exception:
            return None
