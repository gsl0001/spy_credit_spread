# SPY Credit Spread — Strategy & Execution Upgrade

**Status:** Planning complete · Implementation pending
**Created:** 2026-04-11
**Scope:** Full rewrite of strategy/execution core to support multiple option structures, directional auto-routing, per-strategy presets, and three execution modes (backtest / paper / live).

## What this upgrade adds

1. **Strategy as a top-level feature** — pick a strategy, pick a preset, pick a direction, run it.
2. **Multiple option structures** — Long Call/Put, Vertical Spreads, Straddles/Strangles, Iron Condors/Butterflies.
3. **Auto direction routing** — signal outputs a bias (long/short/neutral), structure factory picks the matching option structure.
4. **Per-strategy preset library** — JSON files in repo, versioned alongside code.
5. **Unified engine** — same signal/filter/structure code runs in Backtest, Paper, and Live modes via swappable broker adapters.
6. **Interactive Brokers live trading** — via `ib_insync` + TWS/Gateway. Alpaca stays as secondary paper option.
7. **Safety layer** — max concurrent positions + emergency "close all" kill switch.
8. **Universal filter layer** — RSI/EMA/SMA200/Volume + VIX regime bands + FOMC/CPI/NFP event blackouts.
9. **New Strategy Hub UI** — dedicated top-level page replaces sidebar-only config.

## Folder contents

| File | Purpose |
|---|---|
| `README.md` | This file — upgrade overview |
| `QUESTIONS.md` | Clarifying questions asked + answers (decision log) |
| `PLAN.md` | Master implementation plan (tasks, TDD, commits) |
| `specs/00-architecture.md` | Target component diagram + data flow |
| `specs/01-option-structures.md` | OptionStructure ABC + all 4 structure families |
| `specs/02-broker-adapters.md` | BrokerAdapter protocol + Sim/Alpaca/IBKR impls |
| `specs/03-presets-schema.md` | JSON preset schema + loader rules |
| `specs/04-filters-universal.md` | Universal filter chain + event calendar |
| `specs/05-frontend-hub.md` | Strategy Hub page spec (routes, components) |
| `specs/06-safety.md` | Kill switches, position limits, confirm flows |
| `prototypes/` | Smoke tests (e.g., IBKR connection) |

## Implementation handoff

Open `PLAN.md` — it is a TDD, bite-sized, commit-per-step plan intended to be executed by `superpowers:subagent-driven-development` or `superpowers:executing-plans`.
