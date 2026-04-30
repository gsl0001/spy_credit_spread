---
title: Risk Mode
tags: [mode, risk, safety]
---

# Risk Mode

> [!abstract] The firewall
> Risk Mode shows you the **pre-trade gate** that every order must pass — *before* the broker sees it. If any check fails, the order is rejected and the reason is logged.

## The five gates

```mermaid
flowchart TD
    O[Order proposed] --> G1{Market<br/>open?}
    G1 -- no --> Stop[REJECT — market closed]
    G1 -- yes --> G2{Daily loss<br/>under cap?}
    G2 -- no --> Stop2[REJECT — daily loss limit]
    G2 -- yes --> G3{Concurrent<br/>positions OK?}
    G3 -- no --> Stop3[REJECT — too many open]
    G3 -- yes --> G4{Event<br/>blackout?}
    G4 -- yes --> Stop4[REJECT — FOMC/CPI/NFP]
    G4 -- no --> G5{Buying power<br/>sufficient?}
    G5 -- no --> Stop5[REJECT — insufficient BP]
    G5 -- yes --> Send[FORWARD to broker]
```

## What you see on the screen

| Tile | What it tells you |
|------|-------------------|
| **Concurrent cap** | "1 of 2 used" |
| **Daily loss** | "Today: -0.4%, cap: -2.0%" |
| **Market hours** | "Open" / "Closed" |
| **Event blackout** | "Clear" / "FOMC at 2pm" |
| **Buying power utilization** | "23% used" |

Each tile is **green** when safe, **yellow** approaching limit, **red** when blocked.

## The five gates explained

### 1. Market hours

Source: `core/calendar.py` — checks NYSE schedule, US holidays, half days. The platform refuses to trade outside RTH (regular trading hours) by default.

### 2. Daily loss limit

Source: `core/risk.py` reading from `core/journal.py`. Sums today's realized P&L. If it exceeds `DAILY_LOSS_LIMIT_PCT` of equity (default **2 %**), all new orders are blocked until tomorrow.

> [!warning] Why this matters
> Without a daily loss cap, a bad signal can compound losses across the day. The cap is a circuit breaker.

### 3. Concurrent position cap

Default `MAX_CONCURRENT_POSITIONS=2`. Stops you from opening a third position while two are still active.

> [!info] Why a cap?
> Correlation. SPY-only strategies are highly correlated trade-to-trade. Three positions = one big bet. Two = two half bets.

### 4. Event blackout

Source: `core/calendar.py` reading `config/events_2026.json`. The file lists FOMC meetings, CPI prints, NFP releases. The platform refuses to open new positions on those days (configurable).

```mermaid
flowchart LR
    File[events_2026.json] --> Cal[core/calendar.py]
    Cal --> Q{Today on list?}
    Q -- yes --> Block[Block new entries]
    Q -- no --> OK[Allow]
```

> [!tip] Update the file annually
> Drop a new `events_2027.json` in `config/` and update `EVENT_CALENDAR_FILE` in `.env`.

### 5. Buying power

Reads broker buying power. Refuses orders that would exceed it. For Margin/PortfolioMargin accounts, IBKR computes the requirement.

## Tuning the gates

```bash
# config/.env
MAX_CONCURRENT_POSITIONS=2
DAILY_LOSS_LIMIT_PCT=2.0
DEFAULT_STOP_LOSS_PCT=50.0
DEFAULT_TAKE_PROFIT_PCT=50.0
DEFAULT_TRAILING_STOP_PCT=0.0
FILL_TIMEOUT_SECONDS=30
MONITOR_INTERVAL_SECONDS=15
LIMIT_PRICE_HAIRCUT=0.05
```

> [!example] Tighter risk for new strategies
> Until a strategy proves itself, run with:
> - `MAX_CONCURRENT_POSITIONS=1`
> - `DAILY_LOSS_LIMIT_PCT=1.0`
>
> Loosen only after the journal shows consistent green months.

## What happens on a block

```mermaid
sequenceDiagram
    participant UI
    participant API
    participant R as Risk gate
    participant J as Journal

    UI->>API: POST /api/ibkr/execute
    API->>R: Pre-trade check
    R-->>API: BLOCK + reason
    API->>J: Insert event(type=risk_block, reason)
    API-->>UI: 400 + reason
    UI->>UI: Show toast with reason
```

The audit trail in [[Journal Mode]] will show the rejection so you can audit *why* trades didn't fire later.

## Kill switch — the override

When everything is wrong, the **kill switch** bypasses normal flow:

- `POST /api/ibkr/flatten_all` — close all IBKR positions
- `POST /api/paper/kill_switch` — close all Alpaca positions

Both endpoints also stop the scanner so no new orders fire.

---

Next: [[Live Mode]] · [[Journal Mode]]
