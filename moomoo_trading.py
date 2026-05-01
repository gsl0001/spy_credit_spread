"""Moomoo (Futu) broker adapter implementing core.broker.BrokerProtocol.

Requires moomoo OpenD v8.3+ running locally (default 127.0.0.1:11111).
Install dependency: pip install moomoo-api --upgrade

Legged spread execution note:
  moomoo has no atomic combo order API.  Spreads are placed as two sequential
  single-leg orders with an automatic safety net:
    - Long leg fill timeout → cancel long → return error
    - Short leg fill timeout → cancel short → market-sell long leg (flatten) → return error
  All outcomes are journaled.

Connect flow:
  1. Open OpenSecTradeContext with whatever filter the user picked (default NONE
     for both market and firm — OpenD shows every account it has).
  2. Call get_acc_list() so we can see exactly which accounts OpenD has.
  3. Pick the first account whose trd_env matches and whose trdmarket_auth list
     contains 'US' (so US options can be traded).
  4. For REAL trading only, call unlock_trade(password) to enable order placement.
  5. Query account info to confirm and return summary.

trd_env note:
  0 = simulate (paper trading) — unlock_trade not required
  1 = real trading            — unlock_trade required
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


def _accounts_to_dicts(acc_data) -> list[dict[str, Any]]:
    """Normalise acc_list DataFrame rows to plain dicts (lists JSON-friendly)."""
    rows: list[dict[str, Any]] = []
    if acc_data is None or len(acc_data) == 0:
        return rows
    for _, row in acc_data.iterrows():
        d = {col: row[col] for col in acc_data.columns}
        # trdmarket_auth is a list — leave it; everything else stringify if exotic
        for k, v in list(d.items()):
            if hasattr(v, "tolist"):
                d[k] = v.tolist()
        rows.append(d)
    return rows


def _account_can_trade_us(row: dict[str, Any]) -> bool:
    """True if this account row has 'US' in its trdmarket_auth list."""
    auth = row.get("trdmarket_auth")
    if isinstance(auth, (list, tuple, set)):
        return "US" in auth
    if isinstance(auth, str):
        return "US" in auth  # string fallback: contains 'US'
    return False


class MoomooTrader:
    """Implements BrokerProtocol against moomoo OpenD."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        trade_password: str = "",
        trd_env: int = 0,
        security_firm: str = "NONE",
        filter_trdmarket: str = "NONE",
    ) -> None:
        self._host = host
        self._port = port
        self._trade_password = trade_password
        self._trd_env_int = trd_env   # 0=simulate, 1=real
        self._security_firm_str = security_firm
        self._filter_trdmarket_str = filter_trdmarket
        self._acc_id: int | None = None
        self._quote_ctx = None
        self._trd_ctx = None
        self._connected = False
        # resolved after import — set in connect()
        self._ft_trd_env = None

    # ── Diagnostics ───────────────────────────────────────────────────────────

    @staticmethod
    async def probe(host: str = "127.0.0.1", port: int = 11111) -> dict[str, Any]:
        """Open a permissive context, list every account, close.  Pure diagnostic.

        Used by /api/moomoo/probe so the UI can show the user what's actually
        available before they pick a Mode/Firm/Market.
        """
        try:
            import moomoo as ft
        except ImportError as exc:
            return {"ok": False, "error": "moomoo-api not installed", "detail": str(exc)}

        loop = asyncio.get_event_loop()

        def _do_probe():
            trd_ctx = None
            try:
                trd_ctx = ft.OpenSecTradeContext(
                    filter_trdmarket=ft.TrdMarket.NONE,
                    host=host,
                    port=port,
                    security_firm=ft.SecurityFirm.NONE,
                )
                ret, acc_data = trd_ctx.get_acc_list()
                if ret != ft.RET_OK:
                    return {"ok": False, "error": f"get_acc_list failed: {acc_data}"}
                accounts = _accounts_to_dicts(acc_data)
                return {"ok": True, "accounts": accounts, "count": len(accounts)}
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                if trd_ctx is not None:
                    try:
                        trd_ctx.close()
                    except Exception:
                        pass

        return await loop.run_in_executor(None, _do_probe)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> dict[str, Any]:
        """Open quote + trade contexts, pick correct account, unlock if real."""
        try:
            import moomoo as ft
        except ImportError as exc:
            raise RuntimeError(
                "moomoo-api is not installed. Run: pip install moomoo-api"
            ) from exc

        trd_env_obj = ft.TrdEnv.REAL if self._trd_env_int == 1 else ft.TrdEnv.SIMULATE
        self._ft_trd_env = trd_env_obj
        env_label = "REAL" if self._trd_env_int == 1 else "SIMULATE"
        loop = asyncio.get_event_loop()

        def _do_connect():
            quote_ctx = None
            trd_ctx = None
            try:
                quote_ctx = ft.OpenQuoteContext(host=self._host, port=self._port)

                security_firm = getattr(
                    ft.SecurityFirm, self._security_firm_str, ft.SecurityFirm.NONE,
                )
                filter_market = getattr(
                    ft.TrdMarket, self._filter_trdmarket_str, ft.TrdMarket.NONE,
                )
                trd_ctx = ft.OpenSecTradeContext(
                    filter_trdmarket=filter_market,
                    host=self._host,
                    port=self._port,
                    security_firm=security_firm,
                )

                # Step 1: enumerate accounts BEFORE unlock_trade so we get a
                # diagnostic if the user's filter combination hides everything.
                ret, acc_data = trd_ctx.get_acc_list()
                if ret != ft.RET_OK:
                    raise RuntimeError(f"get_acc_list failed: {acc_data}")
                all_accounts = _accounts_to_dicts(acc_data)
                if not all_accounts:
                    raise RuntimeError(
                        "OpenD returned zero accounts. Check that the moomoo "
                        "desktop app is logged in with at least one account "
                        "(real or paper) provisioned for US options."
                    )

                # Step 2: pick an account matching the requested env + US auth.
                env_matches = [a for a in all_accounts if a.get("trd_env") == env_label]
                if not env_matches:
                    available_envs = sorted({a.get("trd_env") for a in all_accounts})
                    raise RuntimeError(
                        f"No {env_label} account found. OpenD has: {available_envs}. "
                        f"Switch Mode in the UI or enable a {env_label.lower()} "
                        f"account in moomoo. Visible accounts:\n"
                        + _format_accounts(all_accounts)
                    )

                us_capable = [a for a in env_matches if _account_can_trade_us(a)]
                chosen = us_capable[0] if us_capable else env_matches[0]
                acc_id = int(chosen["acc_id"])
                if not us_capable:
                    logger.warning(
                        "No US-options-capable %s account found — using acc_id=%s "
                        "(trdmarket_auth=%s).  US option orders will likely be "
                        "rejected by the broker.",
                        env_label, acc_id, chosen.get("trdmarket_auth"),
                    )

                # Step 3: unlock_trade for REAL only.
                #
                # Two OpenD modes:
                # - GUI: API unlock is disabled; user must click the "Unlock"
                #   button in the OpenD desktop app.  We try unlock_trade and
                #   silently treat the GUI-mode error as "already unlocked
                #   externally" so the user just needs to do it once at OpenD
                #   startup.  If they haven't, place_order will fail later with
                #   a clear "trade not unlocked" error.
                # - Headless: PIN via API works.
                if self._trd_env_int == 1:
                    pin = self._trade_password
                    if pin:
                        ret, data = trd_ctx.unlock_trade(password=pin)
                        if ret != ft.RET_OK:
                            msg = str(data)
                            if "Unlock button" in msg or "GUI version" in msg:
                                logger.info(
                                    "OpenD GUI mode detected — trusting "
                                    "user-side unlock via OpenD UI."
                                )
                            else:
                                raise RuntimeError(
                                    f"unlock_trade failed: {data}. "
                                    "Check that the PIN matches your moomoo "
                                    "trade PIN exactly."
                                )
                    else:
                        logger.info(
                            "No PIN provided — assuming OpenD GUI is already "
                            "unlocked via the desktop app."
                        )

                # Step 4: confirm by querying account info.
                # currency='USD' — default is HKD which fails for non-HK accounts
                # (e.g. FUTUCA: "This account does not support converting to
                # this currency").  USD works for US-trading-capable accounts.
                ret, summary = trd_ctx.accinfo_query(
                    trd_env=trd_env_obj, acc_id=acc_id, currency="USD",
                )
                if ret != ft.RET_OK:
                    raise RuntimeError(f"accinfo_query failed: {summary}")
                return quote_ctx, trd_ctx, acc_id, summary.iloc[0].to_dict(), all_accounts
            except Exception:
                if trd_ctx is not None:
                    try: trd_ctx.close()
                    except Exception: pass
                if quote_ctx is not None:
                    try: quote_ctx.close()
                    except Exception: pass
                raise

        quote_ctx, trd_ctx, acc_id, raw_summary, all_accounts = await loop.run_in_executor(None, _do_connect)
        self._quote_ctx = quote_ctx
        self._trd_ctx = trd_ctx
        self._acc_id = acc_id
        self._connected = True
        logger.info("MoomooTrader connected. acc_id=%s env=%s", acc_id, env_label)
        return {
            "connected": True,
            "acc_id": acc_id,
            "trd_env": env_label,
            "account": self._map_account(raw_summary),
            "all_accounts": all_accounts,
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
    def _safe_float(v: Any, default: float = 0.0) -> float:
        """Coerce moomoo field to float; many fields are 'N/A' on simulate."""
        if v is None:
            return default
        if isinstance(v, str) and (v == "N/A" or v.strip() == ""):
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _map_account(cls, raw: dict) -> dict[str, Any]:
        """Normalise moomoo account fields to project standard names.

        Simulate accounts return 'N/A' for many fields — coerce safely.
        """
        f = cls._safe_float
        return {
            "equity":           f(raw.get("total_assets")),
            "buying_power":     f(raw.get("power")),
            "excess_liquidity": f(raw.get("net_cash_power")),
            "unrealized_pnl":   f(raw.get("unrealized_pl")),
            "realized_pnl":     f(raw.get("realized_pl")),
            "cash":             f(raw.get("cash")),
        }

    async def get_account_summary(self) -> dict[str, Any]:
        self._require_connected()
        import moomoo as ft
        loop = asyncio.get_event_loop()

        def _fetch():
            ret, data = self._trd_ctx.accinfo_query(
                trd_env=self._ft_trd_env, acc_id=self._acc_id, currency="USD",
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
        """Return chain DataFrame enriched with bid/ask from market snapshots.

        moomoo's get_option_chain only returns contract metadata (code, strike,
        option_type, expiry).  Quote data (bid/ask) requires a separate
        get_market_snapshot call per code.  We do that here and merge so
        callers get a single DataFrame with strike_price, option_type,
        bid_price, ask_price, last_price, volume, open_interest.

        expiry_date: 'YYYY-MM-DD'
        """
        self._require_connected()
        loop = asyncio.get_event_loop()
        code = f"US.{symbol}"

        def _fetch_chain():
            ret, data = self._quote_ctx.get_option_chain(
                code=code,
                start=expiry_date,
                end=expiry_date,
            )
            if ret != 0:
                raise RuntimeError(f"get_option_chain: {data}")
            return data

        chain = await loop.run_in_executor(None, _fetch_chain)
        if chain is None or len(chain) == 0:
            return chain

        # moomoo OpenD caps get_market_snapshot at ~200 codes per call.
        codes = [str(c) for c in chain["code"].tolist() if c]

        def _fetch_snapshots(codes_subset):
            ret, data = self._quote_ctx.get_market_snapshot(codes_subset)
            if ret != 0:
                logger.warning("get_market_snapshot failed: %s", data)
                return None
            return data

        snapshot_rows: dict[str, dict[str, Any]] = {}
        for i in range(0, len(codes), 200):
            batch = codes[i:i + 200]
            df = await loop.run_in_executor(None, _fetch_snapshots, batch)
            if df is None or len(df) == 0:
                continue
            for _, row in df.iterrows():
                snapshot_rows[str(row["code"])] = {
                    "bid_price": float(row.get("bid_price", 0) or 0),
                    "ask_price": float(row.get("ask_price", 0) or 0),
                    "last_price": float(row.get("last_price", 0) or 0),
                    "volume": int(row.get("volume", 0) or 0),
                    "open_interest": int(row.get("option_open_interest", 0) or 0),
                }

        # Merge quotes into chain — set 0 for any code we couldn't snapshot.
        chain = chain.copy()
        for col, default in (
            ("bid_price", 0.0), ("ask_price", 0.0), ("last_price", 0.0),
            ("volume", 0), ("open_interest", 0),
        ):
            chain[col] = chain["code"].map(
                lambda c, _col=col, _def=default: snapshot_rows.get(str(c), {}).get(_col, _def)
            )
        return chain

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
                trd_env=self._ft_trd_env,
                acc_id=self._acc_id,
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
                    trd_env=self._ft_trd_env,
                    acc_id=self._acc_id,
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
                trd_env=self._ft_trd_env,
                acc_id=self._acc_id,
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


def _format_accounts(accounts: list[dict[str, Any]]) -> str:
    """Pretty-print account list for diagnostic error messages."""
    if not accounts:
        return "  (none)"
    lines = []
    for a in accounts:
        lines.append(
            f"  acc_id={a.get('acc_id')} "
            f"env={a.get('trd_env')} "
            f"firm={a.get('security_firm')} "
            f"auth={a.get('trdmarket_auth')} "
            f"status={a.get('acc_status')}"
        )
    return "\n".join(lines)
