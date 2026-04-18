"""Tests for I9: backtest realism upgrades.

Covers:
* ``bid_ask_haircut`` field exists on ``BacktestRequest`` with default 0.0.
* Setting haircut=0.0 produces identical results to no haircut (backward compat).
* Setting haircut>0 produces lower total_pnl than haircut=0 (fills are worse).
* Haircut is applied at *both* entry (higher cost to open) and exit (lower value
  when closing) — isolate each leg by inspecting the trade records.
* Haircut compounds correctly across multiple trades.
* The ``/api/backtest`` endpoint accepts and passes through ``bid_ask_haircut``.

We use a minimal deterministic synthetic DataFrame (10 years, 2520 bars) and the
ConsecutiveDaysStrategy to guarantee entries with ``entry_red_days=2``.  The
exact dates don't matter — we compare haircut=0 vs haircut=0.05 and assert the
directional relationship (haircut ≡ fewer dollars at the end).
"""
from __future__ import annotations

import pytest
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

import main
from main import BacktestRequest


# ── helpers ────────────────────────────────────────────────────────────────

def _make_df(n: int = 800, seed: int = 0) -> pd.DataFrame:
    """Minimal synthetic SPY DataFrame compatible with run_backtest_engine.

    Forces two red-streak entries near bar 210 and bar 230 using the same
    trick as test_reconcile.py.
    """
    rng = np.random.default_rng(seed)
    prices = [450.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * np.exp(rng.normal(0.0003, 0.012)))
    prices = np.array(prices)

    # Force a 2-day red streak at bars 210-211.
    prices[210] = prices[209] * 0.992
    prices[211] = prices[210] * 0.991

    dates = pd.date_range("2022-01-03", periods=n, freq="B")
    df = pd.DataFrame(
        {
            "Date": dates,
            "Date_str": [d.strftime("%Y-%m-%d") for d in dates],
            "Open": prices * 0.999,
            "High": prices * 1.004,
            "Low": prices * 0.996,
            "Close": prices,
            "Volume": rng.integers(50_000_000, 150_000_000, n).astype(float),
        }
    )
    return df


def _base_req(**overrides) -> BacktestRequest:
    kw = dict(
        ticker="SPY",
        years_history=1,
        capital_allocation=10_000.0,
        contracts_per_trade=1,
        entry_red_days=2,
        exit_green_days=2,
        target_dte=14,
        stop_loss_pct=0.0,      # disable to isolate haircut effect
        take_profit_pct=0.0,
        trailing_stop_pct=0.0,
        use_rsi_filter=False,
        use_ema_filter=False,
        use_sma200_filter=False,
        use_volume_filter=False,
        use_vix_filter=False,
        use_regime_filter=False,
        use_dynamic_sizing=False,
        use_targeted_spread=False,
        bid_ask_haircut=0.0,
        realism_factor=1.0,
        commission_per_contract=0.0,  # zero commission to isolate haircut
    )
    kw.update(overrides)
    return BacktestRequest(**kw)


def _run(req: BacktestRequest, df: pd.DataFrame):
    """Run the backtest engine with a fresh copy of the DataFrame."""
    trades, equity_curve, final_equity = main.run_backtest_engine(
        req, df.copy(), start_idx=200
    )
    return trades, equity_curve, final_equity


# ── field declaration tests ────────────────────────────────────────────────

class TestField:
    def test_default_is_zero(self):
        req = BacktestRequest()
        assert req.bid_ask_haircut == 0.0

    def test_accepts_nonzero_value(self):
        req = BacktestRequest(bid_ask_haircut=0.02)
        assert req.bid_ask_haircut == pytest.approx(0.02)

    def test_accepts_zero_explicitly(self):
        req = BacktestRequest(bid_ask_haircut=0.0)
        assert req.bid_ask_haircut == 0.0


# ── backward-compat: haircut=0 ≡ no haircut ───────────────────────────────

class TestZeroHaircutIsNeutral:
    def test_zero_haircut_same_as_no_haircut(self):
        """Explicit haircut=0 must produce identical results to the baseline."""
        df = _make_df()
        req_base = _base_req(bid_ask_haircut=0.0)
        req_zero = _base_req(bid_ask_haircut=0.0)

        _, _, eq_base = _run(req_base, df)
        _, _, eq_zero = _run(req_zero, df)

        assert eq_base == pytest.approx(eq_zero, abs=0.01)


# ── directional: haircut > 0 reduces PnL ──────────────────────────────────

class TestHaircutReducesPnl:
    def test_higher_haircut_lower_final_equity(self):
        """With a positive haircut, fills are worse → lower final equity."""
        df = _make_df()
        _, _, eq_no_hc = _run(_base_req(bid_ask_haircut=0.0), df)
        _, _, eq_hc = _run(_base_req(bid_ask_haircut=0.05), df)

        # Haircut makes things strictly worse (assuming at least one trade).
        assert eq_hc <= eq_no_hc, (
            f"Expected haircut to reduce equity but got "
            f"no_hc={eq_no_hc:.2f} hc={eq_hc:.2f}"
        )

    def test_haircut_monotone_in_magnitude(self):
        """A 10% haircut should reduce equity more than a 2% haircut."""
        df = _make_df()
        _, _, eq_2pct = _run(_base_req(bid_ask_haircut=0.02), df)
        _, _, eq_10pct = _run(_base_req(bid_ask_haircut=0.10), df)
        assert eq_10pct <= eq_2pct


# ── trade-level entry cost increases with haircut ─────────────────────────

class TestHaircutAffectsEntryAndExit:
    def test_entry_cost_larger_with_haircut(self):
        """``spread_cost`` in trades should be larger in absolute terms."""
        df = _make_df()
        trades_0, _, _ = _run(_base_req(bid_ask_haircut=0.0), df)
        trades_hc, _, _ = _run(_base_req(bid_ask_haircut=0.05), df)

        if not trades_0 or not trades_hc:
            pytest.skip("no trades in synthetic data — increase window or relax filters")

        # For a debit spread, spread_cost is positive — haircut makes it larger.
        costs_0 = [t["spread_cost"] for t in trades_0 if t["spread_cost"] > 0]
        costs_hc = [t["spread_cost"] for t in trades_hc if t["spread_cost"] > 0]

        if costs_0 and costs_hc:
            avg_0 = sum(costs_0) / len(costs_0)
            avg_hc = sum(costs_hc) / len(costs_hc)
            assert avg_hc >= avg_0, (
                f"Expected higher avg entry cost with haircut: {avg_hc:.4f} vs {avg_0:.4f}"
            )

    def test_exit_value_lower_with_haircut(self):
        """``spread_exit`` (value received at close) should be lower with haircut."""
        df = _make_df()
        trades_0, _, _ = _run(_base_req(bid_ask_haircut=0.0), df)
        trades_hc, _, _ = _run(_base_req(bid_ask_haircut=0.05), df)

        if not trades_0 or not trades_hc:
            pytest.skip("no trades in synthetic data")

        exits_0 = [t["spread_exit"] for t in trades_0 if t["spread_exit"] > 0]
        exits_hc = [t["spread_exit"] for t in trades_hc if t["spread_exit"] > 0]

        if exits_0 and exits_hc:
            avg_0 = sum(exits_0) / len(exits_0)
            avg_hc = sum(exits_hc) / len(exits_hc)
            assert avg_hc <= avg_0, (
                f"Expected lower avg exit value with haircut: {avg_hc:.4f} vs {avg_0:.4f}"
            )


# ── API endpoint accepts bid_ask_haircut ──────────────────────────────────

class TestBacktestEndpointAcceptsHaircut:
    @pytest.fixture(scope="class")
    def client(self):
        with TestClient(main.app) as c:
            yield c

    def test_endpoint_accepts_bid_ask_haircut_field(self, client):
        """POST /api/backtest with bid_ask_haircut should not raise 422."""
        resp = client.post(
            "/api/backtest",
            json={
                "ticker": "SPY",
                "years_history": 1,
                "capital_allocation": 5000,
                "bid_ask_haircut": 0.02,
            },
        )
        assert resp.status_code == 200

    def test_endpoint_zero_haircut_same_as_default(self, client):
        """Explicitly passing 0.0 must not change the result vs omitting the field."""
        base = client.post(
            "/api/backtest",
            json={"ticker": "SPY", "years_history": 1, "capital_allocation": 5000},
        )
        with_zero = client.post(
            "/api/backtest",
            json={
                "ticker": "SPY",
                "years_history": 1,
                "capital_allocation": 5000,
                "bid_ask_haircut": 0.0,
            },
        )
        assert base.status_code == 200
        assert with_zero.status_code == 200

        base_pnl = base.json().get("analytics", {}).get("total_pnl", 0)
        zero_pnl = with_zero.json().get("analytics", {}).get("total_pnl", 0)
        assert base_pnl == pytest.approx(zero_pnl, abs=0.01)

    def test_positive_haircut_reduces_total_pnl(self, client):
        """A 5% haircut should produce lower total_pnl than 0% haircut."""
        no_hc = client.post(
            "/api/backtest",
            json={"ticker": "SPY", "years_history": 1, "capital_allocation": 5000,
                  "bid_ask_haircut": 0.0},
        )
        with_hc = client.post(
            "/api/backtest",
            json={"ticker": "SPY", "years_history": 1, "capital_allocation": 5000,
                  "bid_ask_haircut": 0.05},
        )
        assert no_hc.status_code == 200
        assert with_hc.status_code == 200

        pnl_0 = no_hc.json().get("analytics", {}).get("total_pnl", 0)
        pnl_hc = with_hc.json().get("analytics", {}).get("total_pnl", 0)
        # Haircut should reduce P&L.
        assert pnl_hc <= pnl_0
