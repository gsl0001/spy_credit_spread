# Home Trading Desk — Multi-Strategy System Plan

**Status:** Planning v2 · Code pending
**Last revised:** 2026-05-03
**Author:** Architecture review for solo ex-prop trader running from home
**Primary broker:** moomoo (OpenD) · IBKR optional
**Implementation horizon:** ~6-8 calendar weeks of code + 2-week paper burn-in

---

## §0 — Persona, goals, constraints

### Who this is for
Solo trader, ex-prop, self-funded, trading from home. Used to having a risk desk,
dev team, and Bloomberg behind them. Now alone with a laptop, moomoo OpenD,
and the same disciplined risk culture they were trained in. Will not deploy a
strategy without backtest + paper validation. Will not size up without 2 weeks
of small-live data. Wants to sleep at night.

### What "done" looks like
1. **3-5 uncorrelated strategies running concurrently on a single moomoo account**, each with its own capital allocation, P&L, and risk gates.
2. **Adding a new strategy = drop a file, restart, deploy in UI.** No edits to core code.
3. **Every dollar of P&L attributable** to a specific strategy and instance.
4. **Multiple kill switches** reachable from web UI, Telegram, and CLI — independently testable.
5. **Daily/weekly review prompts** so I don't accidentally let a broken strategy bleed for a week.
6. **Full audit trail**: every signal, every decision, every fill, every error — in SQLite, exportable as CSV for tax accountant.
7. **Cold restart safe**: kill the server mid-trade, restart, and every position + every running strategy comes back exactly as it was.
8. **Backtest↔live parity**: same code paths in both modes; fill prices reconcile to backtest theoretical within 5%.

### What I refuse to ship without
- Per-strategy daily loss circuit breaker (auto-pause, not just alert)
- Aggregate portfolio loss circuit breaker (auto-flatten everything)
- Orphan-position detection that runs every minute
- Telegram alerts on any anomaly (broker disconnect, slow fill, P&L outside historical bounds, error rate spike)
- Manual override that always works even if the strategy supervisor is wedged
- 2-week paper burn-in before any strategy goes live with real money

### Numerical limits I'm planning around (operator-tunable)
| Limit | Target | Rationale |
|---|---|---|
| Max concurrent strategies | 10 | More than I can mentally track |
| Max concurrent positions | 5 across all strategies | Avoids margin-call risk on $25k account |
| Per-strategy daily loss | 1% of allocated capital | 1R = 1 day of edge |
| Portfolio daily loss | 2% of total equity | Prop-style hard stop |
| Per-trade max risk | 0.5% of total equity | 200 trades to ruin if I'm wrong about edge |
| Strategy circuit breaker | 3 consecutive errors → auto-pause | Catches broken strategies before they bleed |
| Order fill SLA | 30s for limit, 5s for market | Beyond → cancel + alert |
| OpenD heartbeat staleness | 5min warn, 15min critical | Catches silent disconnects |
| Backtest deviation from live | < 5% per-trade slippage budget | Beyond → investigate before next deploy |

---

## §1 — Operating model (how I use this day-to-day)

### Daily workflow
| Time (ET) | Action | Surfaces |
|---|---|---|
| Pre-market 08:30 | Telegram: `/status` | Broker connection, scheduled events, yesterday's P&L |
| 09:25 | UI: Strategy Hub → glance at deployed instances, all green pills | Web |
| 09:30-16:00 | Phone unlocks only on Telegram alerts; otherwise hands off | Telegram |
| 16:05 | Auto Telegram digest fires: per-strategy P&L, anomalies, tomorrow's events | Telegram |
| 16:30 | UI: review trades → tag any anomalies in journal notes | Web |
| Friday EOD | Auto weekly digest: Sharpe, drawdown, strategy correlations, "kill candidates" | Telegram + Web |

### Weekly review (mandatory, blocked on calendar)
- Every deployed strategy: still profitable on rolling 4-week basis?
- Any strategy with > 2× historical drawdown? → demote to paper
- Capital allocation rebalance? (reweight winners, starve losers)
- New strategies in `paper` tier: any ready to promote to `small_live`?
- Any pending FOMC/CPI/NFP next week? Pre-emptive pause for vulnerable strategies

### Strategy lifecycle (no strategy skips a tier)
```
                  ┌──────────────┐
   New strategy → │   backtest   │  ≥ 1 year of data
                  └──────┬───────┘  Sharpe > 0.8, max_dd < 15%
                         ▼
                  ┌──────────────┐
                  │ paper_simulate│  ≥ 2 weeks live paper
                  └──────┬───────┘  Live fills within 5% of backtest theoretical
                         ▼
                  ┌──────────────┐
                  │  small_live  │  1 contract, $500 cap, 4 weeks
                  └──────┬───────┘  Realized P&L within 1σ of expected
                         ▼
                  ┌──────────────┐
                  │ scaled_live  │  Operator-set capital, ongoing review
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐
                  │   retired    │  Auto if rolling 4w P&L < 0
                  └──────────────┘
```

The system **enforces** these tiers — a strategy in `backtest` tier physically cannot place real orders.

---

## §2 — Non-functional requirements

| Dimension | Target | How it's verified |
|---|---|---|
| **Reliability** | 99% scheduler uptime during RTH | Heartbeat staleness alert; weekly review of `events.error*` counts |
| **Latency** | Tick → signal: <100ms; Signal → order placed: <500ms | Per-tick log timestamps; nightly batch analysis |
| **Recovery** | Cold restart → full state restored < 30s | Tested weekly via planned `pkill -HUP -f main.py` |
| **Audit** | Every order traceable to a `(strategy_id, instance_id, signal_id)` triple | SQL query in journal; included in tax export |
| **Security** | No secrets in logs, journal, or git | grep CI check; `.env` in `.gitignore` (already done) |
| **Data integrity** | Journal SQLite WAL; daily backup off-machine | Cron `sqlite3 .backup`; restore drill monthly |
| **Operator control** | Every action reversible within 60s via UI/Telegram/CLI | Drilled monthly with `/flatten` + restart |

---

## §3 — System architecture

### Component diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Operator surfaces                                                       │
│  ┌─────────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐ │
│  │  Web UI     │  │ Telegram │  │   CLI    │  │ Daily/Weekly digest  │ │
│  │ Strategy Hub│  │  Bot     │  │ scripts/ │  │  (auto-emailed)      │ │
│  └──────┬──────┘  └────┬─────┘  └────┬─────┘  └───────────┬──────────┘ │
└─────────┼──────────────┼─────────────┼────────────────────┼────────────┘
          ▼              ▼             ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        FastAPI process (single)                          │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │              StrategyManager (asyncio supervisor)                 │  │
│  │  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │  │
│  │  │  ORB    │ │OrderFlow│ │ConDays   │ │CustomA   │ │CustomB  │ │  │
│  │  │ inst-01 │ │ inst-02 │ │ inst-03  │ │ inst-04  │ │ inst-05 │ │  │
│  │  └────┬────┘ └────┬────┘ └────┬─────┘ └────┬─────┘ └────┬────┘ │  │
│  │       │           │           │            │             │      │  │
│  │       └───────────┴────┬──────┴────────────┴─────────────┘      │  │
│  │                        ▼                                         │  │
│  │              [Per-strategy try/except boundary]                  │  │
│  │                        │                                         │  │
│  │                  Circuit breaker                                 │  │
│  └────────────────────────┼─────────────────────────────────────────┘  │
│                           ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                   Pre-trade Risk Gateway                          │  │
│  │  Per-strategy:                  │  Aggregate (portfolio):         │  │
│  │   - capital budget              │   - total margin ≤ excess_liq   │  │
│  │   - max_positions               │   - total open positions ≤ 5    │  │
│  │   - daily_loss_limit            │   - daily portfolio loss ≤ 2%   │  │
│  │   - tier gate (backtest blocks) │   - per-symbol throttle         │  │
│  │   - event blackout              │   - news event halt             │  │
│  └────────────────────────┬─────────────────────────────────────────┘  │
│                           ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │         Position Sizing Engine (operator-selected per strategy)  │  │
│  │  fixed | dynamic_risk | targeted_spread | vol_target | kelly_frac│  │
│  └────────────────────────┬─────────────────────────────────────────┘  │
│                           ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │            Broker Registry  (single OpenD connection, fanned)     │  │
│  │      moomoo · ibkr (optional) · alpaca paper                     │  │
│  └────────────────────────┬─────────────────────────────────────────┘  │
│                           ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │         Journal (SQLite WAL) — strategy_id on every row           │  │
│  │   positions · orders · fills · events · scanner_logs · tier_log  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Background jobs (APScheduler)                                    │  │
│  │   monitor_tick (15s) · fill_reconcile (15s) ·                    │  │
│  │   moomoo_orphan_recon (60s) · daily_digest (16:05 ET) ·          │  │
│  │   weekly_review (Fri 16:30 ET) · regime_classifier (5m) ·        │  │
│  │   anomaly_scan (1m)                                              │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  Market Data Layer (single source)                       │
│   OpenD subscriptions deduped · tick distributor · bar aggregator        │
│   Snapshot cache (5s TTL for quotes; per-bar for OHLC)                  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Why single-process

- One OpenD connection serves all strategies (OpenD has subscription caps)
- One SQLite writer (WAL handles concurrent reads but write-coordination is simpler in-process)
- One operator UI, one log stream, one heartbeat
- Crash isolation via per-instance `try/except` boundaries — one strategy raising doesn't kill the supervisor
- Multi-process is overkill for 10 strategies on one machine; would re-introduce IPC complexity for negligible robustness gain

### Failure modes addressed
| Failure | Detection | Response |
|---|---|---|
| OpenD socket drops | Health-check tick fails | Auto-reconnect with exp backoff; halt new entries until back |
| Strategy raises exception | Per-instance try/except | Log to journal, inc error count; pause after N consecutive |
| Strategy hangs (deadlock) | Per-instance heartbeat | Force-cancel task after 30s; restart |
| Broker rejects order | Structured response | Log to journal as `order_rejected`; strategy decides retry vs skip |
| Fill timeout | `_wait_for_fill` deadline | Cancel order; for legged spreads, market-flatten any filled leg |
| Journal write fails | Exception bubbles up | Halt ALL strategies; alert operator; degraded mode (read-only) |
| Server crash mid-trade | Cold-restart hydrate | Replay journal; reattach to broker; resume monitoring |
| Operator unreachable | (None — by design, system is autonomous) | Built-in safeties enforce limits without operator |

---

## §4 — Strategy plugin contract

### File layout
```
strategies/
  __init__.py
  base.py              # StrategyInstance ABC + supporting types
  orb.py               # existing
  order_flow_0dte.py   # existing
  consecutive_days.py  # existing
  combo_spread.py      # existing
  dryrun.py            # existing
  buy_the_dip.py       # NEW — reference implementation
  vol_target_iron_condor.py  # NEW — for diversification
  earnings_straddle.py       # FUTURE
```

### Required interface (will live in `strategies/base.py`)
```python
@dataclass(frozen=True)
class StrategyMetadata:
    strategy_id: str
    name: str
    description: str
    cadence: Literal["tick", "1m", "5m", "15m", "1h", "1d", "cron"]
    cron_expr: Optional[str] = None       # required if cadence == "cron"
    bar_history_required: int = 200       # bars needed for indicators
    subscriptions: list[str] = field(default_factory=list)
                                          # symbols to subscribe to feeds for
    default_config: dict = field(default_factory=dict)
    config_schema: dict = field(default_factory=dict)   # JSON Schema
    supported_brokers: list[str] = field(default_factory=lambda: ["moomoo"])
    risk_profile: Literal["low", "medium", "high"] = "medium"
                                          # informs default position sizing


@dataclass(frozen=True)
class Signal:
    signal_type: Literal["entry", "exit"]
    side: Literal["bull", "bear", "long", "short", "neutral"]
    symbol: str
    confidence: float                     # 0..1
    reason: str
    metadata: dict = field(default_factory=dict)
                                          # strategy-specific context
                                          # (e.g. OR levels, breakout price)


class StrategyInstance(ABC):
    """One running deployment of a strategy. Owned by StrategyManager."""

    config: StrategyConfig                # operator-set per instance
    metadata: StrategyMetadata            # static, from class
    broker: BrokerProtocol
    journal: Journal
    market_data: MarketDataLayer

    @classmethod
    @abstractmethod
    def metadata(cls) -> StrategyMetadata: ...

    # Lifecycle (called by StrategyManager)
    @abstractmethod
    async def start(self) -> dict: ...
    @abstractmethod
    async def stop(self, *, flatten: bool = False) -> dict: ...
    async def pause(self) -> dict: ...    # default: stop entries, monitor exits
    async def resume(self) -> dict: ...

    # Driven by manager scheduler
    @abstractmethod
    async def tick(self) -> list[Signal]: ...

    # Optional hooks (default no-op)
    async def on_position_filled(self, position: Position) -> None: ...
    async def on_position_closed(self, position: Position, pnl: float) -> None: ...
    async def on_regime_change(self, old: Regime, new: Regime) -> None: ...

    # Status reporting (called by UI poll, ≤1Hz)
    @abstractmethod
    def status(self) -> StrategyStatus: ...
```

### Strategy author workflow (the 8-step contract)

1. `python3 scripts/new_strategy.py --id rsi_meanrev --name "RSI Mean Reversion" --cadence 5m`
   - Generates `strategies/rsi_meanrev.py` (template)
   - Generates `tests/test_rsi_meanrev.py` (3 skeleton tests)
   - Generates `docs/strategies/rsi_meanrev.md` (template doc)
2. Implement `metadata()` — name, description, config schema
3. Implement `tick()` — return list of Signals when triggered
4. Implement `on_position_filled` / `on_position_closed` if you need custom exit logic
5. Write tests: at least one per filter, one happy path, one error path
6. Run `pytest tests/test_rsi_meanrev.py -v` → green
7. Run `python3 scripts/backtest_strategy.py rsi_meanrev --years 2` → see Sharpe/DD
8. Restart server → strategy auto-discovered → appears in UI catalog → operator deploys

**No edits to `main.py`, `core/scanner.py`, or any router needed.** This is the prime directive.

---

## §5 — Multi-strategy execution model

### Scheduling
- **Bar-driven** (`cadence="5m"` etc.): manager registers an APScheduler job that calls `instance.tick()` every interval. Skew first-fire by `instance_id` hash to avoid all strategies hitting the broker at once.
- **Tick-driven** (`cadence="tick"`): manager spawns a long-running `asyncio.create_task(instance.tick_loop())`. Instance subscribes via `MarketDataLayer.subscribe(symbol, callback)`; manager monitors heartbeat.
- **Cron** (`cadence="cron"`): APScheduler cron job fires `tick()` once at the declared time.

### Conflict resolution between strategies
When two strategies fire entry signals on the same symbol within the same minute:
- **Default**: both proceed independently. Aggregate position cap protects total exposure.
- **Per-symbol throttle** (operator-tunable): max N entries per symbol per M minutes across all strategies. Surplus signals get queued (later) or dropped (logged as `throttled_by_aggregate`).
- **Mutex symbols** (operator-declared, optional): "only one strategy can hold SPY at a time". First-to-fire wins; others log `mutex_blocked` until first exits.

### Capital allocation
- Operator sets `max_capital` per instance at deploy time (no auto-rebalancing in v1)
- `CapitalAllocator` tracks per-instance `committed` (sum of open entry costs)
- Pre-trade hook: instance's order rejected if `committed + new_debit > max_capital`
- Aggregate gate: reject if total `committed` across all instances > `account.excess_liquidity * 0.8`
- v2: vol-target rebalancer (auto-shrink underperformers, grow winners)

### Tick distribution (avoid duplicate subscriptions)
```
strategy1.subscribe("SPY", cb1)  ─┐
strategy2.subscribe("SPY", cb2)  ─┼──→ MarketDataLayer dedupes
strategy3.subscribe("QQQ", cb3)  ─┘    OpenD sees 2 subs (SPY, QQQ)

Tick arrives:
  SPY tick → market_data → fan out to cb1, cb2 in parallel
  QQQ tick → market_data → cb3
```
- Operator never thinks about subscription limits; layer enforces ≤ 200 symbols
- Tick latency budget: <50ms from OpenD callback to strategy callback (verified via instrumentation)

---

## §6 — Risk architecture

### Three layers, evaluated in order

**Layer 1 — Strategy tier gate** (cheapest check first)
```
if instance.tier == "backtest":           reject all live orders
if instance.tier == "paper" and broker == "real":  reject
if instance.state == "paused":             reject
if instance.state == "halted":             reject (and won't be queried)
```

**Layer 2 — Per-strategy gates**
```
if open_positions(strategy_id) >= instance.max_positions:  reject
if today_pnl(strategy_id) <= -instance.max_daily_loss:     halt instance + reject
if commit + debit > instance.max_capital:                   reject
if today.weekday() not in strategy.allowed_days:            reject
if today.is_news_day and strategy.skip_news:                reject
```

**Layer 3 — Aggregate portfolio gates**
```
if sum(open_positions across all) >= MAX_TOTAL_POSITIONS:   reject + alert
if today_portfolio_pnl <= -MAX_PORTFOLIO_DAILY_LOSS:        halt EVERYTHING + flatten + alert
if total_margin_required > account.excess_liquidity * 0.8:  reject
if symbol_throttle.would_exceed(symbol):                    queue or drop
```

### Daily portfolio loss circuit breaker (the most important safety)
- Computed from journal: sum of realized + unrealized P&L for today across all strategy_ids
- Trigger: ≤ -2% of `account.equity_at_open`
- Action: `StrategyManager.emergency_halt()` — set every instance to `halted`, then trigger `broker.flatten_all()` for each broker
- Telegram critical alert + audible desktop notification
- **Cannot be bypassed without operator intervention** (UI confirm dialog with 5s delay)

### Per-strategy circuit breaker (catches broken code)
- 3 consecutive `tick()` exceptions → instance auto-paused, alert
- Tick latency p99 > 5x baseline for 10 ticks → alert (potential broker issue)
- Order rejection rate > 30% in last 1h → alert (likely config drift)

---

## §7 — Position sizing engine

### Methods (operator-selected per instance)

| Method | Use when | Formula |
|---|---|---|
| `fixed` | Smoke test, dry-run | `contracts = N` |
| `dynamic_risk` | Vol-aware sizing | `contracts = floor(equity × risk_pct / max_loss_per_contract)` |
| `targeted_spread` | Cap-bounded sizing | `contracts = floor(min(equity × pct, max_alloc) / cost)`, falls back to fixed if exceeds |
| `vol_target` (NEW) | Risk-parity multi-strategy | `contracts = (target_vol / strategy_realized_vol) × baseline_contracts` |
| `kelly_frac` (NEW) | Strategies with stable edge stats | `contracts = floor(kelly_pct × edge × equity / cost)`, kelly capped at 0.25 |

### Vol-target sizing detail
- Each strategy publishes its rolling 30-day realized P&L vol
- Operator sets `target_portfolio_vol = 1%` (e.g.)
- Allocator scales each strategy's contracts so its contribution to portfolio vol equals `target / N_strategies`
- Recomputed nightly; takes effect on next trade
- Side benefit: as a strategy's vol drops (regime favors it), it gets MORE contracts; as vol spikes, it gets fewer

### Kelly fraction with safety floor
- Requires backtest with ≥ 100 trades to compute edge stats
- `kelly_fraction = (win_rate × avg_win - (1 - win_rate) × avg_loss) / avg_win`
- Cap at 0.25 (quarter-Kelly is standard prop practice; full Kelly blows up too easily)
- Per-strategy enable flag — only mature strategies should use this

---

## §8 — Market data layer

### Responsibilities
- Single point of contact with OpenD for quotes/ticks/bars
- Subscription dedup (10 strategies want SPY → 1 OpenD sub)
- Snapshot cache (last quote per symbol, TTL 5s) — strategies polling spot can hit cache
- Bar aggregation (build 1m bars from ticks if needed; 5m bars from 1m)
- Historical bar fetch with cache (one yfinance call per (symbol, interval, range))
- Exposes:
  - `async get_snapshot(symbol) -> Quote`
  - `async get_bars(symbol, interval, period) -> DataFrame`
  - `subscribe(symbol, callback)` / `unsubscribe(symbol, callback)`
  - `health() -> dict` (last tick per symbol, sub count, error count)

### Data freshness contract (verified continuously)
- Snapshot cache: max age 5s during RTH; reject as stale otherwise
- Tick latency from OpenD to callback: p99 < 100ms
- Bar fetch: p99 < 500ms for ≤ 1d, < 5s for ≤ 60d

---

## §9 — Backtest framework

### Primary backtest harness
- Already exists for ORB (`core/backtest_orb.py`)
- Generalize to a `Backtester` class that takes any `StrategyInstance` subclass + historical data
- Records: trades, equity curve, per-trade slippage, fill latency assumed
- Outputs identical to live: same Position/Order/Signal types

### Walk-forward analysis (mandatory for tier promotion to `small_live`)
```
For each window:
  In-sample (12 months) → optimize params (grid search)
  Out-of-sample (3 months) → run with optimized params, no re-fitting
  Record OOS Sharpe + DD
Aggregate across windows → check OOS metrics
Promotion gate: OOS Sharpe > 0.6, OOS max_DD < 1.5x in-sample max_DD
```

### Live↔backtest parity validator (runs nightly)
For each `paper_simulate` and `small_live` instance:
- Pull today's actual fills
- Re-run the strategy with today's bars in backtest mode
- Compare: signal count (should match exactly), entry strikes (should match), fill prices (should match within 5%), realized P&L (should match within 5%)
- Deviation > threshold → alert; investigate before next deploy

This is the **most important** validation — if live diverges from backtest, the backtest is a lie and the strategy's edge is fictional.

---

## §10 — Strategy lifecycle gates

### Gate enforcement (system-enforced, not operator-discretionary)

| Tier | Can place real orders? | Validation required to advance |
|---|---|---|
| `backtest` | No (simulator only) | ≥ 1y data, Sharpe > 0.8, max_DD < 15% |
| `paper_simulate` | Only on `paper` or `simulate` brokers | ≥ 2 weeks live paper, parity within 5% |
| `small_live` | Yes, capped at $500 / 1 contract | ≥ 4 weeks small_live, P&L within 1σ of backtest |
| `scaled_live` | Yes, full operator-configured cap | Operator review every 2 weeks |
| `retired` | No | Auto-demoted if 4-week rolling P&L < 0 |

Tier stored in `StrategyConfig.tier`, persisted in journal `tier_log` table with promotion timestamps + operator notes.

---

## §11 — Operations

### Deployment
- Single VM (Linode/DigitalOcean $20/mo, 4GB RAM)
- `systemd` service for `uvicorn main:app`, `Restart=on-failure`
- nginx reverse proxy with letsencrypt (so I can hit UI from phone)
- moomoo OpenD running on same VM (or separate moomoo machine; OpenD reachable via local network)
- Daily backups: `sqlite3 .backup` → S3 / Backblaze
- Off-VM Telegram bot — survives VM crash to alert me

### Monitoring stack
- `/api/health` endpoint exposes everything Prometheus could want (already exists, extend with multi-strategy metrics)
- Grafana dashboard (optional but recommended): per-strategy P&L, error rate, fill latency, OpenD heartbeat
- Telegram for everything that needs my eyes

### Runbook (in `docs/RUNBOOK.md`, updated with each phase)
- Server won't start
- OpenD disconnected
- Strategy stuck in error loop
- Position appears at broker not in journal
- Position appears in journal not at broker
- Daily loss circuit breaker tripped
- Need to roll back to previous version
- Need to add a new broker
- Need to add a new strategy

### Recovery drills (monthly, scheduled)
- Drill 1: kill `uvicorn` mid-trade → restart → verify positions hydrate
- Drill 2: pull moomoo OpenD network cable → verify reconnect + halt → reconnect → verify resume
- Drill 3: corrupt journal → restore from backup → verify state matches broker
- Drill 4: simulate -2% portfolio loss in journal → verify auto-flatten fires

---

## §12 — Implementation plan

Each phase: ships independently, leaves codebase green, verifiable.
**Critical path: Phase 0 → 1 → 2 → 3 → 4 (must be sequential).**
**Parallel candidates:** Phases 5/6/7 after Phase 4.

---

### Phase 0 — Schema migration: add `strategy_id` everywhere
**Why first:** every other phase depends on per-strategy attribution. Without it, "per-strategy P&L" is impossible. Smallest blast radius (additive schema change).

**Estimated effort:** 6-10 hours

**Files to create**
- `core/migrations/001_add_strategy_id.py` — DDL + backfill
- `tests/test_migration_001.py` — verify existing rows get `strategy_id='manual'`

**Files to edit**
- `core/journal.py` — add `strategy_id` to `Position`, `Order`, `Event` dataclasses + SQL schema; add `list_open_by_strategy()`, `today_pnl_by_strategy()`, `event_count_by_strategy()`
- `main.py` — propagate `strategy_id` through `_moomoo_execute_impl`, `_ibkr_execute_impl`, `moomoo_exit`, `moomoo_flatten_all`, `cleanup_stale`; default to `"manual"` for ad-hoc routes
- `core/monitor.py` — propagate when recording exit orders
- `core/fill_watcher.py` — propagate when reconciling
- `core/moomoo_reconciler.py` — when auto-closing phantoms, set `strategy_id='reconcile'`

**Idempotency keys redesigned**
- Old: `f"scan:{date}:{symbol}:{preset_name}"`
- New: `f"strategy:{instance_id}:{date}:{symbol}:{signal_type}"`

**Tests required**
- All 442 existing tests still pass (run last)
- Migration script: existing positions/orders/events get `'manual'`; new rows require explicit `strategy_id`
- `journal.today_pnl_by_strategy('orb-01')` returns only that strategy's trades
- `journal.list_open_by_strategy('orb-01')` returns only that strategy's positions

**Definition of done**
- 442+ tests green
- Manual sanity: place an order via `/api/moomoo/execute` (manually, no strategy), verify journal row shows `strategy_id='manual'`
- Backups: `cp data/trades.db data/trades.db.pre-phase0` before running migration

**Commit message template**
```
feat(journal): add strategy_id column to positions/orders/events

- Migration 001 adds strategy_id (default 'manual') to existing rows
- Position/Order/Event dataclasses extended with new field
- All execute paths propagate strategy_id; defaults to 'manual' for ad-hoc routes
- New journal queries: list_open_by_strategy, today_pnl_by_strategy, event_count_by_strategy
- Idempotency key format updated: f"strategy:{instance_id}:{date}:{symbol}:{signal_type}"

Verified: 442+ existing tests still pass. Manual smoke: ad-hoc moomoo order
journals with strategy_id='manual'.
```

---

### Phase 1 — Strategy plugin contract + registry
**Why second:** can't write `StrategyManager` without knowing what a `StrategyInstance` looks like. Pure Python, no broker integration; lowest risk.

**Estimated effort:** 8-14 hours

**Files to create**
- `strategies/base.py` — `StrategyInstance` ABC, `StrategyMetadata`, `StrategyConfig`, `StrategyStatus`, `Signal`, `Regime` dataclasses (replace existing thin `BaseStrategy`)
- `core/strategy_registry.py` — auto-discovery via `pkgutil.iter_modules` + `inspect.getmembers`; validates each subclass calls `metadata()` returning a valid schema
- `tests/test_strategy_registry.py` — fake strategies in `tests/fixtures/strategies/`; verify discovery, validation, schema validation
- `tests/fixtures/strategies/passing_strategy.py` — minimal valid strategy
- `tests/fixtures/strategies/invalid_no_metadata.py` — should fail discovery

**Files to edit**
- Each existing strategy file (`strategies/orb.py`, etc.) gains a `metadata()` classmethod returning `StrategyMetadata`. Existing `check_entry`/`check_exit` methods stay (used by old backtest path).

**Tests required**
- Registry discovery returns all 5 existing strategies + the fixtures
- Invalid strategies fail with clear error message at startup
- `StrategyMetadata.config_schema` validates against JSON Schema spec
- `Signal` is hashable + serializable to JSON

**Definition of done**
- Registry endpoint `GET /api/strategies/catalog` returns 5 entries with full metadata
- Each existing strategy passes registry validation
- Tests green

**Commit message template**
```
feat(strategies): add plugin registry + StrategyInstance ABC

- core/strategy_registry.py auto-discovers any StrategyInstance subclass
  in strategies/ at startup. Invalid strategies log + skip.
- strategies/base.py defines the new ABC: metadata(), start(), stop(),
  tick(), pause(), resume(), status(). Existing strategies extended
  with metadata() classmethod; check_entry/check_exit retained for
  backtest path compatibility.
- New types: StrategyMetadata, StrategyConfig, StrategyStatus, Signal, Regime.

Verified: GET /api/strategies/catalog returns 5 strategies with full schema.
Registry tests cover discovery + validation.
```

---

### Phase 2 — StrategyManager: lifecycle + scheduling + isolation
**Why third:** the supervisor that owns instance lifecycles. Highest design risk; needs careful testing.

**Estimated effort:** 16-24 hours

**Files to create**
- `core/strategy_manager.py` — singleton, holds `dict[instance_id, StrategyInstance]`; lifecycle ops; scheduling
- `core/strategy_supervisor.py` — per-instance try/except boundary, error counting, circuit breaker
- `tests/test_strategy_manager.py` — deploy 3 fakes, verify isolation, lifecycle, circuit breaker
- `tests/test_supervisor.py` — error counting, breaker trip + reset

**Files to edit**
- `main.py` — new endpoints: `/api/strategies/catalog`, `/instances` (GET), `/deploy` (POST), `/{id}/pause` `/resume` `/stop`, `/{id}/status`, `/halt_all` (emergency)
- Remove `_preset_scanner` global (mark deprecated; old endpoints return 410 Gone with hint)

**Endpoint contract**
```
POST /api/strategies/deploy
{
  "strategy_id": "orb",
  "instance_id": "orb-01",       # operator-chosen, must be unique
  "broker": "moomoo",
  "tier": "paper_simulate",      # backtest | paper_simulate | small_live | scaled_live
  "capital": 5000.0,
  "max_positions": 2,
  "max_daily_loss": 100.0,
  "params": {                    # strategy-specific, validated against config_schema
    "or_minutes": 5,
    "offset": 1.50,
    ...
  }
}
→ 200 {"ok": true, "instance_id": "orb-01", "state": "active"}
or 400 {"error": "validation_failed", "details": [...]}
```

**Tests required**
- Deploy 3 fake strategies; one raises in tick() → others keep running
- Pause → resume → stop lifecycle works
- Circuit breaker: 3 consecutive errors → auto-pause; reset on manual resume
- Scheduling: bar-driven instances fire at correct intervals; tick-driven instances start their loops
- Crash recovery: kill the supervisor task mid-tick → manager respawns within 5s

**Definition of done**
- Deploy 3 of the same strategy with different params → all 3 run independently
- Force one to error → verify others unaffected, error logged with `strategy_id`
- Telegram `/instances` command lists all deployed
- Tests green

---

### Phase 3 — Capital allocator + aggregate risk gateway
**Why fourth:** can't safely run multiple strategies without enforcing per-strategy capital + portfolio-wide caps.

**Estimated effort:** 12-18 hours

**Files to create**
- `core/capital_allocator.py` — per-instance budget tracking, request/release API
- `core/aggregate_risk.py` — portfolio-wide gates (total positions, total margin, portfolio daily loss)
- `tests/test_capital_allocator.py`
- `tests/test_aggregate_risk.py`

**Files to edit**
- `core/risk.py` — `RiskContext` extended with `instance_id`, `instance_capital`, `instance_committed`, `instance_daily_pnl`. `evaluate_pre_trade` calls allocator + aggregate before existing gates
- `main.py` `_moomoo_execute_impl` (and IBKR) — call `allocator.request_budget(instance_id, debit)` before placing; release on order_failed
- New endpoint: `GET /api/portfolio/risk_status` — current per-strategy + aggregate state

**Allocator contract**
```python
class CapitalAllocator:
    def deposit(self, instance_id: str, amount: float) -> None: ...
    def withdraw(self, instance_id: str, amount: float) -> None: ...
    def request_budget(self, instance_id: str, debit: float) -> AllocationDecision: ...
    def release_budget(self, instance_id: str, debit: float) -> None: ...
    def commit_position(self, instance_id: str, position_id: str, debit: float) -> None: ...
    def close_position(self, instance_id: str, position_id: str, realized_pnl: float) -> None: ...
    def state(self, instance_id: str) -> AllocationState: ...
    def aggregate_state(self) -> AggregateState: ...
```

**Tests required**
- Concurrent budget requests serialized correctly (no double-spend)
- Daily loss > limit → instance halted, future requests rejected
- Portfolio loss > limit → emergency_halt fires across all instances
- Margin check: sum of all `committed` > available_margin → reject
- Symbol throttle: ≥ N entries on SPY in last M minutes → reject

**Definition of done**
- Two `dryrun-moomoo` instances running concurrently → each respects its budget
- Force one to lose → verify it halts; verify other unaffected
- Force aggregate -2% portfolio P&L → verify emergency_halt + flatten fires
- Tests green

---

### Phase 4 — Migrate existing 5 strategies to new ABC
**Why fifth:** wires existing strategies into the new manager. Mostly mechanical wrapping.

**Estimated effort:** 10-16 hours

**Files to edit (one strategy at a time, commit per strategy)**
- `strategies/orb.py` — `OrbStrategy` extends `StrategyInstance`; `tick()` returns Signals
- `strategies/order_flow_0dte.py` — already has lifecycle methods; adapt to ABC
- `strategies/consecutive_days.py` — wrap; cadence `"1d"`
- `strategies/combo_spread.py` — wrap; cadence `"1d"`
- `strategies/dryrun.py` — wrap; cadence `"5m"`; useful for smoke-testing the manager

**Tests required**
- For each: existing strategy tests still pass
- New: deploy via manager + verify tick() returns expected Signals on synthetic bars
- ORB-on-manager produces same signals as ORB-via-old-scanner on same bars (parity check)

**Definition of done**
- Operator can deploy any of the 5 via `POST /api/strategies/deploy`
- Each respects new risk gates
- Tests green; old preset-based scanner still works in parallel (cutover in Phase 6)

---

### Phase 5 — Strategy Hub frontend
**Why fifth (parallel with 4):** depends on stable Phase 2/3 API; can be built in parallel with Phase 4.

**Estimated effort:** 18-28 hours

**Files to create**
- `frontend/src/views/StrategyHubView.jsx` — top-level tab
- `frontend/src/components/StrategyCatalog.jsx` — cards per available strategy
- `frontend/src/components/DeployedInstanceCard.jsx` — per-instance status + controls
- `frontend/src/components/DeployModal.jsx` — auto-form from `config_schema`
- `frontend/src/components/AggregateMetrics.jsx` — total P&L, exposure, allocation pie
- `frontend/src/components/EmergencyHaltButton.jsx` — big red button with confirm
- `frontend/src/api.js` — add strategy endpoints

**Files to edit**
- `frontend/src/App.jsx` — add new view to router
- `frontend/src/sidebar.jsx` — add nav entry

**UX flow validated**
1. Operator opens Strategy Hub
2. Sees catalog of 5 strategies as cards
3. Clicks "+Deploy" on ORB
4. Modal: enter `instance_id`, broker, tier (defaults to paper_simulate), capital, max_positions, max_daily_loss
5. Strategy-specific params auto-rendered from JSON Schema
6. Click Deploy → strategy starts → appears in Deployed Instances list
7. Click pause/resume/stop on instance card
8. Click instance → drill-down view: signals, trades, P&L curve, error log

**Tests required**
- JSX syntax checks
- Manual UX walkthrough (browser)
- Aggregate metrics math correct (sum of per-instance P&L = total)

**Definition of done**
- Deploy 2 dryrun instances from UI; both visible in Deployed list
- Pause one; verify it stops emitting signals
- Click emergency halt; verify all stopped + flatten fired
- UI degrades gracefully when backend down

---

### Phase 6 — Cutover from old preset scanner
**Why sixth:** clean removal of the legacy singleton. Done after migration is proven.

**Estimated effort:** 4-8 hours

**Files to delete (after grace period)**
- `_preset_scanner` global in main.py
- Old `/api/scanner/preset/*` endpoints (replaced)
- "Strategy Scanner" card in MoomooView (Strategy Hub takes its place)

**Migration**
- On first startup after Phase 6, if `config/deployed_instances.json` doesn't exist, auto-create from active presets in `config/presets.json`
- All 3 moomoo presets (`s1-moomoo`, `dry-run-moomoo`, `orb-5m-moomoo`) become deployed instances at `paper_simulate` tier
- Document migration in `docs/MIGRATION_to_v2.md`

**Definition of done**
- Old preset endpoints return 410 Gone with hint
- MoomooView simplified
- Telegram `/preset_*` commands removed; replaced with `/strategies`, `/deploy`, `/pause`, `/resume`
- Tests green

---

### Phase 7 — Strategy library: scaffolder + reference impl + docs
**Why seventh:** makes adding strategies trivial — the prime directive.

**Estimated effort:** 6-10 hours

**Files to create**
- `scripts/new_strategy.py` — CLI scaffold tool
- `scripts/templates/strategy.py.tmpl` — template
- `scripts/templates/test_strategy.py.tmpl` — test skeleton
- `strategies/buy_the_dip.py` — reference implementation (simple RSI<30 → bull call)
- `tests/test_buy_the_dip.py`
- `docs/ADDING_A_STRATEGY.md` — step-by-step author guide

**Definition of done**
- `python3 scripts/new_strategy.py --id rsi_meanrev --name "RSI Mean Reversion" --cadence 5m` creates 3 files
- Reference strategy backtests, paper-trades, deploys via UI without errors
- Docs walkthrough completes start-to-finish in < 30 minutes for someone new

---

### Phase 8 — Production hardening + market data layer
**Why eighth:** addresses scale + reliability for real-money operation.

**Estimated effort:** 16-24 hours

**Files to create**
- `core/market_data.py` — subscription dedup, snapshot cache, tick distributor
- `core/regime_classifier.py` — VIX bucketing + ADX trend/range; published to all instances
- `core/anomaly_detector.py` — per-strategy P&L outside historical bounds, fill latency outside SLA
- `core/parity_validator.py` — nightly live↔backtest comparison
- `tests/test_market_data.py`
- `tests/test_regime_classifier.py`
- `tests/test_anomaly_detector.py`
- `tests/test_parity_validator.py`

**Files to edit**
- `strategies/order_flow_0dte.py` — switch from direct OpenD subscription to `market_data.subscribe()`
- `core/strategy_manager.py` — call `instance.on_regime_change()` when regime classifier updates
- `main.py` — schedule new jobs (anomaly_scan @ 1m, parity_validator @ EOD)

**Definition of done**
- Two strategies subscribed to SPY → only one OpenD subscription created
- Regime classifier publishes "low_vol_trending" / "high_vol_choppy" / etc.
- Force fill latency outside SLA → anomaly detector fires alert
- Run parity validator on yesterday's data → diff report generated

---

### Phase 9 — Walk-forward backtest framework + parity validator
**Why ninth:** required before promoting any strategy from paper_simulate to small_live.

**Estimated effort:** 14-22 hours

**Files to create**
- `core/walk_forward.py` — split historical into train/test windows, run optimizer per window, report OOS metrics
- `core/optimizer.py` — grid + Bayesian search for strategy params
- `scripts/walk_forward.py` — CLI
- `tests/test_walk_forward.py`

**Files to edit**
- `core/backtest_orb.py` — generalize to `Backtester` class accepting any StrategyInstance

**Definition of done**
- `python3 scripts/walk_forward.py --strategy orb --years 3 --train 12m --test 3m` produces window-by-window report
- Walk-forward Sharpe + DD computed per window
- Aggregate OOS metrics drive tier promotion gate (Phase 10 enforces)

---

### Phase 10 — Tier enforcement + lifecycle workflow
**Why tenth:** the safety system that prevents premature live deployment.

**Estimated effort:** 8-12 hours

**Files to create**
- `core/tier_manager.py` — promotion/demotion rules, journal `tier_log` table
- `tests/test_tier_manager.py`

**Files to edit**
- `main.py` — `/api/strategies/{id}/promote` endpoint (operator-confirmed); auto-demotion job
- `core/strategy_manager.py` — refuse to start instance whose tier doesn't match broker (e.g., `tier='backtest'` + `broker='moomoo'` → reject)
- Frontend deploy modal — tier dropdown enforces broker compatibility

**Definition of done**
- New strategy deploys at `backtest` tier by default
- Cannot place real orders from `backtest` tier (system-enforced)
- Operator can promote via UI; promotion records reason + metrics in `tier_log`
- Auto-demotion: rolling 4-week P&L < 0 → tier drops one level + alert
- Tests green

---

### Phase 11 — Operator decision support
**Why eleventh:** I need help making good decisions, not just data.

**Estimated effort:** 10-16 hours

**Files to create**
- `core/daily_review.py` — generates daily digest with action items
- `core/weekly_review.py` — generates weekly digest with promotion/demotion candidates
- `core/correlation_monitor.py` — pairwise P&L correlation matrix per strategy
- `tests/test_daily_review.py`

**Files to edit**
- `core/telegram_bot.py` — `/digest`, `/correlations`, `/recommend` commands
- `main.py` — schedule digest jobs (16:05 ET daily, 16:30 ET Friday)

**Daily digest content (Telegram message)**
```
📊 Daily Digest — 2026-05-04

Portfolio P&L: +$215.40 (+0.86%)
🎯 Targets hit: 2 | 🛑 Stops hit: 1 | ⏰ Time exits: 0

Per-strategy:
  ORB-01    : +$140.20 (3 trades, 67% wr)
  OFD-02    : +$ 95.20 (1 trade)
  ConDays-03:  $  0.00 (no signal)

⚠️ Anomalies:
  - OFD-02 fill latency p99 = 1.2s (SLA: 500ms) — investigate
  - SPY OpenD heartbeat dropped 3× today

📅 Tomorrow: NFP at 08:30 ET — these instances auto-paused:
  - ORB-01 (skip_news_days=true)
  - OFD-02 (volatility_filter)

💡 Action items:
  - [ ] Review OFD-02 fill latency before next deploy
  - [ ] No promotions ready
  - [ ] Weekly review tomorrow @ 16:30 ET
```

**Definition of done**
- Daily digest fires automatically at 16:05 ET; arrives in Telegram
- Weekly digest: Friday 16:30 ET; includes correlation matrix + tier recommendations
- `/recommend` command on demand returns same data
- Tests cover digest generation logic

---

### Phase 12 — Backups, recovery drills, runbook
**Why twelfth:** can't go live without rehearsed disaster recovery.

**Estimated effort:** 8-12 hours

**Files to create**
- `scripts/backup_journal.sh` — `sqlite3 .backup` to S3 / Backblaze
- `scripts/restore_journal.sh` — restore + verify integrity
- `scripts/drill_*.sh` — recovery drill scripts
- `docs/RUNBOOK.md` — operator playbook
- `docs/DR_DRILLS.md` — drill checklist + last-run log

**Files to edit**
- `core/strategy_manager.py` — `rehydrate()` reads journal on startup, restores running instances
- `main.py` — graceful shutdown handler (SIGTERM → flatten? halt? configurable)

**Definition of done**
- Daily backup runs via cron; verified restore in test environment
- Drill 1 (uvicorn kill mid-trade): positions hydrate, instances resume; runtime < 30s
- Drill 2 (OpenD network drop): system halts entries, resumes on reconnect
- Drill 3 (corrupt journal): restore from backup; state matches broker
- Drill 4 (force -2% portfolio loss): emergency_halt + flatten fires; manual reset required
- All drills documented + dated in `docs/DR_DRILLS.md`

---

### Phase 13 — Live readiness gate + 2-week paper burn-in
**Why last:** no shortcut.

**Operator (not code) effort:** 2 calendar weeks

**Pre-burn-in checklist (must be 100% green)**
- [ ] All Phase 0-12 phases complete + tests green
- [ ] All 4 DR drills executed in last week
- [ ] Backups verified by restore test in last week
- [ ] Telegram alerts firing for all configured anomalies
- [ ] Operator daily review prompt received + actioned for ≥ 5 consecutive days

**Burn-in protocol**
- Deploy 3-5 strategies at `paper_simulate` tier on simulate account
- Each with realistic capital ($5k-$10k notional)
- Run for 10 trading days minimum
- Daily review every EOD; weekly review on Friday
- End-of-burn-in metrics:
  - Per strategy: live realized P&L vs backtest theoretical (within 5%?)
  - Per strategy: signal count parity (match exactly?)
  - Per strategy: error count (< 1 per day?)
  - Aggregate: portfolio drawdown stays under -1.5% of equity?
  - Aggregate: zero unhandled exceptions in `logs/`?

**Promotion gate**
- 1 week post burn-in: review with self (or trading buddy if available)
- Promote ≤ 1 strategy to `small_live` ($500 cap, 1 contract)
- Run `small_live` for 4 more weeks before scaling

---

## §13 — Session continuation protocol

This system will be built across many AI sessions. Each session must:

### At session start
1. Read `docs/MULTI_STRATEGY_PLAN.md` (this file)
2. Read `docs/PROGRESS_LOG.md` (created in Phase 0; updated after each phase)
3. Run `git log --oneline -20` to see recent commits
4. Run `pytest -q` to verify tests still green
5. Identify the next pending phase from PROGRESS_LOG

### During session
- Work strictly within ONE phase
- Use `TaskCreate`/`TaskUpdate` for the phase's sub-tasks
- Commit after each sub-task with the message template from the phase spec
- Run targeted tests after each sub-task; full suite before phase commit
- Update `docs/PROGRESS_LOG.md` with phase completion notes

### At session end
- Phase complete? Update `PROGRESS_LOG.md` with date + verification output + any deferred work
- Phase incomplete? Update `PROGRESS_LOG.md` with current sub-task + what's pending + what's needed to resume
- Push to git (only after operator confirms it's safe)

### `docs/PROGRESS_LOG.md` template (created in Phase 0)
```markdown
# Multi-Strategy Implementation Progress

## Phase 0 — Schema migration: add strategy_id everywhere
- Status: in-progress | completed | blocked
- Started: 2026-MM-DD
- Completed: 2026-MM-DD
- Verified by: test command + result
- Sub-tasks:
  - [x] Migration script written
  - [x] Position/Order/Event extended
  - [ ] All execute paths propagate strategy_id
  - [ ] 442+ tests still pass
- Notes: <anything an AI session needs to pick up>

## Phase 1 — ...
```

---

## §14 — Cost analysis (so I can plan capital deployment)

### Infrastructure cost
- VM: $20/mo
- Domain + SSL: $15/yr
- Backup storage: $5/mo
- moomoo OpenD: $0 (included with brokerage)
- IBKR market data: $0 (paper) or $30-100/mo (live)
- **Total: ~$30-130/mo**

### Cost per trade (moomoo)
- Commission: $0.65/contract typical
- Per spread (4 legs round-trip): $2.60
- Slippage budget: $5/leg = $20/spread (conservative; tighter on liquid 0DTE)
- **Total: ~$23/spread round-trip on simulate; ~$23 on live**

### Break-even per strategy per day
- Per-strategy daily cost (1 trade, $500 risk): $23 / $500 = 4.6% drag
- For Sharpe ≥ 1.0 strategy on $5k allocation: needs ~$15 net P&L/day to clear costs
- Validates: don't deploy strategies that don't trade enough or don't have enough edge

---

## §15 — What I'm explicitly NOT building (yet)

Out of scope for v1; documented so future me knows:

- Multi-account support (one moomoo account per process)
- Multi-broker per strategy (each instance bound to one broker)
- GA / hyperparameter optimization beyond grid search
- ML signal generation (not ruling out — but separate project)
- Cross-broker arbitrage
- Strategy marketplace / sharing
- Real-time orderbook visualization in UI
- Mobile native app (Telegram is mobile-enough)
- Cluster deployment (single-machine for v1)
- Tax-loss harvesting automation

---

## §16 — Operator decision points (need answers before Phase 0)

Please answer in `docs/operator_answers.md`:

1. **Total capital you'll deploy across all strategies?** (drives cap math)
2. **How many concurrent strategies in v1?** (5? 10?)
3. **moomoo only or IBKR also?** (IBKR adds complexity but more product breadth)
4. **First strategy to migrate after Phase 4?** (recommend ORB — most tested)
5. **Pause vs Stop — when I "stop" a strategy, do I want positions auto-flattened?** (recommend: no, manual confirm)
6. **Telegram chat for alerts — solo or shared with anyone?**
7. **Daily/weekly digest delivery time?** (default 16:05 / Fri 16:30 ET)
8. **Backup target?** (S3 vs Backblaze vs local NAS)
9. **VM provider preference?** (Linode / DigitalOcean / Hetzner / self-hosted)
10. **Live deployment target date?** (drives whether we cut corners or do everything right)

Once answered, Phase 0 begins.

---

## §17 — Verification harness (lives across all phases)

To prevent regressions, every phase must pass these meta-tests:

- `pytest -q` → all green
- `python3 -c "import main; print(len(main.app.routes))"` → expected count for current phase
- `python3 scripts/test_moomoo_order.py` → live broker smoke (skip if no OpenD)
- `python3 scripts/backtest_orb.py --days 30 --no-events --no-vix` → backtest still runs
- `node frontend/check_syntax.js` → JSX clean
- `git status` → clean working tree before phase commit

---

**End of plan. Ready for operator decisions in §16.**
