# Server Deployment Guide

This guide describes how to run the SPY Options Trading system on a server without the Web UI.

## Console Control Board

The `console_live.py` script provides a rich, interactive "Control Board" for managing live trading from the terminal. It features a real-time dashboard and a command-line interface.

### Running the Control Board

```bash
python console_live.py --preset "s1"
```

### Dashboard Panels

-   **Header**: Shows system name, current time, and Leadership status (Leader/Follower).
-   **System Health**: Real-time stats on open positions, today's P&L, and loop heartbeat (monitor/fill/scanner).
-   **Active Positions**: A summary table of currently tracked trades.
-   **Recent Events**: A rolling log of system actions (fills, signals, errors).

### Interactive Commands

Type these commands at the `CMD>` prompt:

| Command | Action |
| :--- | :--- |
| `ls` | List all available scanner presets. |
| `use <name>` | Switch the active scanner preset on the fly. |
| `pos` | Display a detailed table of all open and pending positions. |
| `orders` | List the most recent order history. |
| `flatten` | **Kill Switch**: Immediately attempts to close all open positions at market mid. |
| `scan on/off` | Toggle the background market scanner. |
| `clear` | Clear the log panel. |
| `help` | Show command summary. |
| `exit` | Gracefully shut down the system. |

### Deployment Best Practices

-   **Persistence**: Run inside `tmux` or `screen`.
-   **Monitoring**: The dashboard's "Last Tick" columns will turn yellow/red if background processes stall.
-   **Leadership**: Only one instance can be `LEADER` (holding the lock at `data/monitor.lock`). Secondary instances will run in `FOLLOWER` mode (read-only monitoring).

### Logs

Detailed structured logs are written to the `logs/` directory in JSONL format, categorized by date.

```bash
tail -f logs/2026-04-22.jsonl | jq .
```
