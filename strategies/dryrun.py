import pandas as pd
import numpy as np
from datetime import time
from strategies.base import BaseStrategy

class DryRunStrategy(BaseStrategy):
    """
    Test strategy for 'dry run' verification.

    Logic:
    - Opens a trade every 10 minutes during regular trading hours (ET).
    - Closes each trade exactly 5 minutes later.

    Entry minutes (ET, 09:30 → 15:50):
        :00, :10, :20, :30, :40, :50
    Exit minutes (5 min after each entry):
        :05, :15, :25, :35, :45, :55
    """

    # Intraday strategy — needs 5-minute ET-localised bars so that
    # ``ts.time()`` lines up with the entry/exit windows below.
    # 5d of yfinance 5-minute history is plenty of warm-up.
    BAR_SIZE: str = "5 mins"
    HISTORY_PERIOD: str = "5d"
    VETTING_RESULT: str = "shipped"

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

        # Trade every 10 minutes during RTH (09:30 ET first valid bar is 09:30,
        # last entry 15:50 so the 15:55 exit lands before the close).
        if t.minute % 10 != 0 or t.second != 0:
            return False
        if t < time(9, 30) or t > time(15, 50):
            return False
        return True

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        ts = row.name if isinstance(df.index, pd.DatetimeIndex) else row.get("Date")
        if ts is None or not hasattr(ts, "time"):
            return False, ""
            
        t = ts.time()

        # Exit 5 minutes after each :00/:10/:20/:30/:40/:50 entry, i.e. on
        # the :05/:15/:25/:35/:45/:55 bar.
        if t.second == 0 and t.minute % 10 == 5 and time(9, 35) <= t <= time(15, 55):
            return True, "test_complete"
        return False, ""
