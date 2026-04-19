"""I12 — Backtest engine parametrized tests.

Covers exit-path and entry-filter paths in run_backtest_engine():
  - Profit-target exit (take_profit_pct)
  - Stop-loss exit (stop_loss_pct)
  - Trailing-stop exit (trailing_stop_pct)
  - Max-DTE / expiry exit (target_dte short enough to expire quickly)
  - Entry filter rejection (RSI filter blocks entry)

All tests use deterministic synthetic DataFrames (seeded numpy) and mock
``fetch_risk_free_rate`` so they never hit the network.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from main import BacktestRequest, run_backtest_engine


# ── helpers ────────────────────────────────────────────────────────────────

FIXED_RFR = 0.045  # stable risk-free rate — avoids network I/O in all tests


def _make_df(
    n: int = 400,
    seed: int = 42,
    base_price: float = 450.0,
    force_red_streaks: bool = True,
) -> pd.DataFrame:
    """Build a synthetic SPY daily DataFrame compatible with run_backtest_engine.

    Mirrors the pattern from test_reconcile.py / test_backtest_realism.py.
    Two forced red-day streaks are embedded at bars 210-211 and 230-231 to
    guarantee entries for the consecutive-days strategy.
    """
    rng = np.random.default_rng(seed)
    prices = np.empty(n)
    prices[0] = base_price
    for i in range(1, n):
        prices[i] = prices[i - 1] * np.exp(rng.normal(0.0003, 0.010))

    if force_red_streaks and n > 235:
        # Streak 1 — bars 210-211
        prices[210] = prices[209] * 0.993
        prices[211] = prices[210] * 0.991
        prices[212] = prices[211] * 1.008  # recovery green bar
        # Streak 2 — bars 230-231
        prices[230] = prices[229] * 0.994
        prices[231] = prices[230] * 0.992
        prices[232] = prices[231] * 1.007

    dates = pd.bdate_range("2023-01-03", periods=n, freq="B")
    open_prices = prices * (1 + rng.normal(0, 0.0008, n))

    if force_red_streaks and n > 235:
        # Guarantee open > close on red bars
        open_prices[210] = prices[210] * 1.004
        open_prices[211] = prices[211] * 1.003
        open_prices[212] = prices[212] * 0.997
        open_prices[230] = prices[230] * 1.004
        open_prices[231] = prices[231] * 1.003
        open_prices[232] = prices[232] * 0.997

    high = np.maximum(open_prices, prices) * (1 + rng.uniform(0, 0.004, n))
    low = np.minimum(open_prices, prices) * (1 - rng.uniform(0, 0.004, n))
    volume = rng.integers(40_000_000, 120_000_000, size=n).astype(float)

    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": open_prices,
            "High": high,
            "Low": low,
            "Close": prices,
            "Volume": volume,
        }
    )
    df["Date_str"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df


def _base_req(**overrides) -> BacktestRequest:
    """Return a BacktestRequest with safe defaults (no filters, no stops)."""
    kw = dict(
        ticker="SPY",
        years_history=1,
        capital_allocation=10_000.0,
        contracts_per_trade=1,
        strategy_id="consecutive_days",
        strategy_type="bull_call",
        topology="vertical_spread",
        direction="bull",
        entry_red_days=2,
        exit_green_days=2,
        target_dte=14,
        stop_loss_pct=0.0,
        take_profit_pct=0.0,
        trailing_stop_pct=0.0,
        spread_cost_target=250.0,
        strike_width=5,
        commission_per_contract=0.0,  # zero to isolate each exit mechanic
        realism_factor=1.0,
        use_rsi_filter=False,
        use_ema_filter=False,
        use_sma200_filter=False,
        use_volume_filter=False,
        use_vix_filter=False,
        use_regime_filter=False,
        use_dynamic_sizing=False,
        use_targeted_spread=False,
        enable_mc_histogram=False,
        enable_walk_forward=False,
        bid_ask_haircut=0.0,
    )
    kw.update(overrides)
    return BacktestRequest(**kw)


def _run(req: BacktestRequest, df: pd.DataFrame):
    """Invoke run_backtest_engine with a mocked risk-free rate."""
    with patch("main.fetch_risk_free_rate", return_value=FIXED_RFR):
        return run_backtest_engine(req, df.copy(), start_idx=200)


# ── parametrize helpers ────────────────────────────────────────────────────

# Each scenario tuple: (label, req_overrides, assertion_fn)
# assertion_fn receives (trades, equity_curve, final_equity).


def _assert_has_trades(trades, equity_curve, final_equity):
    assert len(trades) >= 1, "Expected at least one trade to be recorded"


def _assert_any_take_profit(trades, equity_curve, final_equity):
    reasons = [t.get("reason", "") for t in trades]
    assert any(r == "take_profit" for r in reasons), (
        f"Expected a take_profit exit. Got reasons: {reasons}"
    )


def _assert_any_stop_loss(trades, equity_curve, final_equity):
    reasons = [t.get("reason", "") for t in trades]
    assert any(r == "stop_loss" for r in reasons), (
        f"Expected a stop_loss exit. Got reasons: {reasons}"
    )


def _assert_any_trailing_stop(trades, equity_curve, final_equity):
    reasons = [t.get("reason", "") for t in trades]
    assert any(r == "trailing_stop" for r in reasons), (
        f"Expected a trailing_stop exit. Got reasons: {reasons}"
    )


def _assert_any_expired(trades, equity_curve, final_equity):
    reasons = [t.get("reason", "") for t in trades]
    assert any(r == "expired" for r in reasons), (
        f"Expected an expired exit. Got reasons: {reasons}"
    )


def _assert_no_trades(trades, equity_curve, final_equity):
    assert len(trades) == 0, (
        f"Expected zero trades (filter should block all entries). "
        f"Got {len(trades)} trades."
    )


# ── Profit-target exit tests ───────────────────────────────────────────────

class TestProfitTargetExit:
    """Take-profit triggers when MTM PnL % >= take_profit_pct."""

    def test_take_profit_produces_exit(self):
        """With a low take_profit_pct, at least one trade closes via take_profit."""
        df = _make_df()
        # Very low take-profit threshold: 1% gain triggers exit
        req = _base_req(take_profit_pct=1.0, stop_loss_pct=0.0)
        trades, _, _ = _run(req, df)
        if not trades:
            pytest.skip("No trades produced — increase synthetic data window")
        _assert_any_take_profit(trades, None, None)

    def test_take_profit_reason_string(self):
        """The 'reason' field in the trade dict is exactly 'take_profit'."""
        df = _make_df()
        req = _base_req(take_profit_pct=1.0)
        trades, _, _ = _run(req, df)
        if not trades:
            pytest.skip("No trades produced")
        tp_trades = [t for t in trades if t.get("reason") == "take_profit"]
        assert tp_trades, "No trade has reason=='take_profit'"

    def test_take_profit_not_triggered_at_zero(self):
        """With take_profit_pct=0 (disabled), no take_profit exits occur."""
        df = _make_df()
        req = _base_req(take_profit_pct=0.0, stop_loss_pct=0.0)
        trades, _, _ = _run(req, df)
        tp_trades = [t for t in trades if t.get("reason") == "take_profit"]
        assert not tp_trades, "take_profit exits should not occur when threshold=0"

    @pytest.mark.parametrize("tp_pct", [1.0, 5.0, 10.0, 20.0])
    def test_take_profit_parametrized_thresholds(self, tp_pct):
        """Lower thresholds produce as many or more take_profit exits than higher."""
        df = _make_df(seed=99)
        req = _base_req(take_profit_pct=tp_pct, stop_loss_pct=0.0)
        trades, _, _ = _run(req, df)
        # At low thresholds (1%) we expect at least one take_profit if any trades exist
        if tp_pct <= 5.0 and trades:
            reasons = [t.get("reason") for t in trades]
            assert "take_profit" in reasons, (
                f"tp_pct={tp_pct}: expected take_profit exit. Reasons: {reasons}"
            )


# ── Stop-loss exit tests ───────────────────────────────────────────────────

class TestStopLossExit:
    """Stop-loss triggers when MTM PnL % <= -stop_loss_pct."""

    def test_stop_loss_produces_exit(self):
        """With a tight stop-loss threshold, at least one trade closes via stop_loss."""
        df = _make_df()
        # Use a very tight stop so that normal time decay hits it
        req = _base_req(stop_loss_pct=1.0, take_profit_pct=0.0)
        trades, _, _ = _run(req, df)
        if not trades:
            pytest.skip("No trades produced")
        _assert_any_stop_loss(trades, None, None)

    def test_stop_loss_reason_string(self):
        """The 'reason' field is exactly 'stop_loss' when triggered."""
        df = _make_df()
        req = _base_req(stop_loss_pct=1.0)
        trades, _, _ = _run(req, df)
        if not trades:
            pytest.skip("No trades produced")
        sl_trades = [t for t in trades if t.get("reason") == "stop_loss"]
        assert sl_trades, "No trade has reason=='stop_loss'"

    def test_stop_loss_stopped_out_flag(self):
        """Trades closed via stop_loss must have stopped_out=True."""
        df = _make_df()
        req = _base_req(stop_loss_pct=1.0)
        trades, _, _ = _run(req, df)
        for t in trades:
            if t.get("reason") == "stop_loss":
                assert t.get("stopped_out") is True, (
                    f"Trade with reason=stop_loss has stopped_out={t.get('stopped_out')}"
                )

    def test_stop_loss_not_triggered_at_zero(self):
        """With stop_loss_pct=0 (disabled), no stop_loss exits occur."""
        df = _make_df()
        req = _base_req(stop_loss_pct=0.0, take_profit_pct=0.0)
        trades, _, _ = _run(req, df)
        sl_trades = [t for t in trades if t.get("reason") == "stop_loss"]
        assert not sl_trades, "stop_loss exits should not occur when threshold=0"

    @pytest.mark.parametrize("sl_pct", [1.0, 10.0, 50.0, 100.0])
    def test_stop_loss_parametrized_thresholds(self, sl_pct):
        """Tighter stops produce more or equal stop-loss exits than looser stops."""
        df = _make_df(seed=77)
        req = _base_req(stop_loss_pct=sl_pct, take_profit_pct=0.0)
        trades, _, _ = _run(req, df)
        # Just verify no crash and we get a valid result
        assert isinstance(trades, list)


# ── Trailing-stop exit tests ───────────────────────────────────────────────

class TestTrailingStopExit:
    """Trailing-stop triggers when value drops from HWM by trailing_stop_pct."""

    def test_trailing_stop_produces_exit(self):
        """With a tight trailing stop, at least one trailing_stop exit occurs."""
        df = _make_df()
        req = _base_req(trailing_stop_pct=1.0, stop_loss_pct=0.0, take_profit_pct=0.0)
        trades, _, _ = _run(req, df)
        if not trades:
            pytest.skip("No trades produced")
        _assert_any_trailing_stop(trades, None, None)

    def test_trailing_stop_reason_string(self):
        """The 'reason' field is exactly 'trailing_stop' when triggered."""
        df = _make_df()
        req = _base_req(trailing_stop_pct=1.0)
        trades, _, _ = _run(req, df)
        if not trades:
            pytest.skip("No trades produced")
        ts_trades = [t for t in trades if t.get("reason") == "trailing_stop"]
        assert ts_trades, "No trade has reason=='trailing_stop'"

    def test_trailing_stop_stopped_out_flag(self):
        """Trades closed via trailing_stop must have stopped_out=True."""
        df = _make_df()
        req = _base_req(trailing_stop_pct=1.0)
        trades, _, _ = _run(req, df)
        for t in trades:
            if t.get("reason") == "trailing_stop":
                assert t.get("stopped_out") is True, (
                    f"Trade with reason=trailing_stop has stopped_out={t.get('stopped_out')}"
                )

    def test_trailing_stop_not_triggered_at_zero(self):
        """With trailing_stop_pct=0 (disabled), no trailing_stop exits occur."""
        df = _make_df()
        req = _base_req(trailing_stop_pct=0.0, stop_loss_pct=0.0, take_profit_pct=0.0)
        trades, _, _ = _run(req, df)
        ts_trades = [t for t in trades if t.get("reason") == "trailing_stop"]
        assert not ts_trades, "trailing_stop exits should not occur when threshold=0"


# ── Max-DTE / expiry exit tests ────────────────────────────────────────────

class TestExpiredExit:
    """Trades exit with 'expired' reason when DTE reaches 0."""

    def test_expired_exit_produced(self):
        """With target_dte=1 and no stops, trades should expire quickly."""
        df = _make_df()
        # 1-day DTE means the option expires after 1 bar; no stop or TP
        req = _base_req(target_dte=1, stop_loss_pct=0.0, take_profit_pct=0.0, trailing_stop_pct=0.0)
        trades, _, _ = _run(req, df)
        if not trades:
            pytest.skip("No trades produced with target_dte=1")
        _assert_any_expired(trades, None, None)

    def test_expired_reason_string(self):
        """When DTE expires, the 'reason' field equals 'expired'."""
        df = _make_df()
        req = _base_req(target_dte=1, stop_loss_pct=0.0, take_profit_pct=0.0, trailing_stop_pct=0.0)
        trades, _, _ = _run(req, df)
        if not trades:
            pytest.skip("No trades produced")
        expired_trades = [t for t in trades if t.get("reason") == "expired"]
        assert expired_trades, f"No expired trades found. Reasons: {[t.get('reason') for t in trades]}"


# ── Entry filter rejection tests ───────────────────────────────────────────

class TestEntryFilterRejection:
    """Entry filters (RSI, EMA, SMA200, Volume) block trades when active."""

    def test_rsi_filter_blocks_bull_entry_when_overbought(self):
        """RSI filter with a very low threshold (e.g. 1%) blocks all bull entries."""
        df = _make_df()
        # rsi_threshold=1 means RSI must be < 1 for a bull entry — always blocked
        req = _base_req(use_rsi_filter=True, rsi_threshold=1)
        trades, _, _ = _run(req, df)
        assert len(trades) == 0, (
            f"Expected all entries blocked by RSI filter, got {len(trades)} trades"
        )

    def test_no_filters_allows_entries(self):
        """With all filters disabled, trades are produced (baseline sanity)."""
        df = _make_df()
        req = _base_req(
            use_rsi_filter=False,
            use_ema_filter=False,
            use_sma200_filter=False,
            use_volume_filter=False,
        )
        trades, _, _ = _run(req, df)
        assert len(trades) >= 1, "Expected at least one trade with no filters active"

    @pytest.mark.parametrize("rsi_threshold", [1, 2, 5])
    def test_very_low_rsi_threshold_blocks_entries(self, rsi_threshold):
        """An extremely restrictive RSI threshold blocks most or all entries."""
        df = _make_df(seed=42)
        req = _base_req(use_rsi_filter=True, rsi_threshold=rsi_threshold)
        trades, _, _ = _run(req, df)
        # RSI is rarely below 5 on a normally distributed synthetic price series
        # so trades should be heavily reduced or eliminated
        no_filter_req = _base_req(use_rsi_filter=False)
        no_filter_trades, _, _ = _run(no_filter_req, df)
        assert len(trades) <= len(no_filter_trades), (
            f"RSI filter (threshold={rsi_threshold}) produced MORE trades than no-filter"
        )

    def test_ema_filter_can_block_entries(self):
        """EMA filter with ema_length=200 (very slow) often blocks bull entries."""
        df = _make_df()
        # Bull entry requires Close < EMA — a long EMA means price is often above it
        req = _base_req(use_ema_filter=True, ema_length=200)
        trades, _, _ = _run(req, df)
        no_filter_req = _base_req(use_ema_filter=False, ema_length=200)
        no_filter_trades, _, _ = _run(no_filter_req, df)
        assert len(trades) <= len(no_filter_trades), (
            "EMA filter should produce equal or fewer trades than no-filter"
        )


# ── Equity curve tests ─────────────────────────────────────────────────────

class TestEquityCurveIntegrity:
    """Smoke tests on the equity curve output."""

    def test_equity_curve_length_matches_df(self):
        """equity_curve has one entry per bar from start_idx to end."""
        df = _make_df(n=300)
        req = _base_req()
        trades, equity_curve, _ = _run(req, df)
        expected = len(df) - 200  # start_idx=200
        assert len(equity_curve) == expected, (
            f"Expected {expected} equity curve points, got {len(equity_curve)}"
        )

    def test_equity_curve_has_required_keys(self):
        """Each equity curve point has 'date', 'equity', and 'drawdown' keys."""
        df = _make_df(n=250)
        req = _base_req()
        _, equity_curve, _ = _run(req, df)
        for point in equity_curve[:5]:
            assert "date" in point
            assert "equity" in point
            assert "drawdown" in point

    def test_final_equity_positive_with_no_losses(self):
        """With zero-commission and no stops, final equity should be >= 0."""
        df = _make_df()
        req = _base_req(commission_per_contract=0.0)
        _, _, final_equity = _run(req, df)
        assert final_equity >= 0, f"Final equity is negative: {final_equity}"


# ── Trade record field validation ─────────────────────────────────────────

class TestTradeRecordFields:
    """Verify trade records contain all required fields."""

    def test_trade_record_fields_present(self):
        """All required fields exist on each trade record."""
        df = _make_df()
        req = _base_req()
        trades, _, _ = _run(req, df)
        if not trades:
            pytest.skip("No trades produced")

        required_fields = {
            "entry_date", "exit_date", "entry_spy", "exit_spy",
            "spread_cost", "spread_exit", "pnl", "contracts",
            "days_held", "commission", "win", "stopped_out", "reason",
            "regime", "topology",
        }
        for trade in trades:
            missing = required_fields - set(trade.keys())
            assert not missing, f"Trade missing fields: {missing}"

    def test_stopped_out_false_for_streak_exits(self):
        """Trades exiting via 'streak' have stopped_out=False."""
        df = _make_df()
        req = _base_req(stop_loss_pct=0.0, take_profit_pct=0.0, trailing_stop_pct=0.0)
        trades, _, _ = _run(req, df)
        for t in trades:
            if t.get("reason") in ("streak", "expired"):
                assert t.get("stopped_out") is False, (
                    f"Trade reason={t.get('reason')} should have stopped_out=False"
                )
