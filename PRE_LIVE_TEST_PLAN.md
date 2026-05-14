# Pre-Live Autonomous Test Plan — Moomoo (2026-05-12 → 2026-05-18)

**System model:** the bot trades **autonomously**. Scanner → strike picker → risk gate → broker → journal → monitor → exit, all unattended. Humans observe outputs; humans do not click buttons. Going live on Mon **2026-05-18** with real money.

**Constraint:** four trading days (Tue–Fri) plus weekend review.
**Pass criterion:** Fri EOD audit reports 12/12 PASS. If any FAIL, push live by one week.

---

## Architecture under test (every link must hold without human help)

```
APScheduler (every 60s)
  → _run_preset_tick           [active preset = canary-moomoo, auto_execute=True]
  → strategy.scan(SPY)          [ORB(5min) on TWTF, VIX 12-30, no event days]
  → signal_fired?
  → moomoo_execute_impl
      → idempotency claim (events table)
      → chain quality / preflight
      → risk gate (max_concurrent_positions, market_open, blackout)
      → pre-journal: positions row state='pending'   ← V4 fix
      → broker.place_spread (leg1 → leg2)
          → wait_for_fill_detail returns avg_fill_price ← V1 fix
      → promote pending → 'open' with real entry_cost ← V2 fix
      → record_order × 2 (with real fill_price)
      → record_fill × 2                                ← V3 fix
  → monitor loop (every Ns)
      → MTM via get_spread_mid
      → stop_loss / take_profit / time_exit trigger
      → place_exit (leg1 sell + leg2 buy)
      → close_position (state='closed', realized_pnl computed)
  → reconciler (every Ns)
      → if broker has unjournaled legs → pair into vertical_spread orphan ← V5 fix
      → if journal has missing legs → close as 'reconcile_phantom'
  → EOD audit (cron at 16:30 ET)
      → scripts/eod_audit.py prints PASS/FAIL × 12
```

---

## Vulnerabilities

### Fixed in code (validated by unit tests, must be validated by audit this week)

| ID | Issue | Fix |
|----|-------|-----|
| V1 | `fill_price` was the quoted limit, not broker's actual `dealt_avg_price` | `_wait_for_fill_detail` returns `avg_fill_price`; success path uses it |
| V2 | `entry_cost` from quote, not fill | recomputed from real fill prices in success path |
| V3 | `fills` table empty for moomoo | record_fill × 2 added |
| V4 | Restart mid-spread orphaned both legs | pre-journal `pending` row before place_spread |
| V5 | Reconciler split spreads into 2 single-leg orphans | pair-into-spread pre-pass |
| V6 | UI hid `risk_rejected` / `broken_spread` / `order_failed` | rejection set expanded |
| V7 | `uvicorn --reload` killed in-flight orders | startup `reload_mode_warning` + `run-live.sh` |
| V8 | All 10 moomoo presets `auto_execute=True` (would collide) | canary-moomoo is the only one |

### Open — must decide before Mon

| ID | Issue | Severity | Decision needed |
|----|-------|----------|-----------------|
| V9 | Reconciler-orphan rows have `entry_cost=0` → bogus realized_pnl when monitor closes them | medium | Hydrate from broker order history OR exclude orphans from daily_pnl rollup |
| V10 | `ALLOW_BYPASS_EVENT_BLACKOUT` env-var still controls news-day skip | medium | `unset ALLOW_BYPASS_EVENT_BLACKOUT` in `.zshrc` and confirm `echo $ALLOW_BYPASS_EVENT_BLACKOUT` is empty before Mon |
| V11 | `chain_max_bid_ask_pct=0.50` default; preflight rejected 4 spreads on 5/11 | low | Canary uses 0.25; if it doesn't starve this week, promote to all presets |
| V12 | yfinance "Invalid Crumb" 401s, no retry | low | Watch frequency; >5/day → add retry-on-401 in `core/yf_safe` |
| V13 | `max_daily_loss` circuit breaker — present? | **HIGH for live** | Audit `core/risk_gate.py` / `evaluate_pre_trade`; add if missing |
| V14 | Stale leg2 quote (long_bid may have moved between leg1 placement and leg2 placement) | medium | For live, re-snap NBBO inside `place_spread` before leg2; paper week tells us how often this matters |

---

## Canary preset (the only one trading this week)

`canary-moomoo` in `config/presets.json`:

| Field | Value |
|-------|-------|
| broker | moomoo (env=SIMULATE) |
| strategy | `orb` (Opening Range Breakout) |
| bar_size | 5 mins |
| days | TWTF (Tue/Wed/Thu/Fri) |
| entry window | first 5-min bar of session; breakout fires within ~30 min |
| sizing | 1 contract fixed, `max_allocation_cap=$250`, `risk_percent=1%` |
| target_dte | 7 |
| strike_width | $5 |
| stop / take | 40% / 40% |
| chain quality | `max_bid_ask_pct=0.25`, `min_volume=50`, `min_open_interest=100` |
| VIX filter | 12–30 |
| time_exit | 15:30 ET (auto-flatten if still open) |
| skip news days | True (CPI/FOMC/NFP blackout) |
| **auto_execute** | **True** — system trades unattended |

Expected behavior: at most one trade per day Tue–Fri. Some days the strategy may not fire (no breakout in range, VIX out of band, news day). That's fine — the system idling correctly is also a PASS.

All 10 other moomoo presets have `auto_execute=False` so they cannot collide.

---

## Daily plan

### **Mon 2026-05-11 (today) — Setup, then walk away**

Before market close (or now, since market is closed):
1. `kill 16279` (stop the `--reload` server).
2. `./run-live.sh &` (run detached; let it survive your terminal close).
3. Verify startup: `tail -F logs/$(date +%Y-%m-%d).jsonl | grep -E "reload_mode_warning|leader-lock|MoomooTrader"`. Expect leader-lock acquired, moomoo connected env=SIMULATE, **no** reload_mode_warning.
4. Confirm canary is active: `curl -s http://127.0.0.1:8000/api/presets/active` → should show `canary-moomoo`.
5. Confirm journal clean: `python3 scripts/eod_audit.py 2026-05-11` — expect 9–12 PASS (the 3 known FAILs are from yesterday's incident and won't recur).

Then **stop touching the system**. The next 4 days are autonomous.

### **Tue 2026-05-12 — First autonomous trade**

You do: nothing during market hours.

System does: at 09:30 ET, ORB strategy computes the first 5-min range. If SPY breaks the upper bound and VIX is in range, system fires a bull_call_spread. Monitor MTMs every minute, exits on stop/take/time.

At 16:30 ET (cron):
```sh
python3 scripts/eod_audit.py
```
Expected: 12/12 PASS. Investigate any FAIL same evening.

If 12/12 PASS — system traded itself, journaled itself, exited itself. Good day.
If trade didn't fire at all — also OK, but check `events` for why (likely `vix_out_of_range` or `event_day_blackout`).

### **Wed 2026-05-13 — Failure-injection day (after-hours)**

Morning + market hours: same as Tue, fully autonomous.

After market close (16:30 ET), four controlled injections **in this order**, audit after each:

1. **Mid-order kill test (paper)** — open the moomoo trading UI, manually buy 1 SPY Jul C 480 to seed broker state. Then trigger a canary entry via direct API call, but `kill -9` the server within 2s of the "leg1 placed" log line:
   ```sh
   # Terminal A
   tail -F logs/$(date +%Y-%m-%d).jsonl | grep -E "leg1 placed|leg2 placed|FILLED"
   # Terminal B
   curl -X POST http://127.0.0.1:8000/api/moomoo/execute -H 'Content-Type: application/json' \
        -d '{"symbol":"SPY","direction":"bull_call","contracts":1,"strike_width":5,"target_dte":7,"spread_cost_target":150,"stop_loss_pct":40,"take_profit_pct":40,"client_order_id":"injection-mid-kill","preset_name":"canary-moomoo"}'
   # Terminal A — as soon as you see "leg1 placed":
   kill -9 $(pgrep -f "uvicorn main:app")
   # Wait 5s, then restart
   ./run-live.sh &
   ```
   **Expected:** boot reconcile pairs the orphan legs into ONE `reconcile_orphan_spread:` row (V5). Pre-journaled `pending` row from V4 should already cover the legs so reconciler finds journal_legs == broker_legs and creates ZERO orphans. PASS criterion: `sqlite3 data/trades.db "SELECT id, topology, state FROM positions WHERE id LIKE 'injection-mid-kill%' OR id LIKE 'reconcile_orphan_spread%';"` returns one row (the pre-journaled one, now `open` or `closed`), NOT a `reconcile_orphan` row.

2. **Network drop** — `sudo pfctl -e -f /dev/stdin <<EOF` block moomoo for 60s, fire entry, expect `order_failed` event, no orphan position.

3. **Preflight rejection forced** — patch canary's `chain_max_bid_ask_pct` to 0.01 (impossible), fire, expect `order_rejected` event, surfaced in UI.

4. **Risk gate forced** — open a position manually so canary's next signal hits `max_concurrent_positions`. Expect `risk_rejected` event, surfaced in UI.

After each: `python3 scripts/eod_audit.py` — check the relevant rows (3, 5, 6, 7) didn't regress.

### **Thu 2026-05-14 — Concurrency (2 strategies, autonomous)**

Pre-market: flip `s1-moomoo` (consecutive_days, daily bar) to `auto_execute=True` in addition to canary. Bump `risk.max_open_positions` from 1 to 2 in settings.

Market hours: both strategies may fire independently. System should handle 2 concurrent moomoo positions with independent monitors and exits.

EOD: `python3 scripts/eod_audit.py`. Manual check: `sqlite3 data/trades.db "SELECT id, topology, state, realized_pnl FROM positions WHERE entry_time >= date('now') AND broker='moomoo';"`. Expect ≤2 rows, each with realistic numbers.

### **Fri 2026-05-15 — Full production simulation**

Pre-market: enable any presets you intend to run live (max 3–4 to keep observable). `auto_execute=True` on all of them.

Market hours: **do not touch the code or UI all day**. Don't tail logs. Don't peek at the journal. Pretend you're at the dentist.

16:30 ET cron: `python3 scripts/eod_audit.py`.

**This is the live gate.** If Fri EOD reports 12/12 PASS — Mon goes live. If anything FAILs — push live by one week and triage over the weekend.

### **Sat-Sun 2026-05-16/17 — Freeze**

Saturday:
- Review the week's `events` table: `sqlite3 data/trades.db "SELECT kind, COUNT(*) FROM events WHERE time >= '2026-05-12' GROUP BY kind ORDER BY 2 DESC;"`.
- Decide on V9 (orphan entry_cost) and V13 (max_daily_loss).
- Run full unit tests: `python3 -m pytest tests/ -q`.
- Commit and tag `pre-live-v1`.

Sunday evening:
- `unset ALLOW_BYPASS_EVENT_BLACKOUT` confirmed.
- Verify moomoo broker can be flipped to LIVE without code changes.
- Re-run audit on the week's data — print to file, save with commit.

### **Mon 2026-05-18 — Go-live**

Pre-market:
1. Flip moomoo env to LIVE (whatever env var or config the broker uses).
2. Set `max_allocation_cap=$100` on canary for first live day. Tiny.
3. `./run-live.sh &`.
4. Verify startup log: `acc_id=<your live ID>` `env=REAL` or `LIVE`.

Market hours: **do not touch anything.** The bot trades. You observe.

16:30 ET: `python3 scripts/eod_audit.py`. First live audit.

If audit FAILs in first hour → `kill` and triage. The pre-journaled `pending` row plus the paired-orphan reconciler should make recovery clean even from a live position.

---

## Autonomous monitoring (cron entries)

Add to your crontab so audits run themselves:

```cron
# EOD audit at 16:30 ET = 20:30 UTC = 21:30 PDT in spring (adjust to your TZ)
30 16 * * 1-5 cd /Users/gsl0001/Downloads/spy_credit_spread && /usr/bin/python3 scripts/eod_audit.py 2>&1 | tee -a logs/eod_audit.log
```

Add a Telegram-on-FAIL wrapper if you have notifications wired:

```sh
python3 scripts/eod_audit.py || curl -X POST .../send_message -d "EOD audit FAILED"
```

---

## Audit script (`scripts/eod_audit.py`) — what it checks

| # | Invariant | Meaning |
|---|---|---|
| 1 | No orphan positions open at EOD | reconciler hasn't found broker-only legs the bot didn't open |
| 2 | No `pending` positions stranded | every pre-journaled row either promoted to `open` or closed |
| 3 | Every closed position has ≥2 entry fills | fills table is real |
| 4 | `risk_rejected:max_concurrent_positions` count < 100 | not stuck in orphan-block loop |
| 5 | Zero `order_failed` events | broker calls succeeded |
| 6 | Zero `broken_spread` events | no leg2-after-leg1 failures |
| 7 | Zero `reconcile_orphan_recorded` events | system journaled its own trades — no crashes |
| 8 | `Order.fill_price == Fill.price` per leg | V1/V2 working |
| 9 | `server_startup` count ≤ 2 | no `--reload` thrash |
| 10 | Zero `reload_mode_warning` events | not running with `--reload` |
| 11 | `daily_pnl.realized == SUM(positions.realized_pnl)` | rollup math correct |
| 12 | Zero `bypass_event_blackout` events | news filter not disabled |

Exit code 0 = all pass. Nonzero = at least one fail, count = nonzero exit.

---

## Manual escape hatches (if autonomous goes wrong)

| Symptom | Command |
|---|---|
| Bot stuck blocking new entries | `sqlite3 data/trades.db "SELECT id, state FROM positions WHERE broker='moomoo' AND state IN ('pending','open','closing');"` then close stragglers via `/api/journal/close_position` or direct UPDATE |
| Bot opened position not in journal | Force reconcile: `curl -X POST http://127.0.0.1:8000/api/moomoo/reconcile` |
| Bot won't stop trading | `kill $(pgrep -f "uvicorn main:app")` |
| Need full reset | Stop server, `mv data/trades.db data/trades.db.bak.$(date +%s)`, restart (will create fresh journal; broker still holds positions — reconcile them in next session) |

---

## Go/no-go gate for Mon 5/18

All of these must be ✅:

- [ ] **Tue–Fri EOD audits**: all 4 days returned 12/12 PASS
- [ ] **Wed mid-kill injection**: no `reconcile_orphan` row, only `reconcile_orphan_spread` or a clean `pending → open` promotion
- [ ] **Wed network-drop injection**: `order_failed` event, no orphan
- [ ] **Wed preflight/risk rejections**: visible in UI Order Log
- [ ] **Thu concurrency**: 2 positions tracked & exited independently
- [ ] **Fri**: zero code edits during market hours
- [ ] **V9** (orphan entry_cost) decided
- [ ] **V13** (max_daily_loss) confirmed present in `evaluate_pre_trade` or added
- [ ] **V10** `echo $ALLOW_BYPASS_EVENT_BLACKOUT` returns empty
- [ ] **All 52 unit tests pass** on `pre-live-v1` tag
- [ ] **Telegram daily digest** reproduces correct realized P&L

If any ❌ → push live by 1 week. Real money on a failing audit is unacceptable.
