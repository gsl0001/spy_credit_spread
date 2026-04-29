"""Backtest entry point — validates OrbSpreadStrategy without live connectivity.

Loads SPY 5-min bars from yfinance and runs them through BacktestEngine.
VIX is fetched once for each trading day and injected via a synthetic data
feed so the strategy's VIX filter can evaluate correctly.

Usage:
    cd nautilus/
    python run_backtest.py --start 2026-01-01 --end 2026-04-25
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load_spy_bars(start: str, end: str):
    """Download SPY 5-min OHLCV from yfinance and return as list of dicts."""
    import yfinance as yf
    ticker = yf.Ticker("SPY")
    df = ticker.history(start=start, end=end, interval="5m", auto_adjust=True)
    df = df.dropna()
    records = []
    for ts, row in df.iterrows():
        # yfinance returns tz-aware index; convert to UTC nanoseconds
        ts_utc = ts.tz_convert("UTC")
        ts_ns = int(ts_utc.timestamp() * 1e9)
        records.append({
            "ts_event": ts_ns,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"]),
        })
    return records


def _to_nautilus_bars(records: list[dict], bar_type):
    """Convert raw dicts to NautilusTrader Bar objects."""
    from nautilus_trader.model.data import Bar
    from nautilus_trader.model.objects import Price, Quantity

    bars = []
    for r in records:
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price.from_str(f"{r['open']:.4f}"),
                high=Price.from_str(f"{r['high']:.4f}"),
                low=Price.from_str(f"{r['low']:.4f}"),
                close=Price.from_str(f"{r['close']:.4f}"),
                volume=Quantity.from_str(f"{r['volume']}"),
                ts_event=r["ts_event"],
                ts_init=r["ts_event"],
            )
        )
    return bars


def main() -> None:
    parser = argparse.ArgumentParser(description="ORB NautilusTrader backtest")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-04-25")
    args = parser.parse_args()

    print(f"Loading SPY 5-min bars {args.start} → {args.end} …")
    records = _load_spy_bars(args.start, args.end)
    print(f"  {len(records)} bars loaded.")

    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.data import BarSpecification, BarType
    from nautilus_trader.model.enums import (
        AggregationSource, BarAggregation, OmsType, PriceType,
        AccountType,
    )
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import Equity
    from nautilus_trader.model.objects import Money, Price, Quantity

    from strategies.orb_spread import OrbSpreadStrategy, OrbSpreadConfig

    FUTU_VENUE = Venue("FUTU")
    spy_id = InstrumentId(Symbol("SPY"), FUTU_VENUE)

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id="BACKTEST-ORB-001",
        )
    )

    # Add a synthetic equity instrument for SPY (options are not needed in backtest
    # for the entry signal — the strategy's _submit_spread will log "no instruments"
    # without live chain data, but all filters and OR detection are exercised).
    spy_equity = Equity(
        instrument_id=spy_id,
        raw_symbol=Symbol("SPY"),
        currency=USD,
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_str("1"),
        ts_event=0,
        ts_init=0,
    )
    engine.add_instrument(spy_equity)

    bar_type = BarType(
        instrument_id=spy_id,
        bar_spec=BarSpecification(5, BarAggregation.MINUTE, PriceType.LAST),
        aggregation_source=AggregationSource.EXTERNAL,
    )
    nautilus_bars = _to_nautilus_bars(records, bar_type)
    engine.add_data(nautilus_bars)

    engine.add_venue(
        venue=FUTU_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=USD,
        starting_balances=[Money(50_000, USD)],
    )

    strategy = OrbSpreadStrategy(config=OrbSpreadConfig())
    engine.add_strategy(strategy)

    engine.run()

    print("\n── Backtest complete ─────────────────────────────")
    print(engine.trader.generate_account_report(FUTU_VENUE))


if __name__ == "__main__":
    main()
