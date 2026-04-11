import pandas as pd
import numpy as np
from strategies.base import BaseStrategy

class ComboSpreadStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return "Combo Spread"

    def compute_indicators(self, df: pd.DataFrame, req) -> pd.DataFrame:
        df = df.copy()
        
        # Combo Spread Specific Indicators (with defaults or from req)
        sma1_p = getattr(req, 'combo_sma1', 3)
        sma2_p = getattr(req, 'combo_sma2', 8)
        sma3_p = getattr(req, 'combo_sma3', 10)
        ema1_p = getattr(req, 'combo_ema1', 5)
        ema2_p = getattr(req, 'combo_ema2', 3)

        df['sma_01'] = df['Close'].rolling(window=sma1_p).mean()
        df['sma_02'] = df['Close'].rolling(window=sma2_p).mean()
        df['sma_03'] = df['Close'].rolling(window=sma3_p).mean()
        df['ema_01'] = df['Close'].ewm(span=ema1_p, adjust=False).mean()
        df['ema_02'] = df['Close'].ewm(span=ema2_p, adjust=False).mean()
        df['ohlc_avg'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4.0
        
        # Base indicators for filters
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
        if i < 1: return False
        
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        # Entry 01
        e1 = (row['Close'] < row['sma_03'] and 
              row['Close'] <= row['sma_01'] and 
              prev['Close'] > prev['sma_01'] and 
              row['Open'] > row['ema_01'] and 
              row['sma_02'] < prev['sma_02'])
        
        # Entry 02
        e2 = (row['Close'] < row['ema_02'] and 
              row['Open'] > row['ohlc_avg'] and 
              row['Volume'] <= prev['Volume'] and 
              (row['Close'] < min(prev['Open'], prev['Close']) or 
               row['Close'] > max(prev['Open'], prev['Close'])))
        
        return bool(e1 or e2)

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        days_held = i - trade_state['entry_idx']
        
        # Combo specific exits
        max_bars = getattr(req, 'combo_max_bars', 10)
        max_profit_closes = getattr(req, 'combo_max_profit_closes', 5)
        
        # Track profit closes manually to avoid state persistence in strategy
        # We look back from entry to now to count profit closes
        if days_held > 1:
            profit_closes = 0
            for j in range(trade_state['entry_idx'] + 1, i + 1):
                if df.iloc[j]['Close'] > trade_state['entry_price']:
                    profit_closes += 1
            
            if profit_closes >= max_profit_closes:
                return True, "max_profit_closes"
        
        if days_held >= max_bars:
            return True, "max_bars"
            
        return False, ""
