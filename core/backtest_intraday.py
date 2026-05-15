"""Generic 1m/5m intraday backtest engine.

Sibling to ``core/backtest_orb.py`` but **not** ORB-specific. Drives entry
and exit decisions through ``strategy.check_entry`` / ``strategy.check_exit``
the same way ``run_backtest_engine`` (daily) does — just on intraday bars.

Use this engine for any strategy with ``BAR_SIZE in {"1 min", "5 mins"}``
whose trigger is **not** an opening-range breakout. The ORB engine encodes
the OR window, breakout-touch, and direction inference; this engine does
none of that and instead lets the strategy decide bar-by-bar.

Pricing reuses the linear delta-vs-entry model from the ORB engine
(``_spread_value_at_underlying``) — adequate for sensitivity testing
short-hold debit verticals; swap in Black-Scholes if you need precision.

Inputs:
  bars: DataFrame indexed by ET-localized timestamp with columns
    [open, high, low, close, volume]. RTH only (09:30–16:00 ET).
  req: BacktestRequest-like duck — accessed attributes are listed in
    ``_RequestProto`` below. The main backtest endpoint passes its
    ``BacktestRequest`` directly.
  strategy: BaseStrategy instance — must implement ``compute_indicators``,
    ``check_entry``, and ``check_exit``.

Output: same shape as ``run_orb_backtest`` so the adapter in main.py
(``_adapt_orb_report``) works unchanged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Optional, Protocol

import pandas as pd

from core.backtest_orb import _spread_value_at_underlying


class _RequestProto(Protocol):
    strategy_id: str
    strategy_type: str
    direction: str
    take_profit_pct: float
    stop_loss_pct: float
    capital_allocation: float
    contracts_per_trade: int
    spread_cost_target: float
    commission_per_contract: float
    strike_width: int


@dataclass(frozen=True)
class IntradayBacktestConfig:
    """Engine-level knobs separate from strategy params.

    Strategy-specific entry/exit logic lives on the strategy class; this
    config covers cost model, session window, and force-exit time.
    """
    session_open: time = time(9, 30)
    session_close: time = time(15, 55)   # force-flat 5 min before close
    entry_cutoff: time = time(15, 30)    # no new entries after this
    slippage_per_leg_dollars: float = 5.0  # ≈$0.05 mid drift × 100
    spread_delta: float = 0.40             # linear delta model coefficient


@dataclass
class _Trade:
    date: date
    entry_time: pd.Timestamp
    entry_price: float
    direction: str
    long_strike: float
    short_strike: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str
    pnl_pct: float
    pnl_dollars: float
    spread_value_at_exit: float


def _normalize_index(bars: pd.DataFrame) -> pd.DataFrame:
    """Add ET-naive helper columns (_et / _date / _time) used for grouping."""
    idx = bars.index
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        et = idx.tz_convert("America/New_York").tz_localize(None)
    else:
        et = pd.DatetimeIndex(idx)
    out = bars.copy()
    out["_et"] = et
    out["_date"] = et.date
    out["_time"] = et.time
    return out


def _resolve_direction(req: _RequestProto) -> str:
    if getattr(req, "strategy_type", "") == "bear_put":
        return "bear"
    if getattr(req, "strategy_type", "") == "bull_call":
        return "bull"
    return "bear" if getattr(req, "direction", "bull") == "bear" else "bull"


def run_intraday_backtest(
    bars: pd.DataFrame,
    req: _RequestProto,
    strategy: Any,
    config: Optional[IntradayBacktestConfig] = None,
) -> dict:
    """Replay a generic intraday strategy across intraday bars.

    The strategy's ``check_entry``/``check_exit`` are called with the
    full bar index ``i`` (across all days) so multi-bar lookbacks work
    naturally. Force-exit at session close prevents overnight carry —
    intraday strategies must close same day by contract.
    """
    if config is None:
        config = IntradayBacktestConfig()
    if bars is None or len(bars) == 0:
        return _empty_result(req, config)

    df = _normalize_index(bars)
    # Strategy may rely on shared indicators; mirror the daily engine.
    try:
        df = strategy.compute_indicators(df, req)
    except Exception:
        # Strategy didn't need indicators or already had them
        pass

    direction = _resolve_direction(req)
    width_points = int(getattr(req, "strike_width", 5))
    capital = float(getattr(req, "capital_allocation", 10_000.0))
    contracts = int(getattr(req, "contracts_per_trade", 1))
    debit_paid = float(getattr(req, "spread_cost_target", 250.0))
    tp_pct = float(getattr(req, "take_profit_pct", 50.0))
    sl_pct = float(getattr(req, "stop_loss_pct", 50.0))
    commission = float(getattr(req, "commission_per_contract", 0.65))

    debit_per_share = debit_paid / 100.0
    tp_per_share = debit_per_share * (1 + tp_pct / 100.0)
    sl_per_share = debit_per_share * (1 - sl_pct / 100.0)

    trades: list[_Trade] = []
    equity = capital
    equity_curve: list[dict] = [{"date": str(df["_et"].iloc[0].date()), "equity": equity}]
    max_eq_seen = equity

    in_trade = False
    entry_idx = -1
    entry_ts = None
    entry_price = 0.0
    long_strike = 0.0
    short_strike = 0.0
    entry_day = None

    n = len(df)
    for i in range(n):
        row = df.iloc[i]
        t = row["_time"]
        d = row["_date"]
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])

        # Outside session — skip
        if t < config.session_open or t > config.session_close:
            continue

        if in_trade:
            # Touch-based TP/SL using high/low extremes within the bar
            if direction == "bull":
                best_value = _spread_value_at_underlying(
                    high, long_strike, short_strike, "bull",
                    debit_per_share, entry_underlying=entry_price,
                    spread_delta=config.spread_delta,
                )
                worst_value = _spread_value_at_underlying(
                    low, long_strike, short_strike, "bull",
                    debit_per_share, entry_underlying=entry_price,
                    spread_delta=config.spread_delta,
                )
            else:
                best_value = _spread_value_at_underlying(
                    low, long_strike, short_strike, "bear",
                    debit_per_share, entry_underlying=entry_price,
                    spread_delta=config.spread_delta,
                )
                worst_value = _spread_value_at_underlying(
                    high, long_strike, short_strike, "bear",
                    debit_per_share, entry_underlying=entry_price,
                    spread_delta=config.spread_delta,
                )

            exit_now = False
            exit_reason = ""
            exit_price = close
            spread_value_at_exit = debit_per_share

            if best_value >= tp_per_share:
                exit_now = True
                exit_reason = "take_profit"
                exit_price = high if direction == "bull" else low
                spread_value_at_exit = tp_per_share
            elif worst_value <= sl_per_share:
                exit_now = True
                exit_reason = "stop_loss"
                exit_price = low if direction == "bull" else high
                spread_value_at_exit = sl_per_share
            else:
                # Strategy-driven exit
                trade_state = {
                    "entry_idx": entry_idx,
                    "entry_price": entry_price,
                    "direction": direction,
                    "days_held": 0,  # intraday → always same session
                }
                try:
                    should_exit, reason = strategy.check_exit(df, i, trade_state, req)
                except Exception:
                    should_exit, reason = False, ""
                # Force-flat at session close regardless of strategy
                if t >= config.session_close or d != entry_day:
                    should_exit = True
                    reason = reason or "session_close"
                if should_exit:
                    exit_now = True
                    exit_reason = reason or "strategy_exit"
                    spread_value_at_exit = _spread_value_at_underlying(
                        close, long_strike, short_strike, direction,
                        debit_per_share, entry_underlying=entry_price,
                        spread_delta=config.spread_delta,
                    )

            if exit_now:
                gross_pnl_per_contract = (spread_value_at_exit - debit_per_share) * 100.0
                commissions = commission * 4
                slippage = config.slippage_per_leg_dollars * 4
                net_pnl_per_contract = gross_pnl_per_contract - commissions - slippage
                net_pnl = net_pnl_per_contract * contracts
                pnl_pct = (net_pnl_per_contract / debit_paid) * 100 if debit_paid > 0 else 0

                equity += net_pnl
                max_eq_seen = max(max_eq_seen, equity)
                trades.append(_Trade(
                    date=entry_day,
                    entry_time=entry_ts,
                    entry_price=entry_price,
                    direction=direction,
                    long_strike=long_strike,
                    short_strike=short_strike,
                    exit_time=row.name,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    pnl_pct=pnl_pct,
                    pnl_dollars=net_pnl,
                    spread_value_at_exit=spread_value_at_exit,
                ))
                equity_curve.append({"date": str(d), "equity": round(equity, 2)})
                in_trade = False
            continue

        # Not in trade — check entry, but only before cutoff
        if t >= config.entry_cutoff:
            continue
        try:
            fire = bool(strategy.check_entry(df, i, req))
        except Exception:
            fire = False
        if not fire:
            continue

        # Open a position. Strike selection mirrors ORB engine.
        entry_idx = i
        entry_ts = row.name
        entry_price = close
        entry_day = d
        if direction == "bull":
            long_strike = round(close + 1.50)
            short_strike = long_strike + width_points
        else:
            long_strike = round(close - 1.50)
            short_strike = long_strike - width_points
        in_trade = True

    return _summarize(trades, equity_curve, capital)


def _summarize(trades: list[_Trade], equity_curve: list[dict], capital: float) -> dict:
    if not trades:
        return {
            "config": {},
            "trades": [],
            "equity_curve": equity_curve or [{"date": "", "equity": capital}],
            "stats": _empty_stats(capital),
        }

    n = len(trades)
    wins = [t for t in trades if t.pnl_dollars > 0]
    losses = [t for t in trades if t.pnl_dollars <= 0]
    total_pnl = sum(t.pnl_dollars for t in trades)
    win_rate = len(wins) / n
    avg_win_pct = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
    avg_loss_pct = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0
    expectancy_pct = win_rate * avg_win_pct + (1 - win_rate) * avg_loss_pct

    peak = equity_curve[0]["equity"]
    max_dd = 0.0
    for pt in equity_curve:
        peak = max(peak, pt["equity"])
        dd = (pt["equity"] - peak) / peak * 100 if peak > 0 else 0
        max_dd = min(max_dd, dd)

    rets = [t.pnl_pct / 100 for t in trades]
    mean_r = sum(rets) / len(rets)
    var = sum((r - mean_r) ** 2 for r in rets) / len(rets)
    sd = math.sqrt(var)
    sharpe = (mean_r * math.sqrt(252) / sd) if sd > 0 else 0.0

    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1

    return {
        "config": {},
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
            "starting_capital": capital,
            "ending_capital": round(equity_curve[-1]["equity"], 2) if equity_curve else capital,
        },
    }


def _empty_result(req: _RequestProto, config: IntradayBacktestConfig) -> dict:
    capital = float(getattr(req, "capital_allocation", 10_000.0))
    return {
        "config": {},
        "trades": [],
        "equity_curve": [{"date": "", "equity": capital}],
        "stats": _empty_stats(capital),
    }


def _empty_stats(capital: float) -> dict:
    return {
        "total_trades": 0,
        "wins": 0, "losses": 0,
        "win_rate": 0.0,
        "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
        "expectancy_pct": 0.0,
        "total_pnl": 0.0,
        "max_drawdown_pct": 0.0,
        "sharpe": 0.0,
        "exits_by_reason": {},
        "starting_capital": capital,
        "ending_capital": capital,
    }


def _trade_dict(t: _Trade) -> dict:
    return {
        "date": str(t.date),
        "entry_time": str(t.entry_time),
        "entry_price": round(t.entry_price, 2),
        "direction": t.direction,
        "long_strike": float(t.long_strike),
        "short_strike": float(t.short_strike),
        "exit_time": str(t.exit_time),
        "exit_price": round(t.exit_price, 2),
        "exit_reason": t.exit_reason,
        "pnl_pct": round(t.pnl_pct, 2),
        "pnl_dollars": round(t.pnl_dollars, 2),
        "spread_value_at_exit": round(t.spread_value_at_exit * 100, 2),
    }
