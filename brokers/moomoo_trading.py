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
from datetime import datetime, timezone
from typing import Any, Optional

from core.broker import BrokerNotConnected, LegSpec, SpreadRequest

logger = logging.getLogger(__name__)

# Poll interval and timeout for leg fill checks
_POLL_INTERVAL_S = 0.5
_LEG_TIMEOUT_S = 30.0

# Retry backoff state for ensure_connected
_RETRY_DELAYS = [5, 10, 20, 40, 60]  # seconds


def _close_session_kwargs(ft_module) -> dict:
    """Return a kwargs dict to pass ``session=...`` to ``place_order`` for
    close / flatten paths so afterhours fills are eligible.

    moomoo OpenAPI exposes a ``Session`` enum with values like
    ``ALL / ETH / OVERNIGHT / RTH / NONE`` depending on SDK version. We pick
    the most permissive value that exists at runtime so a moomoo SDK upgrade
    doesn't break the build. Returns ``{}`` (skip the param) if the SDK has
    no ``Session`` enum or none of the expected values.

    Why on close paths only: entry orders should stay RTH-default to avoid
    accidentally legging into a spread on illiquid afterhours quotes; only
    flattens / stops / orphan cleanups benefit from extended sessions.
    """
    session_enum = getattr(ft_module, "Session", None)
    if session_enum is None:
        return {}
    for name in ("ALL", "ETH", "OVERNIGHT", "RTH"):
        val = getattr(session_enum, name, None)
        if val is not None:
            return {"session": val}
    return {}


def _get_loop_safe() -> asyncio.AbstractEventLoop:
    """Return a usable event loop from any context (coroutine, sync handler,
    APScheduler worker thread). Prefers the currently-running loop; falls back
    to the policy's loop or a fresh one if neither exists.

    Used only by ``schedule_reconnect`` which can be called from a thread
    that has no running loop.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass
    try:
        return asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


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
        # Health tracking: last successful broker call, used by /heartbeat
        # and the OpenD-staleness alert.  Updated on every successful API
        # round-trip.
        self._last_healthy_iso: str | None = None

        # ── Auto-reconnect state ──────────────────────────────────────
        self._auto_reconnect = True
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_attempt = 0
        self._max_reconnect_backoff = 60  # seconds
        self._reconnecting = False
        self._intentional_disconnect = False

    def _mark_healthy(self) -> None:
        """Stamp now() as the last successful OpenD call timestamp."""
        from datetime import datetime, timezone
        self._last_healthy_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def last_healthy_iso(self) -> str | None:
        return self._last_healthy_iso

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

        loop = asyncio.get_running_loop()

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
        loop = asyncio.get_running_loop()

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
        self._mark_healthy()
        logger.info("MoomooTrader connected. acc_id=%s env=%s", acc_id, env_label)
        return {
            "connected": True,
            "acc_id": acc_id,
            "trd_env": env_label,
            "account": self._map_account(raw_summary),
            "all_accounts": all_accounts,
        }

    def disconnect(self) -> None:
        self._intentional_disconnect = True
        self._cancel_reconnect()
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

    # ── Auto-reconnect ────────────────────────────────────────────────────

    @property
    def reconnecting(self) -> bool:
        return self._reconnecting

    @property
    def reconnect_attempt(self) -> int:
        return self._reconnect_attempt

    def _cancel_reconnect(self) -> None:
        """Cancel any pending reconnect task."""
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        self._reconnect_task = None
        self._reconnecting = False
        self._reconnect_attempt = 0

    async def _reconnect_loop(self) -> None:
        """Background coroutine: exponential backoff reconnect.

        Retries connect() with delays: 2s, 4s, 8s, 16s, … max 60s.
        Sends Telegram alerts on each attempt and on success/failure.
        """
        self._reconnecting = True
        self._reconnect_attempt = 0

        def _notify(msg: str, level: str = "warning") -> None:
            try:
                from core.notifier import notify_alert
                notify_alert(level, msg)
            except Exception:
                pass

        _notify("⚠️ OpenD connection lost — auto-reconnect starting", "warning")
        logger.warning("Auto-reconnect: OpenD connection lost, starting backoff loop")

        while not self._intentional_disconnect:
            try:
                from core.connection_flags import is_auto_enabled
                if not is_auto_enabled("moomoo"):
                    logger.info("Auto-reconnect: aborted (header toggle off)")
                    break
            except Exception:
                pass
            self._reconnect_attempt += 1
            delay = min(2 ** self._reconnect_attempt, self._max_reconnect_backoff)
            logger.info("Auto-reconnect: attempt #%d in %ds", self._reconnect_attempt, delay)

            await asyncio.sleep(delay)

            if self._intentional_disconnect:
                break

            try:
                result = await self.connect()
                if result.get("connected"):
                    _notify(
                        f"✅ OpenD reconnected after {self._reconnect_attempt} attempt(s)",
                        "info",
                    )
                    logger.info(
                        "Auto-reconnect: SUCCESS after %d attempt(s)",
                        self._reconnect_attempt,
                    )
                    self._reconnecting = False
                    self._reconnect_attempt = 0
                    return
            except Exception as exc:
                logger.warning(
                    "Auto-reconnect: attempt #%d failed: %s",
                    self._reconnect_attempt, exc,
                )

            # Alert every 5 attempts
            if self._reconnect_attempt % 5 == 0:
                _notify(
                    f"🔴 OpenD still down after {self._reconnect_attempt} reconnect attempts",
                    "critical",
                )

        self._reconnecting = False
        logger.info("Auto-reconnect: loop stopped (intentional disconnect)")

    def schedule_reconnect(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Schedule a reconnect task if not already running.

        Called from the health check / monitor tick when is_alive() == False
        and intentional_disconnect is False.
        """
        if self._intentional_disconnect:
            return
        if self._reconnecting:
            return  # already trying
        if self.is_alive():
            return  # still connected
        try:
            from core.connection_flags import is_auto_enabled
            if not is_auto_enabled("moomoo"):
                return  # header toggle off
        except Exception:
            pass

        target_loop = loop or _get_loop_safe()
        self._reconnect_task = target_loop.create_task(self._reconnect_loop())

    # ── ensure_connected (IBKR-parity) ────────────────────────────────────────

    async def ensure_connected(self) -> dict[str, Any]:
        """Reconnect if the connection dropped, with exponential backoff.

        Mirrors IBKRTrader.ensure_connected() so callers don't need
        to pre-check is_alive() before every API call.
        """
        if self.is_alive():
            return {"success": True, "msg": "Already connected"}

        if self._intentional_disconnect:
            return {"success": False, "msg": "Intentionally disconnected"}

        try:
            from core.connection_flags import is_auto_enabled
            if not is_auto_enabled("moomoo"):
                return {"success": False, "msg": "moomoo auto-reconnect disabled"}
        except Exception:
            pass

        # Exponential backoff
        idx = min(self._reconnect_attempt, len(_RETRY_DELAYS) - 1)
        if self._reconnect_attempt > 0:
            delay = _RETRY_DELAYS[idx]
            elapsed = _time_module.monotonic() - getattr(self, "_last_retry_time", 0)
            if elapsed < delay:
                wait = round(delay - elapsed, 1)
                return {"success": False, "msg": f"Backoff: waiting {wait}s before retry"}

        self._reconnect_attempt += 1
        self._last_retry_time = _time_module.monotonic()

        try:
            result = await self.connect()
            if result.get("connected"):
                self._reconnect_attempt = 0
                return {"success": True, "msg": "Reconnected"}
            return {"success": False, "msg": result.get("error", "Connect failed")}
        except Exception as exc:
            return {"success": False, "msg": str(exc)}

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
        await self.ensure_connected()
        import moomoo as ft
        loop = asyncio.get_running_loop()

        def _fetch():
            ret, data = self._trd_ctx.accinfo_query(
                trd_env=self._ft_trd_env, acc_id=self._acc_id, currency="USD",
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"accinfo_query: {data}")
            return data.iloc[0].to_dict()

        raw = await loop.run_in_executor(None, _fetch)
        self._mark_healthy()
        return self._map_account(raw)

    # ── Positions ─────────────────────────────────────────────────────────────

    async def get_positions(self) -> list[dict[str, Any]]:
        await self.ensure_connected()
        import moomoo as ft
        loop = asyncio.get_running_loop()

        def _fetch():
            ret, data = self._trd_ctx.position_list_query(
                trd_env=self._ft_trd_env, acc_id=self._acc_id
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"position_list_query: {data}")
            return data.to_dict(orient="records")

        result = await loop.run_in_executor(None, _fetch)
        self._mark_healthy()
        return result

    # ── Live price ────────────────────────────────────────────────────────────

    async def get_live_price(self, symbol: str) -> dict[str, Any]:
        await self.ensure_connected()
        loop = asyncio.get_running_loop()
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

        result = await loop.run_in_executor(None, _fetch)
        self._mark_healthy()
        return result

    # ── Option chain ──────────────────────────────────────────────────────────

    async def list_option_expiries(self, symbol: str) -> list[str]:
        """Return a sorted list of valid SPY option expiry dates ('YYYY-MM-DD')
        according to moomoo's authoritative calendar.

        Replaces the previous "today + N + walk over weekends" hack that
        didn't know about market holidays. Cached for 1 hour per symbol so
        the per-tick scanner doesn't burn the SDK's chain-API quota.

        Returns ``[]`` if the SDK call fails — caller should fall back to
        calendar math.
        """
        cache = getattr(self, "_expiry_cache", None)
        if cache is None:
            cache = {}
            self._expiry_cache = cache
        now = _time_module.monotonic()
        cached = cache.get(symbol)
        if cached and (now - cached[0]) < 3600:
            return list(cached[1])

        self._require_connected()
        loop = asyncio.get_running_loop()
        code = f"US.{symbol}"

        def _fetch():
            import moomoo as ft
            ret, data = self._quote_ctx.get_option_expiration_date(code=code)
            if ret != ft.RET_OK:
                return None, str(data)
            # DataFrame columns vary across SDK versions: prefer 'strike_time'
            # (current), fall back to 'date' (older), then any column whose
            # values look ISO-formatted.
            col = None
            for cand in ("strike_time", "date"):
                if cand in data.columns:
                    col = cand
                    break
            if col is None:
                # last resort: first object-typed column
                obj_cols = [c for c in data.columns if data[c].dtype == "O"]
                if obj_cols:
                    col = obj_cols[0]
            if col is None:
                return [], "no expiry column"
            return [str(v)[:10] for v in data[col].tolist()], None

        try:
            expiries, err = await loop.run_in_executor(None, _fetch)
        except Exception as exc:
            logger.warning("list_option_expiries(%s) failed: %s", symbol, exc)
            return []
        if expiries is None:
            logger.warning("list_option_expiries(%s) ret error: %s", symbol, err)
            return []
        expiries = sorted({e for e in expiries if e and len(e) == 10})
        cache[symbol] = (now, expiries)
        return expiries

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
        loop = asyncio.get_running_loop()
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
        self._mark_healthy()
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

        Hardened against the leg2-timeout class of failure that produced
        Thursday's $8K orphan-leg incident:

          1. Pre-flight: fetch a live mid for both legs and reject if either
             leg has a degenerate quote (zero / crossed / very wide). This
             prevents the most common cause of leg2 not filling.
          2. Leg 1 (long): BUY limit at req.long_leg.price.
          3. Leg 2 (short): SELL limit at req.short_leg.price — only placed
             after leg 1 fills.
          4. If leg 2 times out: flatten leg 1 with a marketable-limit at
             ``bid - safety_buffer`` (rather than a raw market order that
             can fill at any price during a wide / illiquid quote). Returns
             ``status: "broken_spread"`` so the caller can journal this as
             an open orphan position the monitor will manage.
        """
        self._require_connected()

        # ── 1. Pre-flight quote sanity ────────────────────────────────────
        try:
            preflight = await self._preflight_spread_quotes(
                req.long_leg, req.short_leg,
                max_bid_ask_pct=float(getattr(req, "max_bid_ask_pct", 0.50) or 0.50),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("place_spread preflight quote failed: %s", exc)
            preflight = None
        if preflight and preflight.get("error"):
            logger.warning(
                "place_spread aborted preflight: %s",
                preflight.get("reason"),
            )
            return {"status": "error", "reason": "preflight_" + preflight["error"],
                    **preflight}

        # ── 2. Leg 1 ──────────────────────────────────────────────────────
        long_order_id = await self._place_single_leg(
            leg=req.long_leg,
            side="buy",
            qty=req.qty,
            client_ref=f"{req.client_order_id}_L1",
        )
        logger.info("Moomoo leg1 placed: order_id=%s", long_order_id)

        leg1 = await self._wait_for_fill_detail(long_order_id, timeout=_LEG_TIMEOUT_S)
        if leg1["outcome"] != "filled":
            cancel_res = await self._cancel_order_sync(long_order_id)
            if leg1["outcome"] == "partial":
                # Some long contracts filled — flatten the partial so we don't
                # carry naked length. The caller will journal the broken state.
                logger.warning(
                    "Moomoo leg1 partial fill (%d/%d) — flattening filled portion",
                    leg1["filled_qty"], leg1["total_qty"],
                )
                flatten_id = None
                try:
                    flatten_id = await self._marketable_limit_close(
                        req.long_leg, qty=leg1["filled_qty"], side="sell",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("partial-leg1 flatten failed: %s", exc)
                return {
                    "status": "broken_spread",
                    "reason": "long_leg_partial",
                    "leg1_order_id": long_order_id,
                    "leg1_filled_qty": leg1["filled_qty"],
                    "leg1_total_qty": leg1["total_qty"],
                    "flatten_order_id": flatten_id,
                    "cancel_ok": cancel_res.get("ok", False),
                }
            logger.warning(
                "Moomoo leg1 %s; cancelled (cancel_ok=%s). req=%s",
                leg1["outcome"], cancel_res.get("ok"), req.client_order_id,
            )
            return {"status": "error", "reason": f"long_leg_{leg1['outcome']}",
                    "leg1_order_id": long_order_id,
                    "cancel_ok": cancel_res.get("ok", False)}

        logger.info("Moomoo leg1 filled: order_id=%s qty=%d",
                    long_order_id, leg1["filled_qty"])

        # ── 3. Leg 2 ──────────────────────────────────────────────────────
        short_order_id = await self._place_single_leg(
            leg=req.short_leg,
            side="sell",
            qty=req.qty,
            client_ref=f"{req.client_order_id}_L2",
        )
        logger.info("Moomoo leg2 placed: order_id=%s", short_order_id)

        leg2 = await self._wait_for_fill_detail(short_order_id, timeout=_LEG_TIMEOUT_S)
        if leg2["outcome"] != "filled":
            cancel_res = await self._cancel_order_sync(short_order_id)
            logger.warning(
                "Moomoo leg2 %s (filled %d/%d, cancel_ok=%s); flattening leg1 long. req=%s",
                leg2["outcome"], leg2["filled_qty"], leg2["total_qty"],
                cancel_res.get("ok"), req.client_order_id,
            )
            # Marketable-limit close: bid - $0.05 buffer, never raw market.
            # If we can't get a quote to compute the limit, fall through to
            # market and journal the spread as broken — better to keep the
            # orphan on the books than abandon it silently.
            flatten_id = None
            flatten_reason = "broken_spread"
            try:
                flatten_id = await self._marketable_limit_close(
                    req.long_leg, qty=req.qty, side="sell",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("marketable-limit flatten failed: %s — falling back to market", exc)
                try:
                    flatten_id = await self._place_market_close(
                        req.long_leg, qty=req.qty, side="sell",
                    )
                except Exception as exc2:  # noqa: BLE001
                    logger.error("market flatten ALSO failed: %s — leg1 remains open", exc2)
                    flatten_reason = "broken_spread_unflattened"
            return {
                "status": "broken_spread",
                "reason": flatten_reason,
                "leg1_order_id": long_order_id,
                "leg2_order_id": short_order_id,
                "leg1_filled_qty": leg1["filled_qty"],
                "leg2_filled_qty": leg2["filled_qty"],
                "leg2_outcome": leg2["outcome"],
                "flatten_order_id": flatten_id,
                "cancel_ok": cancel_res.get("ok", False),
            }

        logger.info("Moomoo leg2 filled: order_id=%s qty=%d",
                    short_order_id, leg2["filled_qty"])
        return {
            "status": "ok",
            "leg1_order_id": long_order_id,
            "leg2_order_id": short_order_id,
            "leg1_filled_qty": leg1["filled_qty"],
            "leg2_filled_qty": leg2["filled_qty"],
            "leg1_avg_fill_price": float(leg1.get("avg_fill_price") or 0.0),
            "leg2_avg_fill_price": float(leg2.get("avg_fill_price") or 0.0),
        }

    async def _preflight_spread_quotes(
        self, long_leg: LegSpec, short_leg: LegSpec,
        max_bid_ask_pct: float = 0.50,
    ) -> dict[str, Any]:
        """Snapshot live NBBO for both legs; refuse if either is unusable.

        Refuses on:
          - zero / negative bid or ask on either leg
          - crossed quote (ask < bid)
          - bid-ask spread wider than ``max_bid_ask_pct`` of mid on either
            leg. Caller (place_spread) threads this from
            ``SpreadRequest.max_bid_ask_pct`` which in turn is plumbed from
            the preset's ``chain_max_bid_ask_pct``. Default 0.50 preserves
            historical behavior when called from code paths that don't set
            the field.
        Returns ``{}`` (success) or ``{"error": "...", "reason": "..."}``.
        """
        import moomoo as ft  # local import: keep moomoo optional
        loop = asyncio.get_running_loop()

        codes = [
            self._option_code("SPY", long_leg.expiry, long_leg.right, long_leg.strike),
            self._option_code("SPY", short_leg.expiry, short_leg.right, short_leg.strike),
        ]

        def _snap():
            ret, data = self._quote_ctx.get_market_snapshot(codes)
            if ret != ft.RET_OK:
                return None, str(data)
            return data.to_dict(orient="records"), None

        rows, err = await loop.run_in_executor(None, _snap)
        if err is not None or not rows:
            return {"error": "quote_unavailable", "reason": err or "empty snapshot"}

        for row, leg_label in zip(rows, ("long", "short")):
            bid = float(row.get("bid_price") or 0)
            ask = float(row.get("ask_price") or 0)
            if bid <= 0 or ask <= 0:
                return {"error": "no_quote",
                        "reason": f"{leg_label}_leg_zero_bid_or_ask",
                        "bid": bid, "ask": ask}
            if ask < bid:
                return {"error": "crossed_quote",
                        "reason": f"{leg_label}_leg_ask<bid",
                        "bid": bid, "ask": ask}
            mid = (bid + ask) / 2.0
            if mid > 0 and (ask - bid) / mid > max_bid_ask_pct:
                return {"error": "spread_too_wide",
                        "reason": f"{leg_label}_leg_{(ask-bid)/mid*100:.0f}pct",
                        "bid": bid, "ask": ask}
        return {}

    async def _marketable_limit_close(
        self, leg: LegSpec, qty: int, side: str, buffer: float = 0.05,
    ) -> str:
        """Close ``leg`` with a marketable-limit (NBBO ± buffer) instead of a
        raw market order. Protects against fills at absurd prices when the
        quote is wide or stale (the original 22-spread incident class).
        """
        import moomoo as ft
        loop = asyncio.get_running_loop()
        code = self._option_code("SPY", leg.expiry, leg.right, leg.strike)

        def _snap_then_place():
            ret, data = self._quote_ctx.get_market_snapshot([code])
            if ret != ft.RET_OK:
                raise RuntimeError(f"snapshot failed: {data}")
            row = data.to_dict(orient="records")[0]
            bid = float(row.get("bid_price") or 0)
            ask = float(row.get("ask_price") or 0)
            # SELL to close → take the bid minus buffer to cross the spread.
            # BUY to close  → take the ask plus  buffer.
            if side == "sell":
                limit = max(0.01, round(bid - buffer, 2)) if bid > 0 else 0.01
                trd_side = ft.TrdSide.SELL
            else:
                limit = round(ask + buffer, 2) if ask > 0 else 9999.0
                trd_side = ft.TrdSide.BUY
            ret, data = self._trd_ctx.place_order(
                price=limit, qty=qty, code=code, trd_side=trd_side,
                order_type=ft.OrderType.NORMAL,
                trd_env=self._ft_trd_env, acc_id=self._acc_id,
                remark="marketable_limit_close",
                **_close_session_kwargs(ft),
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"place_order marketable-limit failed: {data}")
            return str(data["order_id"].iloc[0])

        return await loop.run_in_executor(None, _snap_then_place)

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
        res = await self._cancel_order_sync(order_id)
        return {
            "status": "ok" if res.get("ok") else "error",
            "order_id": order_id,
            **({"reason": res["reason"]} if not res.get("ok") and res.get("reason") else {}),
        }

    async def get_order_status(self, order_id: str) -> dict[str, Any] | None:
        """Return order status in the standard fill_watcher format.

        Returns None if the order cannot be found (treated as no broker record).
        """
        if not self.is_alive():
            return None
        import moomoo as ft
        loop = asyncio.get_running_loop()

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

    async def get_recent_fill_for_leg(
        self,
        symbol: str,
        expiry: str,
        right: str,
        strike: float,
        side: str,
        qty: int,
        lookback_days: int = 5,
    ) -> Optional[dict[str, Any]]:
        """Look up the most recent broker-side fill that matches a leg spec.

        Used by the reconciler to hydrate ``entry_cost`` for orphan positions
        (V9). Without this, orphans get entry_cost=0 and any realized_pnl on
        close is meaningless.

        Matches on option code (symbol+expiry+right+strike) and trade side
        (BUY for long, SELL for short). Returns the most recent match within
        ``lookback_days``.

        Returns::
            {"price": float, "qty": int, "time": str, "order_id": str}
        or None if no match.
        """
        if not self.is_alive():
            return None
        import moomoo as ft
        loop = asyncio.get_running_loop()

        code = self._option_code(symbol, expiry, right, float(strike))
        want_side = "BUY" if side == "long" else "SELL"

        def _fetch():
            # Try deal_list_query first (today's deals), fall back to
            # history_deal_list_query for older ones if needed. Both return
            # the same shape on success.
            try:
                ret, data = self._trd_ctx.deal_list_query(
                    code=code,
                    trd_env=self._ft_trd_env,
                    acc_id=self._acc_id,
                )
            except Exception:  # noqa: BLE001
                ret, data = ft.RET_ERROR, None
            if ret != ft.RET_OK or data is None or data.empty:
                try:
                    from datetime import timedelta
                    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
                    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    ret, data = self._trd_ctx.history_deal_list_query(
                        code=code,
                        start=start,
                        end=end,
                        trd_env=self._ft_trd_env,
                        acc_id=self._acc_id,
                    )
                except Exception:  # noqa: BLE001
                    return None
                if ret != ft.RET_OK or data is None or data.empty:
                    return None

            # Filter to our side, take most recent by create_time.
            matches = []
            for _, row in data.iterrows():
                row_side = str(row.get("trd_side", "")).upper()
                if row_side != want_side:
                    continue
                matches.append({
                    "price": float(row.get("price", 0) or 0),
                    "qty": int(float(row.get("qty", 0) or 0)),
                    "time": str(row.get("create_time", "")),
                    "order_id": str(row.get("order_id", "")),
                })
            if not matches:
                return None
            matches.sort(key=lambda m: m["time"], reverse=True)
            return matches[0]

        try:
            return await loop.run_in_executor(None, _fetch)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "get_recent_fill_for_leg(%s %s %s) failed: %s",
                symbol, strike, side, exc,
            )
            return None

    async def get_spread_mid(self, legs: list[dict]) -> float | None:
        """Compute spread midpoint from individual leg option snapshots.

        ``legs`` is the list stored in Position.legs:
        [{expiry, strike, right, side, qty}, ...].
        Returns long_leg_mid - short_leg_mid (positive = debit spread still has value).
        Returns None if any leg quote is unavailable.
        """
        self._require_connected()
        loop = asyncio.get_running_loop()
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
        """Build moomoo option code: US.SPY260425C580000

        moomoo uses ``int(strike * 1000)`` with NO leading-zero padding.
        SPY 580.00 → "580000".  SPY 950.00 → "950000".  We confirmed this
        empirically against /api/moomoo/chain — every returned code uses
        6 digits naturally for SPY's 500-950 range.  Earlier versions
        zero-padded to 8 digits which produced codes like
        "00721000" that moomoo rejected as "Cannot find <code> in US Stocks".
        """
        yy = expiry[2:4]
        mm = expiry[4:6]
        dd = expiry[6:8]
        strike_int = int(round(strike * 1000))
        return f"US.{symbol}{yy}{mm}{dd}{right}{strike_int}"

    async def _place_single_leg(
        self, leg: LegSpec, side: str, qty: int, client_ref: str
    ) -> str:
        import moomoo as ft
        loop = asyncio.get_running_loop()
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
        """Bool wrapper kept for backward compatibility. Returns True only on
        a *complete* fill (``FILLED_ALL``); a partial fill returns False so
        existing callers behave as before. New code should prefer
        :meth:`_wait_for_fill_detail` which surfaces the partial state.
        """
        result = await self._wait_for_fill_detail(order_id, timeout)
        return result.get("outcome") == "filled"

    async def _wait_for_fill_detail(
        self, order_id: str, timeout: float,
    ) -> dict[str, Any]:
        """Poll moomoo's order_list_query until the order reaches a terminal
        state or the timeout expires. Returns a dict::

            {
              "outcome":    "filled" | "partial" | "cancelled" | "failed" | "timeout",
              "status":     <last raw moomoo status string seen, or None>,
              "filled_qty": <int — total dealt qty, may be 0>,
              "total_qty":  <int — original order qty>,
            }

        ``partial`` means the deadline expired with non-zero ``filled_qty`` but
        the order was still working — the caller should cancel the remainder
        and decide whether to flatten the partial.
        """
        import moomoo as ft
        loop = asyncio.get_running_loop()
        deadline = _time_module.monotonic() + timeout
        last_status: str | None = None
        last_filled_qty: int = 0
        last_total_qty: int = 0
        last_avg_price: float = 0.0

        terminal_filled = {"FILLED_ALL"}
        terminal_cancelled = {"CANCELLED_ALL", "CANCELLED_PART", "DELETED",
                              "CANCELLING_PART", "CANCELLING_ALL"}
        terminal_failed = {"FAILED", "DISABLED"}

        while _time_module.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_S)

            def _check():
                ret, data = self._trd_ctx.order_list_query(
                    order_id=order_id,
                    trd_env=self._ft_trd_env,
                    acc_id=self._acc_id,
                )
                if ret != ft.RET_OK or data.empty:
                    return None, 0, 0, 0.0
                row = data.iloc[0]
                status = str(row.get("order_status", ""))
                filled = int(float(row.get("dealt_qty", 0) or 0))
                total = int(float(row.get("qty", 0) or 0))
                avg = float(row.get("dealt_avg_price", 0) or 0)
                return status, filled, total, avg

            status, filled_qty, total_qty, avg_price = await loop.run_in_executor(None, _check)
            if status is not None:
                last_status = status
                last_filled_qty = filled_qty
                last_total_qty = total_qty
                last_avg_price = avg_price

            if status in terminal_filled:
                return {"outcome": "filled", "status": status,
                        "filled_qty": filled_qty, "total_qty": total_qty,
                        "avg_fill_price": avg_price}
            if status in terminal_cancelled:
                return {"outcome": "cancelled", "status": status,
                        "filled_qty": filled_qty, "total_qty": total_qty,
                        "avg_fill_price": avg_price}
            if status in terminal_failed:
                return {"outcome": "failed", "status": status,
                        "filled_qty": filled_qty, "total_qty": total_qty,
                        "avg_fill_price": avg_price}
            # FILLED_PART is non-terminal: keep waiting for FILLED_ALL.

        # Deadline expired. If anything filled, surface it as 'partial'.
        outcome = "partial" if last_filled_qty > 0 else "timeout"
        return {"outcome": outcome, "status": last_status,
                "filled_qty": last_filled_qty, "total_qty": last_total_qty,
                "avg_fill_price": last_avg_price}

    async def _cancel_order_sync(self, order_id: str) -> dict[str, Any]:
        """Cancel an order, returning a structured outcome.

        Old behaviour was to ignore ``modify_order``'s ``(ret, data)`` return
        — a failed cancel (e.g. order already filled, network error) silently
        left orphan legs open. Now we unpack, log, and surface the result so
        callers can decide whether to escalate (e.g. force a market flatten).

        Returns one of:
          ``{"ok": True,  "order_id": ...}`` on RET_OK
          ``{"ok": False, "order_id": ..., "reason": <str>}`` otherwise
        """
        import moomoo as ft
        loop = asyncio.get_running_loop()

        def _cancel():
            ret, data = self._trd_ctx.modify_order(
                modify_order_op=ft.ModifyOrderOp.CANCEL,
                order_id=order_id,
                qty=0,
                price=0,
                trd_env=self._ft_trd_env,
                acc_id=self._acc_id,
            )
            return ret, data

        try:
            ret, data = await loop.run_in_executor(None, _cancel)
        except Exception as exc:
            logger.warning("cancel_order %s exception: %s", order_id, exc)
            return {"ok": False, "order_id": order_id, "reason": str(exc)}

        if ret != ft.RET_OK:
            reason = str(data)
            logger.warning(
                "cancel_order %s failed: ret=%s reason=%s",
                order_id, ret, reason,
            )
            return {"ok": False, "order_id": order_id, "reason": reason}
        return {"ok": True, "order_id": order_id}

    async def _place_market_close(self, leg: LegSpec, qty: int, side: str) -> str:
        import moomoo as ft
        loop = asyncio.get_running_loop()
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
                **_close_session_kwargs(ft),
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"place_order MARKET failed ({side} {code}): {data}")
            return str(data["order_id"].iloc[0])

        return await loop.run_in_executor(None, _place)

    # ── Historical bars (IBKR-parity) ─────────────────────────────────────────

    async def get_historical_bars(
        self, symbol: str, duration_days: int = 30, bar_size: str = "1D"
    ) -> list[dict[str, Any]]:
        """Fetch historical OHLCV bars from moomoo.

        Mirrors IBKRTrader.get_historical_bars() for parity.
        bar_size: 1D, 1W, 1M, 1m, 5m, 15m, 60m (or moomoo K_* constants)
        """
        await self.ensure_connected()
        import moomoo as ft
        from datetime import datetime, timedelta
        loop = asyncio.get_running_loop()

        bar_map = {
            "1D": ft.KLType.K_DAY, "K_DAY": ft.KLType.K_DAY,
            "1W": ft.KLType.K_WEEK, "K_WEEK": ft.KLType.K_WEEK,
            "1M": ft.KLType.K_MON, "K_MON": ft.KLType.K_MON,
            "1m": ft.KLType.K_1M, "K_1M": ft.KLType.K_1M,
            "5m": ft.KLType.K_5M, "K_5M": ft.KLType.K_5M,
            "15m": ft.KLType.K_15M, "K_15M": ft.KLType.K_15M,
            "60m": ft.KLType.K_60M, "K_60M": ft.KLType.K_60M,
        }
        kl_type = bar_map.get(bar_size, ft.KLType.K_DAY)
        code = f"US.{symbol}"
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=duration_days)).strftime("%Y-%m-%d")

        def _fetch():
            ret, data, _ = self._quote_ctx.request_history_kline(
                code, start=start, end=end, ktype=kl_type, max_count=500
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"request_history_kline: {data}")
            return data.to_dict(orient="records")

        result = await loop.run_in_executor(None, _fetch)
        self._mark_healthy()
        return [
            {
                "Date": r.get("time_key", ""),
                "Open": r.get("open", 0),
                "High": r.get("high", 0),
                "Low": r.get("low", 0),
                "Close": r.get("close", 0),
                "Volume": r.get("volume", 0),
            }
            for r in result
        ]

    # ── Active orders (IBKR-parity) ───────────────────────────────────────────

    async def get_active_orders(self) -> list[dict[str, Any]]:
        """Return all pending/submitted orders. Mirrors IBKRTrader.get_active_orders()."""
        await self.ensure_connected()
        import moomoo as ft
        loop = asyncio.get_running_loop()

        def _fetch():
            ret, data = self._trd_ctx.order_list_query(
                trd_env=self._ft_trd_env, acc_id=self._acc_id,
                status_filter_list=[
                    ft.OrderStatus.SUBMITTING,
                    ft.OrderStatus.SUBMITTED,
                    ft.OrderStatus.WAITING_SUBMIT,
                    ft.OrderStatus.FILLED_PART,
                ],
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"order_list_query: {data}")
            return data.to_dict(orient="records")

        result = await loop.run_in_executor(None, _fetch)
        self._mark_healthy()
        return [
            {
                "orderId": str(r.get("order_id", "")),
                "symbol": r.get("code", ""),
                "action": r.get("trd_side", ""),
                "qty": int(r.get("qty", 0)),
                "type": r.get("order_type", ""),
                "lmtPrice": float(r.get("price", 0)),
                "status": r.get("order_status", ""),
                "filled": int(r.get("dealt_qty", 0)),
                "avgFillPrice": float(r.get("dealt_avg_price", 0)),
                "createTime": r.get("create_time", ""),
            }
            for r in result
        ]

    # ── Daily P&L (IBKR-parity) ──────────────────────────────────────────────

    @property
    def daily_pnl(self) -> float:
        """Best-effort daily P&L from account info. Returns 0 if unavailable."""
        if not self.is_alive():
            return 0.0
        try:
            import moomoo as ft
            ret, data = self._trd_ctx.accinfo_query(
                trd_env=self._ft_trd_env, acc_id=self._acc_id, currency="USD",
            )
            if ret != ft.RET_OK:
                return 0.0
            row = data.iloc[0].to_dict()
            return self._safe_float(row.get("realized_pl", 0)) + self._safe_float(row.get("unrealized_pl", 0))
        except Exception:
            return 0.0


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


# ── Singleton connection manager (IBKR-parity) ───────────────────────────────

_moomoo_instances: dict[str, MoomooTrader] = {}


async def get_moomoo_connection(
    host: str = "127.0.0.1",
    port: int = 11111,
    trade_password: str = "",
    trd_env: int = 0,
    security_firm: str = "NONE",
    filter_trdmarket: str = "NONE",
) -> tuple[MoomooTrader | None, str]:
    """Mirrors get_ib_connection() — returns (trader, status_msg).

    Reuses existing trader instances by host:port key, auto-reconnecting
    if the connection dropped.
    """
    key = f"{host}:{port}:{trd_env}"

    if key not in _moomoo_instances:
        _moomoo_instances[key] = MoomooTrader(
            host=host, port=port, trade_password=trade_password,
            trd_env=trd_env, security_firm=security_firm,
            filter_trdmarket=filter_trdmarket,
        )

    trader = _moomoo_instances[key]
    if not trader.is_alive():
        try:
            result = await trader.connect()
            if not result.get("connected"):
                return None, result.get("error", "Connect failed")
        except Exception as exc:
            return None, str(exc)

    return trader, "OK"

