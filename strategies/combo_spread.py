import pandas as pd
import numpy as np
from strategies.base import BaseStrategy

class ComboSpreadStrategy(BaseStrategy):
    VETTING_RESULT: str = "shipped"

    @property
    def name(self) -> str:
        return "Combo Spread"

    @classmethod
    def get_schema(cls) -> dict:
        return {
            "combo_sma1": {"type": "number", "default": 3, "min": 1, "label": "SMA 1"},
            "combo_sma2": {"type": "number", "default": 8, "min": 1, "label": "SMA 2"},
            "combo_sma3": {"type": "number", "default": 10, "min": 1, "label": "SMA 3"},
            "combo_ema1": {"type": "number", "default": 5, "min": 1, "label": "EMA 1"},
            "combo_ema2": {"type": "number", "default": 3, "min": 1, "label": "EMA 2"},
            "combo_max_bars": {"type": "number", "default": 10, "min": 1, "label": "Max Bars Hold"},
            "combo_max_profit_closes": {"type": "number", "default": 5, "min": 1, "label": "Max Profit Closes"}
        }

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
        if i < 1:
            return False

        row = df.iloc[i]
        prev = df.iloc[i - 1]

        # Skip warmup bars where strategy indicators are still NaN
        if pd.isna(row['sma_03']) or pd.isna(prev['sma_02']):
            return False

        is_bear = (
            getattr(req, 'direction', '') == 'bear'
            or getattr(req, 'strategy_type', '') == 'bear_put'
        )

        # Outside-body test — close breaches prior candle's body (engulf/gap)
        outside_body = (
            row['Close'] < min(prev['Open'], prev['Close'])
            or row['Close'] > max(prev['Open'], prev['Close'])
        )

        if is_bear:
            # Bearish momentum start → buy puts / put spread expecting down move
            e1 = (
                row['Close'] > row['sma_03']
                and row['Close'] >= row['sma_01']
                and prev['Close'] < prev['sma_01']
                and row['Open'] < row['ema_01']
                and row['sma_02'] > prev['sma_02']
            )
            e2 = (
                row['Close'] > row['ema_02']
                and row['Open'] < row['ohlc_avg']
                and row['Volume'] <= prev['Volume']
                and outside_body
            )
        else:
            # Bullish reversal after weakness → buy calls / call spread
            e1 = (
                row['Close'] < row['sma_03']
                and row['Close'] <= row['sma_01']
                and prev['Close'] > prev['sma_01']
                and row['Open'] > row['ema_01']
                and row['sma_02'] < prev['sma_02']
            )
            e2 = (
                row['Close'] < row['ema_02']
                and row['Open'] > row['ohlc_avg']
                and row['Volume'] <= prev['Volume']
                and outside_body
            )

        return bool(e1 or e2)

    def check_exit(self, df: pd.DataFrame, i: int, trade_state: dict, req) -> tuple[bool, str]:
        row = df.iloc[i]
        is_bear = (
            getattr(req, 'direction', '') == 'bear'
            or getattr(req, 'strategy_type', '') == 'bear_put'
        )

        max_bars = int(getattr(req, 'combo_max_bars', 10))
        max_profit_closes = int(getattr(req, 'combo_max_profit_closes', 5))

        days_held = i - trade_state['entry_idx']
        new_dte = trade_state['entry_dte'] - days_held

        # 1. DTE expiry — must exit before Black-Scholes T hits zero
        if new_dte <= 0:
            return True, "expired"

        # 2. Favourable-close profit take — O(1) incremental counter persisted
        #    in trade_state across exit checks for the lifetime of this trade
        entry_price = trade_state['entry_price']
        favourable = (
            row['Close'] < entry_price if is_bear
            else row['Close'] > entry_price
        )
        trade_state['profit_closes'] = trade_state.get('profit_closes', 0) + (
            1 if favourable else 0
        )

        if trade_state['profit_closes'] >= max_profit_closes:
            return True, "max_profit_closes"

        # 3. Time stop
        if days_held >= max_bars:
            return True, "max_bars"

        return False, ""
