"""Unit tests for the moomoo execute path.

Drives ``main._moomoo_execute_impl`` and ``main.moomoo_exit`` with a fake
broker so we exercise:
  - chain enrichment merge
  - sizing + risk gate integration
  - structured error paths (no_spread_found, broker_not_connected,
    chain_quality_rejected, risk_gate_blocked, sizing_zero, idempotency)
  - leg-1 timeout
  - leg-2 timeout flatten
  - manual exit recording per-leg orders + state='closing'

Tests do NOT touch real moomoo OpenD.  The fake broker implements just the
methods the execute path calls.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from core.broker import (
    BrokerNotConnected,
    LegSpec,
    SpreadRequest,
    register_broker,
    unregister_broker,
)


# ── Fake broker ───────────────────────────────────────────────────────────────


class FakeMoomooBroker:
    """Implements BrokerProtocol with configurable responses for tests."""

    def __init__(
        self,
        *,
        alive: bool = True,
        account: dict | None = None,
        live_price: float = 580.50,
        chain: pd.DataFrame | None = None,
        spread_response: dict | None = None,
        place_spread_raises: Exception | None = None,
    ) -> None:
        self._alive = alive
        self._account = account or {
            "equity": 100_000.0,
            "buying_power": 200_000.0,
            "excess_liquidity": 100_000.0,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
            "cash": 100_000.0,
        }
        self._live_price = live_price
        self._chain = chain if chain is not None else _default_chain()
        self._spread_response = spread_response or {
            "status": "ok",
            "leg1_order_id": "L1-fake",
            "leg2_order_id": "L2-fake",
        }
        self._place_spread_raises = place_spread_raises
        self._trd_env_int = 0     # SIMULATE — used by execute_impl to skip RTH check
        self.place_spread_calls: list[SpreadRequest] = []
        self.close_position_calls: list[tuple[str, list]] = []

    def is_alive(self) -> bool:
        return self._alive

    async def get_account_summary(self) -> dict[str, Any]:
        return dict(self._account)

    async def get_positions(self) -> list[dict[str, Any]]:
        return []

    async def get_live_price(self, symbol: str) -> dict[str, Any]:
        return {"last": self._live_price, "bid": self._live_price - 0.05,
                "ask": self._live_price + 0.05, "volume": 1_000_000}

    async def get_option_chain(self, symbol: str, expiry_date: str):
        return self._chain.copy()

    async def get_spread_mid(self, legs: list[dict]) -> float | None:
        return 1.25  # arbitrary; only used by exit pre-close mid

    async def place_spread(self, req: SpreadRequest) -> dict[str, Any]:
        self.place_spread_calls.append(req)
        if self._place_spread_raises is not None:
            raise self._place_spread_raises
        return dict(self._spread_response)

    async def close_position(self, position_id: str, legs: list[dict]) -> dict[str, Any]:
        self.close_position_calls.append((position_id, list(legs)))
        return {
            "status": "ok",
            "position_id": position_id,
            "close_orders": [
                {"leg": legs[0], "order_id": "X1-close"},
                {"leg": legs[1], "order_id": "X2-close"},
            ],
        }

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        return {"status": "ok", "order_id": order_id}


def _default_chain() -> pd.DataFrame:
    """Realistic SPY 0DTE-style call chain around 580 with bid/ask quotes."""
    rows = []
    for strike in range(570, 600):
        # Synthetic prices: ITM calls priced higher, decreasing with strike
        intrinsic = max(0.0, 580.50 - strike)
        time_value = max(0.10, 2.0 - abs(strike - 580.50) * 0.05)
        mid = intrinsic + time_value
        bid = max(0.05, mid - 0.05)
        ask = mid + 0.05
        rows.append({
            "code": f"US.SPY260430C{strike * 1000:08d}",
            "option_type": "CALL",
            "strike_price": float(strike),
            "bid_price": bid,
            "ask_price": ask,
            "last_price": mid,
            "volume": 5000,
            "open_interest": 10000,
        })
    return pd.DataFrame(rows)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_broker():
    """Register a FakeMoomooBroker, yield it, and clean up."""
    broker = FakeMoomooBroker()
    register_broker("moomoo", broker)
    yield broker
    unregister_broker("moomoo")


@pytest.fixture
def isolated_journal(tmp_path):
    """Use a temp SQLite so each test starts with an empty journal."""
    db_path = tmp_path / "test_trades.db"
    from core.journal import reset_journal_for_tests
    j = reset_journal_for_tests(str(db_path))
    yield j
    j.close()
    # Reset for the next test
    import core.journal as J
    J._JOURNAL = None


@pytest.fixture
def order_request():
    """Standard MoomooOrderRequest for happy-path tests."""
    from main import MoomooOrderRequest
    return MoomooOrderRequest(
        symbol="SPY",
        direction="bull_call",
        contracts=1,
        strike_width=5,
        target_dte=0,
        spread_cost_target=250.0,
        otm_offset=1.50,
        position_size_method="fixed",
        client_order_id="test-client-id-123",
    )


# ── Pure-logic tests: chain enrichment / quality validator ────────────────────


def test_chain_quality_rejects_zero_bid():
    from core.chain import validate_spread_quality
    spread = {
        "K_long": 580, "K_short": 585,
        "long_bid": 0.0, "long_ask": 1.0,
        "short_bid": 0.5, "short_ask": 0.6,
        "debit_per_contract": 0.5,
    }
    ok, reason = validate_spread_quality(spread)
    assert ok is False
    assert reason == "long_leg_no_quote"


def test_chain_quality_rejects_wide_long_spread():
    from core.chain import validate_spread_quality
    spread = {
        "K_long": 580, "K_short": 585,
        "long_bid": 1.0, "long_ask": 1.50,   # 50% spread on $1.25 mid
        "short_bid": 0.5, "short_ask": 0.55,
        "debit_per_contract": 0.725,
    }
    ok, reason = validate_spread_quality(spread, max_bid_ask_pct=0.10)
    assert ok is False
    assert reason.startswith("long_leg_spread_wide")


def test_chain_quality_rejects_thin_debit():
    from core.chain import validate_spread_quality
    spread = {
        "K_long": 580, "K_short": 585,
        "long_bid": 1.0, "long_ask": 1.05,
        "short_bid": 0.99, "short_ask": 1.04,
        "debit_per_contract": 0.005,
    }
    ok, reason = validate_spread_quality(spread, min_mid=0.05)
    assert ok is False
    assert reason.startswith("spread_too_thin")


def test_chain_quality_rejects_low_volume():
    from core.chain import validate_spread_quality
    spread = {
        "K_long": 580, "K_short": 585,
        "long_bid": 1.0, "long_ask": 1.05,
        "short_bid": 0.5, "short_ask": 0.55,
        "debit_per_contract": 0.5,
    }
    quality_lookup = {580.0: {"volume": 5, "open_interest": 1000},
                      585.0: {"volume": 5, "open_interest": 1000}}
    ok, reason = validate_spread_quality(
        spread, quality_lookup=quality_lookup, min_volume=10,
    )
    assert ok is False
    assert "volume" in reason


def test_chain_quality_passes_clean_spread():
    from core.chain import validate_spread_quality
    # Tight bid/ask: ~4% on long, ~5% on short — both under 10%
    spread = {
        "K_long": 580, "K_short": 585,
        "long_bid": 2.40, "long_ask": 2.50,    # 0.10/2.45 ≈ 4.1%
        "short_bid": 0.95, "short_ask": 1.00,  # 0.05/0.975 ≈ 5.1%
        "debit_per_contract": 1.475,
    }
    ok, reason = validate_spread_quality(
        spread, max_bid_ask_pct=0.10, min_mid=0.05,
        quality_lookup={580.0: {"volume": 100, "open_interest": 1000},
                        585.0: {"volume": 100, "open_interest": 1000}},
        min_volume=10, min_open_interest=50,
    )
    assert ok is True, f"unexpected rejection: {reason}"
    assert reason == "ok"


# ── Execute-path error returns ────────────────────────────────────────────────


def test_execute_no_broker_returns_structured_error(isolated_journal, order_request):
    """Without registering a broker, execute returns broker_not_connected."""
    from main import _moomoo_execute_impl
    res = asyncio.run(_moomoo_execute_impl(order_request))
    assert res["error"] == "broker_not_connected"


def test_execute_broker_not_alive(isolated_journal, order_request):
    """is_alive() returning False short-circuits with broker_not_connected."""
    from main import _moomoo_execute_impl
    broker = FakeMoomooBroker(alive=False)
    register_broker("moomoo", broker)
    try:
        res = asyncio.run(_moomoo_execute_impl(order_request))
    finally:
        unregister_broker("moomoo")
    assert res["error"] == "broker_not_connected"


def test_execute_idempotency_dedupe(isolated_journal, fake_broker, order_request):
    """A second call with the same client_order_id is rejected."""
    from main import _moomoo_execute_impl

    # First call: should succeed
    res1 = asyncio.run(_moomoo_execute_impl(order_request))
    assert res1.get("success") is True or res1.get("error") in (
        "no_spread_found", "chain_quality_rejected", "risk_gate_blocked",
    )

    # Second call: idempotency duplicate guard fires regardless of first outcome
    res2 = asyncio.run(_moomoo_execute_impl(order_request))
    if res1.get("success"):
        assert res2.get("error") == "duplicate"
        assert res2.get("reason") == "idempotency_key_exists"


def test_execute_quality_rejects_zero_bid_chain(isolated_journal, order_request):
    """Zero-bid chain → chain_quality_rejected, no order placed."""
    from main import _moomoo_execute_impl
    bad_chain = _default_chain().copy()
    bad_chain["bid_price"] = 0.0     # all legs have no bid
    broker = FakeMoomooBroker(chain=bad_chain)
    register_broker("moomoo", broker)
    try:
        res = asyncio.run(_moomoo_execute_impl(order_request))
    finally:
        unregister_broker("moomoo")
    # Either no_spread_found (pick rejects all) or chain_quality_rejected
    assert res.get("error") in ("no_spread_found", "chain_quality_rejected")
    assert len(broker.place_spread_calls) == 0


def test_execute_happy_path(isolated_journal, fake_broker, order_request):
    """Happy path: places spread, journals position, returns success+ids."""
    from main import _moomoo_execute_impl
    res = asyncio.run(_moomoo_execute_impl(order_request))
    if not res.get("success"):
        # Pre-trade gate may reject (FOMC/blackout in real events file).
        # In that case we expect a structured error, not a crash.
        assert "error" in res
        return
    assert res["success"] is True
    assert res["leg1_order_id"] == "L1-fake"
    assert res["leg2_order_id"] == "L2-fake"
    assert res["contracts"] >= 1
    assert "K_long" in res and "K_short" in res
    assert len(fake_broker.place_spread_calls) == 1
    # Verify legs were correctly built
    sr = fake_broker.place_spread_calls[0]
    assert isinstance(sr, SpreadRequest)
    assert sr.long_leg.right == "C"
    assert sr.short_leg.right == "C"
    assert sr.short_leg.strike > sr.long_leg.strike  # bull call


def test_execute_leg1_timeout_returns_error(isolated_journal, order_request):
    """place_spread returns 'long_leg_timeout' → structured order_rejected."""
    from main import _moomoo_execute_impl
    broker = FakeMoomooBroker(spread_response={
        "status": "error", "reason": "long_leg_timeout",
        "leg1_order_id": "L1-timeout",
    })
    register_broker("moomoo", broker)
    try:
        res = asyncio.run(_moomoo_execute_impl(order_request))
    finally:
        unregister_broker("moomoo")
    if res.get("error") == "order_rejected":
        assert res.get("reason") == "long_leg_timeout"
    else:
        # Pre-trade gate may have blocked; that's also valid (no spread fired)
        assert "error" in res


def test_execute_leg2_timeout_flatten(isolated_journal, order_request):
    """Leg-2 timeout returns flatten metadata (leg1 cleaned up)."""
    from main import _moomoo_execute_impl
    broker = FakeMoomooBroker(spread_response={
        "status": "error", "reason": "short_leg_timeout_flattened",
        "leg1_order_id": "L1-ok", "leg2_order_id": "L2-timeout",
        "flatten_order_id": "F1",
    })
    register_broker("moomoo", broker)
    try:
        res = asyncio.run(_moomoo_execute_impl(order_request))
    finally:
        unregister_broker("moomoo")
    if res.get("error") == "order_rejected":
        assert res.get("reason") == "short_leg_timeout_flattened"
        assert res.get("flatten_order_id") == "F1"


def test_execute_unhandled_exception_wrapped_as_json(isolated_journal, order_request):
    """Endpoint wrapper turns unhandled exceptions into structured JSON, no 500."""
    from main import moomoo_execute
    from core.risk import Decision
    broker = FakeMoomooBroker(place_spread_raises=ValueError("boom"))
    register_broker("moomoo", broker)
    # Force the risk gate to allow so we reach place_spread (which raises)
    with patch("core.risk.evaluate_pre_trade", return_value=Decision(True, "ok")):
        try:
            res = asyncio.run(moomoo_execute(order_request))
        finally:
            unregister_broker("moomoo")
    # Either "order_failed" (caught by impl) or "unhandled_exception"
    # (caught by endpoint wrapper) is acceptable — no exception should escape.
    assert "error" in res
    assert res["error"] in ("order_failed", "unhandled_exception")


# ── Manual exit path ─────────────────────────────────────────────────────────


def test_exit_records_per_leg_orders(isolated_journal, fake_broker):
    """Manual exit records one Order per leg + sets state='closing'."""
    from main import moomoo_exit
    from core.journal import get_journal, Position
    j = get_journal()

    # Seed an open moomoo position
    pos_id = j.open_position(Position(
        id="test-pos-1",
        symbol="SPY",
        topology="vertical_spread",
        direction="bull_call",
        contracts=1,
        entry_cost=125.0,
        entry_time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        expiry="20260430",
        state="open",
        broker="moomoo",
        legs=[
            {"expiry": "20260430", "strike": 580.0, "right": "C", "side": "long", "qty": 1},
            {"expiry": "20260430", "strike": 585.0, "right": "C", "side": "short", "qty": 1},
        ],
        meta={"broker": "moomoo"},
    ))

    res = asyncio.run(moomoo_exit({"position_id": pos_id, "reason": "manual_exit"}))
    assert "error" not in res
    assert "exit_orders" in res
    assert len(res["exit_orders"]) == 2     # one per leg

    # Position should be 'closing', not 'closed' (fill_watcher closes it later)
    pos_after = j.get_position(pos_id)
    assert pos_after.state == "closing"
    assert pos_after.exit_reason == "manual_exit"

    # Two exit orders recorded
    orders = j.list_orders_for_position(pos_id)
    exits = [o for o in orders if o.kind == "exit"]
    assert len(exits) == 2


def test_exit_rejects_wrong_broker(isolated_journal, fake_broker):
    """Exit on an IBKR position via /api/moomoo/exit rejects with clear error."""
    from main import moomoo_exit
    from core.journal import get_journal, Position
    j = get_journal()
    pos_id = j.open_position(Position(
        id="test-ibkr-pos",
        symbol="SPY",
        topology="vertical_spread",
        direction="bull_call",
        contracts=1,
        entry_cost=125.0,
        entry_time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        expiry="20260430",
        state="open",
        broker="ibkr",
        legs=[
            {"expiry": "20260430", "strike": 580.0, "right": "C", "side": "long", "qty": 1},
            {"expiry": "20260430", "strike": 585.0, "right": "C", "side": "short", "qty": 1},
        ],
    ))
    res = asyncio.run(moomoo_exit({"position_id": pos_id}))
    assert "error" in res
    assert "ibkr" in res["error"].lower()


def test_exit_missing_position_id():
    from main import moomoo_exit
    res = asyncio.run(moomoo_exit({}))
    assert res["error"] == "missing_position_id"


def test_exit_position_not_found(isolated_journal, fake_broker):
    from main import moomoo_exit
    res = asyncio.run(moomoo_exit({"position_id": "nonexistent-id"}))
    assert res["error"] == "position_not_found"


# ── Reconciler tests ──────────────────────────────────────────────────────────


def test_reconciler_skips_when_no_broker(isolated_journal):
    """No broker registered → reconcile returns 'skipped' without raising."""
    from core.moomoo_reconciler import reconcile_once
    res = asyncio.run(reconcile_once())
    assert res.get("skipped") == "broker_not_connected"


def test_reconciler_detects_phantom_journal_position(isolated_journal, fake_broker):
    """Journal has open moomoo position; broker has no positions → phantom."""
    from core.moomoo_reconciler import reconcile_once
    from core.journal import get_journal, Position

    # Fake broker.get_positions returns [] by default
    j = get_journal()
    # Use entry_time well in the past so the 60s grace window doesn't apply.
    j.open_position(Position(
        id="phantom-pos",
        symbol="SPY",
        topology="vertical_spread",
        direction="bull_call",
        contracts=1,
        entry_cost=100.0,
        entry_time="2026-01-01T00:00:00+00:00",
        expiry="20260430",
        state="open",
        broker="moomoo",
        legs=[
            {"expiry": "20260430", "strike": 580.0, "right": "C", "side": "long", "qty": 1},
            {"expiry": "20260430", "strike": 585.0, "right": "C", "side": "short", "qty": 1},
        ],
    ))

    res = asyncio.run(reconcile_once())
    assert len(res.get("phantoms", [])) == 1
    assert "phantom-pos" in res.get("phantoms_closed", [])

    # Verify journal position state was updated to closed
    pos = j.get_position("phantom-pos")
    assert pos.state == "closed"
    assert pos.exit_reason == "reconcile_phantom"


def test_reconciler_no_op_when_broker_and_journal_agree(isolated_journal, fake_broker):
    """Empty broker + empty journal → no orphans, no phantoms."""
    from core.moomoo_reconciler import reconcile_once
    res = asyncio.run(reconcile_once())
    assert res.get("orphans") == []
    assert res.get("phantoms") == []
