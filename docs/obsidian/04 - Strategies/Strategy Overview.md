---
title: Strategy Overview
tags: [strategy, overview]
---

# Strategy Overview

> [!abstract] In one line
> A **strategy** is a small Python class that says "yes, enter now" or "yes, exit now" on every bar.

## The contract

Every strategy implements four methods:

```python
class BaseStrategy(ABC):
    name: str

    @classmethod
    def get_schema(cls) -> dict: ...

    def compute_indicators(self, df, req) -> pd.DataFrame: ...

    def check_entry(self, df, i, req) -> bool: ...

    def check_exit(self, df, i, state, req) -> tuple[bool, str]: ...
```

That's it. The engine takes care of pricing, risk gates, sizing, journaling, and broker plumbing.

## Built-in strategies

| Strategy | Idea | Best in |
|----------|------|---------|
| [[Consecutive Days]] | Buy after N red days, sell after M green days | Mean-reverting / range-bound markets |
| [[Combo Spread]] | SMA crossdown + EMA confirmation, or low-volume EMA breakout | Trending markets with volume signals |

## How strategies plug in

```mermaid
flowchart LR
    Reg[StrategyFactory.STRATEGIES<br/>main.py] --> A[consecutive_days]
    Reg --> B[combo_spread]
    Reg --> C[your_new_strategy]
    Reg --> Engine[Backtest engine<br/>+ Scanner]
```

When the user picks a strategy in the sidebar, the API receives `strategy: "consecutive_days"` and looks it up in the registry.

## Anatomy of a tick

```mermaid
sequenceDiagram
    participant E as Engine
    participant S as Strategy
    Note over E: Bar i arrives

    alt Not in trade
        E->>S: check_entry(df, i, req)
        S-->>E: True / False
        alt True
            E->>E: Apply filters → if pass, open
        end
    else In trade
        E->>S: check_exit(df, i, state, req)
        S-->>E: (True, reason) / (False, "")
        alt True
            E->>E: Close, record P&L + reason
        end
    end
```

## Choosing the right strategy

> [!info] Which one fits my view?

```mermaid
flowchart TD
    Q{What's your<br/>market view?} --> R[Range / mean-reverting]
    Q --> T[Trending]
    Q --> N[Neutral / vol play]

    R --> CD[Consecutive Days]
    T --> CS[Combo Spread]
    N --> Top[Use any strategy with a<br/>neutral topology like<br/>iron_condor or straddle]
```

## Custom strategies

Want a momentum breakout? An IV crush play? A custom regime filter? See [[Building Your Own]].

---

Next: [[Consecutive Days]] · [[Combo Spread]] · [[Building Your Own]]
