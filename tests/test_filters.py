"""Filter parity tests.

Demonstrates that the single `apply_filters` function correctly
handles every filter from the backtest engine — so the live scanner
can call the same function and produce identical decisions.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from core.filters import apply_filters, attach_regime, resolve_bias


@dataclass
class Req:
    # Use the same field names as BacktestRequest.
    use_rsi_filter: bool = False
    rsi_threshold: int = 30
    use_ema_filter: bool = False
    ema_length: int = 10
    use_sma200_filter: bool = False
    use_volume_filter: bool = False
    use_vix_filter: bool = False
    vix_min: float = 15.0
    vix_max: float = 35.0
    use_regime_filter: bool = False
    regime_allowed: str = "all"
    direction: str = "bull"
    strategy_type: str = "bull_call"


def _row(**fields) -> pd.Series:
    return pd.Series(fields)


@pytest.mark.unit
def test_all_filters_off_allows():
    req = Req()
    allowed, reason = apply_filters(_row(Close=500, RSI=50), req)
    assert allowed is True
    assert reason == ""


@pytest.mark.unit
def test_rsi_filter_bull():
    req = Req(use_rsi_filter=True, rsi_threshold=30)
    # Bull requires RSI < threshold → 25 OK, 35 rejected
    assert apply_filters(_row(RSI=25), req)[0] is True
    assert apply_filters(_row(RSI=35), req) == (False, "rsi_filter")


@pytest.mark.unit
def test_rsi_filter_bear_inverted():
    req = Req(use_rsi_filter=True, rsi_threshold=30, direction="bear")
    # Bear requires RSI > (100 - 30) = 70
    assert apply_filters(_row(RSI=75), req)[0] is True
    assert apply_filters(_row(RSI=60), req) == (False, "rsi_filter")


@pytest.mark.unit
def test_sma200_filter():
    req = Req(use_sma200_filter=True)
    # Bull: Close > SMA_200 → allowed
    assert apply_filters(_row(Close=510, SMA_200=500), req)[0] is True
    assert apply_filters(_row(Close=490, SMA_200=500), req) == (False, "sma200_filter")


@pytest.mark.unit
def test_volume_filter():
    req = Req(use_volume_filter=True)
    assert apply_filters(_row(Volume=200, Volume_MA=100), req)[0] is True
    assert apply_filters(_row(Volume=50, Volume_MA=100), req) == (False, "volume_filter")


@pytest.mark.unit
def test_vix_filter():
    req = Req(use_vix_filter=True, vix_min=15, vix_max=35)
    assert apply_filters(_row(VIX=20), req)[0] is True
    assert apply_filters(_row(VIX=10), req) == (False, "vix_below_min")
    assert apply_filters(_row(VIX=40), req) == (False, "vix_above_max")


@pytest.mark.unit
def test_regime_filter():
    req = Req(use_regime_filter=True, regime_allowed="bull")
    assert apply_filters(_row(regime="bull"), req)[0] is True
    assert apply_filters(_row(regime="bear"), req) == (False, "regime_filter")


@pytest.mark.unit
def test_attach_regime():
    df = pd.DataFrame({
        "Close":   [100, 110, 90, 95],
        "SMA_200": [100, 100, 100, 100],
        "SMA_50":  [100, 105, 95, 95],
    })
    out = attach_regime(df)
    assert out.iloc[1]["regime"] == "bull"
    assert out.iloc[2]["regime"] == "bear"


@pytest.mark.unit
def test_resolve_bias_legacy():
    assert resolve_bias(Req(direction="bull", strategy_type="")) == "bull"
    assert resolve_bias(Req(direction="bear", strategy_type="")) == "bear"
    # Legacy field overrides
    assert resolve_bias(Req(direction="", strategy_type="bear_put")) == "bear"
