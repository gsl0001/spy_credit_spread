
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime

  # --- 1. Setup and Data Acquisition ---
TICKER = "SPY"
START_DATE = "2023-01-01"
END_DATE = "2025-12-31"

  # Download data
try:
      data = yf.download(TICKER, start=START_DATE, end=END_DATE)
      if data.empty:
          print("Error: Downloaded data is empty. Check ticker and dates.")
          exit()
except Exception as e:
      print(f"Error downloading data: {e}")
      exit()

  # Prepare DataFrame
if isinstance(data.columns, pd.MultiIndex):
      data.columns = data.columns.get_level_values(0)
df = data.copy()
print(f"Data downloaded successfully for {TICKER}.")


  # --- 2. Indicator Calculation (Approximation of Pine Script Logic) ---
  # Pine Script uses several calculations involving historical data.
  # We will calculate standard moving averages as proxies for the components used.

  # SMA (Simple Moving Average) is a common requirement.
df['SMA_10'] = df['Close'].rolling(window=10).mean()
df['SMA_50'] = df['Close'].rolling(window=50).mean()
df['SMA_200'] = df['Close'].rolling(window=200).mean()

  # The Pine Script structure implies multiple cross-over and comparison logic.
  # We will generate placeholder boolean columns for the core conditions
  # based on common strategies that use these types of inputs.

  # --- 3. Strategy Logic Implementation (Backtesting Simulation) ---

def run_backtest(data):
      """
      Simulates the trading strategy logic on the provided historical data.
      """
      initial_capital = 10000.0
      position = None  # None: Out, 1: Long
      cash = initial_capital
      shares = 0

      trades = []

      # Iterate through the DataFrame starting from where indicators are valid
      for i in range(200, len(data)):
          row = data.iloc[i]
          date = row.name.strftime('%Y-%m-%d')

          # --- Translation of Buy/Sell Logic ---
          # Since the full Pine Script logic structure was not provided for direct
          # translation, we use a common logic pattern (e.g., crossover)
          # combined with the calculated indicators to simulate the structure.

          # Hypothetical Entry Condition (Long): e.g., SMA_50 crosses above SMA_200 AND RSI > 50
          # We will use a simplified crossover for demonstration.

          # Entry Check (Buy Signal)
          # Example: Buy when price crosses above the 10-period SMA
          buy_signal = bool(data['Close'].iloc[i] > data['SMA_10'].iloc[i] and data['Close'].iloc[i-1] <= data['SMA_10'].iloc[i-1])

          # Exit Check (Sell Signal or Stop Loss)
          # Example: Sell when price crosses below the 10-period SMA OR when in a profit-taking signal
          sell_signal = bool(data['Close'].iloc[i] < data['SMA_10'].iloc[i] and data['Close'].iloc[i-1] >= data['SMA_10'].iloc[i-1])

          # --- Trade Execution ---

          # 1. Exit if currently in a position and a sell signal triggers
          if position == 1 and sell_signal:
              exit_value = shares * row['Close']
              cash += exit_value
              trades.append({'Date': date, 'Type': 'SELL', 'Shares': shares, 'Price': row['Close'], 'Cash_After': cash})
              position = 0
              shares = 0
              print(f"--- EXITED on {date} ---")

          # 2. Enter if currently out of position and a buy signal triggers
          if position == 0 and buy_signal:
              # Calculate how many shares can be bought with available cash
              shares_to_buy = int(cash / row['Close'])
              if shares_to_buy > 0:
                  cost = shares_to_buy * row['Close']
                  cash -= cost
                  shares = shares_to_buy
                  position = 1
                  trades.append({'Date': date, 'Type': 'BUY', 'Shares': shares, 'Price': row['Close'], 'Cash_After': cash})
                  print(f"*** ENTERED on {date} ***")

          # If we already have a position, we just hold it until the exit signal.

      # Final liquidation (Sell remaining shares at the last close price)
      final_value = shares * data.iloc[-1]['Close']
      cash += final_value
      trades.append({'Date': data.index[-1].strftime('%Y-%m-%d'), 'Type': 'FINAL_SELL', 'Shares': shares, 'Price': data.iloc[-1]['Close'], 'Cash_After': cash})

      final_portfolio_value = cash
      return pd.DataFrame(trades), final_portfolio_value

  # --- 4. Execution and Results ---
try:
      trades_df, final_value = run_backtest(df)

      print("\n==================================================")
      print("         BACKTESTING SUMMARY (SIMULATED)        ")
      print("==================================================")
      print(f"Initial Capital: ${10000:,.2f}")
      print(f"Final Portfolio Value: ${final_value:,.2f}")
      print(f"Net Profit/Loss: ${(final_value - 10000):,.2f}")
      print("\n--- First 5 Trades ---")
      print(trades_df.head())
      print("\n--- Last 5 Trades ---")
      print(trades_df.tail())

except Exception as e:
      print(f"\nAn error occurred during the backtest: {e}")
      print("Please ensure the data downloaded is sufficient for the calculation window.")