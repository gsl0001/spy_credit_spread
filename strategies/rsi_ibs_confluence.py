"""RSI(2) + IBS confluence mean-reversion on SPY.

Sources:
  - Larry Connors, "Short-Term Trading Strategies That Work": RSI(2)
    identifies short-term oversold conditions in liquid equity ETFs.
  - Quantpedia, "Internal Bar Strength as Equity Market Predictor": low IBS
    (close near the daily low) has historically predicted next-day reversal.

Logic:
  Entry (long bias):
    - RSI(2) below entry_rsi (default 15).
    - IBS below entry_ibs (default 0.25).
    - Optional uptrend filter: Close > SMA(trend_sma).

  Exit:
    - RSI(2) reverts above exit_rsi, or
    - Close > SMA(exit_sma), or
    - first up-close, max-hold, or expiry.

Topology: bull-call debit spread, 7 DTE, $5 wide. This is a high-conviction
short-hold reversal setup; the confluence should reduce false positives versus
RSI(2) or IBS alone.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class RsiIbsConfluenceStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "shipped"

    @property
    def name(self) -> str:
        return "RSI(2) + IBS Confluence"

    @classmethod
    def id(cls) -> str:
        return "rsi_ibs_confluence"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "rsi_period": {"type": "number", "default": 2, "min": 2, "max": 5,
                           "label": "RSI Period"},
            "entry_rsi": {"type": "number", "default": 15, "min": 1, "max": 30,
                          "label": "Entry RSI Max"},
            "exit_rsi": {"type": "number", "default": 70, "min": 50, "max": 90,
                         "label": "Exit RSI Min"},
            "entry_ibs": {"type": "number", "default": 0.25, "min": 0.05, "max": 0.5,
                          "label": "Entry IBS Max"},
            "trend_sma": {"type": "number", "default": 200, "min": 50, "max": 250,
                          "label": "Trend Filter SMA"},
            "use_trend_filter": {"type": "boolean", "default": True,
                                 "label": "Use Trend Filter"},
            "exit_sma": {"type": "number", "default": 5, "min": 3, "max": 20,
                         "label": "Exit SMA"},
            "max_hold_days": {"type": "number", "default": 5, "min": 1, "max": 15,
                              "label": "Max Hold Days"},
            "exit_on_up_close": {"type": "boolean", "default": True,
                                 "label": "Exit on First Up Close"},
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        v = params.get(key)
        return v if v is not None else getattr(req, key, default)

    @staticmethod
    def _is_bear(req) -> bool:
        return (
            getattr(req, "direction", "") == "bear"
            or getattr(req, "strategy_type", "") == "bear_put"
        )

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        period = int(self._get(req, "rsi_period", 2))
        trend = int(self._get(req, "trend_sma", 200))
        exit_n = int(self._get(req, "exit_sma", 5))

        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df[f"RSI_{period}"] = (100 - (100 / (1 + rs))).fillna(50)

        avg_gain14 = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss14 = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs14 = avg_gain14 / avg_loss14.replace(0, np.nan)
        df["RSI"] = (100 - (100 / (1 + rs14))).fillna(50)

        rng = (df["High"] - df["Low"]).replace(0, np.nan)
        df["IBS"] = ((df["Close"] - df["Low"]) / rng).fillna(0.5)
        df["prev_close"] = df["Close"].shift(1)

        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()
        df[f"SMA_{exit_n}"] = df["Close"].rolling(window=exit_n).mean()
        ema_len = int(getattr(req, "ema_length", 10) or 10)
        df[f"EMA_{ema_len}"] = df["Close"].ewm(span=ema_len, adjust=False).mean()
        df["Volume_MA"] = df["Volume"].rolling(window=10).mean()
        log_ret = np.log(df["Close"] / df["Close"].shift(1))
        df["HV_21"] = (log_ret.rolling(window=21).std() * np.sqrt(252)).fillna(0.15)
        return df

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        trend = int(self._get(req, "trend_sma", 200))
        if i < trend:
            return False
        row = df.iloc[i]
        period = int(self._get(req, "rsi_period", 2))
        rsi = row.get(f"RSI_{period}")
        ibs = row.get("IBS")
        if rsi is None or ibs is None or pd.isna(rsi) or pd.isna(ibs):
            return False
        is_bear = self._is_bear(req)
        if is_bear:
            if float(rsi) <= (100 - float(self._get(req, "entry_rsi", 15))):
                return False
            if float(ibs) <= (1.0 - float(self._get(req, "entry_ibs", 0.25))):
                return False
        else:
            if float(rsi) >= float(self._get(req, "entry_rsi", 15)):
                return False
            if float(ibs) >= float(self._get(req, "entry_ibs", 0.25)):
                return False

        if bool(self._get(req, "use_trend_filter", True)):
            sma = row.get(f"SMA_{trend}")
            if sma is None or pd.isna(sma):
                return False
            if is_bear and float(row["Close"]) >= float(sma):
                return False
            if (not is_bear) and float(row["Close"]) <= float(sma):
                return False
        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        days_held = i - trade_state["entry_idx"]
        period = int(self._get(req, "rsi_period", 2))
        rsi = row.get(f"RSI_{period}")
        is_bear = self._is_bear(req)
        if rsi is not None and not pd.isna(rsi):
            exit_rsi = float(self._get(req, "exit_rsi", 70))
            if is_bear and float(rsi) < (100 - exit_rsi):
                return True, "rsi_target"
            if (not is_bear) and float(rsi) > exit_rsi:
                return True, "rsi_target"

        if bool(self._get(req, "exit_on_up_close", True)) and i >= 1:
            prev_close = row.get("prev_close")
            if prev_close is not None and not pd.isna(prev_close):
                if is_bear and float(row["Close"]) < float(prev_close):
                    return True, "down_close"
                if (not is_bear) and float(row["Close"]) > float(prev_close):
                    return True, "up_close"

        exit_n = int(self._get(req, "exit_sma", 5))
        sma_exit = row.get(f"SMA_{exit_n}")
        if sma_exit is not None and not pd.isna(sma_exit):
            if is_bear and float(row["Close"]) < float(sma_exit):
                return True, "sma_cross"
            if (not is_bear) and float(row["Close"]) > float(sma_exit):
                return True, "sma_cross"

        if days_held >= int(self._get(req, "max_hold_days", 5)):
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
