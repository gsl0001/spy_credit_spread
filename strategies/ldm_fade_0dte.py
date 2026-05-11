"""LDM-Fade 0DTE — afternoon opening-range BREAKOUT FADE on SPY.

Sources:
  - Inverted signal from `strategies/ldm_0dte.py` rejection: continuation
    play had 30% WR / Sharpe -7.39, implying the opposite direction has
    ~65-70% WR.
  - Garleanu-Pedersen-Poteshman, "Demand-Based Option Pricing" (2009);
    Ni-Pearson-Poteshman (2005) on options-driven pinning. 0DTE flow has
    intensified the effect: late-day breakouts get faded as gamma
    hedging by dealers pushes price back toward max-pain strikes.
  - Reuters/JPM 2024 desk notes describing the "0DTE fade" — when SPY
    breaks an afternoon range late session, the most common outcome is
    reversion, not continuation.

Distinct from existing strategies:
  - `orb` is a morning-OR continuation play. This is an afternoon-OR
    REVERSAL play.
  - `ldm_0dte` (rejected) was the continuation version of the same
    afternoon trigger; this is the inverted twin.

Logic:
  Afternoon opening range:
    - Observe a 15-minute window starting at `or_start_time`
      (default 14:00 ET).
    - Compute OR high / OR low.

  Entry (FADE):
    - When SPY closes ABOVE OR_high → enter BEAR_PUT debit spread
      (fade the upside breakout, betting on reversion below OR_high).
    - When SPY closes BELOW OR_low → enter BULL_CALL debit spread
      (fade the downside break, betting on reversion above OR_low).
    - Engine handles the inversion via `fade_mode=True` config flag.

  Exit:
    - take_profit_pct (default 50%)
    - stop_loss_pct (default 50%)
    - time_exit (default 15:55) — never carry 0DTE to last 5 min.

Topology: 0DTE debit spread (direction selected dynamically by engine).
Reuses `core/backtest_orb.py` engine with `fade_mode=True` and
`or_start_time=14:00`.

VETTING RESULT (2026-05-10): REJECTED.
  Tested SPY 60d (yfinance 5m cap), $5-wide 0DTE.
  4 sweeps:
    (15min, off=0.50, 50/50)        : 60 tr, WR 45.0%, Sh -2.95, PnL -$774
    (30min, off=0.50, 50/50)        : 52 tr, WR 50.0%, Sh -0.76, PnL -$188
    (15min, off=1.00, 50/50)        : 52 tr, WR 46.2%, Sh -2.45, PnL -$570
    (30min, off=0.50, SL=40/TP=80)  : 52 tr, WR 48.1%, Sh -0.70, PnL -$182
  All sweeps fail WR≥45% (some pass) AND PF≥1.5, Sharpe≥1.0, positive
  PnL — fade direction does materially better than continuation
  (−$188 vs −$1,938) but still loses.

  Root cause: the implied "70% complement WR" from continuation's 30%
  doesn't materialize because TP/SL bracketing eats both sides — when
  the fade is right the breakout often retraces only partially (no TP
  hit, time-exit at small gain), and when wrong it continues to 50% SL.
  Per-trade EV is roughly zero net of commission.

  Path to graduation:
    1. Replace symmetric debit with **0DTE iron condor** centered at
       SPY's 14:00 close — wins on pinning regardless of which side
       breaks. Engine work required (no condor support today).
    2. Add a max-pain magnet filter: only fade when SPY is far from
       weekly max-pain strike. Requires options-flow data ingestion.
    3. Use a tighter time-of-day window (e.g., 15:00-15:30 entry,
       exit 15:50) where 0DTE gamma is most concentrated. Try as a
       sweep before deeper engine work.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class LdmFade0dteStrategy(BaseStrategy):
    BAR_SIZE: str = "5 mins"
    HISTORY_PERIOD: str = "3y"
    VETTING_RESULT: str = "rejected"

    @property
    def name(self) -> str:
        return "LDM-Fade 0DTE"

    @classmethod
    def id(cls) -> str:
        return "ldm_fade_0dte"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "or_start_hhmm": {"type": "string", "default": "14:00",
                              "label": "Afternoon OR Start (ET)"},
            "or_minutes": {"type": "number", "default": 15, "min": 5, "max": 60,
                           "label": "OR Window Minutes"},
            "offset": {"type": "number", "default": 0.50, "min": 0.0, "max": 5.0,
                       "label": "Breakout Offset Points"},
            "width": {"type": "number", "default": 5, "min": 1, "max": 20,
                      "label": "Strike Width"},
            "min_range_pct": {"type": "number", "default": 0.05, "min": 0.0, "max": 1.0,
                              "label": "Min OR Range (% of price)"},
            "vix_min": {"type": "number", "default": 12, "min": 0, "max": 50},
            "vix_max": {"type": "number", "default": 30, "min": 0, "max": 80},
            "allowed_days": {"type": "string", "default": "MTWRF",
                             "label": "Allowed Weekdays"},
            "skip_news_days": {"type": "boolean", "default": True},
            "entry_cutoff_hhmm": {"type": "string", "default": "15:30"},
            "time_exit_hhmm": {"type": "string", "default": "15:55"},
        }

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
