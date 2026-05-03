
New Changes (Need to be done)

1. Exit Logic

• ① Exit Signal: (From Strategy - TOP PRIORITY)

• ② Before Close on Day of expiration

• ③ TP / SL / TS: (Take Profit / Stop Loss / Trailing Stop)

2. Position Sizing

Make a selection list for position sizing options:

• ① Fixed n-contracts

• ② % Dynamic (Risk %)

• ③ Targeted Spread: (X% of capital with cap). If over cap, then use res n-contracts if possible.


Got it. Here is the transcription of your notes for sections 3 and 4:
## ③ Strategies
 * **①** Add config for all parms [parameters] for both strategies respectively.
 * **②** Add them to hyper tuner as well.
## ④ Scanner → New Module

 * It handles all the scanning on live data.
 * Loads preset and scan for signal.
 * Scan interval
 * Passes signal to Live / Paper Trading.
 * Scans for exit signal.
 * Passes signal.
#**Add this to both Live and Paper:**
 * Runs independent of Backtester.
 * Relies on presets for conditions.
 * Make sure always has preset before scan.
 * Shows preset conditions in Scanner col [column].
 * Also passes trade ticket data like position / size / risk etc. to ticketing.

