# Recovery Playbook (no-Claude / usage-limit fallback)

Use this when something fails and you can't reach me. Each scenario maps a
Telegram alert you might receive to the exact command(s) to fix it.

---

## 🛑 EMERGENCY: stop the bot immediately

**From your phone**, text your bot:
```
/preset_stop
```
That halts auto_execute for every preset. The bot stops opening new positions
instantly. Existing positions continue to be monitored/exited normally.

**To flatten everything right now:**
```
/flatten moomoo
```
That closes every open moomoo position at market.

**Complete kill (no monitoring, no exits):**
On the laptop:
```sh
kill $(pgrep -f "uvicorn main:app")
```
You're now off — broker holds whatever it holds. Manual cleanup via moomoo's
own UI from there.

---

## 🚨 EOD audit reports a FAIL

Each Telegram nightly digest tells you which invariant(s) failed. Find the
row in this table and act:

| Audit FAIL | Most likely cause | Fix |
|---|---|---|
| no orphan positions left open at EOD | broker has legs the bot didn't open | next reconciler tick records them; if persists run `curl -X POST http://127.0.0.1:8000/api/moomoo/reconcile` |
| no positions stranded in pending | process killed mid-spread, pre-journal row left | manually close via SQL (see below) |
| every closed position has ≥2 entry fills | Fill rows weren't written | non-blocking, log it and continue |
| risk_rejected count sane | stuck position blocking risk gate | identify + close stuck position (see below) |
| zero order_failed events | broker rejected an order outright | inspect last 5 events, address broker issue |
| zero broken_spread events | leg2 timed out after leg1 filled | review leg liquidity; tighten chain_max_bid_ask_pct |
| zero reconcile_orphan_recorded events | crash mid-spread orphaned legs | server crashed today — check `logs/run-live.out` |
| Order.fill_price matches Fill.price | journal write-mismatch | non-blocking, log it |
| server_startup count ≤ 2 | bot kept restarting | check `logs/run-live.out` for crash reason |
| no reload_mode_warning event | --reload still on | `kill` server and start with `./run-live.sh` |
| daily_pnl == SUM(positions.realized_pnl) | rollup math broke | non-blocking, will self-correct next trade |
| zero bypass_event_blackout events | env var leaked back in | `unset ALLOW_BYPASS_EVENT_BLACKOUT` and restart server |

### Find a stuck position

```sh
cd /Users/gsl0001/Downloads/spy_credit_spread
sqlite3 data/trades.db "SELECT id, state, topology, entry_time FROM positions WHERE broker='moomoo' AND state IN ('pending','open','closing');"
```

### Close a stuck row in journal (DOES NOT close at broker)

```sh
sqlite3 data/trades.db "UPDATE positions SET state='closed', exit_reason='manual_cleanup', realized_pnl=0, exit_time=datetime('now') WHERE id='<paste-id-here>';"
```

### Flatten at broker before closing journal

Use the bot: `/flatten moomoo` from Telegram, OR moomoo's own UI.

---

## 🔴 Watchdog: "Server crashed"

The watchdog auto-restarts 3 times. If it gives up (🆘 message), manual restart:

```sh
cd /Users/gsl0001/Downloads/spy_credit_spread
set -a; source .env.live; set +a
nohup ./run-live.sh > logs/run-live.out 2>&1 &
disown
```

Check why it crashed:
```sh
tail -80 logs/run-live.out
```

Then send yourself a confirmation:
```sh
python3 -c "from core.telegram_bot import notify; notify('manual restart OK')"
```

---

## 🛑 Pre-market check: BLOCKED

The 06:15 PDT message tells you which check failed:

| Check failed | Fix |
|---|---|
| server process running | restart per "🔴 Watchdog" section above |
| server NOT in --reload mode | `kill` and `./run-live.sh` |
| API responding | usually means server is stuck — kill + restart |
| canary is sole moomoo auto_execute | `python3 -c "import json; d=json.load(open('config/presets.json')); [print(p['name'],p['auto_execute']) for p in d if p['broker']=='moomoo']"` — flip any extras to False in the file |
| no stuck pending/closing from prior days | see "Find a stuck position" |
| ALLOW_BYPASS_EVENT_BLACKOUT unset/0 | `unset ALLOW_BYPASS_EVENT_BLACKOUT` and restart server |
| risk env vars set | env file wasn't sourced — `set -a; source .env.live; set +a` then restart |

---

## ⚠️ Mid-day heartbeat: anomaly

| Field | Bad value | Action |
|---|---|---|
| server: *DOWN* | bot is down | see "🔴 Watchdog" section |
| risk_blocks > 50 | bot stuck firing-and-rejecting (orphan-block) | `/preset_stop` from Telegram, investigate evening |
| open now > MAX_CONCURRENT_POSITIONS | risk gate bypassed somehow | shouldn't happen — `/preset_stop` and investigate |

---

## Quick reference — useful one-liners

```sh
# Where am I right now?
cd /Users/gsl0001/Downloads/spy_credit_spread

# Server alive?
pgrep -f "uvicorn main:app" && echo alive || echo DOWN

# Env loaded in the running process?
ps eww $(pgrep -f "uvicorn main:app") | grep -oE "MAX_CONCURRENT[^ ]*" | head -1

# What's the bot doing right now?
python3 scripts/midday_heartbeat.py

# Run audit on demand (don't wait until 13:30 PDT)
python3 scripts/eod_audit.py

# Today's events at a glance
sqlite3 data/trades.db "SELECT time, kind FROM events WHERE time >= date('now') ORDER BY time DESC LIMIT 20;"

# Today's positions
sqlite3 data/trades.db "SELECT id, state, topology, entry_cost, realized_pnl FROM positions WHERE entry_time >= date('now') AND broker='moomoo';"

# Last 30 lines of the server log
tail -30 logs/run-live.out

# Tail live events (Ctrl+C to stop)
tail -F logs/$(date -u +%Y-%m-%d).jsonl | grep '"logger": "main"'
```

---

## If you want to pause testing entirely until I'm available

```sh
# Stop the bot from trading (positions still monitored)
# From Telegram: /preset_stop

# OR fully stop the server
kill $(pgrep -f "uvicorn main:app")

# Pause cron entirely
crontab -l > /tmp/cron.bak && crontab -r
# To restore later:
crontab /tmp/cron.bak
```

When you're back and reach me, paste the most recent nightly digest and any FAIL details, and I'll triage.

---

## Worst-case rollback to Friday's known-good state

```sh
cd /Users/gsl0001/Downloads/spy_credit_spread
kill $(pgrep -f "uvicorn main:app")
# Snapshot current journal in case you need to reference it
cp data/trades.db data/trades.db.bak.$(date +%s)
# Move the canary to auto_execute=False so nothing fires
python3 -c "
import json
d = json.load(open('config/presets.json'))
for p in d:
    if p['broker']=='moomoo': p['auto_execute']=False
json.dump(d, open('config/presets.json','w'), indent=4)
print('all moomoo presets paused')
"
```
