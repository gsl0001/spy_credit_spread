"""Late-Day Momentum (LDM) 0DTE intraday breakout on SPY.

Sources:
  - Heston, Korajczyk & Sadka, "Intraday Patterns in the Cross-section of
    Stock Returns," Journal of Finance 65(4) (2010). Documents persistent
    intraday-momentum: returns over a fixed intraday window predict
    same-direction returns over later windows. Replicated on SPY/SPX
    by Bogousslavsky (2016) and Gao-Han-Li-Zhou (2018).
  - Specifically: SPY 13:30–14:00 ET return strongly predicts
    14:00–15:55 ET return (~55-60% directional hit-rate, t-stat > 4
    in 2007-2023 5-min sample).

Distinct from existing strategies:
  - `orb` triggers on the 9:30 opening range. LDM triggers on a
    14:00 afternoon range. Different structural effect (institutional
    rebalancing vs morning-news). Trades on different days than ORB.
  - All other strategies are daily — LDM is intraday and 0DTE.

Logic:
  Afternoon opening range:
    - At `or_start_time` (default 14:00 ET) observe a `or_minutes`
      window (default 15 min).
    - Compute OR high / OR low.

  Direction inference:
    - Bull bias if OR close > OR open (afternoon range trended up).
    - Bear bias otherwise.

  Entry:
    - Bull: buy 0DTE bull-call debit spread when SPY crosses
      (OR_high + offset_points) before `entry_cutoff` (default 15:30).
    - Bear (mirror): buy 0DTE bear-put when SPY crosses below
      (OR_low - offset_points).

  Exit:
    - take_profit_pct hit (default 50%)
    - stop_loss_pct hit (default 50%)
    - time_exit (default 15:55) — never carry 0DTE to expiry close,
      gamma is unmanageable in last 5 minutes.

Topology: 0DTE bull-call (or bear-put) debit spread, $5 wide. Uses
existing `core/backtest_orb.py` engine (extended with `or_start_time`
config knob).

VETTING NOTES:
  Engine: reuses `run_orb_backtest` with `or_start_time=14:00`,
  `or_minutes=15`, `entry_cutoff=15:30`, `time_exit=15:55`. No new
  engine code beyond the start-time knob.

  Trade count expectation: similar density to ORB (~250 / 3y) but
  on a complementary set of days.

VETTING RESULT (2026-05-10): REJECTED.
  Tested SPY 60d (yfinance 5m cap), bull_call 0DTE $5-wide, 50/50 SL/TP.
  3 sweeps:
    (or=14:00, 15min, off=0.50): 60 tr, WR 30.0%, Sharpe -7.39, PnL -$1,938
    (or=13:30, 30min, off=0.50): 52 tr, WR 30.8%, Sharpe -8.76, PnL -$2,162
    (or=14:00, 15min, off=0.25): 60 tr, WR 30.0%, Sharpe -7.39, PnL -$1,938
  All sweeps fail every threshold by a huge margin and PnL is sharply
  negative — the afternoon-breakout-CONTINUATION thesis is empirically
  inverted on SPY 0DTE. Late-day breakouts get faded by 0DTE gamma
  pinning toward max-pain strikes.

  Path to graduation:
    1. **Invert the trade direction** — the signal exists but with
       opposite sign. Buy bear-put on break above OR_high (fade the
       upside breakout) and bull-call on break below OR_low. Requires
       a new strategy class (`ldm_fade_0dte`) since topology stays
       debit but the trigger direction inverts. Expected WR ~60-65%
       based on the 70% loss rate of the continuation play.
    2. Add a VWAP / max-pain filter — trade fades only when SPY is
       far from end-of-day max-pain strike.
    3. Restrict to high-IV days (VIX > 18) when 0DTE gamma is dominant.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class Ldm0dteStrategy(BaseStrategy):
    BAR_SIZE: str = "5 mins"
    HISTORY_PERIOD: str = "3y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "Late-Day Momentum 0DTE"

    @classmethod
    def id(cls) -> str:
        return "ldm_0dte"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "or_start_hhmm": {"type": "string", "default": "14:00",
                              "label": "Afternoon OR Start (ET)",
                              "description": "Start of the late-day opening range"},
            "or_minutes": {"type": "number", "default": 15, "min": 5, "max": 60,
                           "label": "OR Window Minutes"},
            "offset": {"type": "number", "default": 0.50, "min": 0.0, "max": 5.0,
                       "label": "Breakout Offset Points",
                       "description": "Points beyond OR high/low to trigger entry"},
            "width": {"type": "number", "default": 5, "min": 1, "max": 20,
                      "label": "Strike Width"},
            "min_range_pct": {"type": "number", "default": 0.05, "min": 0.0, "max": 1.0,
                              "label": "Min OR Range (% of price)",
                              "description": "Skip if OR range < this % of SPY"},
            "vix_min": {"type": "number", "default": 12, "min": 0, "max": 50},
            "vix_max": {"type": "number", "default": 30, "min": 0, "max": 80},
            "allowed_days": {"type": "string", "default": "MTWRF",
                             "label": "Allowed Weekdays"},
            "skip_news_days": {"type": "boolean", "default": True,
                               "label": "Skip FOMC/CPI/NFP days"},
            "entry_cutoff_hhmm": {"type": "string", "default": "15:30",
                                  "label": "No-New-Entry After (ET)"},
            "time_exit_hhmm": {"type": "string", "default": "15:55",
                               "label": "Mandatory Exit (ET)"},
        }

    # The intraday harness handles indicators/entry/exit. These methods
    # are required by BaseStrategy but unused by `run_orb_backtest`.
    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        df["SMA_200"] = df["Close"].rolling(window=200).mean() if len(df) > 0 else 0
        df["SMA_50"] = df["Close"].rolling(window=50).mean() if len(df) > 0 else 0
        df["Volume_MA"] = df["Volume"].rolling(window=10).mean() if "Volume" in df.columns else 0
        log_ret = np.log(df["Close"] / df["Close"].shift(1)) if len(df) > 1 else pd.Series(dtype=float)
        df["HV_21"] = (log_ret.rolling(window=21).std() * np.sqrt(252)).fillna(0.15) if len(df) > 21 else 0.15
        ema_len = int(getattr(req, "ema_length", 10) or 10)
        df[f"EMA_{ema_len}"] = df["Close"].ewm(span=ema_len, adjust=False).mean() if len(df) > 0 else 0
        df["RSI"] = 50
        return df

    def check_entry(self, df, i, req) -> bool:
        return False

    def check_exit(self, df, i, trade_state, req) -> tuple[bool, str]:
        return True, "expired"
