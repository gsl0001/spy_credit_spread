"""FutuOptionsExecClient — single-leg + legged spread execution.

Extends FutuLiveExecClient (nautilus-futu) with options-specific order handling.
Legged spread execution mirrors the safety net in moomoo_trading.py:
  long fills → submit short; short timeout → cancel + market flatten.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Optional

try:
    from nautilus_futu.execution import FutuLiveExecClient
except ImportError:
    class FutuLiveExecClient:
        def __init__(self, *args, **kwargs): pass
        async def _connect(self): pass
        async def _disconnect(self): pass

from nautilus_trader.execution.messages import SubmitOrder, SubmitOrderList

from adapters.futu_options.config import FutuOptionsExecClientConfig

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 0.5


class FutuOptionsExecClient(FutuLiveExecClient):
    """Adds legged spread execution and options order mapping."""

    def __init__(self, loop, name, config: FutuOptionsExecClientConfig,
                 msgbus, cache, clock) -> None:
        super().__init__(loop=loop, name=name, config=config,
                         msgbus=msgbus, cache=cache, clock=clock)
        self._cfg: FutuOptionsExecClientConfig = config
        self._trd_ctx = None
        self._acc_id: Optional[int] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def _connect(self) -> None:
        await super()._connect()
        try:
            import moomoo as ft
            trd_ctx = ft.OpenSecTradeContext(
                filter_trdmarket=ft.TrdMarket.US,
                host=self._cfg.host,
                port=self._cfg.port,
                security_firm=ft.SecurityFirm.FUTUINC,
            )
            if self._cfg.unlock_pwd_md5:
                ret, data = trd_ctx.unlock_trade(password=self._cfg.unlock_pwd_md5)
                if ret != ft.RET_OK:
                    raise RuntimeError(f"unlock_trade failed: {data}")
            acc_id = self._cfg.acc_id
            if not acc_id:
                ret, acc_data = trd_ctx.get_acc_list()
                if ret != ft.RET_OK:
                    raise RuntimeError(f"get_acc_list failed: {acc_data}")
                us_accounts = acc_data[acc_data["trd_market"] == "US"]
                acc_id = int(us_accounts.iloc[0]["acc_id"])
            self._trd_ctx = trd_ctx
            self._acc_id = acc_id
            logger.info("FutuOptionsExecClient connected (acc_id=%s, trd_env=%s).",
                        acc_id, self._cfg.trd_env)
        except Exception as exc:
            logger.error("FutuOptionsExecClient _connect failed: %s", exc)
            raise

    async def _disconnect(self) -> None:
        if self._trd_ctx:
            try:
                self._trd_ctx.close()
            except Exception:
                pass
        await super()._disconnect()

    # ── Single-leg order ──────────────────────────────────────────────────

    async def _submit_order(self, command: SubmitOrder) -> None:
        """Place a single option leg order via OpenD."""
        import moomoo as ft
        order = command.order
        futu_code = order.instrument_id.symbol.value
        trd_env = ft.TrdEnv.REAL if self._cfg.trd_env == 1 else ft.TrdEnv.SIMULATE
        from nautilus_trader.model.enums import OrderSide
        trd_side = ft.TrdSide.BUY if order.side == OrderSide.BUY else ft.TrdSide.SELL

        def _place():
            ret, data = self._trd_ctx.place_order(
                price=float(order.price),
                qty=int(order.quantity),
                code=futu_code,
                trd_side=trd_side,
                order_type=ft.OrderType.NORMAL,
                trd_env=trd_env,
                acc_id=self._acc_id,
                remark=str(order.client_order_id),
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"place_order failed ({futu_code}): {data}")
            return str(data["order_id"].iloc[0])

        try:
            broker_order_id = await asyncio.to_thread(_place)
            self.generate_order_submitted(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=broker_order_id,
                ts_event=self._clock.timestamp_ns(),
            )
            asyncio.create_task(
                self._poll_fill(order.client_order_id, broker_order_id,
                                order.instrument_id, command.strategy_id,
                                int(order.quantity))
            )
        except Exception as exc:
            self.generate_order_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=str(exc),
                ts_event=self._clock.timestamp_ns(),
            )

    # ── Legged spread ─────────────────────────────────────────────────────

    async def _submit_order_list(self, command: SubmitOrderList) -> None:
        """Execute a 2-leg debit spread as sequential single legs with safety net."""
        if len(command.order_list) != 2:
            logger.error("_submit_order_list: expected 2 legs, got %d", len(command.order_list))
            return

        long_order, short_order = command.order_list
        timeout = self._cfg.leg_fill_timeout_s

        long_id = await self._place_leg_raw(long_order)
        if long_id is None:
            self._reject_all(command, "long_leg_place_failed")
            return

        long_filled = await self._wait_fill(long_id, timeout)
        if not long_filled:
            await self._cancel_raw(long_id)
            self._reject_all(command, "long_leg_timeout")
            return

        short_id = await self._place_leg_raw(short_order)
        if short_id is None:
            await self._market_flatten(long_order)
            self._reject_all(command, "short_leg_place_failed_flattened")
            return

        short_filled = await self._wait_fill(short_id, timeout)
        if not short_filled:
            await self._cancel_raw(short_id)
            await self._market_flatten(long_order)
            self._reject_all(command, "short_leg_timeout_flattened")
            return

        logger.info("Legged spread filled: long=%s short=%s", long_id, short_id)

    # ── Reconciliation ────────────────────────────────────────────────────

    async def generate_order_status_reports(self, *args, **kwargs):
        """Query today's orders from OpenD for startup reconciliation."""
        import moomoo as ft
        trd_env = ft.TrdEnv.REAL if self._cfg.trd_env == 1 else ft.TrdEnv.SIMULATE
        def _fetch():
            ret, data = self._trd_ctx.order_list_query(
                acc_id=self._acc_id, trd_env=trd_env,
            )
            return ret, data
        try:
            ret, data = await asyncio.to_thread(_fetch)
            if ret == 0:
                logger.info("Reconciliation: found %d orders.", len(data))
        except Exception as exc:
            logger.warning("generate_order_status_reports failed: %s", exc)
        return []

    async def generate_fill_reports(self, *args, **kwargs):
        import moomoo as ft
        trd_env = ft.TrdEnv.REAL if self._cfg.trd_env == 1 else ft.TrdEnv.SIMULATE
        def _fetch():
            ret, data = self._trd_ctx.history_deal_list(
                acc_id=self._acc_id, trd_env=trd_env,
            )
            return ret, data
        try:
            await asyncio.to_thread(_fetch)
        except Exception as exc:
            logger.warning("generate_fill_reports failed: %s", exc)
        return []

    async def generate_position_status_reports(self, *args, **kwargs):
        import moomoo as ft
        trd_env = ft.TrdEnv.REAL if self._cfg.trd_env == 1 else ft.TrdEnv.SIMULATE
        def _fetch():
            ret, data = self._trd_ctx.position_list_query(
                acc_id=self._acc_id, trd_env=trd_env,
            )
            return ret, data
        try:
            await asyncio.to_thread(_fetch)
        except Exception as exc:
            logger.warning("generate_position_status_reports failed: %s", exc)
        return []

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _place_leg_raw(self, order) -> Optional[str]:
        import moomoo as ft
        from nautilus_trader.model.enums import OrderSide
        futu_code = order.instrument_id.symbol.value
        trd_env = ft.TrdEnv.REAL if self._cfg.trd_env == 1 else ft.TrdEnv.SIMULATE
        trd_side = ft.TrdSide.BUY if order.side == OrderSide.BUY else ft.TrdSide.SELL

        def _place():
            ret, data = self._trd_ctx.place_order(
                price=float(order.price), qty=int(order.quantity),
                code=futu_code, trd_side=trd_side,
                order_type=ft.OrderType.NORMAL,
                trd_env=trd_env, acc_id=self._acc_id,
                remark=str(order.client_order_id),
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"place_order {futu_code}: {data}")
            return str(data["order_id"].iloc[0])

        try:
            return await asyncio.to_thread(_place)
        except Exception as exc:
            logger.error("_place_leg_raw failed: %s", exc)
            return None

    async def _wait_fill(self, broker_order_id: str, timeout: int) -> bool:
        import moomoo as ft
        trd_env = ft.TrdEnv.REAL if self._cfg.trd_env == 1 else ft.TrdEnv.SIMULATE
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_S)
            def _check():
                ret, data = self._trd_ctx.order_list_query(
                    order_id=broker_order_id, acc_id=self._acc_id, trd_env=trd_env,
                )
                if ret != ft.RET_OK or data.empty:
                    return None
                return str(data.iloc[0]["order_status"])
            status = await asyncio.to_thread(_check)
            if status == "FILLED_ALL":
                return True
        return False

    async def _cancel_raw(self, broker_order_id: str) -> None:
        import moomoo as ft
        trd_env = ft.TrdEnv.REAL if self._cfg.trd_env == 1 else ft.TrdEnv.SIMULATE
        def _cancel():
            self._trd_ctx.modify_order(
                modify_order_op=ft.ModifyOrderOp.CANCEL,
                order_id=broker_order_id, qty=0, price=0,
                acc_id=self._acc_id, trd_env=trd_env,
            )
        try:
            await asyncio.to_thread(_cancel)
        except Exception as exc:
            logger.warning("_cancel_raw %s: %s", broker_order_id, exc)

    async def _market_flatten(self, long_order) -> None:
        import moomoo as ft
        futu_code = long_order.instrument_id.symbol.value
        trd_env = ft.TrdEnv.REAL if self._cfg.trd_env == 1 else ft.TrdEnv.SIMULATE
        def _sell():
            self._trd_ctx.place_order(
                price=0.0, qty=int(long_order.quantity), code=futu_code,
                trd_side=ft.TrdSide.SELL, order_type=ft.OrderType.MARKET,
                trd_env=trd_env, acc_id=self._acc_id, remark="flatten",
            )
        try:
            await asyncio.to_thread(_sell)
            logger.warning("Market flattened long leg: %s", futu_code)
        except Exception as exc:
            logger.error("_market_flatten failed: %s", exc)

    def _reject_all(self, command: SubmitOrderList, reason: str) -> None:
        for order in command.order_list:
            self.generate_order_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=reason,
                ts_event=self._clock.timestamp_ns(),
            )

    async def _poll_fill(self, client_order_id, broker_order_id,
                         instrument_id, strategy_id, qty: int) -> None:
        filled = await self._wait_fill(broker_order_id, self._cfg.leg_fill_timeout_s)
        if filled:
            self.generate_order_filled(
                strategy_id=strategy_id,
                instrument_id=instrument_id,
                client_order_id=client_order_id,
                venue_order_id=broker_order_id,
                venue_position_id=None,
                trade_id=broker_order_id,
                order_side=None,
                order_type=None,
                last_qty=qty,
                last_px=0.0,
                quote_currency=None,
                commission=None,
                liquidity_side=None,
                ts_event=self._clock.timestamp_ns(),
            )
