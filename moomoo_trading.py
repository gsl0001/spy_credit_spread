"""Moomoo (Futu) broker adapter implementing core.broker.BrokerProtocol.

Requires moomoo OpenD v8.3+ running locally (default 127.0.0.1:11111).
Install dependency: pip install moomoo-api --upgrade

Legged spread execution note:
  moomoo has no atomic combo order API.  Spreads are placed as two sequential
  single-leg orders with an automatic safety net:
    - Long leg fill timeout → cancel long → return error
    - Short leg fill timeout → cancel short → market-sell long leg (flatten) → return error
  All outcomes are journaled.

trd_env note:
  0 = simulate (paper trading) — unlock_trade not required, uses TrdEnv.SIMULATE
  1 = real trading            — unlock_trade required,     uses TrdEnv.REAL
"""
from __future__ import annotations

import asyncio
import logging
import time as _time_module
from typing import Any

from core.broker import BrokerNotConnected, LegSpec, SpreadRequest

logger = logging.getLogger(__name__)

# Poll interval and timeout for leg fill checks
_POLL_INTERVAL_S = 0.5
_LEG_TIMEOUT_S = 30.0


class MoomooTrader:
    """Implements BrokerProtocol against moomoo OpenD."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        trade_password: str = "",
        trd_env: int = 0,
        security_firm: str = "NONE",
    ) -> None:
        self._host = host
        self._port = port
        self._trade_password = trade_password
        self._trd_env_int = trd_env   # 0=simulate, 1=real
        self._security_firm_str = security_firm
        self._acc_id: int | None = None
        self._quote_ctx = None
        self._trd_ctx = None
        self._connected = False
        # resolved after import — set in connect()
        self._ft_trd_env = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> dict[str, Any]:
        """Open quote + trade contexts and unlock trading."""
        try:
            import moomoo as ft
        except ImportError as exc:
            raise RuntimeError(
                "moomoo-api is not installed. Run: pip install moomoo-api"
            ) from exc

        trd_env_obj = ft.TrdEnv.REAL if self._trd_env_int == 1 else ft.TrdEnv.SIMULATE
        self._ft_trd_env = trd_env_obj
        loop = asyncio.get_event_loop()

        def _do_connect():
            quote_ctx = ft.OpenQuoteContext(host=self._host, port=self._port)
            security_firm = getattr(ft.SecurityFirm, self._security_firm_str, ft.SecurityFirm.NONE)
            trd_ctx = ft.OpenSecTradeContext(
                filter_trdmarket=ft.TrdMarket.US,
                host=self._host,
                port=self._port,
                security_firm=security_firm,
            )

            # unlock_trade is only required (and valid) for real trading
            if self._trd_env_int == 1:
                ret, data = trd_ctx.unlock_trade(password=self._trade_password)
                if ret != ft.RET_OK:
                    trd_ctx.close()
                    quote_ctx.close()
                    raise RuntimeError(f"unlock_trade failed: {data}")

            ret, acc_data = trd_ctx.get_acc_list()
            if ret != ft.RET_OK:
                trd_ctx.close()
                quote_ctx.close()
                raise RuntimeError(f"get_acc_list failed: {acc_data}")

            # Filter by matching trd_env, then pick first US-market account
            env_label = "REAL" if self._trd_env_int == 1 else "SIMULATE"
            filtered = acc_data[acc_data["trd_env"] == env_label] if "trd_env" in acc_data.columns else acc_data
            us_rows = filtered[filtered["trd_market"] == "US"] if not filtered.empty else filtered
            if us_rows.empty:
                us_rows = filtered  # fall back: take first account regardless of market
            if us_rows.empty:
                trd_ctx.close()
                quote_ctx.close()
                raise RuntimeError(
                    f"No {env_label} US account found in OpenD. "
                    "Check that moomoo is logged in and the correct trd_env is selected."
                )

            acc_id = int(us_rows.iloc[0]["acc_id"])
            ret, summary = trd_ctx.accinfo_query(trd_env=trd_env_obj, acc_id=acc_id)
            if ret != ft.RET_OK:
                trd_ctx.close()
                quote_ctx.close()
                raise RuntimeError(f"accinfo_query failed: {summary}")
            return quote_ctx, trd_ctx, acc_id, summary.iloc[0].to_dict()

        quote_ctx, trd_ctx, acc_id, raw_summary = await loop.run_in_executor(None, _do_connect)
        self._quote_ctx = quote_ctx
        self._trd_ctx = trd_ctx
        self._acc_id = acc_id
        self._connected = True
        env_label = "REAL" if self._trd_env_int == 1 else "SIMULATE"
        logger.info("MoomooTrader connected. acc_id=%s env=%s", acc_id, env_label)
        return {
            "connected": True,
            "acc_id": acc_id,
            "trd_env": env_label,
            "account": self._map_account(raw_summary),
        }

    def disconnect(self) -> None:
        if self._trd_ctx:
            try:
                self._trd_ctx.close()
            except Exception:
                pass
        if self._quote_ctx:
            try:
                self._quote_ctx.close()
            except Exception:
                pass
        self._connected = False
        self._acc_id = None
        self._quote_ctx = None
        self._trd_ctx = None
        logger.info("MoomooTrader disconnected.")

    def is_alive(self) -> bool:
        return self._connected and self._trd_ctx is not None

    # ── Account ───────────────────────────────────────────────────────────────

    @staticmethod
    def _map_account(raw: dict) -> dict[str, Any]:
        """Normalise moomoo account fields to project standard names."""
        return {
            "equity":           float(raw.get("total_assets", 0)),
            "buying_power":     float(raw.get("power", 0)),
            "excess_liquidity": float(raw.get("net_cash_power", 0)),
            "unrealized_pnl":   float(raw.get("unrealized_pl", 0)),
            "realized_pnl":     float(raw.get("realized_pl", 0)),
            "cash":             float(raw.get("cash", 0)),
        }

    async def get_account_summary(self) -> dict[str, Any]:
        self._require_connected()
        import moomoo as ft
        loop = asyncio.get_event_loop()

        def _fetch():
            ret, data = self._trd_ctx.accinfo_query(
                trd_env=self._ft_trd_env, acc_id=self._acc_id
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"accinfo_query: {data}")
            return data.iloc[0].to_dict()

        raw = await loop.run_in_executor(None, _fetch)
        return self._map_account(raw)

    # ── Positions ─────────────────────────────────────────────────────────────

    async def get_positions(self) -> list[dict[str, Any]]:
        self._require_connected()
        import moomoo as ft
        loop = asyncio.get_event_loop()

        def _fetch():
            ret, data = self._trd_ctx.position_list_query(
                trd_env=self._ft_trd_env, acc_id=self._acc_id
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"position_list_query: {data}")
            return data.to_dict(orient="records")

        return await loop.run_in_executor(None, _fetch)

    # ── Live price ────────────────────────────────────────────────────────────

    async def get_live_price(self, symbol: str) -> dict[str, Any]:
        self._require_connected()
        loop = asyncio.get_event_loop()
        code = f"US.{symbol}"

        def _fetch():
            ret, data = self._quote_ctx.get_market_snapshot([code])
            if ret != 0:
                raise RuntimeError(f"get_market_snapshot: {data}")
            row = data.iloc[0]
            return {
                "last":   float(row.get("last_price", 0)),
                "bid":    float(row.get("bid_price", 0)),
                "ask":    float(row.get("ask_price", 0)),
                "volume": int(row.get("volume", 0)),
            }

        return await loop.run_in_executor(None, _fetch)

    # ── Option chain ──────────────────────────────────────────────────────────

    async def get_option_chain(self, symbol: str, expiry_date: str):
        """Return DataFrame with strike, right, bid, ask, iv, delta, volume, open_interest.

        expiry_date: 'YYYY-MM-DD'
        """
        self._require_connected()
        loop = asyncio.get_event_loop()
        code = f"US.{symbol}"

        def _fetch():
            ret, data = self._quote_ctx.get_option_chain(
                code=code,
                start=expiry_date,
                end=expiry_date,
            )
            if ret != 0:
                raise RuntimeError(f"get_option_chain: {data}")
            return data

        return await loop.run_in_executor(None, _fetch)

    # ── Spread execution ──────────────────────────────────────────────────────

    async def place_spread(self, req: SpreadRequest) -> dict[str, Any]:
        """Execute a debit spread as two sequential legged orders.

        Leg 1 (long): BUY limit at req.long_leg.price
        Leg 2 (short): SELL limit at req.short_leg.price — only placed after leg 1 fills
        Safety net: any timeout triggers cancel + market flatten where needed.
        """
        self._require_connected()
        long_order_id = await self._place_single_leg(
            leg=req.long_leg,
            side="buy",
            qty=req.qty,
            client_ref=f"{req.client_order_id}_L1",
        )
        logger.info("Moomoo leg1 placed: order_id=%s", long_order_id)

        filled = await self._wait_for_fill(long_order_id, timeout=_LEG_TIMEOUT_S)
        if not filled:
            await self._cancel_order_sync(long_order_id)
            logger.warning("Moomoo leg1 timed out, cancelled. req=%s", req.client_order_id)
            return {"status": "error", "reason": "long_leg_timeout", "leg1_order_id": long_order_id}

        logger.info("Moomoo leg1 filled: order_id=%s", long_order_id)

        short_order_id = await self._place_single_leg(
            leg=req.short_leg,
            side="sell",
            qty=req.qty,
            client_ref=f"{req.client_order_id}_L2",
        )
        logger.info("Moomoo leg2 placed: order_id=%s", short_order_id)

        filled = await self._wait_for_fill(short_order_id, timeout=_LEG_TIMEOUT_S)
        if not filled:
            await self._cancel_order_sync(short_order_id)
            logger.warning("Moomoo leg2 timed out; flattening leg1 at market.")
            flatten_id = await self._place_market_close(req.long_leg, qty=req.qty, side="sell")
            return {
                "status": "error",
                "reason": "short_leg_timeout_flattened",
                "leg1_order_id": long_order_id,
                "leg2_order_id": short_order_id,
                "flatten_order_id": flatten_id,
            }

        logger.info("Moomoo leg2 filled: order_id=%s", short_order_id)
        return {
            "status": "ok",
            "leg1_order_id": long_order_id,
            "leg2_order_id": short_order_id,
        }

    async def close_position(self, position_id: str, legs: list[dict]) -> dict[str, Any]:
        """Close all legs with market orders (guarantees fill, used for exits)."""
        self._require_connected()
        results = []
        for leg in legs:
            # Reverse the original side: long → sell, short → buy
            original_side = leg.get("side", "long")
            close_side = "sell" if original_side == "long" else "buy"
            order_id = await self._place_market_close(
                LegSpec(
                    expiry=leg["expiry"],
                    strike=leg["strike"],
                    right=leg["right"],
                    price=0.0,
                ),
                qty=leg.get("qty", 1),
                side=close_side,
            )
            results.append({"leg": leg, "order_id": order_id})
        return {"status": "ok", "position_id": position_id, "close_orders": results}

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        self._require_connected()
        await self._cancel_order_sync(order_id)
        return {"status": "ok", "order_id": order_id}

    async def get_order_status(self, order_id: str) -> dict[str, Any] | None:
        """Return order status in the standard fill_watcher format.

        Returns None if the order cannot be found (treated as no broker record).
        """
        if not self.is_alive():
            return None
        import moomoo as ft
        loop = asyncio.get_event_loop()

        def _fetch():
            ret, data = self._trd_ctx.order_list_query(
                order_id=order_id,
                acc_id=self._acc_id,
                trd_env=self._ft_trd_env,
            )
            if ret != ft.RET_OK or data.empty:
                return None
            row = data.iloc[0]
            raw_status = str(row.get("order_status", ""))
            status = "filled" if raw_status == "FILLED_ALL" else (
                "cancelled" if "CANCEL" in raw_status else (
                    "submitted" if raw_status in ("SUBMITTING", "SUBMITTED") else raw_status
                )
            )
            return {
                "status": status,
                "filled": int(float(row.get("dealt_qty", 0) or 0)),
                "remaining": int(float(row.get("qty", 0) or 0)) - int(float(row.get("dealt_qty", 0) or 0)),
                "avgFillPrice": float(row.get("dealt_avg_price", 0) or 0),
                "commission": 0.0,
            }

        try:
            return await loop.run_in_executor(None, _fetch)
        except Exception as exc:
            logger.warning("get_order_status(%s) failed: %s", order_id, exc)
            return None

    async def get_spread_mid(self, legs: list[dict]) -> float | None:
        """Compute spread midpoint from individual leg option snapshots.

        ``legs`` is the list stored in Position.legs:
        [{expiry, strike, right, side, qty}, ...].
        Returns long_leg_mid - short_leg_mid (positive = debit spread still has value).
        Returns None if any leg quote is unavailable.
        """
        self._require_connected()
        loop = asyncio.get_event_loop()
        codes = [
            self._option_code("SPY", leg["expiry"], leg["right"], float(leg["strike"]))
            for leg in legs
        ]

        def _fetch():
            ret, data = self._quote_ctx.get_market_snapshot(codes)
            if ret != 0:
                return None
            return {str(row["code"]): row for _, row in data.iterrows()}

        try:
            snapshots = await loop.run_in_executor(None, _fetch)
        except Exception as exc:
            logger.warning("get_spread_mid snapshots failed: %s", exc)
            return None

        if not snapshots:
            return None

        total_mid = 0.0
        for leg, code in zip(legs, codes):
            row = snapshots.get(code)
            if row is None:
                return None
            bid = float(row.get("bid_price", 0) or 0)
            ask = float(row.get("ask_price", 0) or 0)
            if bid <= 0 or ask <= 0:
                return None
            mid = (bid + ask) / 2.0
            if leg.get("side") == "long":
                total_mid += mid
            else:
                total_mid -= mid
        return total_mid

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _require_connected(self) -> None:
        if not self.is_alive():
            raise BrokerNotConnected("MoomooTrader is not connected.")

    @staticmethod
    def _option_code(symbol: str, expiry: str, right: str, strike: float) -> str:
        """Build moomoo option code: US.SPY260425C580000"""
        yy = expiry[2:4]
        mm = expiry[4:6]
        dd = expiry[6:8]
        strike_int = int(round(strike * 1000))
        return f"US.{symbol}{yy}{mm}{dd}{right}{strike_int:08d}"

    async def _place_single_leg(
        self, leg: LegSpec, side: str, qty: int, client_ref: str
    ) -> str:
        import moomoo as ft
        loop = asyncio.get_event_loop()
        code = self._option_code("SPY", leg.expiry, leg.right, leg.strike)
        trd_side = ft.TrdSide.BUY if side == "buy" else ft.TrdSide.SELL

        def _place():
            ret, data = self._trd_ctx.place_order(
                price=leg.price,
                qty=qty,
                code=code,
                trd_side=trd_side,
                order_type=ft.OrderType.NORMAL,
                trd_env=self._ft_trd_env,
                acc_id=self._acc_id,
                remark=client_ref,
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"place_order failed ({side} {code}): {data}")
            return str(data["order_id"].iloc[0])

        return await loop.run_in_executor(None, _place)

    async def _wait_for_fill(self, order_id: str, timeout: float) -> bool:
        import moomoo as ft
        loop = asyncio.get_event_loop()
        deadline = _time_module.monotonic() + timeout
        while _time_module.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_S)

            def _check():
                ret, data = self._trd_ctx.order_list_query(
                    order_id=order_id,
                    acc_id=self._acc_id,
                    trd_env=self._ft_trd_env,
                )
                if ret != ft.RET_OK:
                    return None
                return str(data["order_status"].iloc[0])

            status = await loop.run_in_executor(None, _check)
            if status == "FILLED_ALL":
                return True
        return False

    async def _cancel_order_sync(self, order_id: str) -> None:
        import moomoo as ft
        loop = asyncio.get_event_loop()

        def _cancel():
            self._trd_ctx.modify_order(
                modify_order_op=ft.ModifyOrderOp.CANCEL,
                order_id=order_id,
                qty=0,
                price=0,
                acc_id=self._acc_id,
                trd_env=self._ft_trd_env,
            )

        try:
            await loop.run_in_executor(None, _cancel)
        except Exception as exc:
            logger.warning("cancel_order %s failed: %s", order_id, exc)

    async def _place_market_close(self, leg: LegSpec, qty: int, side: str) -> str:
        import moomoo as ft
        loop = asyncio.get_event_loop()
        code = self._option_code("SPY", leg.expiry, leg.right, leg.strike)
        trd_side = ft.TrdSide.BUY if side == "buy" else ft.TrdSide.SELL

        def _place():
            ret, data = self._trd_ctx.place_order(
                price=0.0,
                qty=qty,
                code=code,
                trd_side=trd_side,
                order_type=ft.OrderType.MARKET,
                trd_env=self._ft_trd_env,
                acc_id=self._acc_id,
                remark="flatten",
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"place_order MARKET failed ({side} {code}): {data}")
            return str(data["order_id"].iloc[0])

        return await loop.run_in_executor(None, _place)
