from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from core.scanner import list_strategy_classes
from strategies.mfi_reversion import MfiReversionStrategy


def _req(**overrides):
    params = {
        "strategy_params": {},
        "direction": "bull",
        "strategy_type": "bull_call",
        "ema_length": 10,
    }
    params.update(overrides)
    return SimpleNamespace(**params)


def _bars(rows: int = 230) -> pd.DataFrame:
    close = np.linspace(100, 140, rows)
    high = close + 1
    low = close - 1
    open_ = close + 0.2
    volume = np.full(rows, 1_000_000)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        }
    )


def test_mfi_reversion_registered():
    assert list_strategy_classes()["mfi_reversion"] is MfiReversionStrategy


def test_mfi_reversion_entry_requires_oversold_mfi_and_trend():
    strat = MfiReversionStrategy()
    df = strat.compute_indicators(_bars(), _req())
    i = len(df) - 1
    df.loc[df.index[i], "MFI"] = 15
    df.loc[df.index[i], "RSI"] = 35
    df.loc[df.index[i], "SMA_200"] = df.loc[df.index[i], "Close"] - 5
    assert strat.check_entry(df, i, _req()) is True

    df.loc[df.index[i], "MFI"] = 30
    assert strat.check_entry(df, i, _req()) is False


def test_mfi_reversion_exit_eventually_expires():
    strat = MfiReversionStrategy()
    df = strat.compute_indicators(_bars(), _req())
    should_exit, reason = strat.check_exit(
        df,
        220,
        {"entry_idx": 210, "entry_dte": 7},
        _req(
            strategy_params={"max_hold_days": 15, "exit_on_up_close": False},
            direction="bull",
        ),
    )
    assert should_exit is True
    assert reason == "expired"
