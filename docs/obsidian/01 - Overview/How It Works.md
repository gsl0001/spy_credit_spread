---
title: How It Works
tags: [overview, architecture]
---

# How It Works

> [!abstract] The mental model
> A **strategy** decides *when* to trade. A **topology** decides *what* option structure to trade. **Filters** veto bad setups. **Risk checks** veto bad orders. The chosen path is the same in backtest, paper, and live.

## The trading flow

```mermaid
flowchart TD
    Bar([New price bar arrives]) --> Ind[Compute indicators<br/>RSI, EMA, SMA, VIX, Regime]
    Ind --> Entry{Strategy<br/>says GO?}
    Entry -- No --> Wait[Wait for next bar]
    Entry -- Yes --> Filt{Filters<br/>all pass?}
    Filt -- No --> Wait
    Filt -- Yes --> Risk{Risk gates<br/>all pass?}
    Risk -- No --> Block[Block & log reason]
    Risk -- Yes --> Build[Build option topology<br/>e.g. vertical spread]
    Build --> Order[Submit order<br/>backtest sim / Alpaca / IBKR]
    Order --> Fill[Wait for fill]
    Fill --> Mon[Monitor every bar<br/>stop, target, trail, expiry]
    Mon --> Exit{Exit<br/>triggered?}
    Exit -- No --> Mon
    Exit -- Yes --> Close[Close position<br/>record P&L in journal]
    Close --> Wait
```

## The four moving parts

> [!info] These four pieces are independent
> Swap any one of them without touching the others.

### 1. Strategy — the *signal*

A class that answers two questions on every bar:
- `check_entry(...)` → should we open a trade?
- `check_exit(...)` → should we close it?

Built in: [[Consecutive Days]], [[Combo Spread]]. Add your own → [[Building Your Own]].

### 2. Topology — the *structure*

Once a signal fires, the **topology builder** picks strikes and prices the legs using Black-Scholes.

```mermaid
flowchart LR
    S[Signal fires<br/>direction = bull] --> T[Topology = vertical_spread]
    T --> L1[Leg 1: BUY 1 ATM call]
    T --> L2[Leg 2: SELL 1 OTM call]
    L1 & L2 --> N[Net debit = leg1 - leg2]
```

See [[Topology Overview]] for the full menu.

### 3. Filters — the *vetoes*

Before placing the trade, optional filters check market conditions:

- **RSI** — is it actually oversold/overbought?
- **EMA / SMA200** — is price aligned with trend?
- **Volume** — is there real participation?
- **VIX band** — is volatility in our comfort zone?
- **Regime** — is the broader market bullish/bearish/sideways as required?

See [[Entry Filters]].

### 4. Risk gates — the *firewall*

Even if everything else passes, the risk module can still kill the order. It checks:

- Market is currently open
- We aren't past the daily loss limit
- We don't already hold the max number of concurrent positions
- Today isn't a blackout day (FOMC, CPI, NFP)
- Buying power is sufficient

See [[Risk Mode]].

## Where each piece lives in code

```mermaid
flowchart TD
    UI[frontend/<br/>React 19 + Vite] -->|REST| API[main.py<br/>FastAPI]
    API --> ENG[Backtest engine<br/>main.py]
    API --> CORE[core/]
    API --> STRAT[strategies/]
    API --> ALP[paper_trading.py<br/>Alpaca]
    API --> IB[ibkr_trading.py<br/>IBKR TWS]

    CORE --> S1[settings.py]
    CORE --> S2[risk.py]
    CORE --> S3[monitor.py]
    CORE --> S4[journal.py<br/>SQLite]
    CORE --> S5[calendar.py]
    CORE --> S6[filters.py]
    CORE --> S7[chain.py]

    STRAT --> ST1[base.py]
    STRAT --> ST2[builder.py<br/>Black-Scholes]
    STRAT --> ST3[consecutive_days.py]
    STRAT --> ST4[combo_spread.py]
```

## The promise of *parity*

> [!success] Backtest = Scanner = Live
> The exact same `core/filters.py` runs in:
> 1. The historical [[Backtest Mode]] loop
> 2. The live [[Scanner Mode]] cron job
> 3. The [[Paper Mode]] and [[Live Mode]] auto-execute paths
>
> If your backtest says "go," the scanner would have said "go" too — assuming identical conditions.

---

Next: [[System Architecture]] · [[Installation]]
