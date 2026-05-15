---
name: paper-trading-ops
description: Use when checking, restarting, reconnecting, auditing, or explaining the local moomoo paper-trading system in this repo. Covers OpenD, FastAPI backend, frontend URL, scanner preset status, moomoo SIMULATE connection, watchdog state, premarket checks, EOD audits, and simple operator instructions.
---

# Paper Trading Ops

Use this skill for operational work on the local `C:\spy\master` paper-trading stack. The goal is to answer "is it running?", "what happened?", and "what should I do?" without disturbing live paper trading more than needed.

## System Shape

- Repo: `C:\spy\master`
- Backend: FastAPI on `http://127.0.0.1:8000`
- Frontend: Vite app on `http://127.0.0.1:5173`
- Moomoo OpenD: `127.0.0.1:11111`
- Paper preset: `canary-moomoo`
- Expected trading environment: moomoo `SIMULATE`, not `REAL`
- Main scripts:
  - `scripts/run-live.ps1`
  - `scripts/start-paper-system.ps1`
  - `scripts/watchdog.py`
  - `scripts/premarket_check.py`
  - `scripts/eod_audit.py`

## First Response

When this skill triggers, say briefly that you are checking the paper-trading stack. Then inspect before restarting anything.

Start with:

```powershell
git status --short --branch
Test-NetConnection 127.0.0.1 -Port 11111
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
if (Test-Path logs\.watchdog_state.json) { Get-Content logs\.watchdog_state.json }
```

Do not print `.env`, `.env.live`, account tokens, API keys, or secrets.

## Health Check Order

Check in this order:

1. OpenD listening on `11111`.
2. Backend listening on `8000`.
3. Backend can reach moomoo account through `/api/moomoo/account`.
4. Scanner is armed with `/api/scanner/preset/status`.
5. Premarket checklist passes with `python scripts\premarket_check.py`.
6. If investigating a completed day, EOD audit passes with `python scripts\eod_audit.py YYYY-MM-DD`.

Useful commands:

```powershell
try { Invoke-RestMethod -Uri http://127.0.0.1:8000/api/moomoo/account -TimeoutSec 15 | ConvertTo-Json -Depth 8 } catch { $_.Exception.Message; if ($_.ErrorDetails.Message) { $_.ErrorDetails.Message } }
try { Invoke-RestMethod -Uri http://127.0.0.1:8000/api/scanner/preset/status -TimeoutSec 15 | ConvertTo-Json -Depth 8 } catch { $_.Exception.Message; if ($_.ErrorDetails.Message) { $_.ErrorDetails.Message } }
python scripts\premarket_check.py
```

## Restart And Reconnect

Only restart what is down.

If OpenD is not listening:

- Tell the user to open/log in to moomoo/OpenD.
- Do not continue reconnect attempts until port `11111` is listening.

If backend is down:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-paper-system.ps1 -Preset canary-moomoo
```

If backend is up but moomoo is disconnected, reconnect:

```powershell
$body = @{host='127.0.0.1'; port=11111; trd_env=0; security_firm='NONE'; filter_trdmarket='NONE'} | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/moomoo/connect -Body $body -ContentType 'application/json' -TimeoutSec 45
```

Confirm the response says:

- `connected: true`
- `trd_env: SIMULATE`

If it shows `REAL`, stop and warn the user.

## Investigating What Happened

Use local evidence:

- `python scripts\eod_audit.py YYYY-MM-DD`
- `/api/scanner/preset/status` history
- Journal/database rows for positions, orders, fills, events, and daily PnL when needed
- Recent logs under `logs/`

Explain simply:

- Whether any trade fired
- Whether any order/fill happened
- Whether there were open/closed/orphan positions
- Whether the backend restarted
- Whether moomoo disconnected
- Whether the scanner was merely armed but had `no_signal`

Prefer plain language over trading jargon. Example:

"The system was armed, but moomoo was disconnected inside the backend after a restart. No trade fired and no order was placed."

## Guardrails

- Do not create or arm a new moomoo preset unless the user explicitly asks.
- Do not switch to real trading.
- Do not edit risk limits during an ops check.
- Do not kill processes unless the process is clearly stale and blocking the requested recovery.
- Preserve user changes in the repo.
- If the scanner is armed and moomoo is connected, leave it alone.
- Use `http://127.0.0.1:5173/` as the user-facing app URL; `8000` is the API.

## Final Answer

Keep the final answer short and operational:

- Current state: running / not running
- Moomoo state: connected / disconnected, SIMULATE only
- Scanner state: preset and latest signal
- Audit/check result
- What the user needs to do next
