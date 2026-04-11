import yfinance as yf
import pandas as pd
from typing import Tuple

def fetch_data(ticker: str = "AAPL", start_date: str = "2020-01-01", end_date: str = "2023-12-31") -> pd.DataFrame:
      """
      Fetches historical stock data using yfinance.

      Args:
          ticker: The stock ticker symbol.
          start_date: Start date for data retrieval.
          end_date: End date for data retrieval.

      Returns:
          A pandas DataFrame with OHLCV data.
      """
      print(f"--- Fetching data for {ticker} from {start_date} to {end_date} ---")
      try:
          data = yf.download(ticker, start=start_date, end=end_date)
          if data.empty:
              raise ValueError("Downloaded data is empty. Check ticker or dates.")
          return data.copy()
      except Exception as e:
          print(f"Error fetching data: {e}")
          return pd.DataFrame()

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
      """
      Calculates standard technical indicators (SMA, EMA).
      """
      if df.empty:
          return df

      print("--- Calculating indicators ---")
      df['SMA_50'] = df['Close'].rolling(window=50).mean()
      df['SMA_200'] = df['Close'].rolling(window=200).mean()
      df['EMA_12'] = df['Close'].ewm(span=12, adjust=False).mean()
      df['RSI_14'] = calculate_rsi(df['Close'], window=14) # Placeholder for RSI calculation

      return df

def calculate_rsi(series: pd.Series, window: int) -> pd.Series:
      """A basic placeholder for RSI calculation."""
      # In a real scenario, use a dedicated TA library (like ta-lib)
      print("Warning: Using a placeholder for RSI calculation.")
      rsi_series = pd.Series(index=series.index, dtype=float)
      # Dummy values to maintain structure
      rsi_series[:] = 50.0
      return rsi_series

  

