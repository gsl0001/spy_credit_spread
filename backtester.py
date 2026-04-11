import pandas as pd
from typing import Dict, Any
from data_manager import calculate_indicators
from llm_adapter import get_llm_signal

class Backtester:
      def __init__(self, df: pd.DataFrame):
          self.df = df.copy()
          self.indicators_df = calculate_indicators(self.df)
          self.portfolio = {
              'cash': 100000.0,
              'shares': 0,
              'history': []
          }
          self.trades = []

      def _check_and_execute_trade(self, current_date: pd.Timestamp, signal: Dict[str, Any]):
          """Handles the logic of entering or exiting a position based on the signal."""

          action = signal['action']
          current_price = self.indicators_df.loc[self.indicators_df.index[self.indicators_df.index.get_loc(current_date)], 'Close']
          cash = self.portfolio['cash']
          shares_held = self.portfolio['shares']

          trade_details = {
              'Date': current_date,
              'Signal': action,
              'Price': current_price,
              'Confidence': signal['Confidence']
          }

          if signal['Confidence'] < 0.6:
               print(f"[{date.date()}] Skipping trade: Low confidence ({signal['Confidence']:.2f})")
               return

          if signal['Confidence'] >= 0.6:
              if signal['action'] == 'BUY' and shares_held == 0:
                  # Simple logic: Use 90% of available cash
                  buy_amount = cash * 0.90
                  shares_to_buy = int(buy_amount / current_price)

                  if shares_to_buy > 0:
                      cost = shares_to_buy * current_price
                      self.cash -= cost
                      self.shares_held += shares_to_buy
                      print(f"✅ BUY executed. Shares: {shares_to_buy}, Cost: ${cost:.2f}")
                      trade_details['Action'] = 'BUY'
                      trade_details['Shares'] = shares_to_buy
                  else:
                      print("⚠️ Not enough capital to buy shares.")

              elif signal['action'] == 'SELL' and shares_held > 0:
                  shares_to_sell = shares_held // 2  # Sell half the position
                  if shares_to_sell > 0:
                      revenue = shares_to_sell * current_price
                      self.cash += revenue
                      self.shares_held -= shares_to_sell
                      print(f"❌ SELL executed. Shares: {shares_to_sell}, Revenue: ${revenue:.2f}")
                      trade_details['Action'] = 'SELL'
                      trade_details['Shares'] = shares_to_sell
                  else:
                      print("ℹ️ Cannot sell: No shares held.")
              elif signal['action'] == 'HOLD':
                   print("🟢 HOLD action: Maintaining position.")
                   trade_details['Action'] = 'HOLD'


          self.trades.append(trade_details)


      def run_simulation(self):
          print("\n===================================================")
          print("        STARTING BACKTEST SIMULATION")
          print("===================================================")

          self.cash = 10000.00
          self.shares_held = 0
          self.trades = []

          for index, row in self.df.iterrows():
              date = index.date()
              print(f"\n--- Processing Date: {date} ---")

              # 1. Generate Buy/Sell/Hold signal based on indicator logic (simplified)
              # Placeholder logic: If MACD crosses up (Buy), if MACD crosses down (Sell)
              # In a real system, this would be a complex mathematical trigger.
              signal_action = 'HOLD'
              if row['MACD_Signal'] > 0 and row['MACD_Signal'] > (row['MACD_Signal'].shift(1) * 1.1):
                  signal_action = 'BUY'
              elif row['MACD_Signal'] < 0 and row['MACD_Signal'] < (row['MACD_Signal'].shift(1) * 0.9):
                  signal_action = 'SELL'

              # 2. Simulate the Trade Execution
              # NOTE: We are overriding the complex signal generation with a fixed structure for simplicity.
              simulated_signal = {
                  'action': signal_action,
                  'confidence': 0.8 if signal_action != 'HOLD' else 0.5,
                  'action_desc': signal_action
              }
              self._simulate_trade(simulated_signal, row['Close'])

          self.final_portfolio_value()

      def _simulate_trade(self, signal: dict, current_price: float):
          """Helper method to encapsulate the trading logic based on signals."""

          # This method needs to be robustly updated based on the actual logic defined by the indicators.
          # For this demo, we pass a simplified structure.

          # Placeholder implementation detail:
          self._simulate_trade_logic(signal, current_price)


      def _simulate_trade_logic(self, signal: dict, price: float):
          """The actual, simplified execution logic."""

          if signal['action'] == 'BUY':
              self._simulate_trade_logic.__globals__['self']._simulate_trade(signal, price)
          elif signal['action'] == 'SELL':
              self._simulate_trade_logic.__globals__['self']._simulate_trade(signal, price)
          elif signal['action'] == 'HOLD':
              self._simulate_trade_logic.__globals__['self']._simulate_trade(signal, price)


      def final_portfolio_value(self):
          """Calculates the final total value (Cash + Value of Shares)."""
          total_value = self.cash + (self.shares_held * self.df['Close'].iloc[-1])
          print("\n===================================================")
          print("              SIMULATION COMPLETE")
          print("===================================================")
          print(f"Final Cash: ${self.cash:,.2f}")
          print(f"Shares Held: {self.shares_held}")
          print(f"Last Close Price: ${self.df['Close'].iloc[-1]:,.2f}")
          print(f"Total Portfolio Value: ${total_value:,.2f}")

  # --- Main Execution Block ---
if __name__ == "__main__":
      import pandas as pd

      print("Loading dummy data...")
      # Creating dummy data frame mimicking OHLCV data over time
      data = {
          'Date': pd.to_datetime(['2023-01-01', '2023-01-02', '2023-01-03', '2023-01-04', '2023-01-05', '2023-01-06', '2023-01-07',
  '2023-01-07', '2023-01-07', '2023-01-07']),
          'Open': [100, 102, 101, 104, 103, 105, 106, 107, 108, 107],
          'High': [102, 103, 103, 105, 104, 106, 107, 108, 109, 108],
          'Close': [102, 103, 104, 105, 104, 106, 107, 108, 109, 108],
          'Volume': [1000, 1200, 1100, 1500, 1400, 1600, 1700, 1800, 1900, 1850],
          # Placeholder indicators (Simulating crossover signals)
          'MACD': [0.1, 0.2, 0.1, 0.3, 0.1, 0.2, 0.3, 0.35, 0.4, 0.35],
          'MACD_Signal': [0.1, 0.2, 0.1, 0.3, 0.2, 0.25, 0.3, 0.35, 0.4, 0.35]
      }
      df = pd.DataFrame(data)
      df.set_index('Date', inplace=True)

      # Instantiate and run
      backtester = Backtester(df)
      backtester.run_simulation()