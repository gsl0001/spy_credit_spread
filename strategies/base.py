import pandas as pd
from abc import ABC, abstractmethod
import numpy as np


class BaseStrategy(ABC):
    # ── Bar-size contract ────────────────────────────────────────────────
    # These class attributes declare the bar resolution and lookback the
    # strategy needs. The scanner's bars-fetcher reads them via
    # ``cls.BAR_SIZE`` / ``cls.HISTORY_PERIOD`` and translates to the
    # appropriate yfinance interval/period or IBKR
    # barSizeSetting/durationStr — keeping cadence and bar size aligned
    # is an invariant of the strategy class, not a runtime config.
    #
    # ``BAR_SIZE`` uses the IBKR canonical form so it round-trips to
    # ``reqHistoricalDataAsync`` directly. Recognised values:
    #   "1 min", "5 mins", "15 mins", "30 mins", "1 hour", "1 day"
    #
    # ``HISTORY_PERIOD`` is yfinance period notation:
    #   "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"
    BAR_SIZE: str = "1 day"
    HISTORY_PERIOD: str = "1mo"

    # Lifecycle state per strategy-vetting skill. Three values:
    #   "pending"  — written but not yet vetted (default for new strategies)
    #   "shipped"  — backtest cleared the skill's topology-aware bar
    #   "rejected" — failed the bar; PresetStore.save will refuse to ship it
    # Subclasses override at class scope. The /api/strategies endpoint
    # surfaces this so the UI can hide rejected from execution dropdowns
    # while keeping them in the backtester for re-attempts.
    VETTING_RESULT: str = "pending"

    @property
    @abstractmethod
    def name(self) -> str:
        """Display name of the strategy."""
        pass
        
    @classmethod
    @abstractmethod
    def get_schema(cls) -> dict:
        """Return a JSON schema dict defining the specific configurable parameters for this strategy."""
        pass

    @abstractmethod
    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        """Add strategy-specific technical indicators to the dataframe."""
        pass

    @abstractmethod
    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        """Check for entry signal at bar i."""
        pass

    @abstractmethod
    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        """
        Check for exit signal at bar i.
        Returns: (should_exit: bool, reason: str)
        """
        pass
