"""I12 — IBKR adapter mocked socket tests.

Tests for IBKRTrader methods using unittest.mock so no live IBKR connection
is needed.  All ib_insync objects (IB, Stock, Option, ComboLeg, Bag,
LimitOrder, MarketOrder) are mocked at the module level.

Coverage:
  - IBKRTrader instantiation succeeds when HAS_IBSYNC=True
  - connect() success path sets connected=True
  - connect() failure path sets connected=False and returns error dict
  - ensure_connected() reconnects when socket is dropped
  - get_account_summary() maps AccountValue tags → equity/buying_power/etc.
  - get_combo_midpoint() returns (bid+ask)/2 from a mock Ticker
  - place_combo_order() returns order_id from mock Trade
  - get_order_status() via get_active_orders() maps FSM state correctly
  - cancel_order() sends cancellation for a known orderId
  - place_test_order() returns a success dict with orderId
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Module-level fixtures ───────────────────────────────────────────────────

def _make_account_value(tag: str, value: str) -> MagicMock:
    """Return a mock ib_insync AccountValue with .tag and .value attrs."""
    av = MagicMock()
    av.tag = tag
    av.value = value
    return av


def _make_mock_ib(connected: bool = True) -> MagicMock:
    """Return a fully-featured mock IB() instance."""
    ib = MagicMock()
    ib.isConnected.return_value = connected
    ib.connectAsync = AsyncMock()
    ib.accountSummaryAsync = AsyncMock(return_value=[])
    ib.qualifyContractsAsync = AsyncMock()
    ib.placeOrder = MagicMock()
    ib.openTrades = MagicMock(return_value=[])
    ib.portfolio = MagicMock(return_value=[])
    ib.reqMktData = MagicMock()
    ib.cancelMktData = MagicMock()
    ib.cancelOrder = MagicMock()
    ib.disconnect = MagicMock()
    return ib


def _make_trader(ib: MagicMock | None = None):
    """
    Construct an IBKRTrader with all ib_insync symbols patched so no real
    import or socket is needed.
    """
    if ib is None:
        ib = _make_mock_ib()

    with (
        patch("brokers.ibkr_trading.HAS_IBSYNC", True),
        patch("brokers.ibkr_trading.IB", return_value=ib),
        patch("brokers.ibkr_trading.Stock", MagicMock()),
        patch("brokers.ibkr_trading.Option", MagicMock()),
        patch("brokers.ibkr_trading.ComboLeg", MagicMock()),
        patch("brokers.ibkr_trading.Bag", MagicMock()),
        patch("brokers.ibkr_trading.LimitOrder", MagicMock()),
        patch("brokers.ibkr_trading.MarketOrder", MagicMock()),
    ):
        from brokers.ibkr_trading import IBKRTrader
        trader = IBKRTrader.__new__(IBKRTrader)
        trader.host = "127.0.0.1"
        trader.port = 7497
        trader.client_id = 1
        trader.connected = False
        trader.ib = ib
        trader._retry_count = 0
        trader._last_retry_time = 0
        trader._no_mktdata_subs = set()
    return trader


# ── Helper to run async methods in tests ──────────────────────────────────

def _run(coro):
    """Execute an async coroutine synchronously in tests."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── 1. Instantiation ───────────────────────────────────────────────────────

class TestIBKRTraderInstantiation:
    def test_instantiation_with_has_ibsync_true(self):
        """IBKRTrader can be created when HAS_IBSYNC=True."""
        mock_ib_instance = _make_mock_ib()
        with (
            patch("brokers.ibkr_trading.HAS_IBSYNC", True),
            patch("brokers.ibkr_trading.IB", return_value=mock_ib_instance),
            patch("brokers.ibkr_trading.Stock", MagicMock()),
            patch("brokers.ibkr_trading.Option", MagicMock()),
            patch("brokers.ibkr_trading.ComboLeg", MagicMock()),
            patch("brokers.ibkr_trading.Bag", MagicMock()),
            patch("brokers.ibkr_trading.LimitOrder", MagicMock()),
            patch("brokers.ibkr_trading.MarketOrder", MagicMock()),
        ):
            from brokers.ibkr_trading import IBKRTrader
            trader = IBKRTrader(host="127.0.0.1", port=7497, client_id=1)
            assert trader.host == "127.0.0.1"
            assert trader.port == 7497
            assert trader.client_id == 1
            assert trader.connected is False

    def test_instantiation_raises_when_ibsync_missing(self):
        """IBKRTrader raises RuntimeError when HAS_IBSYNC=False."""
        with patch("brokers.ibkr_trading.HAS_IBSYNC", False):
            from brokers.ibkr_trading import IBKRTrader
            with pytest.raises(RuntimeError, match="ib_insync"):
                IBKRTrader()


# ── 2. connect() ───────────────────────────────────────────────────────────

class TestConnect:
    def test_connect_success(self):
        """connect() sets connected=True and returns success dict."""
        ib = _make_mock_ib(connected=False)
        ib.connectAsync = AsyncMock()  # successful connect, no exception

        trader = _make_trader(ib)
        result = _run(trader.connect())

        assert result["success"] is True
        assert trader.connected is True
        ib.connectAsync.assert_called_once_with(
            trader.host, trader.port, clientId=trader.client_id
        )

    def test_connect_failure_connection_refused(self):
        """connect() handles ConnectionRefusedError and returns error dict."""
        ib = _make_mock_ib(connected=False)
        ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("Connection refused"))

        trader = _make_trader(ib)
        result = _run(trader.connect())

        assert result["success"] is False
        assert trader.connected is False
        assert "Connection refused" in result["msg"]

    def test_connect_failure_generic_exception(self):
        """connect() handles any exception and returns error dict."""
        ib = _make_mock_ib(connected=False)
        ib.connectAsync = AsyncMock(side_effect=OSError("Network unreachable"))

        trader = _make_trader(ib)
        result = _run(trader.connect())

        assert result["success"] is False
        assert "Network unreachable" in result["msg"]


# ── 3. ensure_connected() ──────────────────────────────────────────────────

class TestEnsureConnected:
    def test_ensure_connected_when_already_alive(self):
        """ensure_connected() skips reconnect when socket is alive."""
        ib = _make_mock_ib(connected=True)
        trader = _make_trader(ib)
        trader.connected = True

        result = _run(trader.ensure_connected())
        assert result["success"] is True
        # connectAsync must NOT have been called
        ib.connectAsync.assert_not_called()

    def test_ensure_connected_reconnects_when_dropped(self):
        """ensure_connected() reconnects when ib.isConnected() returns False."""
        ib = _make_mock_ib(connected=False)
        ib.connectAsync = AsyncMock()

        trader = _make_trader(ib)
        trader.connected = False  # simulate dropped connection

        with patch("core.connection_flags.is_auto_enabled", return_value=True):
            _run(trader.ensure_connected())
        ib.connectAsync.assert_called_once()


# ── 4. get_account_summary() ───────────────────────────────────────────────

class TestGetAccountSummary:
    def test_maps_account_values_correctly(self):
        """get_account_summary() parses NetLiquidation → equity, etc."""
        ib = _make_mock_ib(connected=True)
        ib.accountSummaryAsync = AsyncMock(
            return_value=[
                _make_account_value("NetLiquidation", "125000.50"),
                _make_account_value("BuyingPower", "300000.00"),
                _make_account_value("ExcessLiquidity", "95000.00"),
                _make_account_value("DailyPnL", "1234.56"),
                _make_account_value("UnrealizedPnL", "567.89"),
            ]
        )

        trader = _make_trader(ib)
        trader.connected = True
        # Prevent ensure_connected from re-connecting
        with patch.object(trader, "is_alive", return_value=True):
            result = _run(trader.get_account_summary())

        assert result["equity"] == pytest.approx(125000.50)
        assert result["buying_power"] == pytest.approx(300000.00)
        assert result["excess_liquidity"] == pytest.approx(95000.00)
        assert result["daily_pnl"] == pytest.approx(1234.56)
        assert result["unrealized_pnl"] == pytest.approx(567.89)

    def test_missing_tags_default_to_zero(self):
        """get_account_summary() defaults to 0 when a tag is absent."""
        ib = _make_mock_ib(connected=True)
        ib.accountSummaryAsync = AsyncMock(return_value=[])  # no tags at all

        trader = _make_trader(ib)
        with patch.object(trader, "is_alive", return_value=True):
            result = _run(trader.get_account_summary())

        assert result["equity"] == 0.0
        assert result["buying_power"] == 0.0
        assert result["excess_liquidity"] == 0.0


# ── 5. get_combo_midpoint() ────────────────────────────────────────────────

class TestGetComboMidpoint:
    def _build_legs(self):
        return [
            {"type": "call", "strike": 450, "expiry": "20241220", "side": "long"},
            {"type": "call", "strike": 455, "expiry": "20241220", "side": "short"},
        ]

    def test_midpoint_is_average_of_bid_ask(self):
        """get_combo_midpoint() returns (bid+ask)/2."""
        ib = _make_mock_ib(connected=True)

        mock_ticker = MagicMock()
        mock_ticker.bid = 1.20
        mock_ticker.ask = 1.40
        ib.reqMktData.return_value = mock_ticker

        # pd must be available for the isnan checks in the real code
        import pandas as _pd

        trader = _make_trader(ib)
        with (
            patch.object(trader, "is_alive", return_value=True),
            patch("brokers.ibkr_trading.pd", _pd),
            patch("brokers.ibkr_trading.Option", MagicMock()),
            patch("brokers.ibkr_trading.ComboLeg", MagicMock()),
            patch("brokers.ibkr_trading.Bag", MagicMock()),
        ):
            mid = _run(trader.get_combo_midpoint("SPY", self._build_legs()))

        assert mid == pytest.approx(1.30)

    def test_midpoint_returns_none_on_nan_bid_ask(self):
        """get_combo_midpoint() returns None when bid/ask are NaN."""
        import math
        import pandas as _pd

        ib = _make_mock_ib(connected=True)

        mock_ticker = MagicMock()
        mock_ticker.bid = float("nan")
        mock_ticker.ask = float("nan")
        ib.reqMktData.return_value = mock_ticker

        trader = _make_trader(ib)
        with (
            patch.object(trader, "is_alive", return_value=True),
            patch("brokers.ibkr_trading.pd", _pd),
            patch("brokers.ibkr_trading.Option", MagicMock()),
            patch("brokers.ibkr_trading.ComboLeg", MagicMock()),
            patch("brokers.ibkr_trading.Bag", MagicMock()),
        ):
            mid = _run(trader.get_combo_midpoint("SPY", self._build_legs()))

        assert mid is None


# ── 6. place_combo_order() ─────────────────────────────────────────────────

class TestPlaceComboOrder:
    def _build_legs(self):
        return [
            {"type": "call", "strike": 450, "expiry": "20241220", "side": "long"},
            {"type": "call", "strike": 455, "expiry": "20241220", "side": "short"},
        ]

    def test_returns_order_id_on_success(self):
        """place_combo_order() returns a dict with orderId from the mock Trade."""
        ib = _make_mock_ib(connected=True)

        mock_trade = MagicMock()
        mock_trade.order.orderId = 42
        mock_trade.orderStatus.status = "Submitted"
        ib.placeOrder.return_value = mock_trade

        trader = _make_trader(ib)
        with (
            patch.object(trader, "is_alive", return_value=True),
            patch("brokers.ibkr_trading.Option", MagicMock()),
            patch("brokers.ibkr_trading.ComboLeg", MagicMock()),
            patch("brokers.ibkr_trading.Bag", MagicMock()),
            patch("brokers.ibkr_trading.LimitOrder", MagicMock(return_value=MagicMock())),
        ):
            result = _run(
                trader.place_combo_order("SPY", self._build_legs(), sc=1, side="BUY", lmtPrice=1.30)
            )

        assert result["orderId"] == 42
        assert result["success"] is True
        assert result["status"] == "Submitted"

    def test_limit_order_used_when_lmt_price_provided(self):
        """place_combo_order() uses LimitOrder when lmtPrice is given."""
        ib = _make_mock_ib(connected=True)

        mock_trade = MagicMock()
        mock_trade.order.orderId = 99
        mock_trade.orderStatus.status = "PreSubmitted"
        ib.placeOrder.return_value = mock_trade

        mock_limit_order_cls = MagicMock(return_value=MagicMock())

        trader = _make_trader(ib)
        with (
            patch.object(trader, "is_alive", return_value=True),
            patch("brokers.ibkr_trading.Option", MagicMock()),
            patch("brokers.ibkr_trading.ComboLeg", MagicMock()),
            patch("brokers.ibkr_trading.Bag", MagicMock()),
            patch("brokers.ibkr_trading.LimitOrder", mock_limit_order_cls),
            patch("brokers.ibkr_trading.MarketOrder", MagicMock()),
        ):
            _run(
                trader.place_combo_order("SPY", self._build_legs(), sc=1, side="BUY", lmtPrice=1.30)
            )

        # LimitOrder constructor was called
        mock_limit_order_cls.assert_called_once()
        call_args = mock_limit_order_cls.call_args
        assert call_args[0][0] == "BUY"   # action
        assert call_args[0][1] == 1        # quantity
        assert call_args[0][2] == pytest.approx(1.30)  # price


# ── 7. get_order_status() via get_active_orders() ─────────────────────────

class TestGetActiveOrders:
    def _make_trade(self, order_id: int, symbol: str, action: str,
                    qty: int, order_type: str, lmt_price: float, status: str):
        t = MagicMock()
        t.order.orderId = order_id
        t.contract.symbol = symbol
        t.order.action = action
        t.order.totalQuantity = qty
        t.order.orderType = order_type
        t.order.lmtPrice = lmt_price
        t.orderStatus.status = status
        return t

    def test_maps_filled_status(self):
        """get_active_orders() maps Filled status correctly."""
        ib = _make_mock_ib(connected=True)
        ib.openTrades.return_value = [
            self._make_trade(1, "SPY", "BUY", 2, "LMT", 1.30, "Filled")
        ]

        trader = _make_trader(ib)
        with patch.object(trader, "is_alive", return_value=True):
            orders = _run(trader.get_active_orders())

        assert len(orders) == 1
        assert orders[0]["orderId"] == 1
        assert orders[0]["status"] == "Filled"
        assert orders[0]["symbol"] == "SPY"

    def test_maps_cancelled_status(self):
        """get_active_orders() maps Cancelled status correctly."""
        ib = _make_mock_ib(connected=True)
        ib.openTrades.return_value = [
            self._make_trade(2, "SPY", "SELL", 1, "LMT", 1.10, "Cancelled")
        ]

        trader = _make_trader(ib)
        with patch.object(trader, "is_alive", return_value=True):
            orders = _run(trader.get_active_orders())

        assert orders[0]["status"] == "Cancelled"

    def test_maps_submitted_status(self):
        """get_active_orders() maps Submitted status correctly."""
        ib = _make_mock_ib(connected=True)
        ib.openTrades.return_value = [
            self._make_trade(3, "SPY", "BUY", 1, "MKT", 0.0, "Submitted")
        ]

        trader = _make_trader(ib)
        with patch.object(trader, "is_alive", return_value=True):
            orders = _run(trader.get_active_orders())

        assert orders[0]["status"] == "Submitted"

    @pytest.mark.parametrize("status", ["Filled", "Cancelled", "Submitted", "PreSubmitted", "Inactive"])
    def test_all_fsm_statuses_pass_through(self, status):
        """get_active_orders() passes through any status string unchanged."""
        ib = _make_mock_ib(connected=True)
        ib.openTrades.return_value = [
            self._make_trade(10, "SPY", "BUY", 1, "LMT", 1.00, status)
        ]

        trader = _make_trader(ib)
        with patch.object(trader, "is_alive", return_value=True):
            orders = _run(trader.get_active_orders())

        assert orders[0]["status"] == status

    def test_empty_open_trades(self):
        """get_active_orders() returns empty list when no open trades."""
        ib = _make_mock_ib(connected=True)
        ib.openTrades.return_value = []

        trader = _make_trader(ib)
        with patch.object(trader, "is_alive", return_value=True):
            orders = _run(trader.get_active_orders())

        assert orders == []


# ── 8. cancel_order() ─────────────────────────────────────────────────────

class TestCancelOrder:
    def test_cancel_known_order_succeeds(self):
        """cancel_order() calls ib.cancelOrder and returns success."""
        ib = _make_mock_ib(connected=True)

        mock_trade = MagicMock()
        mock_trade.order.orderId = 55
        ib.openTrades.return_value = [mock_trade]

        trader = _make_trader(ib)
        with patch.object(trader, "is_alive", return_value=True):
            result = _run(trader.cancel_order(55))

        assert result["success"] is True
        assert "55" in result["msg"]
        ib.cancelOrder.assert_called_once_with(mock_trade.order)

    def test_cancel_unknown_order_fails(self):
        """cancel_order() returns failure when orderId is not found."""
        ib = _make_mock_ib(connected=True)
        ib.openTrades.return_value = []  # no open trades

        trader = _make_trader(ib)
        with patch.object(trader, "is_alive", return_value=True):
            result = _run(trader.cancel_order(999))

        assert result["success"] is False
        assert "999" in result["msg"]


# ── 9. place_test_order() ─────────────────────────────────────────────────

class TestPlaceTestOrder:
    def test_place_test_order_success(self):
        """place_test_order() returns success dict with orderId."""
        ib = _make_mock_ib(connected=True)

        mock_contract = MagicMock()
        mock_contract.conId = 756733  # realistic SPY conId
        ib.qualifyContractsAsync = AsyncMock()

        mock_trade = MagicMock()
        mock_trade.order.orderId = 77
        mock_trade.orderStatus.status = "PreSubmitted"
        ib.placeOrder.return_value = mock_trade

        trader = _make_trader(ib)
        with (
            patch.object(trader, "is_alive", return_value=True),
            patch("brokers.ibkr_trading.Stock", MagicMock(return_value=mock_contract)),
            patch("brokers.ibkr_trading.LimitOrder", MagicMock(return_value=MagicMock())),
            # Avoid the asyncio.sleep(0.5) inside place_test_order
            patch("asyncio.sleep", AsyncMock()),
        ):
            result = _run(trader.place_test_order())

        assert result["success"] is True
        assert result["orderId"] == 77
        assert result["status"] == "PreSubmitted"

    def test_place_test_order_failure_on_exception(self):
        """place_test_order() returns error dict when an exception is raised."""
        ib = _make_mock_ib(connected=True)
        ib.qualifyContractsAsync = AsyncMock(side_effect=RuntimeError("TWS not connected"))

        trader = _make_trader(ib)
        with (
            patch.object(trader, "is_alive", return_value=True),
            patch("brokers.ibkr_trading.Stock", MagicMock()),
        ):
            result = _run(trader.place_test_order())

        assert result["success"] is False
        assert "TWS not connected" in result.get("error", "")


# ── 10. get_ib_connection() factory ───────────────────────────────────────

class TestGetIbConnection:
    def test_returns_none_when_ibsync_missing(self):
        """get_ib_connection() returns (None, error_msg) when ib_insync is unavailable."""
        from brokers.ibkr_trading import get_ib_connection
        # Patch _try_load_ibsync to report failure and set the import error message
        with (
            patch("brokers.ibkr_trading._try_load_ibsync", return_value=False),
            patch("brokers.ibkr_trading._IBSYNC_IMPORT_ERROR", "ModuleNotFoundError: No module named 'ib_insync'"),
        ):
            result, msg = _run(get_ib_connection({"host": "127.0.0.1", "port": 7497, "client_id": 1}))
        assert result is None
        assert "IBKR disabled" in msg or "unavailable" in msg.lower() or "disabled" in msg.lower()

    def test_returns_trader_when_alive(self):
        """get_ib_connection() returns existing connected trader without reconnecting."""
        from brokers.ibkr_trading import _ib_instances

        mock_ib = _make_mock_ib(connected=True)
        fake_trader = _make_trader(mock_ib)
        fake_trader.connected = True

        key = "127.0.0.1:7497:1"
        _ib_instances[key] = fake_trader

        try:
            with (
                patch("brokers.ibkr_trading.HAS_IBSYNC", True),
                patch("brokers.ibkr_trading._try_load_ibsync", return_value=True),
                patch("core.connection_flags.is_auto_enabled", return_value=True),
            ):
                from brokers.ibkr_trading import get_ib_connection
                trader, msg = _run(
                    get_ib_connection({"host": "127.0.0.1", "port": 7497, "client_id": 1})
                )
            assert trader is fake_trader
            assert msg == "OK"
        finally:
            _ib_instances.pop(key, None)
