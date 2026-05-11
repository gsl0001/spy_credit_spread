from __future__ import annotations

from datetime import date, datetime

import pandas as pd

import main
from main import BacktestRequest


def _make_orb_endpoint_bars() -> pd.DataFrame:
    day = date(2026, 4, 27)  # Monday
    rows = [
        {
            "Date": datetime(day.year, day.month, day.day, 9, 30),
            "Open": 580.0,
            "High": 581.0,
            "Low": 579.0,
            "Close": 580.0,
            "Volume": 1_000_000,
        },
        {
            "Date": datetime(day.year, day.month, day.day, 9, 35),
            "Open": 580.0,
            "High": 584.0,
            "Low": 580.0,
            "Close": 583.0,
            "Volume": 900_000,
        },
        {
            "Date": datetime(day.year, day.month, day.day, 9, 40),
            "Open": 583.0,
            "High": 590.0,
            "Low": 583.0,
            "Close": 589.0,
            "Volume": 900_000,
        },
        {
            "Date": datetime(day.year, day.month, day.day, 15, 30),
            "Open": 589.0,
            "High": 590.0,
            "Low": 588.0,
            "Close": 589.0,
            "Volume": 900_000,
        },
    ]
    return pd.DataFrame(rows)


def test_backtest_endpoint_routes_orb_to_intraday_engine(monkeypatch):
    monkeypatch.setattr(main, "fetch_historical_data", lambda *_: _make_orb_endpoint_bars())
    monkeypatch.setattr(main, "fetch_vix_data", lambda *_: pd.DataFrame(columns=["Date", "VIX"]))

    req = BacktestRequest(
        strategy_id="orb",
        strategy_type="bull_call",
        direction="bull",
        target_dte=0,
        years_history=1,
        capital_allocation=10_000,
        contracts_per_trade=1,
        stop_loss_pct=50,
        take_profit_pct=50,
        commission_per_contract=0,
        use_rsi_filter=False,
        use_ema_filter=False,
        use_vix_filter=False,
        enable_mc_histogram=False,
        enable_walk_forward=False,
    )

    result = main.backtest(req)

    assert "error" not in result
    assert result["metrics"]["total_trades"] == 1
    assert result["trades"][0]["reason"] == "take_profit"
