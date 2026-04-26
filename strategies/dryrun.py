import pandas as pd
import numpy as np
from datetime import time
from strategies.base import BaseStrategy

class DryRunStrategy(BaseStrategy):
    """
    Test strategy for 'dry run' verification.

    Logic:
    - Opens a trade exactly at specific times after market open.
    - Closes each trade exactly 5 minutes later.
    - Executes 3 times per day.

    Times (ET):
    1. Open 09:35, Close 09:40
    2. Open 12:00, Close 12:05
    3. Open 14:30, Close 14:35
    """

    # Intraday strategy — needs 5-minute ET-localised bars so that
    # ``ts.time()`` lines up with the entry/exit windows below.
    # 5d of yfinance 5-minute history is plenty of warm-up.
    BAR_SIZE: str = "5 mins"
    HISTORY_PERIOD: str = "5d"

    @property
    def name(self) -> str:
        return "Dry Run (Test)"

    @classmethod
    def id(cls) -> str:
        return "dryrun"

    @classmethod
    def get_schema(cls) -> dict:
        return {}

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        # No technical indicators needed for time-based test
        return df

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        if i < 1: return False
        
        row = df.iloc[i]
        # Handle both DatetimeIndex and 'Date' column
        ts = row.name if isinstance(df.index, pd.DatetimeIndex) else row.get("Date")
        if ts is None or not hasattr(ts, "time"):
            return False
            
        t = ts.time()
        
        # Entry windows (ET)
        entries = [
            time(9, 35),
            time(12, 0),
            time(14, 30)
        ]
        
        return t in entries

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        ts = row.name if isinstance(df.index, pd.DatetimeIndex) else row.get("Date")
        if ts is None or not hasattr(ts, "time"):
            return False, ""
            
        t = ts.time()
        
        # Exit windows (5 mins after entries)
        exits = [
            time(9, 40),
            time(12, 5),
            time(14, 35)
        ]
        
        if t in exits:
            return True, "test_complete"
            
        return False, ""
