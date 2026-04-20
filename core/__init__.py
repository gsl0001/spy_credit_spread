"""Core runtime modules for live SPY credit-spread trading.

Layered on top of the existing backtest engine to provide:
    - settings:   env-backed config (core.settings)
    - journal:    SQLite-backed trade persistence (core.journal)
    - risk:       pre-trade risk check + position sizer (core.risk)
    - chain:      live IBKR option-chain resolver (core.chain)
    - monitor:    live position exit-lifecycle loop (core.monitor)
    - calendar:   market-hours + event blackout (core.calendar)
    - logging_setup: structured JSON logging (core.logging_setup)

Strategy math (strategies/) is intentionally untouched.
"""

__all__ = [
    "settings",
    "journal",
    "risk",
    "chain",
    "monitor",
    "calendar",
    "logging_setup",
]
