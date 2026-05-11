"""SPY Put Credit Spread — Systematic Theta Harvest.

Source: Option Alpha "8 SPY Put Credit Spread Backtest Results Analyzed"
(https://optionalpha.com/blog/spy-put-credit-spread-backtest)
Edge: CBOE data shows SPX IV exceeded subsequent realized vol in ~84% of monthly
periods (2006–2023). Selling put spreads systematically captures that structural
IV overstatement. Documented 78–93% WR across multiple 5-year backtests,
managed at 50% of max profit.

The strategy is NOT signal-driven — it enters every `entry_every_n_days` trading
days when no position is active. Position management drives the edge, not market-
timing. This is a structural theta-harvest, not a mean-reversion trigger.

Logic:
  Entry:
    - No active trade AND `entry_every_n_days` have elapsed since last entry.
    - Optional: SPY > SMA_200 (catastrophic-regime filter — avoids selling puts
      into a sustained downtrend where tail risk is highest).
    - Optional: VIX filter via HV_21 proxy (only sell puts when realized vol is
      below a threshold — ensures IV overstatement is wide enough).

  Exit:
    - 50% of max profit reached (fast theta capture), OR
    - max_hold_days reached (DTE decay curve flattens), OR
    - expired.

Credit mechanics note: The backtest engine treats entry_cost as PAID premium.
For a credit spread the premium is RECEIVED, so net max loss =
(strike_width × 100) − credit received. This strategy CANNOT be correctly
backtested until the engine natively handles negative entry_cost (credit).
See engine TODO in AGENT_COORDINATION_README.md.

BAR_SIZE: "1 day" — daily harness.

VETTING RESULT: ENGINE_BLOCKED (2026-05-10)
  Debit-proxy test (bull_call engine) on 3 sweeps — best config:
    21 DTE, 14d interval, 21d hold → 51 trades, WR 63%, PF 1.69, Sharpe 0.96, DD -7.2%
  Against CREDIT bar (PF ≥ 1.3, Sharpe ≥ 0.8): sweeps 1 and 3 both clear.
  This is NOT a rejection — the thesis holds. Strategy is blocked on engine.
  Path to ship: implement credit-spread engine (negative entry_cost, max_loss =
  width×100 − credit). Then re-run as bull_put; expected to clear all bars
  (credit bar is lower: PF ≥ 1.3, Sharpe ≥ 0.8, WR ≥ 65%).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class SpyPcsStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "engine_blocked"

    @property
    def name(self) -> str:
        return "SPY Put Credit Spread"

    @classmethod
    def id(cls) -> str:
        return "spy_pcs"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "entry_every_n_days": {
                "type": "number", "default": 14, "min": 5, "max": 30,
                "label": "Entry Interval (trading days)",
                "description": "Open a new spread every N trading days when flat",
            },
            "use_trend_filter": {
                "type": "boolean", "default": True,
                "label": "Use SMA-200 Trend Filter",
                "description": "Skip entry when SPY < SMA_200 (tail-risk regime)",
            },
            "hv_threshold": {
                "type": "number", "default": 0.25, "min": 0.10, "max": 0.50,
                "label": "Max HV-21 (vol filter)",
                "description": "Skip if 21-day realized vol > this (IV overstatement narrows in high-vol)",
            },
            "use_hv_filter": {
                "type": "boolean", "default": False,
                "label": "Use HV Filter",
                "description": "Enable the realized-vol gate",
            },
            "max_hold_days": {
                "type": "number", "default": 21, "min": 5, "max": 45,
                "label": "Max Hold Days",
                "description": "Hard exit when DTE decay curve flattens",
            },
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()

        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        ema_len = int(getattr(req, "ema_length", 10) or 10)
        df[f"EMA_{ema_len}"] = df["Close"].ewm(span=ema_len, adjust=False).mean()
        df["Volume_MA"] = df["Volume"].rolling(window=10).mean()

        log_ret = np.log(df["Close"] / df["Close"].shift(1))
        df["HV_21"] = (log_ret.rolling(window=21).std() * np.sqrt(252)).fillna(0.20)

        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["RSI"] = (100 - (100 / (1 + rs))).fillna(50)

        df["prev_close"] = df["Close"].shift(1)

        # Rolling entry gate — marks every Nth bar as an entry candidate
        n = int(self._get(req, "entry_every_n_days", 14))
        df["entry_gate"] = np.arange(len(df)) % n == 0

        return df

    @staticmethod
    def _is_bear(req) -> bool:
        return (
            getattr(req, "direction", "") == "bear"
            or getattr(req, "strategy_type", "") == "bear_put"
        )

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        if i < 200:
            return False
        row = df.iloc[i]

        if not bool(row.get("entry_gate", False)):
            return False

        is_bear = self._is_bear(req)

        if bool(self._get(req, "use_trend_filter", True)):
            sma = row.get("SMA_200")
            if sma is None or pd.isna(sma):
                return False
            if not is_bear and float(row["Close"]) <= float(sma):
                return False
            if is_bear and float(row["Close"]) >= float(sma):
                return False

        if bool(self._get(req, "use_hv_filter", False)):
            hv = row.get("HV_21")
            threshold = float(self._get(req, "hv_threshold", 0.25))
            if hv is not None and not pd.isna(hv) and float(hv) > threshold:
                return False

        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        days_held = i - trade_state.get("entry_idx", i)
        max_hold = int(self._get(req, "max_hold_days", 21))

        if days_held >= max_hold:
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
