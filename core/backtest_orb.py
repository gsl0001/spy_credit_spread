"""ORB backtest engine — simulates the SSRN 6355218 strategy on historical 5-min bars.

Pure-function design — feed in a DataFrame of intraday SPY bars + VIX +
event-calendar dates, get back a list of trades + summary stats.  No
broker, no live data.

Inputs:
  bars: DataFrame indexed by ET-localized timestamp with columns
    [open, high, low, close, volume].  Must include 9:30–16:00 RTH bars.
  vix: optional Series of daily VIX levels indexed by date.  Used for the
    15–25 entry gate.  If None, VIX gate is skipped.
  events: optional set of "YYYY-MM-DD" strings to skip (FOMC/CPI/NFP).

The simulation models the SPY debit-vertical-call P&L with a simple
move-vs-strike model — full Black-Scholes is overkill for an ORB study
where the dominant signal is direction-of-move, not vol surface.

Outputs:
  {
    trades: [{date, entry_time, entry_price, exit_time, exit_price,
              exit_reason, pnl_pct, pnl_dollars, ...}, ...],
    stats: {
      total_trades, wins, losses, win_rate,
      avg_win_pct, avg_loss_pct, expectancy_pct,
      total_pnl, max_drawdown_pct, sharpe,
      ...
    },
    equity_curve: [{date, equity}, ...],
  }
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, time
from typing import Optional

import pandas as pd


@dataclass
class OrbBacktestConfig:
    """Knobs that drive a backtest run.

    Defaults match config/presets.json:orb-5m exactly so backtest results
    map 1:1 to live trading.
    """
    or_minutes: int = 5
    or_start_time: time = time(9, 30)
    fade_mode: bool = False  # If True, invert direction post-breakout (fade)
    offset_points: float = 1.50
    width_points: int = 5
    min_range_pct: float = 0.05      # 0.05% of price
    vix_min: float = 15.0
    vix_max: float = 25.0
    allowed_weekdays: tuple[int, ...] = (0, 2, 4)  # Mon=0, Wed=2, Fri=4
    skip_news_days: bool = True
    take_profit_pct: float = 50.0
    stop_loss_pct: float = 50.0
    time_exit: time = time(15, 30)
    entry_cutoff: time = time(15, 30)  # no new entries at/after this time
    # Capital + sizing
    capital: float = 10_000.0
    contracts_per_trade: int = 1
    spread_cost_per_contract: float = 250.0  # assumed debit, used for risk sizing
    # Cost model
    commission_per_contract: float = 0.65    # IBKR-ish; per leg
    slippage_per_leg_dollars: float = 5.0    # ≈$0.05 mid drift × 100


@dataclass
class _Trade:
    date: date
    entry_time: pd.Timestamp
    entry_price: float           # underlying price at breakout
    direction: str               # "bull" | "bear"
    or_high: float
    or_low: float
    long_strike: float
    short_strike: float
    exit_time: pd.Timestamp
    exit_price: float            # underlying at exit
    exit_reason: str             # "take_profit" | "stop_loss" | "time_exit"
    pnl_pct: float               # P&L as % of debit
    pnl_dollars: float           # net of commissions/slippage
    spread_value_at_exit: float  # estimated spread mid at exit


def _spread_value_at_underlying(
    underlying: float, long_strike: float, short_strike: float,
    direction: str, debit_paid: float, *, entry_underlying: float | None = None,
    spread_delta: float = 0.40,
) -> float:
    """Approximate spread value as underlying moves intraday.

    Two-component model:
      1. Intrinsic value at expiry (the debit-vertical payoff).
      2. Time-value envelope so OTM positions don't price to $0 mid-day.

    For a debit vertical, the spread mid at any point is approximately:
        debit_paid + spread_delta × (underlying − entry_underlying) × sign

    where sign is +1 for bull and −1 for bear. The result is clamped to
    [0, width] so we never go below worthless or above max payoff.

    spread_delta defaults to 0.40 — typical for a $5-wide debit vertical
    placed 1.50 points OTM on SPY. For a more accurate intraday simulation
    callers can sweep this parameter or replace with a full Black-Scholes
    model. For ORB sensitivity testing the linear approximation is adequate.
    """
    width = abs(long_strike - short_strike)
    sign = +1 if direction == "bull" else -1

    # Intrinsic at expiry — used only as the floor/ceiling envelope
    if direction == "bull":
        intrinsic = max(0.0, min(underlying, short_strike) - long_strike)
    else:
        intrinsic = max(0.0, long_strike - max(underlying, short_strike))

    # If we don't have an entry reference, fall back to pure intrinsic
    if entry_underlying is None:
        return min(width, intrinsic)

    # Linear delta-based price evolution from entry
    move = underlying - entry_underlying
    estimated = debit_paid + spread_delta * move * sign

    # Clamp to valid range; intrinsic is the lower bound near expiry
    # (we assume the simulator treats the position as approximately ATM-ish)
    return max(0.0, min(width, estimated))


def _is_news_day(d: date, events: Optional[set[str]]) -> bool:
    if not events:
        return False
    return d.isoformat() in events


def _vix_for_date(vix: Optional[pd.Series], d: date) -> Optional[float]:
    if vix is None:
        return None
    try:
        # Match by date — vix index may be Timestamp or date
        ts = pd.Timestamp(d)
        if ts in vix.index:
            return float(vix.loc[ts])
        # Or look up by string date
        for idx in vix.index:
            if hasattr(idx, "date") and idx.date() == d:
                return float(vix.loc[idx])
    except Exception:
        pass
    return None


def run_orb_backtest(
    bars: pd.DataFrame,
    vix: Optional[pd.Series] = None,
    events: Optional[set[str]] = None,
    config: Optional[OrbBacktestConfig] = None,
) -> dict:
    """Replay ORB strategy across `bars` (5-min, ET-indexed). Returns trades + stats.

    The dataframe index must be tz-aware (or a tz-naive DatetimeIndex
    interpreted as ET).  Required columns: open, high, low, close.
    """
    if config is None:
        config = OrbBacktestConfig()
    if bars is None or len(bars) == 0:
        return _empty_result(config)

    # Normalize index → ET-naive timestamps for grouping by date
    idx = bars.index
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        et = idx.tz_convert("America/New_York").tz_localize(None)
    else:
        et = pd.DatetimeIndex(idx)
    bars = bars.copy()
    bars["_et"] = et
    bars["_date"] = et.date
    bars["_time"] = et.time

    trades: list[_Trade] = []
    equity = config.capital
    equity_curve: list[dict] = [{"date": str(et[0].date()), "equity": equity}]
    max_eq_seen = equity

    # Group by trading day
    for day, day_bars in bars.groupby("_date"):
        # 1. Day-of-week filter
        weekday = day.weekday()
        if weekday not in config.allowed_weekdays:
            continue

        # 2. News-day filter
        if config.skip_news_days and _is_news_day(day, events):
            continue

        # 3. VIX filter
        v = _vix_for_date(vix, day)
        if vix is not None and v is None:
            continue   # no VIX data → safer to skip
        if v is not None and (v < config.vix_min or v > config.vix_max):
            continue

        # 4. Identify the OR window (configurable start to start+or_minutes)
        or_start = config.or_start_time
        or_end = (
            pd.Timestamp.combine(day, or_start)
            + pd.Timedelta(minutes=config.or_minutes)
        ).time()

        or_bars = day_bars[
            (day_bars["_time"] >= or_start) & (day_bars["_time"] < or_end)
        ]
        if len(or_bars) == 0:
            continue
        or_high = or_bars["high"].max()
        or_low = or_bars["low"].min()
        or_range = or_high - or_low
        # 5. Min range filter (% of opening price)
        opening_price = float(or_bars.iloc[0]["open"])
        if opening_price <= 0:
            continue
        if (or_range / opening_price) * 100 < config.min_range_pct:
            continue

        # 6. Walk post-OR bars looking for first breakout
        post_or = day_bars[day_bars["_time"] >= or_end].copy()
        if len(post_or) == 0:
            continue

        breakout = None
        for ts, row in post_or.iterrows():
            t = row["_time"]
            if t >= config.entry_cutoff:
                break
            close = float(row["close"])
            if close > or_high:
                breakout = ("bull", close, ts)
                break
            if close < or_low:
                breakout = ("bear", close, ts)
                break

        if breakout is None:
            continue
        direction, entry_price, entry_ts = breakout
        if config.fade_mode:
            direction = "bear" if direction == "bull" else "bull"

        # 7. Strike selection — long at entry ± offset, rounded to dollar
        if direction == "bull":
            long_strike = round(entry_price + config.offset_points)
            short_strike = long_strike + config.width_points
        else:
            long_strike = round(entry_price - config.offset_points)
            short_strike = long_strike - config.width_points

        # 8. Simulate intraday P&L until exit
        debit_paid = float(config.spread_cost_per_contract)
        tp_value = debit_paid * (1 + config.take_profit_pct / 100.0) / 100.0
        sl_value = debit_paid * (1 - config.stop_loss_pct / 100.0) / 100.0
        # Spread values are quoted in $/share; debit_paid is $/contract = $/share*100
        debit_per_share = debit_paid / 100.0
        tp_per_share = debit_per_share * (1 + config.take_profit_pct / 100.0)
        sl_per_share = debit_per_share * (1 - config.stop_loss_pct / 100.0)

        intraday_after = post_or[post_or.index >= entry_ts].iloc[1:]  # skip entry bar
        exit_ts = entry_ts
        exit_price = entry_price
        exit_reason = "time_exit"
        spread_value_at_exit = debit_per_share

        for ts, row in intraday_after.iterrows():
            t = row["_time"]
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])

            # Touch-based intra-bar TP/SL using high/low + delta model
            if direction == "bull":
                best_value = _spread_value_at_underlying(
                    high, long_strike, short_strike, "bull", debit_per_share,
                    entry_underlying=entry_price)
                worst_value = _spread_value_at_underlying(
                    low, long_strike, short_strike, "bull", debit_per_share,
                    entry_underlying=entry_price)
            else:
                best_value = _spread_value_at_underlying(
                    low, long_strike, short_strike, "bear", debit_per_share,
                    entry_underlying=entry_price)
                worst_value = _spread_value_at_underlying(
                    high, long_strike, short_strike, "bear", debit_per_share,
                    entry_underlying=entry_price)

            if best_value >= tp_per_share:
                exit_ts = ts
                exit_price = high if direction == "bull" else low
                exit_reason = "take_profit"
                spread_value_at_exit = tp_per_share
                break
            if worst_value <= sl_per_share:
                exit_ts = ts
                exit_price = low if direction == "bull" else high
                exit_reason = "stop_loss"
                spread_value_at_exit = sl_per_share
                break
            if t >= config.time_exit:
                exit_ts = ts
                exit_price = close
                exit_reason = "time_exit"
                spread_value_at_exit = _spread_value_at_underlying(
                    close, long_strike, short_strike, direction, debit_per_share,
                    entry_underlying=entry_price,
                )
                break
        else:
            # End of day without explicit exit — close at last bar
            last = intraday_after.iloc[-1] if len(intraday_after) > 0 else post_or.iloc[-1]
            exit_ts = last.name
            exit_price = float(last["close"])
            spread_value_at_exit = _spread_value_at_underlying(
                exit_price, long_strike, short_strike, direction, debit_per_share,
                entry_underlying=entry_price,
            )

        # 9. P&L net of commissions + slippage (2 legs entry + 2 legs exit = 4)
        gross_pnl_per_contract = (spread_value_at_exit - debit_per_share) * 100.0
        commissions = config.commission_per_contract * 4
        slippage = config.slippage_per_leg_dollars * 4
        net_pnl_per_contract = gross_pnl_per_contract - commissions - slippage
        net_pnl = net_pnl_per_contract * config.contracts_per_trade
        pnl_pct = (net_pnl_per_contract / debit_paid) * 100 if debit_paid > 0 else 0

        equity += net_pnl
        max_eq_seen = max(max_eq_seen, equity)

        trades.append(_Trade(
            date=day, entry_time=entry_ts, entry_price=entry_price,
            direction=direction, or_high=or_high, or_low=or_low,
            long_strike=float(long_strike), short_strike=float(short_strike),
            exit_time=exit_ts, exit_price=exit_price, exit_reason=exit_reason,
            pnl_pct=pnl_pct, pnl_dollars=net_pnl,
            spread_value_at_exit=spread_value_at_exit,
        ))
        equity_curve.append({"date": str(day), "equity": round(equity, 2)})

    return _summarize(trades, equity_curve, config)


def _summarize(
    trades: list[_Trade], equity_curve: list[dict], config: OrbBacktestConfig,
) -> dict:
    if not trades:
        return _empty_result(config, equity_curve)

    n = len(trades)
    wins = [t for t in trades if t.pnl_dollars > 0]
    losses = [t for t in trades if t.pnl_dollars <= 0]
    total_pnl = sum(t.pnl_dollars for t in trades)
    win_rate = len(wins) / n if n else 0.0

    avg_win_pct = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
    avg_loss_pct = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0
    expectancy_pct = (
        win_rate * avg_win_pct + (1 - win_rate) * avg_loss_pct
    )

    # Max drawdown from equity curve (from peak)
    peak = equity_curve[0]["equity"]
    max_dd = 0.0
    for pt in equity_curve:
        peak = max(peak, pt["equity"])
        dd = (pt["equity"] - peak) / peak * 100 if peak > 0 else 0
        max_dd = min(max_dd, dd)

    # Daily Sharpe-ish: stdev of pct returns, annualized to ~252
    daily_returns = [t.pnl_pct / 100 for t in trades]
    mean_r = sum(daily_returns) / len(daily_returns)
    var = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
    sd = math.sqrt(var)
    sharpe = (mean_r * math.sqrt(252) / sd) if sd > 0 else 0.0

    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1

    return {
        "config": _config_dict(config),
        "trades": [_trade_dict(t) for t in trades],
        "equity_curve": equity_curve,
        "stats": {
            "total_trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 4),
            "avg_win_pct": round(avg_win_pct, 2),
            "avg_loss_pct": round(avg_loss_pct, 2),
            "expectancy_pct": round(expectancy_pct, 2),
            "total_pnl": round(total_pnl, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe": round(sharpe, 2),
            "exits_by_reason": by_reason,
            "starting_capital": config.capital,
            "ending_capital": round(equity_curve[-1]["equity"], 2) if equity_curve else config.capital,
        },
    }


def _empty_result(config: OrbBacktestConfig, equity_curve: Optional[list[dict]] = None) -> dict:
    return {
        "config": _config_dict(config),
        "trades": [],
        "equity_curve": equity_curve or [{"date": "", "equity": config.capital}],
        "stats": {
            "total_trades": 0,
            "wins": 0, "losses": 0,
            "win_rate": 0.0,
            "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "expectancy_pct": 0.0,
            "total_pnl": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": 0.0,
            "exits_by_reason": {},
            "starting_capital": config.capital,
            "ending_capital": config.capital,
        },
    }


def _trade_dict(t: _Trade) -> dict:
    return {
        "date": str(t.date),
        "entry_time": str(t.entry_time),
        "entry_price": round(t.entry_price, 2),
        "direction": t.direction,
        "or_high": round(t.or_high, 2),
        "or_low": round(t.or_low, 2),
        "long_strike": t.long_strike,
        "short_strike": t.short_strike,
        "exit_time": str(t.exit_time),
        "exit_price": round(t.exit_price, 2),
        "exit_reason": t.exit_reason,
        "pnl_pct": round(t.pnl_pct, 2),
        "pnl_dollars": round(t.pnl_dollars, 2),
        "spread_value_at_exit": round(t.spread_value_at_exit * 100, 2),
    }


def _config_dict(c: OrbBacktestConfig) -> dict:
    return {
        "or_minutes": c.or_minutes,
        "offset_points": c.offset_points,
        "width_points": c.width_points,
        "min_range_pct": c.min_range_pct,
        "vix_min": c.vix_min, "vix_max": c.vix_max,
        "allowed_weekdays": list(c.allowed_weekdays),
        "skip_news_days": c.skip_news_days,
        "take_profit_pct": c.take_profit_pct,
        "stop_loss_pct": c.stop_loss_pct,
        "time_exit": c.time_exit.isoformat(),
        "capital": c.capital,
        "contracts_per_trade": c.contracts_per_trade,
        "spread_cost_per_contract": c.spread_cost_per_contract,
        "commission_per_contract": c.commission_per_contract,
        "slippage_per_leg_dollars": c.slippage_per_leg_dollars,
    }
