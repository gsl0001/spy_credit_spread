"""Money Flow Index oversold reversion on SPY.

Sources:
  - Gene Quong and Avrum Soudack, "The Money Flow Index," Technical
    Analysis of Stocks & Commodities (1997). MFI adapts RSI by weighting
    typical-price moves with volume.
  - StockCharts ChartSchool, "Money Flow Index (MFI)," documents the
    common 20/80 oversold-overbought thresholds.

Distinct from existing strategies:
  - `rsi2` and `ibs` are price-only dip signals. This strategy requires
    volume-confirmed downside money flow, so it should fire on capitulation
    dips where selling pressure is unusually heavy rather than every weak
    close.

Logic:
  Entry (long bias):
    - MFI(mfi_period) < entry_mfi (default 20)
    - Close > SMA(trend_sma), if trend filter is enabled
    - Optional RSI(14) ceiling to keep entries genuinely washed out

  Exit:
    - MFI > exit_mfi (default 50), meaning money flow recovered
    - Optional first up-close exit
    - max_hold_days hard limit
    - Mandatory expiry exit

Topology: bull-call debit spread, 7 DTE, $5 wide. Per strategy-vetting
Step 2, this is a short-hold directional mean-reversion trigger, so a
debit vertical is the supported topology to test first.

VETTING RESULT (2026-05-15): REJECTED.
  Tested through /api/backtest on SPY, bull-call $5-wide 7 DTE debit
  vertical, 50/50 SL/TP, 1 contract, realistic commissions.
  Baseline 3y produced 0 trades. Nearby sweeps also failed:
    entry_mfi=15: 0 trades
    entry_mfi=25: 1 trade, WR 0%, PF 0.0, Sharpe -0.68, PnL -130.49
    no RSI confirmation: 0 trades
  Root cause: MFI capitulation with trend and RSI confirmation is too rare
  on SPY for a standalone debit-spread strategy. Loosening enough to trade
  would turn it into another generic dip signal without evidence of edge.
  Path to graduation: use MFI only as a confluence filter on an existing
  shipped strategy, or re-test as a credit spread after the engine supports
  credit-spread pricing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class MfiReversionStrategy(BaseStrategy):
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "5y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "MFI Reversion"

    @classmethod
    def id(cls) -> str:
        return "mfi_reversion"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "mfi_period": {
                "type": "number",
                "default": 14,
                "min": 5,
                "max": 30,
                "label": "MFI Period",
                "description": "Lookback for Money Flow Index calculation",
            },
            "entry_mfi": {
                "type": "number",
                "default": 20,
                "min": 5,
                "max": 35,
                "label": "Entry MFI Max",
                "description": "Long entry when MFI is below this oversold threshold",
            },
            "exit_mfi": {
                "type": "number",
                "default": 50,
                "min": 35,
                "max": 80,
                "label": "Exit MFI Min",
                "description": "Exit when MFI recovers above this level",
            },
            "trend_sma": {
                "type": "number",
                "default": 200,
                "min": 50,
                "max": 250,
                "label": "Trend Filter SMA",
                "description": "Only buy dips when Close is above this SMA",
            },
            "use_trend_filter": {
                "type": "boolean",
                "default": True,
                "label": "Use Trend Filter",
            },
            "use_rsi_confirm": {
                "type": "boolean",
                "default": True,
                "label": "Use RSI Confirmation",
                "description": "Require RSI(14) below confirmation threshold",
            },
            "rsi_confirm_max": {
                "type": "number",
                "default": 40,
                "min": 20,
                "max": 55,
                "label": "RSI Confirm Max",
            },
            "max_hold_days": {
                "type": "number",
                "default": 5,
                "min": 1,
                "max": 15,
                "label": "Max Hold Days",
            },
            "exit_on_up_close": {
                "type": "boolean",
                "default": True,
                "label": "Exit on First Up Close",
            },
        }

    def _get(self, req, key, default):
        params = getattr(req, "strategy_params", {}) or {}
        value = params.get(key)
        return value if value is not None else getattr(req, key, default)

    @staticmethod
    def _is_bear(req) -> bool:
        return (
            getattr(req, "direction", "") == "bear"
            or getattr(req, "strategy_type", "") == "bear_put"
        )

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        period = int(self._get(req, "mfi_period", 14) or 14)
        trend = int(self._get(req, "trend_sma", 200) or 200)

        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
        raw_money_flow = typical_price * df["Volume"]
        direction = typical_price.diff()
        positive_flow = raw_money_flow.where(direction > 0, 0.0)
        negative_flow = raw_money_flow.where(direction < 0, 0.0).abs()
        pos_sum = positive_flow.rolling(window=period).sum()
        neg_sum = negative_flow.rolling(window=period).sum()
        money_ratio = pos_sum / neg_sum.replace(0, np.nan)
        df["MFI"] = (100 - (100 / (1 + money_ratio))).fillna(50)

        df["SMA_200"] = df["Close"].rolling(window=200).mean()
        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        df[f"SMA_{trend}"] = df["Close"].rolling(window=trend).mean()

        ema_len = int(getattr(req, "ema_length", 10) or 10)
        df[f"EMA_{ema_len}"] = df["Close"].ewm(span=ema_len, adjust=False).mean()
        df["Volume_MA"] = df["Volume"].rolling(window=10).mean()
        log_ret = np.log(df["Close"] / df["Close"].shift(1))
        df["HV_21"] = (log_ret.rolling(window=21).std() * np.sqrt(252)).fillna(0.15)

        delta = df["Close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["RSI"] = (100 - (100 / (1 + rs))).fillna(50)

        df["prev_close"] = df["Close"].shift(1)
        return df

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        period = int(self._get(req, "mfi_period", 14) or 14)
        trend = int(self._get(req, "trend_sma", 200) or 200)
        if i < max(period + 1, trend):
            return False

        row = df.iloc[i]
        mfi = row.get("MFI")
        if mfi is None or pd.isna(mfi):
            return False

        entry_mfi = float(self._get(req, "entry_mfi", 20) or 20)
        is_bear = self._is_bear(req)
        if is_bear:
            mfi_cond = float(mfi) > (100.0 - entry_mfi)
        else:
            mfi_cond = float(mfi) < entry_mfi
        if not mfi_cond:
            return False

        if bool(self._get(req, "use_trend_filter", True)):
            sma = row.get(f"SMA_{trend}")
            if sma is None or pd.isna(sma):
                return False
            close = float(row["Close"])
            if is_bear and close >= float(sma):
                return False
            if (not is_bear) and close <= float(sma):
                return False

        if bool(self._get(req, "use_rsi_confirm", True)):
            rsi = row.get("RSI")
            if rsi is None or pd.isna(rsi):
                return False
            rsi_max = float(self._get(req, "rsi_confirm_max", 40) or 40)
            if is_bear and float(rsi) <= (100.0 - rsi_max):
                return False
            if (not is_bear) and float(rsi) >= rsi_max:
                return False

        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        days_held = i - trade_state["entry_idx"]
        max_hold = int(self._get(req, "max_hold_days", 5) or 5)
        is_bear = self._is_bear(req)

        if bool(self._get(req, "exit_on_up_close", True)) and i >= 1:
            prev_close = row.get("prev_close")
            if prev_close is not None and not pd.isna(prev_close):
                if is_bear and float(row["Close"]) < float(prev_close):
                    return True, "down_close"
                if (not is_bear) and float(row["Close"]) > float(prev_close):
                    return True, "up_close"

        mfi = row.get("MFI")
        if mfi is not None and not pd.isna(mfi):
            exit_mfi = float(self._get(req, "exit_mfi", 50) or 50)
            if is_bear and float(mfi) < (100.0 - exit_mfi):
                return True, "mfi_target"
            if (not is_bear) and float(mfi) > exit_mfi:
                return True, "mfi_target"

        if days_held >= max_hold:
            return True, "max_hold"
        if (trade_state.get("entry_dte", 0) - days_held) <= 0:
            return True, "expired"
        return False, ""
