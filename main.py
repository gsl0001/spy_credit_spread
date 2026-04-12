from functools import lru_cache
from typing import List, Optional
import yfinance as yf
import pandas as pd
import numpy as np
import scipy.stats as si
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Strategy imports
from strategies.consecutive_days import ConsecutiveDaysStrategy
from strategies.combo_spread import ComboSpreadStrategy
from strategies.builder import OptionTopologyBuilder, bs_call_price, bs_put_price

# Trading & Scheduler imports
from ibkr_trading import get_ib_connection
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import asyncio

app = FastAPI(title="SPY Options Backtesting Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# Scanner State
scanner_state = {
    "active": False,
    "frequency": "hourly", # "minutely", "hourly", "open", "close"
    "logs": [],
    "last_run": None,
    "config": None,
    "mode": "paper" # "paper" (Alpaca) or "ibkr"
}

class BacktestRequest(BaseModel):
    ticker: str = "SPY"
    years_history: int = 2
    capital_allocation: float = 10000.0
    contracts_per_trade: int = 1
    use_dynamic_sizing: bool = False
    risk_percent: float = 5.0
    max_trade_cap: float = 0.0
    spread_cost_target: float = 250.0
    
    # Strategy selector
    strategy_id: str = "consecutive_days"  # "consecutive_days", "combo_spread"
    strategy_type: str = "bull_call"      # Legacy field, mapped to direction
    
    # New Topology & Direction fields
    topology: str = "vertical_spread" # "long_call", "vertical_spread", "straddle", "iron_condor", "butterfly"
    direction: str = "bull"          # "bull", "bear", "neutral"
    strike_width: int = 5
    
    # Risk Measures
    take_profit_pct: float = 0.0     # 0 means disabled
    trailing_stop_pct: float = 0.0   # 0 means disabled
    
    # Consecutive Days Params
    entry_red_days: int = 2
    exit_green_days: int = 2
    
    # Combo Spread Params
    combo_sma1: int = 3
    combo_sma2: int = 8
    combo_sma3: int = 10
    combo_ema1: int = 5
    combo_ema2: int = 3
    combo_max_bars: int = 10
    combo_max_profit_closes: int = 5

    target_dte: int = 14
    stop_loss_pct: float = 50
    commission_per_contract: float = 0.65
    use_rsi_filter: bool = True
    rsi_threshold: int = 30
    use_ema_filter: bool = True
    ema_length: int = 10
    use_sma200_filter: bool = False
    use_volume_filter: bool = False
    # Feature toggles
    use_mark_to_market: bool = True
    enable_mc_histogram: bool = True
    enable_walk_forward: bool = False
    walk_forward_windows: int = 4
    # New features
    use_vix_filter: bool = False
    vix_min: float = 15.0
    vix_max: float = 35.0
    use_regime_filter: bool = False
    regime_allowed: str = "all"  # "all", "bull", "bear", "sideways"
    
    # Targeted Spread / Dynamic Sizing
    use_targeted_spread: bool = False
    target_spread_pct: float = 2.0  # % of capital per trade
    max_allocation_cap: float = 2500.0 # max $$ risk per trade
    realism_factor: float = 1.15 # IV Multiplier

class OptimizerRequest(BaseModel):
    base_config: BacktestRequest = BacktestRequest()
    param_x: str = "entry_red_days"
    param_y: str = "target_dte"
    x_values: List[float] = [1, 2, 3, 4]
    y_values: List[float] = [7, 14, 21, 30]


class StrategyFactory:
    STRATEGIES = {
        "consecutive_days": ConsecutiveDaysStrategy,
        "combo_spread": ComboSpreadStrategy
    }

    @staticmethod
    def get_strategy(strategy_id: str):
        strat_cls = StrategyFactory.STRATEGIES.get(strategy_id, ConsecutiveDaysStrategy)
        return strat_cls()

    @staticmethod
    def get_all_strategies():
        return [
            {"id": k, "name": v().name, "schema": v.get_schema()}
            for k, v in StrategyFactory.STRATEGIES.items()
        ]


# ── Data fetching ──────────────────────────────────────────────────────────
@lru_cache(maxsize=10)
def fetch_historical_data(ticker: str, years: int):
    period = f"{years}y"
    df = yf.download(ticker, period=period, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.reset_index(inplace=True)
    if 'Date' not in df.columns and 'index' in df.columns:
        df.rename(columns={'index': 'Date'}, inplace=True)
    if hasattr(df['Date'].dt, 'tz') and df['Date'].dt.tz is not None:
        df['Date'] = df['Date'].dt.tz_localize(None)
    return df


@lru_cache(maxsize=5)
def fetch_risk_free_rate():
    """Fetch 13-week T-Bill (^IRX) as a risk-free rate proxy."""
    try:
        df = yf.download("^IRX", period="5y", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return float(df['Close'].iloc[-1]) / 100.0
    except Exception:
        return 0.045

@lru_cache(maxsize=5)
def fetch_vix_data(years: int):
    """Fetch VIX index data for regime/volatility filtering."""
    try:
        vix = yf.download("^VIX", period=f"{years}y", progress=False)
        if isinstance(vix.columns, pd.MultiIndex):
            vix.columns = vix.columns.get_level_values(0)
        vix.reset_index(inplace=True)
        if 'Date' not in vix.columns and 'index' in vix.columns:
            vix.rename(columns={'index': 'Date'}, inplace=True)
        if hasattr(vix['Date'].dt, 'tz') and vix['Date'].dt.tz is not None:
            vix['Date'] = vix['Date'].dt.tz_localize(None)
        return vix[['Date', 'Close']].rename(columns={'Close': 'VIX'})
    except Exception:
        return pd.DataFrame(columns=['Date', 'VIX'])

# ── Direction routing ──────────────────────────────────────────────────────
def _resolve_builder_direction(topology: str, bias: str) -> str:
    """
    Map a high-level (topology, bias) pair to the concrete `direction` string
    expected by `OptionTopologyBuilder.construct_legs`.

    `bias` is one of: "bull", "bear", "neutral".
    The builder uses topology-specific direction tags (e.g. `bull_call`,
    `bear_put`) so this layer keeps the engine free of topology trivia.
    """
    if topology == "vertical_spread":
        if bias == "bull":
            return "bull_call"
        if bias == "bear":
            return "bear_put"
        return "bull_call"  # neutral falls back to bull
    if topology == "long_call":
        # Auto-swap to long_put when bearish bias is requested
        return "bear" if bias == "bear" else "bull"
    if topology == "long_put":
        return "bear"
    # straddle / iron_condor / butterfly are direction-agnostic
    return bias


# ── Core backtest engine ───────────────────────────────────────────────────
def run_backtest_engine(req: BacktestRequest, df: pd.DataFrame, start_idx: int = 200):
    RISK_FREE_RATE = fetch_risk_free_rate()
    strategy = StrategyFactory.get_strategy(req.strategy_id)
    df = strategy.compute_indicators(df, req)
    
    # Regime detection (global)
    df['regime'] = 'sideways'
    bull_mask = (df['Close'] > df['SMA_200']) & (df['SMA_50'] > df['SMA_200'])
    bear_mask = (df['Close'] < df['SMA_200']) & (df['SMA_50'] < df['SMA_200'])
    df.loc[bull_mask, 'regime'] = 'bull'
    df.loc[bear_mask, 'regime'] = 'bear'

    trades, equity_curve = [], []
    equity = req.capital_allocation
    in_trade = False
    entry_idx = -1
    entry_dte = 0
    entry_cost = 0.0
    current_entry = {}
    max_eq_seen = req.capital_allocation
    
    # Trailing Stop state
    high_water_mark = 0.0

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        date = row['Date_str']

        # Mark-to-market and current position value
        if in_trade:
            dh = i - entry_idx
            T_m = max((entry_dte - dh) / 365.25, 0.0)
            S_m = float(row['Close'])
            sig_m = max(float(row['HV_21']), 0.05)
            sc = current_entry.get("contracts", req.contracts_per_trade)
            
            # Use builder to price the current position
            cv_raw = OptionTopologyBuilder.price_topology(current_entry["legs"], S_m, T_m, RISK_FREE_RATE, sig_m, realism_factor=req.realism_factor)
            mtm_val = cv_raw * sc
            
            # Update high water mark for trailing stop
            high_water_mark = max(high_water_mark, mtm_val)
            
            mtm_equity = equity + mtm_val
        else:
            mtm_equity = equity

        if mtm_equity > max_eq_seen: max_eq_seen = mtm_equity
        dd = ((mtm_equity - max_eq_seen) / max_eq_seen) * 100 if max_eq_seen > 0 else 0
        equity_curve.append({"date": date, "equity": round(mtm_equity, 2), "drawdown": round(dd, 2)})

        if not in_trade:
            if strategy.check_entry(df, i, req):
                allow = True
                is_bear = req.direction == "bear" or req.strategy_type == "bear_put"

                # Standard filters (Global)
                if is_bear:
                    if req.use_rsi_filter and float(row['RSI']) <= (100 - req.rsi_threshold): allow = False
                    if req.use_ema_filter and float(row['Close']) <= float(row[f'EMA_{req.ema_length}']): allow = False
                    if req.use_sma200_filter and float(row['Close']) >= float(row['SMA_200']): allow = False
                else:
                    if req.use_rsi_filter and float(row['RSI']) >= req.rsi_threshold: allow = False
                    if req.use_ema_filter and float(row['Close']) >= float(row[f'EMA_{req.ema_length}']): allow = False
                    if req.use_sma200_filter and float(row['Close']) <= float(row['SMA_200']): allow = False

                if req.use_volume_filter and float(row['Volume']) <= float(row['Volume_MA']): allow = False

                # VIX filter
                if req.use_vix_filter and 'VIX' in row.index:
                    vix_val = float(row['VIX']) if pd.notna(row['VIX']) else 20
                    if vix_val < req.vix_min or vix_val > req.vix_max: allow = False

                if req.use_regime_filter and req.regime_allowed != "all":
                    if row.get('regime', 'sideways') != req.regime_allowed: allow = False

                if allow:
                    S = float(row['Close'])
                    sigma = max(float(row['HV_21']), 0.05)
                    T = req.target_dte / 365.25

                    # Resolve effective bias: prefer explicit `direction`,
                    # fall back to legacy `strategy_type` for older configs.
                    bias = req.direction
                    if req.strategy_type == "bear_put":
                        bias = "bear"
                    elif req.strategy_type == "bull_call":
                        bias = "bull"

                    # Translate (topology, bias) → builder direction string
                    direction = _resolve_builder_direction(req.topology, bias)

                    # Construct position using the Builder
                    pos = OptionTopologyBuilder.construct_legs(
                        topology=req.topology,
                        direction=direction,
                        S=S,
                        T=T,
                        r=RISK_FREE_RATE,
                        sigma=sigma,
                        target_cost=req.spread_cost_target,
                        strike_width=req.strike_width,
                        realism_factor=req.realism_factor
                    )
                    
                    one_cc = pos["net_cost"]
                    if abs(one_cc) < 1e-3 and req.topology != "straddle": 
                        # Fallback for weird pricing
                        continue

                    if req.use_targeted_spread:
                        # Targeted spread sizing: allocation based on % of capital
                        target_risk = equity * (req.target_spread_pct / 100.0)
                        target_risk = min(target_risk, req.max_allocation_cap)
                        risk_per_contract = pos["margin_req"] if pos["margin_req"] > 0 else abs(one_cc)
                        contracts = int(max(1, target_risk // risk_per_contract))
                    elif req.use_dynamic_sizing:
                        # Dynamic sizing based on margin requirement if credit, or cost if debit
                        risk_cap = pos["margin_req"] if pos["margin_req"] > 0 else abs(one_cc)
                        ds = equity * (req.risk_percent / 100.0)
                        if req.max_trade_cap > 0: ds = min(ds, req.max_trade_cap)
                        contracts = int(max(1, ds // risk_cap))
                    else:
                        contracts = req.contracts_per_trade

                    entry_cost = one_cc * contracts
                    sc = contracts
                    comm = req.commission_per_contract * sc * len(pos["legs"])
                    
                    if (entry_cost + comm) > equity and one_cc > 0: continue # Check BP for debits
                    
                    equity -= (entry_cost + comm)
                    in_trade, entry_idx, entry_dte = True, i, req.target_dte
                    current_entry = {
                        "entry_date": date, "entry_spy": S, "entry_cost": entry_cost,
                        "contracts": sc, "commission": comm, "legs": pos["legs"],
                        "regime": row.get('regime', 'unknown'),
                        "entry_idx": i, "entry_dte": req.target_dte, "entry_price": S
                    }
                    high_water_mark = entry_cost
        else:
            # Check strategy exit
            should_exit, reason = strategy.check_exit(df, i, current_entry, req)
            
            days_held = i - entry_idx
            new_dte = entry_dte - days_held
            T_c = max(new_dte / 365.25, 0.0)
            S = float(row['Close'])
            sigma = max(float(row['HV_21']), 0.05)
            sc = current_entry.get("contracts", req.contracts_per_trade)
            
            # Current value with builder
            cv = OptionTopologyBuilder.price_topology(current_entry["legs"], S, T_c, RISK_FREE_RATE, sigma, realism_factor=req.realism_factor) * sc

            # Profit/Loss state for risk measures
            pnl_live = cv - current_entry["entry_cost"]
            cost_basis = abs(current_entry["entry_cost"]) if current_entry["entry_cost"] != 0 else 1.0
            pnl_pct = (pnl_live / cost_basis) * 100

            # Global Risk Measures
            stop_exit = req.stop_loss_pct > 0 and pnl_pct <= -req.stop_loss_pct
            tp_exit = req.take_profit_pct > 0 and pnl_pct >= req.take_profit_pct
            
            # Trailing stop: check drawdown from high water mark
            ts_exit = False
            if req.trailing_stop_pct > 0:
                drawdown_from_peak = high_water_mark - cv
                if drawdown_from_peak > (req.trailing_stop_pct / 100) * cost_basis:
                    ts_exit = True

            if should_exit or stop_exit or tp_exit or ts_exit:
                ec = req.commission_per_contract * sc * len(current_entry["legs"])
                equity += cv - ec
                tc = current_entry.get("commission", 0) + ec
                pnl = cv - current_entry["entry_cost"] - tc
                
                final_reason = reason
                if stop_exit: final_reason = "stop_loss"
                if tp_exit: final_reason = "take_profit"
                if ts_exit: final_reason = "trailing_stop"

                trades.append({
                    "entry_date": current_entry["entry_date"], "exit_date": date,
                    "entry_spy": round(current_entry["entry_spy"], 2), "exit_spy": round(S, 2),
                    "spread_cost": round(current_entry["entry_cost"], 2), "spread_exit": round(cv, 2),
                    "pnl": round(float(pnl), 2), "contracts": sc, "days_held": days_held,
                    "commission": round(tc, 2), "win": bool(pnl > 0),
                    "stopped_out": bool(stop_exit or ts_exit),
                    "reason": final_reason,
                    "regime": current_entry.get("regime", "unknown"),
                    "topology": req.topology
                })
                in_trade = False

    return trades, equity_curve, equity


# ── Analytics ──────────────────────────────────────────────────────────────
def compute_analytics(trades, equity_curve, req):
    from datetime import datetime
    from collections import defaultdict

    total_pnl = sum(t['pnl'] for t in trades)
    wins = sum(1 for t in trades if t['win'])
    win_rate = (wins / len(trades) * 100) if trades else 0.0
    avg_pnl = total_pnl / len(trades) if trades else 0.0

    gross_wins = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gross_losses = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0))
    profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (999.99 if gross_wins > 0 else 0.0)

    max_consec = current_streak = 0
    for t in trades:
        if not t['win']:
            current_streak += 1
            max_consec = max(max_consec, current_streak)
        else:
            current_streak = 0

    hold_days = [t.get('days_held', 0) for t in trades]
    avg_hold = sum(hold_days) / len(hold_days) if hold_days else 0.0

    # Duration distribution (histogram bins)
    dur_dist = []
    if hold_days:
        bins = [0, 2, 5, 8, 12, 16, 21, 30, 999]
        labels = ["0-2d", "3-5d", "6-8d", "9-12d", "13-16d", "17-21d", "22-30d", "30d+"]
        for j in range(len(bins) - 1):
            count = sum(1 for d in hold_days if bins[j] < d <= bins[j+1])
            if count > 0:
                dur_dist.append({"range": labels[j], "count": count})

    # Kelly Criterion: f* = W - (1-W)/R where W=win%, R=avg_win/avg_loss
    avg_win = np.mean([t['pnl'] for t in trades if t['pnl'] > 0]) if any(t['pnl'] > 0 for t in trades) else 0
    avg_loss = abs(np.mean([t['pnl'] for t in trades if t['pnl'] <= 0])) if any(t['pnl'] <= 0 for t in trades) else 1
    w_frac = wins / len(trades) if trades else 0
    win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0
    kelly = round(w_frac - ((1 - w_frac) / win_loss_ratio), 4) if win_loss_ratio > 0 else 0
    kelly_pct = round(kelly * 100, 1)

    # Regime breakdown
    regime_stats = {}
    for regime in ['bull', 'bear', 'sideways']:
        rt = [t for t in trades if t.get('regime') == regime]
        if rt:
            rw = sum(1 for t in rt if t['win'])
            regime_stats[regime] = {
                "trades": len(rt),
                "win_rate": round(rw / len(rt) * 100, 1),
                "pnl": round(sum(t['pnl'] for t in rt), 2),
            }

    # Heatmap
    heatmap_data = []
    if trades:
        stats = defaultdict(lambda: {"wins": 0, "total": 0})
        for t in trades:
            ed = datetime.strptime(t['entry_date'], '%Y-%m-%d')
            key = f"{ed.strftime('%a')}-{ed.strftime('%b')}"
            stats[key]["total"] += 1
            if t['win']: stats[key]["wins"] += 1
        for k, v in stats.items():
            day, month = k.split('-')
            heatmap_data.append({"day": day, "month": month, "win_rate": round(v["wins"]/v["total"]*100, 2), "total": v["total"]})

    # Sharpe/Sortino/Drawdown
    daily_returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i-1]['equity']
        curr = equity_curve[i]['equity']
        daily_returns.append((curr - prev) / prev if prev > 0 else 0)

    ra = np.array(daily_returns)
    sharpe = sortino = 0.0
    max_dd = min((e['drawdown'] for e in equity_curve), default=0.0)
    max_dd_d = 0.0
    if equity_curve:
        peak = equity_curve[0]['equity']
        for e in equity_curve:
            if e['equity'] > peak: peak = e['equity']
            max_dd_d = max(max_dd_d, peak - e['equity'])

    recovery = round(total_pnl / max_dd_d, 2) if max_dd_d > 0 else 0.0

    if len(ra) > 0:
        sd = ra.std(ddof=1) if len(ra) > 1 else 0
        if sd > 0: sharpe = (ra.mean() * 252) / (sd * np.sqrt(252))
        nr = ra[ra < 0]
        ns = nr.std(ddof=1) if len(nr) > 1 else 0
        if ns > 0: sortino = (ra.mean() * 252) / (ns * np.sqrt(252))
        elif sd > 0 and len(nr) == 0: sortino = sharpe

    # Monte Carlo
    mc = {"p05": req.capital_allocation, "p50": req.capital_allocation, "p95": req.capital_allocation,
          "prob_profit": 0.0, "ev": req.capital_allocation, "distribution": []}
    if trades:
        pnls = [t['pnl'] for t in trades]
        mcf = np.array([req.capital_allocation + np.sum(np.random.choice(pnls, size=len(pnls), replace=True)) for _ in range(1000)])
        pp = float(np.sum(mcf > req.capital_allocation)) / len(mcf) * 100
        dist = []
        if req.enable_mc_histogram:
            hc, he = np.histogram(mcf, bins=20)
            for j in range(len(hc)):
                mid = (he[j] + he[j+1]) / 2
                dist.append({"bin": round(float(mid), 0), "count": int(hc[j]), "profitable": bool(mid > req.capital_allocation)})
        mc = {"p05": round(float(np.percentile(mcf, 5)), 2), "p50": round(float(np.percentile(mcf, 50)), 2),
              "p95": round(float(np.percentile(mcf, 95)), 2), "prob_profit": round(pp, 1),
              "ev": round(float(np.mean(mcf)), 2), "distribution": dist}

    metrics = {
        "total_trades": len(trades), "win_rate": round(win_rate, 2), "total_pnl": round(total_pnl, 2),
        "final_equity": round(req.capital_allocation + total_pnl, 2), "avg_pnl": round(avg_pnl, 2),
        "avg_hold_days": round(avg_hold, 1), "sharpe_ratio": round(sharpe, 2), "sortino_ratio": round(sortino, 2),
        "max_drawdown": round(max_dd, 2), "profit_factor": profit_factor, "max_consec_losses": max_consec,
        "recovery_factor": recovery, "kelly_pct": kelly_pct, "kelly_optimal": f"{kelly_pct}%",
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
    }

    return metrics, heatmap_data, mc, dur_dist, regime_stats


# ── Walk-Forward ───────────────────────────────────────────────────────────
def run_walk_forward(req, df):
    total_bars = len(df) - 200
    ws = total_bars // req.walk_forward_windows
    if ws < 20: return []
    results = []
    for w in range(req.walk_forward_windows):
        s = 200 + w * ws
        e = s + ws if w < req.walk_forward_windows - 1 else len(df)
        trades, _, _ = run_backtest_engine(req, df.iloc[:e].copy(), start_idx=s)
        pnl = sum(t['pnl'] for t in trades)
        wins = sum(1 for t in trades if t['win'])
        wr = (wins / len(trades) * 100) if trades else 0
        results.append({
            "window": w+1, "start_date": df.iloc[s]['Date_str'],
            "end_date": df.iloc[min(e-1, len(df)-1)]['Date_str'],
            "trades": len(trades), "win_rate": round(wr, 1),
            "pnl": round(pnl, 2), "profitable": bool(pnl > 0),
        })
    return results


@app.get("/api/strategies")
def get_strategies():
    return StrategyFactory.get_all_strategies()


# ── Main endpoint ──────────────────────────────────────────────────────────
@app.post("/api/backtest")
def backtest(req: BacktestRequest):
    try:
        raw_df = fetch_historical_data(req.ticker, req.years_history)
        df = raw_df.copy() # Indicators computed inside engine now per strategy
        if len(df) == 0: return {"error": "No data returned."}

        # Merge VIX data if filter enabled
        if req.use_vix_filter:
            vix_df = fetch_vix_data(req.years_history)
            if not vix_df.empty:
                df = df.merge(vix_df, on='Date', how='left')
                df['VIX'] = df['VIX'].ffill().fillna(20)

        df['Date_str'] = df['Date'].dt.strftime('%Y-%m-%d')

        ph = df[['Date_str', 'Open', 'High', 'Low', 'Close']].copy()
        ph.columns = ['time', 'open', 'high', 'low', 'close']
        for c in ['open', 'high', 'low', 'close']: ph[c] = ph[c].round(2)
        price_history = ph.to_dict(orient='records')

        trades, equity_curve, equity = run_backtest_engine(req, df)
        metrics, heatmap, mc, dur_dist, regime_stats = compute_analytics(trades, equity_curve, req)

        # Regime timeline (global regime detection happens inside engine for indicators)
        strategy = StrategyFactory.get_strategy(req.strategy_id)
        df_ind = strategy.compute_indicators(df, req)
        df_ind['regime'] = 'sideways'
        df_ind.loc[(df_ind['Close'] > df_ind['SMA_200']) & (df_ind['SMA_50'] > df_ind['SMA_200']), 'regime'] = 'bull'
        df_ind.loc[(df_ind['Close'] < df_ind['SMA_200']) & (df_ind['SMA_50'] < df_ind['SMA_200']), 'regime'] = 'bear'
        regime_timeline = []
        for i in range(200, len(df_ind)):
            regime_timeline.append({"date": df_ind.iloc[i]['Date_str'], "regime": df_ind.iloc[i].get('regime', 'sideways')})

        wf = run_walk_forward(req, df) if req.enable_walk_forward else []

        return {
            "metrics": metrics, "trades": trades, "equity_curve": equity_curve,
            "price_history": price_history, "heatmap": heatmap, "monte_carlo": mc,
            "walk_forward": wf, "duration_dist": dur_dist, "regime_stats": regime_stats,
            "regime_timeline": regime_timeline,
        }
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": str(e)}


# ── SPY Intraday Sparkline ─────────────────────────────────────────────────
@app.get("/api/spy/intraday")
def spy_intraday():
    """Fetch today's SPY 1-min bars for sparkline display."""
    try:
        df = yf.download("SPY", period="1d", interval="1m", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.reset_index(inplace=True)
        dt_col = "Datetime" if "Datetime" in df.columns else df.columns[0]
        data = []
        for _, row in df.iterrows():
            ts = row[dt_col]
            if hasattr(ts, "tz_localize"):
                ts = ts.tz_localize(None) if ts.tzinfo is None else ts.tz_convert(None)
            data.append({"time": str(ts), "close": round(float(row["Close"]), 2)})
        if not data:
            return {"data": [], "current": 0, "change": 0, "change_pct": 0}
        current = data[-1]["close"]
        day_open = data[0]["close"]
        change = round(current - day_open, 2)
        change_pct = round((change / day_open) * 100, 2) if day_open else 0
        return {"data": data, "current": current, "change": change, "change_pct": change_pct}
    except Exception as e:
        return {"error": str(e), "data": [], "current": 0, "change": 0, "change_pct": 0}


# ── Live Options Chain ─────────────────────────────────────────────────────
@app.get("/api/live_chain")
def live_chain(ticker: str = "SPY"):
    """Fetch current real options chain data."""
    try:
        t = yf.Ticker(ticker)
        exps = t.options
        if not exps: return {"error": "No options data available."}

        # Get the first 3 expirations
        chains = []
        for exp in exps[:3]:
            chain = t.option_chain(exp)
            calls = chain.calls[['strike', 'lastPrice', 'bid', 'ask', 'volume', 'openInterest', 'impliedVolatility']].head(10)
            puts = chain.puts[['strike', 'lastPrice', 'bid', 'ask', 'volume', 'openInterest', 'impliedVolatility']].head(10)
            chains.append({
                "expiration": exp,
                "calls": calls.fillna(0).to_dict(orient='records'),
                "puts": puts.fillna(0).to_dict(orient='records'),
            })

        info = t.info
        current_price = info.get('regularMarketPrice', info.get('previousClose', 0))

        return {"ticker": ticker, "price": current_price, "chains": chains}
    except Exception as e:
        return {"error": str(e)}


# ── Optimizer endpoint ─────────────────────────────────────────────────────
@app.post("/api/optimize")
def optimize(req: OptimizerRequest):
    try:
        raw_df = fetch_historical_data(req.base_config.ticker, req.base_config.years_history)
        df = raw_df.copy()
        if len(df) == 0: return {"error": "No data."}
        df['Date_str'] = df['Date'].dt.strftime('%Y-%m-%d')

        results = []
        for xv in req.x_values:
            for yv in req.y_values:
                cfg = req.base_config.model_copy()
                setattr(cfg, req.param_x, type(getattr(cfg, req.param_x))(xv))
                setattr(cfg, req.param_y, type(getattr(cfg, req.param_y))(yv))
                trades, _, _ = run_backtest_engine(cfg, df)
                pnl = sum(t['pnl'] for t in trades)
                wins = sum(1 for t in trades if t['win'])
                wr = (wins / len(trades) * 100) if trades else 0
                results.append({"x": float(xv), "y": float(yv), "pnl": round(pnl, 2),
                                "trades": len(trades), "win_rate": round(wr, 1)})

        return {"param_x": req.param_x, "param_y": req.param_y, "results": results}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": str(e)}


class PaperCredentials(BaseModel):
    api_key: str
    api_secret: str

class PaperOrderRequest(BaseModel):
    api_key: str
    api_secret: str
    symbol: str = "SPY"
    qty: int = 1
    side: str = "buy"

class PaperScanRequest(BaseModel):
    api_key: str
    api_secret: str
    config: dict = {}


@app.post("/api/paper/connect")
def paper_connect(creds: PaperCredentials):
    from paper_trading import check_connection
    return check_connection(creds.api_key, creds.api_secret)

@app.post("/api/paper/positions")
def paper_positions(creds: PaperCredentials):
    from paper_trading import get_positions
    return {"positions": get_positions(creds.api_key, creds.api_secret)}

@app.post("/api/paper/orders")
def paper_orders(creds: PaperCredentials):
    from paper_trading import get_orders
    return {"orders": get_orders(creds.api_key, creds.api_secret)}

@app.post("/api/paper/execute")
def paper_execute(req: PaperOrderRequest):
    from paper_trading import place_equity_order
    return place_equity_order(req.api_key, req.api_secret, req.symbol, req.qty, req.side)

@app.post("/api/paper/scan")
def paper_scan(req: PaperScanRequest):
    from paper_trading import scan_signal
    return scan_signal(req.api_key, req.api_secret, req.config)


# ── Unified Scanner & IBKR endpoints ───────────────────────────────────────

class ScannerConfigRequest(BaseModel):
    # timing_mode: "interval" | "after_open" | "before_close" | "on_open" | "on_close"
    timing_mode: str = "interval"
    timing_value: int = 300   # seconds for interval; minutes offset for after_open/before_close
    mode: str = "paper"       # "paper" or "ibkr"
    auto_execute: bool = False
    config: dict
    creds: dict = {}

def run_market_scan():
    """Background task for scanning market based on frequency."""
    if not scanner_state["active"]: return
    
    current_time = datetime.now().isoformat()
    config = scanner_state.get("config", {})
    mode = scanner_state.get("mode", "paper")
    
    try:
        from paper_trading import scan_signal
        # Perform the scan using the logic in paper_trading (which is generic)
        res = scan_signal("", "", config) # Alpaca keys not needed for just scanning YF data
        
        signal = res.get("signal", False)
        price = res.get("price", 0)
        rsi = res.get("rsi", 0)
        
        log_entry = {
            "time": current_time,
            "signal": signal,
            "price": price,
            "rsi": rsi,
            "msg": f"Scan completed. Signal: {'🟢' if signal else '⚪'}",
            "details": res
        }
        
        # Limit logs to last 50
        scanner_state["logs"].insert(0, log_entry)
        if len(scanner_state["logs"]) > 50:
            scanner_state["logs"].pop()
            
        scanner_state["last_run"] = current_time
        
        # Auto-execute when enabled
        if signal and scanner_state.get("auto_execute", False):
            mode = scanner_state.get("mode", "paper")
            creds = scanner_state.get("creds") or {}
            if mode == "paper" and creds.get("api_key"):
                from paper_trading import place_equity_order
                side = "sell" if config.get("direction", "bull") == "bear" else "buy"
                place_equity_order(creds["api_key"], creds["api_secret"],
                                   config.get("ticker", "SPY"),
                                   config.get("contracts_per_trade", 1) * 100, side)

    except Exception as e:
        print(f"Error in background scan: {e}")
        scanner_state["logs"].insert(0, {
            "time": current_time,
            "signal": False,
            "msg": f"❌ Error: {str(e)}"
        })

@app.post("/api/scanner/start")
def start_scanner(req: ScannerConfigRequest):
    scanner_state["active"] = True
    scanner_state["timing_mode"] = req.timing_mode
    scanner_state["timing_value"] = req.timing_value
    scanner_state["mode"] = req.mode
    scanner_state["config"] = req.config
    scanner_state["auto_execute"] = req.auto_execute

    # Remove existing scan jobs
    for job in scheduler.get_jobs():
        if job.id.startswith("market_scan"):
            job.remove()

    if req.timing_mode == "interval":
        secs = max(req.timing_value, 10)
        scheduler.add_job(run_market_scan, "interval", seconds=secs, id="market_scan")
    elif req.timing_mode == "after_open":
        # N minutes after 9:30 AM ET (UTC-4 in EDT, UTC-5 in EST)
        # Use 13:30 UTC as open reference (works for EDT; adjust for EST by using 14:30)
        total_mins = 30 + req.timing_value  # 9:30 ET + offset → minutes past 9:00
        hour = 9 + total_mins // 60
        minute = total_mins % 60
        scheduler.add_job(run_market_scan, "cron", hour=hour, minute=minute,
                          day_of_week="mon-fri", timezone="America/New_York", id="market_scan")
    elif req.timing_mode == "before_close":
        # N minutes before 4:00 PM ET
        total_mins = 16 * 60 - req.timing_value  # minutes from midnight ET
        hour = total_mins // 60
        minute = total_mins % 60
        scheduler.add_job(run_market_scan, "cron", hour=hour, minute=minute,
                          day_of_week="mon-fri", timezone="America/New_York", id="market_scan")
    elif req.timing_mode == "on_open":
        scheduler.add_job(run_market_scan, "cron", hour=9, minute=30,
                          day_of_week="mon-fri", timezone="America/New_York", id="market_scan")
    elif req.timing_mode == "on_close":
        scheduler.add_job(run_market_scan, "cron", hour=16, minute=0,
                          day_of_week="mon-fri", timezone="America/New_York", id="market_scan")

    return {"status": "started", "timing_mode": req.timing_mode, "timing_value": req.timing_value}

@app.post("/api/scanner/stop")
def stop_scanner():
    scanner_state["active"] = False
    scheduler.remove_all_jobs()
    return {"status": "stopped"}

@app.get("/api/scanner/status")
def get_scanner_status():
    return scanner_state

class IBKRConnectRequest(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1

@app.post("/api/ibkr/connect")
async def ibkr_connect(req: IBKRConnectRequest):
    trader, msg = await get_ib_connection(req.model_dump())
    if trader:
        summary = await trader.get_account_summary()
        return {"connected": True, "summary": summary}
    return {"connected": False, "error": msg}

@app.post("/api/ibkr/positions")
async def ibkr_positions(req: IBKRConnectRequest):
    trader, msg = await get_ib_connection(req.model_dump())
    if trader:
        pos = await trader.get_positions()
        return {"positions": pos}
    return {"error": msg}

class IBKROrderRequest(BaseModel):
    creds: IBKRConnectRequest
    symbol: str
    topology: str
    direction: str
    contracts: int
    strike_width: int = 5
    target_dte: int = 14

@app.post("/api/ibkr/execute")
async def ibkr_execute(req: IBKROrderRequest):
    trader, msg = await get_ib_connection(req.creds.model_dump())
    if not trader: return {"error": msg}
    
    # 1. Fetch current market context (approximate with latest data)
    raw_df = fetch_historical_data(req.symbol, 1)
    if len(raw_df) == 0: return {"error": "Could not fetch underlying data for strikes."}
    
    latest_row = raw_df.iloc[-1]
    S = float(latest_row['Close'])
    # Recompute HV or use a default
    log_ret = np.log(raw_df['Close'] / raw_df['Close'].shift(1))
    sigma = (log_ret.rolling(window=21).std().iloc[-1] * np.sqrt(252))
    if pd.isna(sigma): sigma = 0.20
    
    # 2. Construct legs based on topology
    T = req.target_dte / 365.25
    RISK_FREE_RATE = fetch_risk_free_rate()
    
    pos = OptionTopologyBuilder.construct_legs(
        topology=req.topology,
        direction=req.direction,
        S=S,
        T=T,
        r=RISK_FREE_RATE,
        sigma=sigma,
        target_cost=0, # Market order for live usually
        strike_width=req.strike_width
    )
    
    # 3. Format legs for IBKR (YYYMMDD string format)
    # Note: Modern IBKR requires proper expiry dates. 
    # For this backtester, we'll approximate the nearest Friday.
    # For this backtester, we'll approximate the nearest available daily.
    # SPY has M/W/F and now daily expiries. 
    # For simplicity, we search for the nearest date that isn't a weekend.
    import datetime
    today = datetime.date.today()
    expiry_date = today + datetime.timedelta(days=req.target_dte)
    while expiry_date.weekday() >= 5: # Saturday=5, Sunday=6
        expiry_date += datetime.timedelta(days=1)
    expiry_str = expiry_date.strftime('%Y%m%d')

    ib_legs = []
    for leg in pos["legs"]:
        ib_legs.append({
            "type": leg["type"],
            "strike": leg["strike"],
            "expiry": expiry_str,
            "side": leg["side"]
        })

    # 4. Fetch current midpoint for the combo
    midpoint = await trader.get_combo_midpoint(req.symbol, ib_legs)
    
    # 5. Place the order
    # For credit spreads, mid is usually negative. 
    # For debit spreads, mid is positive.
    # LimitOrder in combo structures typically uses exactly what the mid suggests.
    side = 'BUY' 
    res = await trader.place_combo_order(req.symbol, ib_legs, req.contracts, side=side, lmtPrice=midpoint)
    
    return res
@app.post("/api/ibkr/test_order")
async def ibkr_test_order(req: IBKRConnectRequest):
    trader, msg = await get_ib_connection(req.model_dump())
    if trader:
        res = await trader.place_test_order()
        return res
    return {"error": msg}

@app.get("/api/ibkr/orders")
async def ibkr_get_orders(host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
    trader, msg = await get_ib_connection({"host": host, "port": port, "client_id": client_id})
    if trader:
        orders = await trader.get_active_orders()
        return {"orders": orders}
    return {"error": msg}

@app.post("/api/ibkr/cancel")
async def ibkr_cancel_order(req: dict):
    # expect json { "creds": {...}, "orderId": 123 }
    trader, msg = await get_ib_connection(req.get("creds", {}))
    if trader:
        res = await trader.cancel_order(req.get("orderId"))
        return res
    return {"error": msg}


@app.post("/api/ibkr/heartbeat")
async def ibkr_heartbeat(req: IBKRConnectRequest):
    """Lightweight liveness check — does not reconnect, just reports status."""
    from ibkr_trading import _ib_instances, HAS_IBSYNC
    if not HAS_IBSYNC:
        return {"alive": False, "status": "unavailable"}
    key = f"{req.host}:{req.port}:{req.client_id}"
    trader = _ib_instances.get(key)
    if not trader:
        return {"alive": False, "status": "not_connected"}
    try:
        alive = trader.ib.isConnected()
        if not alive:
            trader.connected = False
        return {"alive": alive, "status": "online" if alive else "dropped"}
    except Exception as e:
        trader.connected = False
        return {"alive": False, "status": "error", "detail": str(e)}


@app.post("/api/ibkr/reconnect")
async def ibkr_reconnect(req: IBKRConnectRequest):
    """Force-reconnect an existing IBKR session."""
    from ibkr_trading import _ib_instances
    key = f"{req.host}:{req.port}:{req.client_id}"
    if key in _ib_instances:
        try:
            _ib_instances[key].ib.disconnect()
        except Exception:
            pass
        del _ib_instances[key]
    trader, msg = await get_ib_connection(req.model_dump())
    if trader:
        summary = await trader.get_account_summary()
        return {"connected": True, "summary": summary}
    return {"connected": False, "error": msg}
