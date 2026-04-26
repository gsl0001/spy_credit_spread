"""Regression tests for the manual-exit and flatten-all order paths.

Three bugs being guarded against:
  1. ``ibkr_exit`` used to fall back to ``mid = 0.01`` when no combo quote
     was available, then submitted with ``haircut_pct=0.0`` — producing
     an unfillable $0.01 limit order that sat until timeout cancellation.
     The endpoint returned ``success: True`` regardless.
  2. ``ibkr_flatten_all`` (the panic kill-switch) used the same
     ``haircut_pct=0.0`` as the algorithmic monitor — which is
     conservative pricing. A panic button should be the *most* aggressive
     thing in the system, not the least.
  3. The market-order fallback path for flatten_all (used when no quote is
     available) needs to journal the exit the same way limit-order exits
     do, or the fill watcher won't reconcile it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Constants: panic must be more aggressive than manual exit ───────────


def test_panic_haircut_more_aggressive_than_manual():
    """If a user clicks FLATTEN ALL, getting filled matters more than
    slippage. Manual exit can be patient; panic cannot."""
    import main
    assert main._EXIT_HAIRCUT_PANIC > main._EXIT_HAIRCUT_MANUAL, (
        "Panic flatten-all must use a wider haircut than a regular manual "
        "close — the user clicked panic for a reason."
    )
    # Both must be non-negative; haircut is added to or subtracted from mid.
    assert main._EXIT_HAIRCUT_MANUAL >= 0
    assert main._EXIT_HAIRCUT_PANIC > 0


# ── ibkr_exit: no-quote returns honest error, not fake success ─────────


@pytest.mark.asyncio
async def test_ibkr_exit_returns_no_quote_error_when_mid_unavailable(tmp_path):
    """The endpoint must NOT submit an unfillable $0.01 order when
    ``get_combo_midpoint`` fails. It must return an honest error so the
    UI doesn't show "✓ Submitted" when nothing real happened."""
    import main
    from core.journal import Journal, Position

    # Set up a journal with one open position so the endpoint has something to act on.
    db = tmp_path / "exit.db"
    j = Journal(str(db))
    j.open_position(Position(
        id="p-no-quote",
        symbol="SPY",
        topology="vertical_spread",
        direction="bull_call",
        contracts=1,
        entry_cost=250.0,
        entry_time="2026-04-25T13:00:00+00:00",
        expiry="2026-05-09",
        legs=({"type": "call", "strike": 500, "side": "long",  "expiry": "20260509"},
              {"type": "call", "strike": 505, "side": "short", "expiry": "20260509"}),
        state="open",
        broker="ibkr",
        meta={"combo_type": "debit"},
    ))

    # Mock get_ib_connection → return a trader whose midpoint fetch returns None.
    fake_trader = MagicMock()
    fake_trader.get_combo_midpoint = AsyncMock(return_value=None)
    fake_trader.place_combo_order = AsyncMock()

    with patch("main.get_ib_connection", AsyncMock(return_value=(fake_trader, "OK"))), \
         patch("core.journal.get_journal", return_value=j):
        result = await main.ibkr_exit({
            "creds": {"host": "127.0.0.1", "port": 7497, "client_id": 1},
            "position_id": "p-no-quote",
        })

    # Critical invariants:
    # 1. Endpoint returns an explicit error code the UI can branch on.
    assert result.get("error") == "no_quote"
    # 2. NO order was sent to the broker (vs. before: a $0.01 unfillable LMT).
    fake_trader.place_combo_order.assert_not_called()
    # 3. Position still in 'open' (not flipped to 'closing' on a phantom exit).
    pos = j.get_position("p-no-quote")
    assert pos is not None and pos.state == "open"
    j.close()


# ── flatten_all: market-order fallback when mid is unavailable ─────────


@pytest.mark.asyncio
async def test_flatten_all_falls_back_to_market_order_when_no_quote(tmp_path):
    """Panic should still close positions even with no quote — fall back
    to a market order that crosses any spread."""
    import main
    from core.journal import Journal, Position

    db = tmp_path / "panic.db"
    j = Journal(str(db))
    j.open_position(Position(
        id="p-panic",
        symbol="SPY",
        topology="vertical_spread",
        direction="bull_call",
        contracts=2,
        entry_cost=500.0,
        entry_time="2026-04-25T13:00:00+00:00",
        expiry="2026-05-09",
        legs=({"type": "call", "strike": 500, "side": "long",  "expiry": "20260509"},
              {"type": "call", "strike": 505, "side": "short", "expiry": "20260509"}),
        state="open",
        broker="ibkr",
        meta={"combo_type": "debit"},
    ))

    fake_trader = MagicMock()
    fake_trader.get_combo_midpoint = AsyncMock(return_value=None)  # no quote
    fake_trader.place_combo_order = AsyncMock(return_value={"orderId": 42, "success": True})

    with patch("main.get_ib_connection", AsyncMock(return_value=(fake_trader, "OK"))), \
         patch("core.journal.get_journal", return_value=j):
        result = await main.ibkr_flatten_all(
            main.IBKRConnectRequest(host="127.0.0.1", port=7497, client_id=1),
        )

    # 1. Order was submitted with lmtPrice=None — i.e., as a market order.
    assert fake_trader.place_combo_order.called
    call_kwargs = fake_trader.place_combo_order.call_args.kwargs
    assert call_kwargs.get("lmtPrice") is None, (
        "When no quote is available, panic should fall back to MARKET, "
        "not a fabricated $0.01 limit."
    )
    # 2. Side is SELL (debit close).
    assert call_kwargs.get("side") == "SELL"
    # 3. Position is now closing, not open.
    pos = j.get_position("p-panic")
    assert pos is not None and pos.state == "closing"
    # 4. Endpoint reported the close.
    assert result.get("closed") == 1
    j.close()


@pytest.mark.asyncio
async def test_flatten_all_uses_aggressive_haircut_when_quote_available(tmp_path):
    """When a quote IS available, panic uses the wide haircut to ensure
    the limit order crosses the spread."""
    import main
    from core.journal import Journal, Position

    db = tmp_path / "panic2.db"
    j = Journal(str(db))
    j.open_position(Position(
        id="p-haircut",
        symbol="SPY",
        topology="vertical_spread",
        direction="bull_call",
        contracts=1,
        entry_cost=250.0,
        entry_time="2026-04-25T13:00:00+00:00",
        expiry="2026-05-09",
        legs=({"type": "call", "strike": 500, "side": "long",  "expiry": "20260509"},
              {"type": "call", "strike": 505, "side": "short", "expiry": "20260509"}),
        state="open",
        broker="ibkr",
        meta={"combo_type": "debit"},
    ))

    mid = 2.50
    fake_trader = MagicMock()
    fake_trader.get_combo_midpoint = AsyncMock(return_value=mid)
    fake_trader.place_combo_order = AsyncMock(return_value={"orderId": 99, "success": True})

    with patch("main.get_ib_connection", AsyncMock(return_value=(fake_trader, "OK"))), \
         patch("core.journal.get_journal", return_value=j):
        await main.ibkr_flatten_all(
            main.IBKRConnectRequest(host="127.0.0.1", port=7497, client_id=1),
        )

    call_kwargs = fake_trader.place_combo_order.call_args.kwargs
    submitted_limit = call_kwargs.get("lmtPrice")
    # SELL at mid - haircut*mid; with PANIC haircut (0.15), limit ≈ 2.125.
    # Manual haircut (0.05) would give ≈ 2.375. Verify the gap is closer
    # to the panic value to prove the wider haircut is being used.
    assert submitted_limit is not None
    expected_panic_limit = round(mid - main._EXIT_HAIRCUT_PANIC * mid, 2)
    expected_manual_limit = round(mid - main._EXIT_HAIRCUT_MANUAL * mid, 2)
    assert abs(submitted_limit - expected_panic_limit) < abs(submitted_limit - expected_manual_limit), (
        f"Flatten-all submitted limit {submitted_limit} should be closer to "
        f"panic-haircut price {expected_panic_limit} than manual-haircut "
        f"price {expected_manual_limit}."
    )
    j.close()
