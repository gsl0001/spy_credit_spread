---
title: Glossary
tags: [glossary, reference]
---

# Glossary

> [!abstract] Plain-English definitions
> Hover over a term in any other note and Obsidian shows the start of this page. Click the wikilink to jump here.

---

## A

**Alpaca** — Free brokerage with a great paper-trading API. Used for [[Paper Mode]].

**APScheduler** — Python library that runs background jobs on a cron-like schedule. Powers the [[Scanner Mode]] and `core/monitor.py`.

**ATM** — *At The Money*. An option whose strike equals the current spot price.

**Audit trail** — The stream of events written to `data/trades.db` so you can replay what happened.

**Auto-execute** — Toggle that, when ON, lets the [[Scanner Mode]] place real orders the moment a signal fires.

---

## B

**BAG combo** — IBKR's wire format for multi-leg orders. The whole bag fills or none of it does.

**Backtest** — Simulating a strategy on historical data. See [[Backtest Mode]].

**Black-Scholes** — Closed-form European option pricing model. Used for backtest pricing.

**Blackout day** — A day on which the [[Risk Mode]] gate blocks new entries. Configured in `config/events_2026.json`.

**Breakeven** — The price at which a trade neither makes nor loses money at expiry.

**Bull / Bear / Sideways** — Market regime labels — see [[Entry Filters]].

---

## C

**`check_entry`** — Strategy method that returns `True` if a new trade should open on the current bar. See [[Building Your Own]].

**`check_exit`** — Strategy method that returns `(True, reason)` if an open trade should close.

**Combo order** — See **BAG combo**.

**Concurrent cap** — Maximum number of simultaneously-open positions. Default 2.

---

## D

**Daily loss limit** — Risk gate that blocks trading once today's P&L is below `-DAILY_LOSS_LIMIT_PCT` of equity.

**Debit** — You pay net premium to enter the trade. Positive `net_cost`.

**Delta** — Sensitivity of an option's price to a $1 move in spot.

**DTE** — *Days To Expiration*. The single most important number in options.

---

## E

**EMA** — *Exponential Moving Average*. Reacts faster than SMA to price changes.

**Equity surrogate** — Buying SPY shares as a stand-in for an option fill in [[Paper Mode]].

**Event blackout** — See **Blackout day**.

**Expiry** — The date an option contract dies. SPY options expire Mon/Wed/Fri.

---

## F

**FastAPI** — The Python web framework powering `main.py`. Gives you `/docs` Swagger UI for free.

**FCFS** — *First Come First Served*. How risk gates work — first trigger wins.

**Fill** — A confirmation from the broker that some or all of an order's quantity was executed.

**Fill watcher** — `core/fill_watcher.py` — a state machine that monitors pending orders for fills or timeouts.

**Filter parity** — The promise that the same `core/filters.py` runs in backtest, scanner, and live. See [[Entry Filters]].

**FOMC** — Federal Open Market Committee. Their meeting days are typical blackout days.

---

## G

**Gamma** — Sensitivity of delta to spot price. Largest near ATM near expiry.

**Greeks** — Delta, gamma, theta, vega, rho — sensitivities of option price to inputs.

---

## H

**Heartbeat** — A periodic ping that confirms a connection is alive. IBKR heartbeat = 15 s.

**Holidays** — Market-closed days. Tracked in `core/calendar.py`.

---

## I

**IB Gateway** — Headless version of TWS. Lighter, no charts.

**IBKR** — Interactive Brokers. The platform's live broker. See [[Live Mode]].

**Idempotency key** — A unique key per logical action that prevents duplicate orders if the request is retried.

**Indicator** — A computed column on the price dataframe (RSI, EMA, SMA, etc.).

**Iron Condor** — 4-leg neutral premium-collection topology. See [[Iron Condor]].

**IV** — *Implied Volatility*. The market's forecast of future volatility. Realized = what actually happened.

---

## J

**Journal** — SQLite ledger at `data/trades.db`. Source of truth. See [[Journal Mode]].

---

## K

**Kelly %** — The theoretical optimal trade size given win rate and R. Always use a fraction (¼ to ½). See [[Metrics Explained]].

**Kill switch** — Big red button that closes all positions and stops the scanner.

---

## L

**Leader election** — `core/leader.py` ensures only one process runs the scanner. Uses `fcntl.flock`.

**Leg** — A single option contract within a multi-leg topology.

**Limit order** — Order with a max price (buy) or min price (sell). Won't fill outside that.

---

## M

**Market hours** — Regular trading hours (RTH). Risk gate blocks orders outside RTH.

**Mark-to-market** — Re-pricing an open position at the current market value every bar.

**Max drawdown** — Largest peak-to-trough decline in equity. See [[Metrics Explained]].

**Midpoint** — The average of bid and ask. Default limit price for combo orders.

**Monte Carlo** — Resampling trades to estimate the distribution of possible outcomes.

---

## N

**Net cost** — Sum of leg prices. Positive = debit (you pay), negative = credit (you receive).

**NFP** — Non-Farm Payrolls. A monthly economic release that often moves markets.

---

## O

**OHLC** — Open, High, Low, Close — the four bars in a candlestick.

**Optimizer** — Grid search across two parameters. See [[Backtest Mode]].

**OTM** — *Out of the Money*. A call with strike > spot, or a put with strike < spot.

---

## P

**Paper trading** — Trading with fake money to validate signals. See [[Paper Mode]].

**Parity** — See **Filter parity**.

**Position sizing** — Deciding how many contracts to trade. Three modes: fixed, dynamic %, targeted spread %.

**Preset** — A saved combination of strategy + filters + risk. Built-ins + user-defined.

**Profit factor** — Sum of winning trades / sum of losing trades. See [[Metrics Explained]].

---

## Q

**Quote** — Bid and ask snapshot from the broker.

---

## R

**Reconciliation** — Comparing live signals to what the backtest engine would have produced. See [[Journal Mode]].

**Regime** — Market label: bull / bear / sideways. Computed from SMA50 vs SMA200 vs price.

**Roll** — Closing an expiring position and opening a similar one further out.

**RSI** — *Relative Strength Index*. Momentum oscillator, 0–100. < 30 = oversold, > 70 = overbought.

**RTH** — *Regular Trading Hours* — 9:30am to 4:00pm Eastern.

---

## S

**Scanner** — Background job that re-runs the strategy on live data. See [[Scanner Mode]].

**Sharpe ratio** — Risk-adjusted return. Higher = better risk-adjusted performance.

**Slippage** — Difference between expected and actual fill price.

**SMA** — *Simple Moving Average*.

**Sortino ratio** — Sharpe using only downside deviation.

**Spread** — A multi-leg position. Most often refers to a vertical spread.

**Stop loss** — Auto-close trigger when unrealized loss exceeds a threshold. See [[Exit Controls]].

**Straddle** — Long ATM call + long ATM put. See [[Straddle]].

---

## T

**Take profit** — Auto-close trigger when unrealized gain exceeds a threshold.

**Theta** — Daily decay of option premium. The time-value tax.

**Topology** — The shape of a multi-leg trade. See [[Topology Overview]].

**Trailing stop** — Stop that follows price up, locking in profit as the trade goes in your favor.

**TWS** — *Trader Workstation*. IBKR's desktop app.

---

## U

**Unrealized P&L** — Profit or loss on open positions, marked to current market.

**Underlying** — The asset the option is on. SPY for this platform.

---

## V

**Vega** — Sensitivity of option price to a 1% change in IV.

**Vertical spread** — Two legs, same expiry, different strikes. See [[Vertical Spread]].

**VIX** — Volatility index. The market's "fear gauge." See [[Entry Filters]].

---

## W

**Walk-forward** — Out-of-sample validation by training on rolling windows.

**Win rate** — % of trades that closed profitable. Useful only with profit factor and avg win/loss.

---

## Y

**yfinance** — Free Yahoo Finance Python wrapper. Source of historical bars and live quotes.

---

Next: [[Start Here|Back to home]]
