"""0DTE Order Flow Strategy — live execution via MoomooTrader.

Subscribes to SPY tick data, runs the OrderFlowEngine to detect
absorption / delta divergence signals, and executes 0DTE ATM options
with built-in OCO risk management (stop-loss + profit target).

Lifecycle:
  bot = ZeroDTEOrderFlowBot(trader)
  await bot.start()     # subscribes to ticks, begins processing
  ...
  await bot.stop()      # unsubscribes, flattens if configured

Integrates with:
  - core/order_flow.py  (tick classification, bar building, signals)
  - core/risk.py        (daily loss limits, position limits)
  - moomoo_trading.py   (MoomooTrader for execution)
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import sys
from pathlib import Path
if __name__ == "__main__":
    # Add project root to path so `from core...` works when run directly
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.order_flow import (
    TickClassifier,
    BarBuilder,
    OrderFlowEngine,
    OrderFlowBar,
    SignalEvent,
)

logger = logging.getLogger(__name__)


# ── Config ───────────────────────────────────────────────────────────────────

@dataclass
class ZeroDTEConfig:
    """Tunable parameters for the 0DTE bot."""
    # Signal thresholds
    lookback_bars: int = 5
    vol_threshold: float = 2.0
    range_threshold_pct: float = 0.05
    delta_threshold: float = 500

    # Execution
    contracts: int = 1
    stop_loss_pct: float = 50.0   # % of premium paid
    profit_target_pct: float = 100.0  # % of premium paid
    fill_timeout_s: float = 30.0

    # Risk limits (per session)
    max_trades_per_day: int = 5
    max_loss_per_day: float = 500.0   # absolute $
    max_open_positions: int = 1       # only 1 0DTE at a time
    min_confidence: float = 0.3       # signal confidence threshold

    # Tick subscription
    symbol: str = "SPY"
    tick_poll_interval_s: float = 0.5  # how often to poll moomoo for ticks

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ── ATM Strike Selection ─────────────────────────────────────────────────────

def get_spy_0dte_ticker(
    spot_price: float,
    side: str,  # 'call' or 'put'
    dt: datetime | None = None,
) -> dict[str, Any]:
    """Select ATM 0DTE option and return moomoo-formatted ticker.

    Rounds spot to nearest $1 strike (SPY uses $1 increments).
    Formats: US.SPY260502C510000

    Returns dict with:
      - code: moomoo option code string
      - strike: float
      - right: 'C' or 'P'
      - expiry: 'YYMMDD' string
    """
    dt = dt or datetime.now(timezone.utc)

    # For 0DTE, expiry is today
    expiry_str = dt.strftime("%y%m%d")  # e.g. "260502"

    # Round to nearest $1 strike
    strike = round(spot_price)
    right = "C" if side == "call" else "P"

    # Moomoo format: US.SPY{YY}{MM}{DD}{C/P}{strike*1000}
    strike_int = int(round(strike * 1000))
    code = f"US.SPY{expiry_str}{right}{strike_int}"

    return {
        "code": code,
        "strike": float(strike),
        "right": right,
        "expiry": expiry_str,
        "expiry_full": dt.strftime("%Y%m%d"),
    }


# ── Position Tracker ─────────────────────────────────────────────────────────

@dataclass
class ActivePosition:
    """Tracks a live 0DTE option position."""
    signal: SignalEvent
    option_code: str
    strike: float
    right: str
    side: str  # 'call' or 'put'
    order_id: str
    entry_price: float = 0.0
    entry_time: float = 0.0
    contracts: int = 1
    stop_price: float = 0.0
    target_price: float = 0.0
    status: str = "pending"  # pending → filled → closed

    def as_dict(self) -> dict[str, Any]:
        return {
            "option_code": self.option_code,
            "strike": self.strike,
            "right": self.right,
            "side": self.side,
            "order_id": self.order_id,
            "entry_price": round(self.entry_price, 2),
            "stop_price": round(self.stop_price, 2),
            "target_price": round(self.target_price, 2),
            "contracts": self.contracts,
            "status": self.status,
            "signal_type": self.signal.signal_type,
        }


# ── The Bot ──────────────────────────────────────────────────────────────────

class ZeroDTEOrderFlowBot:
    """0DTE order flow trading bot for SPY options via Moomoo.

    Usage:
        bot = ZeroDTEOrderFlowBot(trader, config)
        await bot.start()
        # ... runs until stopped
        await bot.stop()
    """

    def __init__(self, trader, config: ZeroDTEConfig | None = None):
        """
        Args:
            trader: MoomooTrader instance (already connected)
            config: Bot configuration (uses defaults if None)
        """
        self._trader = trader
        self.config = config or ZeroDTEConfig()
        self._running = False
        self._task: asyncio.Task | None = None

        # Order flow pipeline
        self._classifier = TickClassifier()
        self._engine = OrderFlowEngine(
            lookback=self.config.lookback_bars,
            vol_threshold=self.config.vol_threshold,
            range_threshold_pct=self.config.range_threshold_pct,
            delta_threshold=self.config.delta_threshold,
            on_signal=self._on_signal,
        )
        self._bar_builder = BarBuilder(on_bar_complete=self._engine.on_bar)

        # Session state
        self._trades_today: int = 0
        self._pnl_today: float = 0.0
        self._active_position: ActivePosition | None = None
        self._closed_positions: list[dict] = []
        self._last_tick_ts: float = 0.0
        self._tick_count: int = 0
        self._signal_queue: asyncio.Queue = asyncio.Queue()
        self._start_time: float = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> dict[str, Any]:
        """Start the tick subscription loop and signal processor."""
        if self._running:
            return {"ok": False, "error": "Already running"}

        if not self._trader.is_alive():
            return {"ok": False, "error": "MoomooTrader is not connected"}

        self._running = True
        self._start_time = _time.time()
        self._classifier.reset()
        self._engine.reset()
        self._bar_builder.reset()
        self._trades_today = 0
        self._pnl_today = 0.0

        # Start the main loop as a background task
        self._task = asyncio.get_running_loop().create_task(self._main_loop())
        logger.info("0DTE OrderFlow bot started (config: %s)", self.config.as_dict())
        return {"ok": True, "msg": "0DTE bot started"}

    async def stop(self) -> dict[str, Any]:
        """Stop the bot and optionally flatten open positions."""
        if not self._running:
            return {"ok": False, "error": "Not running"}

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Flush any partial bar
        self._bar_builder.flush()

        logger.info(
            "0DTE bot stopped. Trades: %d, P&L: $%.2f",
            self._trades_today, self._pnl_today,
        )
        return {
            "ok": True,
            "trades_today": self._trades_today,
            "pnl_today": round(self._pnl_today, 2),
        }

    # ── Main loop ─────────────────────────────────────────────────────────

    async def _main_loop(self):
        """Poll ticks from moomoo and feed into the order flow pipeline."""
        import moomoo as ft
        code = f"US.{self.config.symbol}"
        last_tick_id = 0

        logger.info("0DTE bot: subscribing to %s ticks", code)

        while self._running:
            try:
                loop = asyncio.get_running_loop()

                def _get_ticks():
                    ret, data = self._trader._quote_ctx.get_rt_ticker(
                        code, num=50
                    )
                    if ret != ft.RET_OK:
                        return []
                    return data.to_dict(orient="records")

                ticks = await loop.run_in_executor(None, _get_ticks)

                for tick in ticks:
                    # Skip already-processed ticks
                    seq = tick.get("sequence", 0)
                    if seq <= last_tick_id:
                        continue
                    last_tick_id = seq

                    price = float(tick.get("price", 0))
                    volume = float(tick.get("volume", 0))
                    ts_str = tick.get("time", "")

                    if price <= 0 or volume <= 0:
                        continue

                    # Parse timestamp
                    try:
                        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(
                            tzinfo=timezone.utc
                        ).timestamp()
                    except (ValueError, TypeError):
                        ts = _time.time()

                    # Feed through pipeline
                    classified = self._classifier.classify(price, volume, ts)
                    self._bar_builder.on_tick(classified)
                    self._tick_count += 1
                    self._last_tick_ts = ts

                # Process any pending signals
                while not self._signal_queue.empty():
                    signal = self._signal_queue.get_nowait()
                    await self._execute_signal(signal)

                # Monitor active position
                if self._active_position and self._active_position.status == "filled":
                    await self._monitor_position()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("0DTE bot tick loop error: %s", exc, exc_info=True)

            await asyncio.sleep(self.config.tick_poll_interval_s)

    # ── Signal handler ────────────────────────────────────────────────────

    def _on_signal(self, signal: SignalEvent):
        """Called by OrderFlowEngine when a signal fires. Queue for async processing."""
        # Pre-filter: confidence, risk limits
        if signal.confidence < self.config.min_confidence:
            logger.debug("Signal rejected (low confidence %.3f): %s", signal.confidence, signal.reason)
            return

        if self._trades_today >= self.config.max_trades_per_day:
            logger.info("Signal rejected (max trades/day %d reached)", self.config.max_trades_per_day)
            return

        if self._pnl_today <= -self.config.max_loss_per_day:
            logger.info("Signal rejected (daily loss limit $%.2f hit)", self.config.max_loss_per_day)
            return

        if self._active_position is not None:
            logger.info("Signal rejected (position already open)")
            return

        try:
            self._signal_queue.put_nowait(signal)
            logger.info("Signal queued: %s (confidence=%.3f)", signal.signal_type, signal.confidence)
        except asyncio.QueueFull:
            pass

    # ── Execution ─────────────────────────────────────────────────────────

    async def _execute_signal(self, signal: SignalEvent):
        """Place a market order for the 0DTE ATM option."""
        try:
            # Get current spot price.
            # MoomooTrader.get_live_price returns {"last", "bid", "ask",
            # "volume"} — NOT the raw moomoo "last_price" key.  Reading
            # the wrong key here silently returned 0 and the bot never
            # placed an order.
            spot_data = await self._trader.get_live_price(self.config.symbol)
            spot = (
                spot_data.get("last")
                or spot_data.get("last_price")
                or spot_data.get("price")
                or 0
            )
            if not spot or spot <= 0:
                logger.warning("Cannot execute: no spot price for %s (got %r)",
                               self.config.symbol, spot_data)
                return

            # Select ATM 0DTE option
            opt = get_spy_0dte_ticker(float(spot), signal.side)
            logger.info(
                "0DTE executing: %s %s @ strike $%s (spot=$%.2f, signal=%s)",
                signal.side.upper(), opt["code"], opt["strike"], spot, signal.signal_type,
            )

            # Idempotency key
            bar_ts = signal.bar.bar_ts.strftime("%H%M") if signal.bar.bar_ts else "0000"
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            client_ref = f"0dte:{today}:{signal.signal_type}:{bar_ts}"

            # Place market order via moomoo
            import moomoo as ft
            loop = asyncio.get_running_loop()

            def _place():
                ret, data = self._trader._trd_ctx.place_order(
                    price=0.0,
                    qty=self.config.contracts,
                    code=opt["code"],
                    trd_side=ft.TrdSide.BUY,
                    order_type=ft.OrderType.MARKET,
                    trd_env=self._trader._ft_trd_env,
                    acc_id=self._trader._acc_id,
                    remark=client_ref,
                )
                if ret != ft.RET_OK:
                    raise RuntimeError(f"place_order failed: {data}")
                return str(data["order_id"].iloc[0])

            order_id = await loop.run_in_executor(None, _place)

            # Track the position
            self._active_position = ActivePosition(
                signal=signal,
                option_code=opt["code"],
                strike=opt["strike"],
                right=opt["right"],
                side=signal.side,
                order_id=order_id,
                contracts=self.config.contracts,
                status="pending",
                entry_time=_time.time(),
            )
            self._trades_today += 1

            # Wait for fill
            filled = await self._trader._wait_for_fill(order_id, self.config.fill_timeout_s)
            if filled:
                # Get fill price.  MoomooTrader.get_order_status returns
                # {"status", "filled", "remaining", "avgFillPrice",
                # "commission"} — NOT the raw moomoo "dealt_avg_price"
                # column.  Reading the wrong key left fill_price=0 →
                # stop_price=0 / target_price=0 → instant "profit_target"
                # exit on every fill.  Fall back to raw column for safety.
                status = await self._trader.get_order_status(order_id)
                if status:
                    fill_price = float(
                        status.get("avgFillPrice")
                        or status.get("dealt_avg_price")
                        or 0
                    )
                    stop_price = fill_price * (
                        1 - self.config.stop_loss_pct / 100
                    )
                    target_price = fill_price * (
                        1 + self.config.profit_target_pct / 100
                    )

                    # Atomic swap: build the fully-populated filled record
                    # and replace the pending one in a single assignment so
                    # _monitor_position never observes stop_price=0/target=0
                    # alongside status="filled" (the prior partial-mutation
                    # window caused instant false profit-target exits).
                    self._active_position = ActivePosition(
                        signal=signal,
                        option_code=opt["code"],
                        strike=opt["strike"],
                        right=opt["right"],
                        side=signal.side,
                        order_id=order_id,
                        contracts=self.config.contracts,
                        status="filled",
                        entry_time=_time.time(),
                        entry_price=fill_price,
                        stop_price=stop_price,
                        target_price=target_price,
                    )

                    logger.info(
                        "0DTE FILLED: %s @ $%.2f | Stop: $%.2f | Target: $%.2f",
                        opt["code"], fill_price, stop_price, target_price,
                    )

                    # Send telegram notification
                    self._notify(
                        f"🎯 0DTE ENTRY: {signal.side.upper()} {opt['code']}\n"
                        f"Fill: ${fill_price:.2f} | Stop: ${stop_price:.2f} "
                        f"| Target: ${target_price:.2f}\n"
                        f"Signal: {signal.reason}"
                    )
            else:
                logger.warning("0DTE fill timeout for order %s — cancelling", order_id)
                cancel_res = await self._trader._cancel_order_sync(order_id)
                if not cancel_res.get("ok"):
                    logger.error(
                        "0DTE cancel FAILED for %s: %s — leg may still be working",
                        order_id, cancel_res.get("reason"),
                    )
                self._active_position = None

        except Exception as exc:
            logger.error("0DTE execution error: %s", exc, exc_info=True)
            self._active_position = None

    # ── Position monitoring ───────────────────────────────────────────────

    async def _monitor_position(self):
        """Check if active position hit stop-loss or profit target."""
        pos = self._active_position
        if not pos or pos.status != "filled":
            return

        try:
            # Get current option price via snapshot
            import moomoo as ft
            loop = asyncio.get_running_loop()

            def _get_price():
                ret, data = self._trader._quote_ctx.get_market_snapshot([pos.option_code])
                if ret != ft.RET_OK:
                    return None
                rows = data.to_dict(orient="records")
                if rows:
                    return float(rows[0].get("last_price", 0))
                return None

            current_price = await loop.run_in_executor(None, _get_price)
            if current_price is None or current_price <= 0:
                return

            # Check stop/target
            should_close = False
            reason = ""

            if current_price <= pos.stop_price:
                should_close = True
                reason = "stop_loss"
            elif current_price >= pos.target_price:
                should_close = True
                reason = "profit_target"

            if should_close:
                await self._close_position(pos, current_price, reason)

        except Exception as exc:
            logger.error("0DTE monitor error: %s", exc)

    async def _close_position(self, pos: ActivePosition, exit_price: float, reason: str):
        """Close the active 0DTE position."""
        try:
            import moomoo as ft
            loop = asyncio.get_running_loop()

            def _close():
                ret, data = self._trader._trd_ctx.place_order(
                    price=0.0,
                    qty=pos.contracts,
                    code=pos.option_code,
                    trd_side=ft.TrdSide.SELL,
                    order_type=ft.OrderType.MARKET,
                    trd_env=self._trader._ft_trd_env,
                    acc_id=self._trader._acc_id,
                    remark=f"0dte_exit:{reason}",
                )
                if ret != ft.RET_OK:
                    raise RuntimeError(f"close_order failed: {data}")
                return str(data["order_id"].iloc[0])

            exit_order_id = await loop.run_in_executor(None, _close)

            # Calculate P&L
            pnl = (exit_price - pos.entry_price) * pos.contracts * 100  # options are 100x
            self._pnl_today += pnl

            pos.status = "closed"
            self._closed_positions.append({
                **pos.as_dict(),
                "exit_price": round(exit_price, 2),
                "exit_order_id": exit_order_id,
                "exit_reason": reason,
                "pnl": round(pnl, 2),
                "exit_time": _time.time(),
            })
            self._active_position = None

            emoji = "✅" if pnl >= 0 else "🔴"
            logger.info(
                "0DTE CLOSED: %s @ $%.2f → $%.2f | P&L: $%.2f | Reason: %s",
                pos.option_code, pos.entry_price, exit_price, pnl, reason,
            )
            self._notify(
                f"{emoji} 0DTE EXIT: {pos.option_code}\n"
                f"Entry: ${pos.entry_price:.2f} → Exit: ${exit_price:.2f}\n"
                f"P&L: ${pnl:+.2f} | Reason: {reason}\n"
                f"Day total: ${self._pnl_today:+.2f}"
            )

        except Exception as exc:
            logger.error("0DTE close error: %s", exc, exc_info=True)

    # ── Notifications ─────────────────────────────────────────────────────

    @staticmethod
    def _notify(msg: str):
        """Best-effort Telegram notification.

        The actual module is core.telegram_bot.notify(text).  Earlier
        versions imported ``send_telegram`` from a non-existent
        ``core.notification`` module — every alert was silently swallowed
        by the bare except.
        """
        try:
            from core.telegram_bot import notify
            notify(msg)
        except Exception as e:  # noqa: BLE001
            logger.warning("0DTE telegram notify failed: %s", e)

    # ── Status ────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    def status(self) -> dict[str, Any]:
        """Full bot status for API/UI."""
        return {
            "running": self._running,
            "uptime_s": round(_time.time() - self._start_time, 1) if self._running else 0,
            "tick_count": self._tick_count,
            "last_tick_ts": self._last_tick_ts,
            "trades_today": self._trades_today,
            "pnl_today": round(self._pnl_today, 2),
            "max_trades": self.config.max_trades_per_day,
            "max_loss": self.config.max_loss_per_day,
            "active_position": self._active_position.as_dict() if self._active_position else None,
            "closed_positions": self._closed_positions[-10:],  # last 10
            "order_flow": self._engine.state_dict(),
            "config": self.config.as_dict(),
        }
