#!/usr/bin/env python3
"""ORB backtest CLI.

Usage:
    python3 scripts/backtest_orb.py [--days 60] [--csv path] [--out report.json]

Pulls SPY 5-min bars + ^VIX from yfinance (≤60 days history) and runs the
ORB strategy through ``core.backtest_orb.run_orb_backtest``.  Reports
total trades, win rate, expectancy, max drawdown, equity curve.

Use --csv to feed a custom dataframe (CSV with a ``timestamp`` column +
open/high/low/close/volume).  Bring your own data for >60-day backtests.

Examples:
    # Quick smoke test on last 60 days of yfinance data
    python3 scripts/backtest_orb.py --days 60

    # Custom data + write JSON report
    python3 scripts/backtest_orb.py --csv data/spy_5m_2024.csv --out report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# Make repo importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.backtest_orb import OrbBacktestConfig, run_orb_backtest


def _load_yfinance(days: int) -> tuple[pd.DataFrame, pd.Series]:
    """Pull SPY 5-min bars + ^VIX daily from yfinance (max ~60 days)."""
    import yfinance as yf
    days = min(days, 60)  # yfinance caps 5m intraday at 60 days
    spy = yf.download(
        "SPY", period=f"{days}d", interval="5m",
        prepost=False, progress=False, auto_adjust=False,
    )
    if spy is None or len(spy) == 0:
        raise SystemExit("yfinance returned no SPY data")
    # Flatten multi-index if present
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = [c[0] for c in spy.columns]
    spy.columns = [c.lower() for c in spy.columns]

    vix = yf.download(
        "^VIX", period=f"{days + 30}d", interval="1d",
        progress=False, auto_adjust=False,
    )
    if isinstance(vix.columns, pd.MultiIndex):
        vix.columns = [c[0] for c in vix.columns]
    vix_close = vix["Close"] if "Close" in vix.columns else vix["close"]
    return spy, vix_close


def _load_csv(path: str) -> pd.DataFrame:
    """Load 5-min bars from a CSV with at least: timestamp, open, high, low, close."""
    df = pd.read_csv(path)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)
    elif "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def _load_events(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text())
    out = set()
    for item in data:
        d = item.get("date")
        if d:
            out.add(str(d))
    return out


def _print_report(report: dict, sample_n: int = 10) -> None:
    s = report["stats"]
    print()
    print("=" * 72)
    print("ORB BACKTEST RESULTS")
    print("=" * 72)
    print(f"Total trades:        {s['total_trades']}")
    print(f"Wins / Losses:       {s['wins']} / {s['losses']}  (win rate: {s['win_rate'] * 100:.1f}%)")
    print(f"Avg win  / loss:     +{s['avg_win_pct']:.2f}% / {s['avg_loss_pct']:.2f}%")
    print(f"Expectancy / trade:  {s['expectancy_pct']:+.2f}%")
    print(f"Total P&L:           ${s['total_pnl']:+,.2f}")
    print(f"Capital:             ${s['starting_capital']:,.2f} -> ${s['ending_capital']:,.2f}")
    print(f"Max drawdown:        {s['max_drawdown_pct']:.2f}%")
    print(f"Sharpe (annualized): {s['sharpe']:.2f}")
    print(f"Exits by reason:     {s['exits_by_reason']}")
    print()
    if report["trades"]:
        print(f"Last {min(sample_n, len(report['trades']))} trades:")
        print(f"{'Date':<12} {'Dir':<5} {'Entry':>8} {'Exit':>8} {'Reason':<13} {'P&L %':>8} {'P&L $':>10}")
        for t in report["trades"][-sample_n:]:
            print(
                f"{t['date']:<12} {t['direction']:<5} {t['entry_price']:>8.2f} "
                f"{t['exit_price']:>8.2f} {t['exit_reason']:<13} "
                f"{t['pnl_pct']:>+7.2f}% {t['pnl_dollars']:>+10.2f}"
            )
    print()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=60, help="Days of yfinance history (max 60).")
    p.add_argument("--csv", type=str, help="CSV path (overrides --days).")
    p.add_argument("--vix-csv", type=str, help="Optional VIX daily CSV with date,close.")
    p.add_argument("--events", type=str, default="config/events_2026.json",
                   help="Events JSON to use for news-day filter.")
    p.add_argument("--out", type=str, help="Write full JSON report to this path.")
    p.add_argument("--offset", type=float, default=1.50, help="Strike offset in points.")
    p.add_argument("--width", type=int, default=5, help="Spread width in points.")
    p.add_argument("--vix-min", type=float, default=15.0)
    p.add_argument("--vix-max", type=float, default=25.0)
    p.add_argument("--no-vix", action="store_true", help="Skip VIX filter.")
    p.add_argument("--no-events", action="store_true", help="Skip news-day filter.")
    args = p.parse_args()

    if args.csv:
        bars = _load_csv(args.csv)
        vix = None
        if args.vix_csv:
            vix_df = pd.read_csv(args.vix_csv)
            vix_df["date"] = pd.to_datetime(vix_df["date"])
            vix = vix_df.set_index("date")["close"]
    else:
        bars, vix = _load_yfinance(args.days)
    if args.no_vix:
        vix = None

    events = None if args.no_events else _load_events(Path(args.events))

    config = OrbBacktestConfig(
        offset_points=args.offset,
        width_points=args.width,
        vix_min=args.vix_min,
        vix_max=args.vix_max,
    )

    print(f"Loaded {len(bars)} bars, VIX={'yes' if vix is not None else 'no'}, "
          f"events={'yes' if events else 'no'} ({len(events or [])} dates)")
    report = run_orb_backtest(bars, vix=vix, events=events, config=config)
    _print_report(report)

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, default=str))
        print(f"Full report written: {args.out}")


if __name__ == "__main__":
    main()
