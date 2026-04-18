"""C12 — Backtest ↔ live path reconciliation tests.

Prove the backtest engine and the live-path building blocks agree on a
known replayed SPY scenario.  Three layers of parity:

    1. Signal:  strategy.check_entry() fires on the same bars.
    2. Filter:  inline backtest filters ≡ core.filters.apply_filters().
    3. Sizing:  inline backtest sizing  ≡ core.risk.size_position().

All tests use a **deterministic synthetic DataFrame** so they never hit
the network and always produce the same results.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from strategies.consecutive_days import ConsecutiveDaysStrategy
from strategies.builder import OptionTopologyBuilder
from core.filters import apply_filters, attach_regime
from core.risk import size_position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spy_df(n_bars: int = 260, seed: int = 42) -> pd.DataFrame:
    """Build a synthetic SPY daily-bar DataFrame.

    Pattern engineered to produce at least two 2-red-day entry signals:
      - A random walk around 450 with realistic OHLCV
      - Two embedded red-day streaks at bars 210 and 230
    """
    rng = np.random.default_rng(seed)

    dates = pd.bdate_range("2024-01-02", periods=n_bars, freq="B")
    close = np.empty(n_bars)
    close[0] = 450.0
    for i in range(1, n_bars):
        close[i] = close[i - 1] * (1 + rng.normal(0.0003, 0.008))

    # Force two clean 2-red-day streaks for entry triggers.
    # Streak 1 at bars 210-211
    close[210] = close[209] * 0.995
    close[211] = close[210] * 0.993
    close[212] = close[211] * 1.005  # green recovery

    # Streak 2 at bars 230-231
    close[230] = close[229] * 0.994
    close[231] = close[230] * 0.992
    close[232] = close[231] * 1.006

    open_prices = close * (1 + rng.normal(0, 0.001, n_bars))
    # Ensure red/green alignment at the forced streaks
    open_prices[210] = close[210] * 1.004
    open_prices[211] = close[211] * 1.003
    open_prices[212] = close[212] * 0.997  # green bar
    open_prices[230] = close[230] * 1.004
    open_prices[231] = close[231] * 1.003
    open_prices[232] = close[232] * 0.997

    high = np.maximum(open_prices, close) * (1 + rng.uniform(0, 0.005, n_bars))
    low = np.minimum(open_prices, close) * (1 - rng.uniform(0, 0.005, n_bars))
    volume = rng.integers(40_000_000, 120_000_000, size=n_bars).astype(float)

    df = pd.DataFrame({
        "Date": dates,
        "Open": open_prices,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
    })
    df["Date_str"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df


def _default_req(**overrides) -> SimpleNamespace:
    """Minimal BacktestRequest-equivalent with all filter/sizing fields."""
    defaults = dict(
        ticker="SPY",
        strategy_id="consecutive_days",
        strategy_type="bull_call",
        topology="vertical_spread",
        direction="bull",
        capital_allocation=10_000.0,
        contracts_per_trade=1,
        entry_red_days=2,
        exit_green_days=2,
        target_dte=14,
        stop_loss_pct=50.0,
        take_profit_pct=0.0,
        trailing_stop_pct=0.0,
        spread_cost_target=250.0,
        strike_width=5,
        commission_per_contract=0.65,
        realism_factor=1.15,
        ema_length=10,
        # Filters — all off by default; tests toggle them.
        use_rsi_filter=False,
        rsi_threshold=30,
        use_ema_filter=False,
        use_sma200_filter=False,
        use_volume_filter=False,
        use_vix_filter=False,
        vix_min=15.0,
        vix_max=35.0,
        use_regime_filter=False,
        regime_allowed="all",
        # Sizing
        use_dynamic_sizing=False,
        use_targeted_spread=False,
        risk_percent=5.0,
        max_trade_cap=0.0,
        target_spread_pct=2.0,
        max_allocation_cap=2500.0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _run_backtest_entries(df: pd.DataFrame, req, start_idx: int = 200):
    """Extract entry decisions from run_backtest_engine logic without
    the full equity/exit lifecycle — just entry signal + filter + sizing.

    Returns a list of dicts, one per bar where the engine would enter:
        {bar_idx, signal, filter_pass, contracts, net_cost, margin_req}
    """
    from main import fetch_risk_free_rate, _resolve_builder_direction

    RISK_FREE_RATE = fetch_risk_free_rate()
    strategy = ConsecutiveDaysStrategy()
    df = strategy.compute_indicators(df, req)
    df = attach_regime(df)

    entries = []
    equity = req.capital_allocation

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        if not strategy.check_entry(df, i, req):
            continue

        # -- Inline backtest filter logic (copy from run_backtest_engine) --
        allow = True
        is_bear = req.direction == "bear" or req.strategy_type == "bear_put"

        if is_bear:
            if req.use_rsi_filter and float(row["RSI"]) <= (100 - req.rsi_threshold):
                allow = False
            if req.use_ema_filter and float(row["Close"]) <= float(row[f"EMA_{req.ema_length}"]):
                allow = False
            if req.use_sma200_filter and float(row["Close"]) >= float(row["SMA_200"]):
                allow = False
        else:
            if req.use_rsi_filter and float(row["RSI"]) >= req.rsi_threshold:
                allow = False
            if req.use_ema_filter and float(row["Close"]) >= float(row[f"EMA_{req.ema_length}"]):
                allow = False
            if req.use_sma200_filter and float(row["Close"]) <= float(row["SMA_200"]):
                allow = False

        if req.use_volume_filter and float(row["Volume"]) <= float(row["Volume_MA"]):
            allow = False

        if req.use_vix_filter and "VIX" in row.index:
            vix_val = float(row["VIX"]) if pd.notna(row.get("VIX")) else 20
            if vix_val < req.vix_min or vix_val > req.vix_max:
                allow = False

        if req.use_regime_filter and req.regime_allowed != "all":
            if row.get("regime", "sideways") != req.regime_allowed:
                allow = False

        if not allow:
            continue

        # -- Construct spread (same as backtest engine) --
        S = float(row["Close"])
        sigma = max(float(row["HV_21"]), 0.05)
        T = req.target_dte / 365.25

        bias = req.direction
        if req.strategy_type == "bear_put":
            bias = "bear"
        elif req.strategy_type == "bull_call":
            bias = "bull"
        direction = _resolve_builder_direction(req.topology, bias)

        pos = OptionTopologyBuilder.construct_legs(
            topology=req.topology,
            direction=direction,
            S=S, T=T, r=RISK_FREE_RATE, sigma=sigma,
            target_cost=req.spread_cost_target,
            strike_width=req.strike_width,
            realism_factor=req.realism_factor,
        )
        one_cc = pos["net_cost"]
        if abs(one_cc) < 1e-3:
            continue

        # -- Inline backtest sizing --
        if req.use_targeted_spread:
            target_risk = equity * (req.target_spread_pct / 100.0)
            target_risk = min(target_risk, req.max_allocation_cap)
            risk_per = pos["margin_req"] if pos["margin_req"] > 0 else abs(one_cc)
            contracts_bt = int(max(1, target_risk // risk_per))
        elif req.use_dynamic_sizing:
            risk_cap = pos["margin_req"] if pos["margin_req"] > 0 else abs(one_cc)
            ds = equity * (req.risk_percent / 100.0)
            if req.max_trade_cap > 0:
                ds = min(ds, req.max_trade_cap)
            contracts_bt = int(max(1, ds // risk_cap))
        else:
            contracts_bt = req.contracts_per_trade

        entries.append({
            "bar_idx": i,
            "row": row,
            "signal": True,
            "filter_pass": True,
            "contracts_bt": contracts_bt,
            "net_cost": one_cc,
            "margin_req": pos["margin_req"],
            "equity_at_entry": equity,
            "S": S,
            "sigma": sigma,
        })

        # Advance equity so sizing stays in sync with engine
        comm = req.commission_per_contract * contracts_bt * len(pos["legs"])
        equity -= (one_cc * contracts_bt + comm)

    return entries, df


# ===========================================================================
# Tests
# ===========================================================================

@pytest.mark.reconcile
class TestSignalParity:
    """Verify strategy.check_entry fires on the same bars for both paths."""

    def test_forced_red_streak_detected(self):
        """The two forced red-day streaks should produce entry signals
        somewhere in their respective windows (random walk may add adjacent
        red days, so the exact bar floats by ±1)."""
        df = _make_spy_df()
        req = _default_req()
        entries, _ = _run_backtest_entries(df, req)
        triggered_bars = set(e["bar_idx"] for e in entries)

        # Streak 1 region: bars 209-212 must contain at least one entry
        streak1 = {209, 210, 211, 212}
        assert triggered_bars & streak1, (
            f"Expected entry in streak 1 region {sorted(streak1)}, "
            f"got {sorted(triggered_bars)}"
        )
        # Streak 2 region: bars 229-232
        streak2 = {229, 230, 231, 232}
        assert triggered_bars & streak2, (
            f"Expected entry in streak 2 region {sorted(streak2)}, "
            f"got {sorted(triggered_bars)}"
        )

    def test_live_check_entry_agrees(self):
        """For every bar the backtest entered, check_entry() returns True
        when called independently — proving the signal logic is identical."""
        df = _make_spy_df()
        req = _default_req()
        entries, prepped_df = _run_backtest_entries(df, req)
        strategy = ConsecutiveDaysStrategy()

        for e in entries:
            idx = e["bar_idx"]
            assert strategy.check_entry(prepped_df, idx, req), (
                f"check_entry() returned False at bar {idx}, "
                "but the backtest engine entered here."
            )


@pytest.mark.reconcile
class TestFilterParity:
    """Verify apply_filters() gives the same answer as inline backtest logic."""

    def test_no_filters_both_pass(self):
        """With all filters off, every signal bar passes both paths."""
        df = _make_spy_df()
        req = _default_req()
        entries, prepped_df = _run_backtest_entries(df, req)

        for e in entries:
            allowed, reason = apply_filters(e["row"], req)
            assert allowed, (
                f"apply_filters rejected bar {e['bar_idx']} "
                f"with reason={reason!r}, but backtest passed."
            )

    def test_rsi_filter_parity(self):
        """With RSI filter on, apply_filters and backtest inline agree."""
        df = _make_spy_df()
        req = _default_req(use_rsi_filter=True, rsi_threshold=30)
        strategy = ConsecutiveDaysStrategy()
        prepped_df = strategy.compute_indicators(df, req)
        prepped_df = attach_regime(prepped_df)

        for i in range(200, len(prepped_df)):
            row = prepped_df.iloc[i]
            if not strategy.check_entry(prepped_df, i, req):
                continue

            # Inline backtest logic
            rsi = float(row["RSI"])
            inline_pass = rsi < req.rsi_threshold  # bull direction

            # Shared filter function
            allowed, reason = apply_filters(row, req)

            assert allowed == inline_pass, (
                f"Bar {i}: RSI={rsi:.1f}, inline={inline_pass}, "
                f"apply_filters={allowed} ({reason})"
            )

    def test_ema_filter_parity(self):
        """With EMA filter on, apply_filters and backtest inline agree."""
        df = _make_spy_df()
        req = _default_req(use_ema_filter=True)
        strategy = ConsecutiveDaysStrategy()
        prepped_df = strategy.compute_indicators(df, req)
        prepped_df = attach_regime(prepped_df)

        for i in range(200, len(prepped_df)):
            row = prepped_df.iloc[i]
            if not strategy.check_entry(prepped_df, i, req):
                continue

            close = float(row["Close"])
            ema = float(row[f"EMA_{req.ema_length}"])
            inline_pass = close < ema  # bull direction: Close < EMA passes

            allowed, reason = apply_filters(row, req)

            assert allowed == inline_pass, (
                f"Bar {i}: Close={close:.2f}, EMA={ema:.2f}, "
                f"inline={inline_pass}, apply_filters={allowed} ({reason})"
            )

    def test_sma200_filter_parity(self):
        """With SMA200 filter on, apply_filters and backtest inline agree."""
        df = _make_spy_df()
        req = _default_req(use_sma200_filter=True)
        strategy = ConsecutiveDaysStrategy()
        prepped_df = strategy.compute_indicators(df, req)
        prepped_df = attach_regime(prepped_df)

        for i in range(200, len(prepped_df)):
            row = prepped_df.iloc[i]
            if not strategy.check_entry(prepped_df, i, req):
                continue

            close = float(row["Close"])
            sma200 = float(row["SMA_200"])
            inline_pass = close > sma200  # bull: Close > SMA200 passes

            allowed, reason = apply_filters(row, req)

            assert allowed == inline_pass, (
                f"Bar {i}: Close={close:.2f}, SMA200={sma200:.2f}, "
                f"inline={inline_pass}, apply_filters={allowed} ({reason})"
            )

    def test_volume_filter_parity(self):
        """With Volume filter on, apply_filters and backtest inline agree."""
        df = _make_spy_df()
        req = _default_req(use_volume_filter=True)
        strategy = ConsecutiveDaysStrategy()
        prepped_df = strategy.compute_indicators(df, req)
        prepped_df = attach_regime(prepped_df)

        for i in range(200, len(prepped_df)):
            row = prepped_df.iloc[i]
            if not strategy.check_entry(prepped_df, i, req):
                continue

            vol = float(row["Volume"])
            vol_ma = float(row["Volume_MA"])
            inline_pass = vol > vol_ma

            allowed, reason = apply_filters(row, req)

            assert allowed == inline_pass, (
                f"Bar {i}: Vol={vol:.0f}, Vol_MA={vol_ma:.0f}, "
                f"inline={inline_pass}, apply_filters={allowed} ({reason})"
            )

    def test_regime_filter_parity(self):
        """With regime filter on, apply_filters and backtest inline agree."""
        df = _make_spy_df()
        req = _default_req(use_regime_filter=True, regime_allowed="bull")
        strategy = ConsecutiveDaysStrategy()
        prepped_df = strategy.compute_indicators(df, req)
        prepped_df = attach_regime(prepped_df)

        for i in range(200, len(prepped_df)):
            row = prepped_df.iloc[i]
            if not strategy.check_entry(prepped_df, i, req):
                continue

            regime = row.get("regime", "sideways")
            inline_pass = regime == "bull"

            allowed, reason = apply_filters(row, req)

            assert allowed == inline_pass, (
                f"Bar {i}: regime={regime}, inline={inline_pass}, "
                f"apply_filters={allowed} ({reason})"
            )

    def test_all_filters_combined(self):
        """With multiple filters on simultaneously, parity holds."""
        df = _make_spy_df()
        req = _default_req(
            use_rsi_filter=True, rsi_threshold=70,  # permissive for bull
            use_ema_filter=True,
            use_sma200_filter=True,
        )
        strategy = ConsecutiveDaysStrategy()
        prepped_df = strategy.compute_indicators(df, req)
        prepped_df = attach_regime(prepped_df)

        for i in range(200, len(prepped_df)):
            row = prepped_df.iloc[i]
            if not strategy.check_entry(prepped_df, i, req):
                continue

            # Inline backtest (bull path)
            rsi_ok = float(row["RSI"]) < req.rsi_threshold
            ema_ok = float(row["Close"]) < float(row[f"EMA_{req.ema_length}"])
            sma_ok = float(row["Close"]) > float(row["SMA_200"])
            inline_pass = rsi_ok and ema_ok and sma_ok

            allowed, _ = apply_filters(row, req)

            assert allowed == inline_pass, (
                f"Bar {i}: rsi_ok={rsi_ok}, ema_ok={ema_ok}, sma_ok={sma_ok}, "
                f"inline={inline_pass}, apply_filters={allowed}"
            )


@pytest.mark.reconcile
class TestSizingParity:
    """Verify size_position() produces the same contract count as the
    inline backtest sizing logic."""

    def test_fixed_sizing(self):
        """Fixed mode: both paths return contracts_per_trade."""
        req = _default_req(contracts_per_trade=3)
        contracts_live = size_position(
            equity=req.capital_allocation,
            debit_per_contract=250.0,
            margin_per_contract=250.0,
            mode="fixed",
            fixed_contracts=req.contracts_per_trade,
        )
        assert contracts_live == 3

    def test_dynamic_sizing_parity(self):
        """Dynamic mode: live size_position matches backtest inline logic."""
        equity = 10_000.0
        net_cost = 250.0   # per-contract debit
        margin_req = 250.0
        risk_pct = 5.0
        max_cap = 0.0

        # Backtest inline
        risk_cap = margin_req if margin_req > 0 else abs(net_cost)
        ds = equity * (risk_pct / 100.0)
        if max_cap > 0:
            ds = min(ds, max_cap)
        contracts_bt = int(max(1, ds // risk_cap))

        # Live path
        contracts_live = size_position(
            equity=equity,
            debit_per_contract=net_cost,
            margin_per_contract=margin_req,
            mode="dynamic",
            risk_percent=risk_pct,
            max_trade_cap=max_cap,
        )
        assert contracts_live == contracts_bt, (
            f"Dynamic sizing: bt={contracts_bt}, live={contracts_live}"
        )

    def test_dynamic_sizing_with_cap(self):
        """Dynamic mode with max_trade_cap active."""
        equity = 50_000.0
        net_cost = 300.0
        risk_pct = 5.0
        max_cap = 600.0

        # Backtest inline
        ds = min(equity * (risk_pct / 100.0), max_cap)
        contracts_bt = int(max(1, ds // net_cost))

        contracts_live = size_position(
            equity=equity,
            debit_per_contract=net_cost,
            margin_per_contract=net_cost,
            mode="dynamic",
            risk_percent=risk_pct,
            max_trade_cap=max_cap,
        )
        assert contracts_live == contracts_bt

    def test_targeted_spread_parity(self):
        """Targeted-spread mode matches backtest inline."""
        equity = 20_000.0
        net_cost = 200.0
        margin_req = 200.0
        target_pct = 2.0
        max_alloc = 2500.0

        # Backtest inline
        target_risk = min(equity * (target_pct / 100.0), max_alloc)
        risk_per = margin_req if margin_req > 0 else abs(net_cost)
        contracts_bt = int(max(1, target_risk // risk_per))

        contracts_live = size_position(
            equity=equity,
            debit_per_contract=net_cost,
            margin_per_contract=margin_req,
            mode="targeted_spread",
            target_spread_pct=target_pct,
            max_allocation_cap=max_alloc,
        )
        assert contracts_live == contracts_bt, (
            f"Targeted: bt={contracts_bt}, live={contracts_live}"
        )

    def test_targeted_spread_cap_binds(self):
        """When max_allocation_cap is the binding constraint."""
        equity = 100_000.0
        net_cost = 400.0
        target_pct = 5.0
        max_alloc = 800.0  # tight cap

        target_risk = min(equity * (target_pct / 100.0), max_alloc)
        contracts_bt = int(max(1, target_risk // net_cost))

        contracts_live = size_position(
            equity=equity,
            debit_per_contract=net_cost,
            margin_per_contract=net_cost,
            mode="targeted_spread",
            target_spread_pct=target_pct,
            max_allocation_cap=max_alloc,
        )
        assert contracts_live == contracts_bt
        assert contracts_live == 2  # 800 // 400

    def test_excess_liquidity_clamp(self):
        """size_position clamps by excess_liquidity — a live-only guard."""
        contracts = size_position(
            equity=100_000.0,
            debit_per_contract=200.0,
            margin_per_contract=200.0,
            mode="dynamic",
            risk_percent=10.0,
            excess_liquidity=500.0,  # can only afford 2 contracts
        )
        assert contracts == 2


@pytest.mark.reconcile
class TestEndToEndReconcile:
    """Full integration: run backtest entries, then replay each through
    the live-path building blocks and assert parity."""

    def test_entries_match_live_path(self):
        """Every bar the backtest engine enters, the live path also enters
        with the same contract count."""
        df = _make_spy_df()
        req = _default_req()
        entries, prepped_df = _run_backtest_entries(df, req)
        strategy = ConsecutiveDaysStrategy()

        assert len(entries) >= 2, f"Expected ≥2 entries, got {len(entries)}"

        for e in entries:
            idx = e["bar_idx"]

            # 1. Signal
            assert strategy.check_entry(prepped_df, idx, req)

            # 2. Filter
            allowed, reason = apply_filters(e["row"], req)
            assert allowed, f"Filter rejected bar {idx}: {reason}"

            # 3. Sizing
            from core.risk import sizing_mode_from_request
            mode = sizing_mode_from_request(req)
            contracts_live = size_position(
                equity=e["equity_at_entry"],
                debit_per_contract=e["net_cost"],
                margin_per_contract=e["margin_req"],
                mode=mode,
                fixed_contracts=req.contracts_per_trade,
                risk_percent=req.risk_percent,
                max_trade_cap=req.max_trade_cap,
                target_spread_pct=req.target_spread_pct,
                max_allocation_cap=req.max_allocation_cap,
            )
            assert contracts_live == e["contracts_bt"], (
                f"Bar {idx}: backtest sized {e['contracts_bt']} contracts, "
                f"live sized {contracts_live}"
            )

    def test_entries_with_dynamic_sizing(self):
        """End-to-end with dynamic sizing enabled."""
        df = _make_spy_df()
        req = _default_req(
            use_dynamic_sizing=True,
            risk_percent=5.0,
            max_trade_cap=0.0,
        )
        entries, prepped_df = _run_backtest_entries(df, req)
        strategy = ConsecutiveDaysStrategy()

        assert len(entries) >= 1

        for e in entries:
            allowed, _ = apply_filters(e["row"], req)
            assert allowed

            contracts_live = size_position(
                equity=e["equity_at_entry"],
                debit_per_contract=e["net_cost"],
                margin_per_contract=e["margin_req"],
                mode="dynamic",
                risk_percent=req.risk_percent,
                max_trade_cap=req.max_trade_cap,
            )
            assert contracts_live == e["contracts_bt"]

    def test_entries_with_targeted_sizing(self):
        """End-to-end with targeted-spread sizing."""
        df = _make_spy_df()
        req = _default_req(
            use_targeted_spread=True,
            target_spread_pct=2.0,
            max_allocation_cap=2500.0,
        )
        entries, prepped_df = _run_backtest_entries(df, req)

        assert len(entries) >= 1

        for e in entries:
            contracts_live = size_position(
                equity=e["equity_at_entry"],
                debit_per_contract=e["net_cost"],
                margin_per_contract=e["margin_req"],
                mode="targeted_spread",
                target_spread_pct=req.target_spread_pct,
                max_allocation_cap=req.max_allocation_cap,
            )
            assert contracts_live == e["contracts_bt"]

    def test_bear_direction_signal_and_filter(self):
        """Reconcile with bear direction — signals and filters stay in sync."""
        df = _make_spy_df()
        # For bear: entry triggers on green-day streaks
        req = _default_req(
            direction="bear",
            strategy_type="bear_put",
            entry_red_days=2,  # will count green days for bear
        )
        strategy = ConsecutiveDaysStrategy()
        prepped_df = strategy.compute_indicators(df, req)
        prepped_df = attach_regime(prepped_df)

        for i in range(200, len(prepped_df)):
            if not strategy.check_entry(prepped_df, i, req):
                continue
            row = prepped_df.iloc[i]
            allowed, _ = apply_filters(row, req)
            # With no filters enabled, everything that signals should pass
            assert allowed, f"Bear signal at bar {i} rejected by apply_filters"
