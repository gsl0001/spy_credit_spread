import pandas as pd
from abc import ABC, abstractmethod
import numpy as np

class BaseStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Display name of the strategy."""
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
