Here's a detailed implementation plan for your custom Python bot using Interactive Brokers (IBKR) API. This follows the core logic from the SSRN paper "Regime-Conditional Alpha in SPY 0DTE Opening Range Breakout Strategies" (paper ID 6355218).
The strategy uses debit vertical spreads on SPY 0DTE options triggered by a 5-minute Opening Range Breakout (ORB), with the paper's recommended filters and strike selection (long strike placed 0.96 to 2.00 points from the breakout price for highest EV).
1. High-Level Strategy Logic (for your coding agent)
Objective: Trade only high-probability filtered 0DTE debit vertical spreads on SPY.

Bullish signal: SPY breaks above the 5-min Opening Range High → Buy Bull Call Debit Spread.
Bearish signal: SPY breaks below the 5-min Opening Range Low → Buy Bear Put Debit Spread.

Key Filters (from the paper — critical for lifting win rate from ~47% to ~65%):

Day of week: Only Monday, Wednesday, Friday.
VIX regime: VIX between 15 and 25 (inclusive).
No major macroeconomic events on that day (e.g., FOMC, CPI, NFP — you can hardcode a list or fetch from economic calendar API).
Max 1 trade per day.

Opening Range:

Use first 5 minutes of regular trading hours (9:30:00 – 9:35:00 ET).
OR High = highest high in that window.
OR Low = lowest low in that window.
Only proceed if range size ≥ minimum threshold (e.g., 0.05%–0.10% of price) as a volatility filter.

Entry Timing:

Monitor for breakout after 9:35 ET.
First valid breakout of the day only (ignore subsequent ones).
Enter immediately on breakout (or with small confirmation, e.g., close above/below).

Strike Selection (Most Important Part from the Paper):

At breakout moment, record the breakout price (current SPY price when it crosses OR level).
For Bull Call Debit Spread:
Long call strike = round(breakout_price + offset) where offset is in 0.96 to 2.00 (paper's best EV zone; recommend starting with 1.50 as midpoint).
Short call strike = long strike + width (common: $5 or $6 wide on SPY for good liquidity).

For Bear Put Debit Spread (symmetric):
Long put strike = round(breakout_price - offset)  (same 0.96–2.00 range).
Short put strike = long strike - width.

Use nearest whole dollar strikes (SPY has $1 increments).
Prefer the combination that gives reasonable net debit (check bid/ask before placing).

Position Sizing:

Risk fixed % of account per trade (e.g., 1–2% max loss = net debit paid).
Or fixed dollar risk (e.g., $200–500 per spread).

Exits (Paper uses 25%/50% rules in simulation):

Profit target: Close when spread value reaches +25% to +50% of net debit paid (or use fixed RR like 1:2).
Stop loss: Close when spread loses 50% of debit (or full debit if aggressive).
Time-based exit: Close all positions by 15:30–15:45 ET (avoid holding into close due to gamma/theta).
Optional: Trail or monitor delta/gamma if you want sophistication.

Risk Management:

Max 1 trade/day.
No trading on high-impact news days.
Check bid-ask spread before entry (avoid wide markets).
Log everything: OR levels, breakout price, strikes chosen, net debit, P/L.