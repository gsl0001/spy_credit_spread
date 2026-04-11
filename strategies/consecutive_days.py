import pandas as pd
import numpy as np
from strategies.base import BaseStrategy

class ConsecutiveDaysStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return "Consecutive Days"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "entry_red_days": {"type": "number", "default": 2, "min": 1, "label": "Entry Streak Days"},
            "exit_green_days": {"type": "number", "default": 2, "min": 1, "label": "Exit Streak Days"}
        }

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        df['is_green'] = df['Close'] > df['Open']
        df['is_red']   = df['Close'] < df['Open']

        def streak(col):
            s = col.astype(int)
            group = (col != col.shift()).cumsum()
            return s.groupby(group).cumsum().where(col, 0)

        df['greenDays'] = streak(df['is_green'])
        df['redDays']   = streak(df['is_red'])
        
        # Base indicators needed for filtering and regime
        df[f'EMA_{req.ema_length}'] = df['Close'].ewm(span=req.ema_length, adjust=False).mean()
        df['SMA_200'] = df['Close'].rolling(window=200).mean()
        df['SMA_50']  = df['Close'].rolling(window=50).mean()
        df['Volume_MA'] = df['Volume'].rolling(window=10).mean()

        log_ret = np.log(df['Close'] / df['Close'].shift(1))
        df['HV_21'] = (log_ret.rolling(window=21).std() * np.sqrt(252)).fillna(0.15)

        delta = df['Close'].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df['RSI'] = (100 - (100 / (1 + rs))).fillna(50)
        
        return df

    def check_entry(self, df: pd.DataFrame, i: int, req) -> bool:
        row = df.iloc[i]
        is_bear = req.strategy_type == "bear_put"
        
        if is_bear:
            streak_val = int(row['greenDays'])
        else:
            streak_val = int(row['redDays'])
            
        # Entry logic matches previous main.py
        entry_trigger = (streak_val == req.entry_red_days or streak_val == req.entry_red_days + 1)
        return entry_trigger

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        is_bear = req.strategy_type == "bear_put"
        
        days_held = i - trade_state['entry_idx']
        new_dte = trade_state['entry_dte'] - days_held
        
        exit_streak = int(row['redDays'] if is_bear else row['greenDays']) >= req.exit_green_days
        expired = new_dte <= 0
        
        if exit_streak: return True, "streak"
        if expired: return True, "expired"
        
        return False, ""
