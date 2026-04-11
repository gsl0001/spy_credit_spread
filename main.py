from functools import lru_cache
from typing import List, Optional
import yfinance as yf
import pandas as pd
import numpy as np
import scipy.stats as si
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="SPY Options Backtesting Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BacktestRequest(BaseModel):
    ticker: str = "SPY"
    years_history: int = 2
    capital_allocation: float = 10000.0
    contracts_per_trade: int = 1
    use_dynamic_sizing: bool = False
    risk_percent: float = 5.0
    max_trade_cap: float = 0.0
    spread_cost_target: float = 250.0
    # Strategy type: "bull_call" or "bear_put"
    strategy_type: str = "bull_call"
    entry_red_days: int = 2
    exit_green_days: int = 2
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

class OptimizerRequest(BaseModel):
    base_config: BacktestRequest = BacktestRequest()
    param_x: str = "entry_red_days"
    param_y: str = "target_dte"
    x_values: List[float] = [1, 2, 3, 4]
    y_values: List[float] = [7, 14, 21, 30]


# ── Data fetching (cached on raw OHLCV only) ──────────────────────────────
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


# ── Black-Scholes European option prices ──────────────────────────────────
def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0:
        return max(0.0, S - K)
    if sigma <= 0:
        return max(0.0, S - K * np.exp(-r * T))
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * si.norm.cdf(d1) - K * np.exp(-r * T) * si.norm.cdf(d2)


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Put price via put-call parity: P = C - S + K*e^(-rT)"""
    if T <= 0:
        return max(0.0, K - S)
    if sigma <= 0:
        return max(0.0, K * np.exp(-r * T) - S)
    c = bs_call_price(S, K, T, r, sigma)
    return c - S + K * np.exp(-r * T)


# ── Indicator computation ───────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame, ema_length: int) -> pd.DataFrame:
    df = df.copy()

    df['is_green'] = df['Close'] > df['Open']
    df['is_red']   = df['Close'] < df['Open']

    def streak(col: pd.Series) -> pd.Series:
        s = col.astype(int)
        group = (col != col.shift()).cumsum()
        cumulative = s.groupby(group).cumsum()
        return cumulative.where(col, 0)

    df['greenDays'] = streak(df['is_green'])
    df['redDays']   = streak(df['is_red'])

    df[f'EMA_{ema_length}'] = df['Close'].ewm(span=ema_length, adjust=False).mean()
    df['SMA_200'] = df['Close'].rolling(window=200).mean()
    df['Volume_MA'] = df['Volume'].rolling(window=10).mean()

    log_ret = np.log(df['Close'] / df['Close'].shift(1))
    hv = log_ret.rolling(window=21).std() * np.sqrt(252)
    df['HV_21'] = hv.fillna(0.15)

    delta = df['Close'].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI'] = df['RSI'].fillna(50)

    return df


# ── Core backtest engine (reusable for optimizer + walk-forward) ────────────
def run_backtest_engine(req: BacktestRequest, df: pd.DataFrame, start_idx: int = 200):
    """Run a single backtest pass on prepared data. Returns (trades, equity_curve, equity)."""
    RISK_FREE_RATE = 0.045
    is_bear = req.strategy_type == "bear_put"
    price_fn = bs_put_price if is_bear else bs_call_price

    trades       = []
    equity_curve = []
    equity       = req.capital_allocation

    in_trade     = False
    entry_idx    = -1
    entry_dte    = 0
    K_long       = 0
    K_short      = 0
    entry_cost   = 0.0
    current_entry = {}

    max_eq_seen = req.capital_allocation
    for i in range(start_idx, len(df)):
        row  = df.iloc[i]
        date = row['Date_str']

        # ── Mark-to-market: re-price open position daily ──
        if in_trade and req.use_mark_to_market:
            days_held_mtm = i - entry_idx
            new_dte_mtm   = entry_dte - days_held_mtm
            T_mtm         = max(new_dte_mtm / 365.25, 0.0)
            S_mtm         = float(row['Close'])
            sigma_mtm     = max(float(row['HV_21']), 0.05)
            saved_c       = current_entry.get("contracts", req.contracts_per_trade)

            p1 = price_fn(S_mtm, K_long,  T_mtm, RISK_FREE_RATE, sigma_mtm)
            p2 = price_fn(S_mtm, K_short, T_mtm, RISK_FREE_RATE, sigma_mtm)

            if is_bear:
                mtm_value = (p1 - p2) * 100 * saved_c  # long put - short put
            else:
                mtm_value = (p1 - p2) * 100 * saved_c  # long call - short call

            mtm_equity = equity + mtm_value  # cash + unrealized position value
        else:
            mtm_equity = equity

        if mtm_equity > max_eq_seen:
            max_eq_seen = mtm_equity
        dd = ((mtm_equity - max_eq_seen) / max_eq_seen) * 100 if max_eq_seen > 0 else 0

        equity_curve.append({"date": date, "equity": round(mtm_equity, 2), "drawdown": round(dd, 2)})

        if not in_trade:
            # ── ENTRY LOGIC ────────────────────────────────────────────
            if is_bear:
                # Bear put spread: enter after consecutive GREEN days
                streak_val = int(row['greenDays'])
                entry_trigger = (streak_val == req.entry_red_days or streak_val == req.entry_red_days + 1)
            else:
                # Bull call spread: enter after consecutive RED days
                streak_val = int(row['redDays'])
                entry_trigger = (streak_val == req.entry_red_days or streak_val == req.entry_red_days + 1)

            if entry_trigger:
                allow_entry = True

                if is_bear:
                    # Bear: RSI overbought filter (inverted)
                    if req.use_rsi_filter and float(row['RSI']) <= (100 - req.rsi_threshold):
                        allow_entry = False
                    # Bear: price ABOVE EMA (strong — expecting reversal down)
                    if req.use_ema_filter and float(row['Close']) <= float(row[f'EMA_{req.ema_length}']):
                        allow_entry = False
                    if req.use_sma200_filter and float(row['Close']) >= float(row['SMA_200']):
                        allow_entry = False
                else:
                    # Bull: original filter logic
                    if req.use_rsi_filter and float(row['RSI']) >= req.rsi_threshold:
                        allow_entry = False
                    if req.use_ema_filter and float(row['Close']) >= float(row[f'EMA_{req.ema_length}']):
                        allow_entry = False
                    if req.use_sma200_filter and float(row['Close']) <= float(row['SMA_200']):
                        allow_entry = False

                if req.use_volume_filter and float(row['Volume']) <= float(row['Volume_MA']):
                    allow_entry = False

                if allow_entry:
                    S     = float(row['Close'])
                    sigma = max(float(row['HV_21']), 0.05)
                    T     = req.target_dte / 365.25

                    target_cost_per_share = req.spread_cost_target / 100.0

                    if is_bear:
                        # Bear put spread: buy ATM put, sell lower-strike put
                        K1 = round(S)  # ATM long put
                        p1 = bs_put_price(S, K1, T, RISK_FREE_RATE, sigma)

                        best_K2   = K1 - 5
                        best_diff = float('inf')
                        final_p1  = p1
                        final_p2  = 0.0

                        for ks in range(K1 - 1, max(K1 - 41, 1), -1):
                            p2   = bs_put_price(S, ks, T, RISK_FREE_RATE, sigma)
                            cost = p1 - p2
                            if cost < 0:
                                break
                            diff = abs(cost - target_cost_per_share)
                            if diff < best_diff:
                                best_diff = diff
                                best_K2   = ks
                                final_p2  = p2

                        K_long  = K1
                        K_short = best_K2
                        one_contract_cost = (final_p1 - final_p2) * 100
                    else:
                        # Bull call spread: buy ATM call, sell higher-strike call
                        K1 = round(S)
                        c1 = bs_call_price(S, K1, T, RISK_FREE_RATE, sigma)

                        best_K2   = K1 + 5
                        best_diff = float('inf')
                        final_c1  = c1
                        final_c2  = 0.0

                        for ks in range(K1 + 1, K1 + 41):
                            c2   = bs_call_price(S, ks, T, RISK_FREE_RATE, sigma)
                            cost = c1 - c2
                            if cost < 0:
                                break
                            diff = abs(cost - target_cost_per_share)
                            if diff < best_diff:
                                best_diff = diff
                                best_K2   = ks
                                final_c2  = c2

                        K_long  = K1
                        K_short = best_K2
                        one_contract_cost = (final_c1 - final_c2) * 100

                    if one_contract_cost <= 0:
                        continue

                    if req.use_dynamic_sizing:
                        desired_size = equity * (req.risk_percent / 100.0)
                        if req.max_trade_cap > 0:
                            desired_size = min(desired_size, req.max_trade_cap)
                        contracts = int(max(1, desired_size // one_contract_cost))
                    else:
                        contracts = req.contracts_per_trade

                    entry_cost = one_contract_cost * contracts
                    commission = req.commission_per_contract * contracts * 2
                    total_cost = entry_cost + commission

                    if total_cost > equity:
                        continue

                    equity   -= total_cost
                    in_trade  = True
                    entry_idx = i
                    entry_dte = req.target_dte
                    current_entry = {
                        "entry_date": date,
                        "entry_spy":  S,
                        "spread_cost": entry_cost,
                        "contracts": contracts,
                        "commission": commission,
                    }

        else:
            # ── EXIT LOGIC ─────────────────────────────────────────────
            days_held   = i - entry_idx
            new_dte     = entry_dte - days_held
            T_current   = max(new_dte / 365.25, 0.0)
            S           = float(row['Close'])
            sigma       = max(float(row['HV_21']), 0.05)

            p1_now = price_fn(S, K_long,  T_current, RISK_FREE_RATE, sigma)
            p2_now = price_fn(S, K_short, T_current, RISK_FREE_RATE, sigma)
            saved_contracts = current_entry.get("contracts", req.contracts_per_trade)
            current_value = (p1_now - p2_now) * 100 * saved_contracts

            if is_bear:
                # Bear: exit on consecutive RED days (market moved in your favor)
                exit_streak = int(row['redDays']) >= req.exit_green_days
            else:
                # Bull: exit on consecutive GREEN days
                exit_streak = int(row['greenDays']) >= req.exit_green_days

            stop_exit  = (req.stop_loss_pct > 0
                          and current_value <= entry_cost * (1 - req.stop_loss_pct / 100))
            expired    = new_dte <= 0

            if exit_streak or stop_exit or expired:
                exit_commission = req.commission_per_contract * saved_contracts * 2
                equity += current_value - exit_commission
                total_commission = current_entry.get("commission", 0) + exit_commission
                pnl     = current_value - entry_cost - total_commission
                trades.append({
                    "entry_date":  current_entry["entry_date"],
                    "exit_date":   date,
                    "entry_spy":   round(current_entry["entry_spy"], 2),
                    "exit_spy":    round(S, 2),
                    "spread_cost": round(entry_cost, 2),
                    "spread_exit": round(current_value, 2),
                    "pnl":         round(float(pnl), 2),
                    "contracts":   saved_contracts,
                    "days_held":   days_held,
                    "commission":  round(total_commission, 2),
                    "win":         bool(pnl > 0),
                    "stopped_out": bool(stop_exit and not exit_streak),
                    "expired":     bool(expired and not exit_streak and not stop_exit),
                })
                in_trade = False

    return trades, equity_curve, equity


# ── Analytics computation ──────────────────────────────────────────────────
def compute_analytics(trades, equity_curve, req):
    from datetime import datetime
    from collections import defaultdict

    total_pnl = sum(t['pnl'] for t in trades)
    wins      = sum(1 for t in trades if t['win'])
    losses    = len(trades) - wins
    win_rate  = (wins / len(trades) * 100) if trades else 0.0
    avg_pnl   = total_pnl / len(trades) if trades else 0.0

    # Profit factor
    gross_wins  = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gross_losses = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0))
    profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else float('inf') if gross_wins > 0 else 0.0

    # Max consecutive losses
    max_consec_losses = 0
    current_streak = 0
    for t in trades:
        if not t['win']:
            current_streak += 1
            max_consec_losses = max(max_consec_losses, current_streak)
        else:
            current_streak = 0

    # Hold days
    hold_days = [t.get('days_held', 0) for t in trades]
    avg_hold_days = sum(hold_days) / len(hold_days) if hold_days else 0.0

    # Heatmap
    heatmap_data = []
    if trades:
        stats = defaultdict(lambda: {"wins": 0, "total": 0})
        for t in trades:
            ed = datetime.strptime(t['entry_date'], '%Y-%m-%d')
            dname = ed.strftime('%a')
            mname = ed.strftime('%b')
            stats[f"{dname}-{mname}"]["total"] += 1
            if t['win']:
                stats[f"{dname}-{mname}"]["wins"] += 1
        for k, v in stats.items():
            day, month = k.split('-')
            hm_wr = (v["wins"] / v["total"]) * 100
            heatmap_data.append({"day": day, "month": month, "win_rate": round(hm_wr, 2), "total": v["total"]})

    # Sharpe & Sortino
    daily_returns = []
    for i in range(1, len(equity_curve)):
        prev_eq = equity_curve[i-1]['equity']
        curr_eq = equity_curve[i]['equity']
        ret = (curr_eq - prev_eq) / prev_eq if prev_eq > 0 else 0
        daily_returns.append(ret)

    returns_arr = np.array(daily_returns)
    sharpe = 0.0
    sortino = 0.0
    max_dd = min((item['drawdown'] for item in equity_curve), default=0.0)
    max_dd_dollars = 0.0

    if len(equity_curve) > 0:
        equities = [e['equity'] for e in equity_curve]
        peak = equities[0]
        for eq in equities:
            if eq > peak:
                peak = eq
            dd_d = peak - eq
            if dd_d > max_dd_dollars:
                max_dd_dollars = dd_d

    # Recovery factor
    recovery_factor = round(total_pnl / max_dd_dollars, 2) if max_dd_dollars > 0 else 0.0

    if len(returns_arr) > 0:
        std_dev = returns_arr.std(ddof=1) if len(returns_arr) > 1 else 0
        if std_dev > 0:
            sharpe = (returns_arr.mean() * 252) / (std_dev * np.sqrt(252))
        neg_ret = returns_arr[returns_arr < 0]
        neg_std = neg_ret.std(ddof=1) if len(neg_ret) > 1 else 0
        if neg_std > 0:
            sortino = (returns_arr.mean() * 252) / (neg_std * np.sqrt(252))
        elif std_dev > 0 and len(neg_ret) == 0:
            sortino = sharpe

    # Monte Carlo
    monte_carlo = {
        "p05": req.capital_allocation, "p50": req.capital_allocation,
        "p95": req.capital_allocation, "prob_profit": 0.0,
        "ev": req.capital_allocation, "distribution": []
    }
    if trades:
        trade_pnls = [t['pnl'] for t in trades]
        n_trades = len(trade_pnls)
        mc_finals = []
        for _ in range(1000):
            sample = np.random.choice(trade_pnls, size=n_trades, replace=True)
            mc_finals.append(req.capital_allocation + np.sum(sample))

        mc_finals = np.array(mc_finals)
        prob_profit = float(np.sum(mc_finals > req.capital_allocation)) / len(mc_finals) * 100

        # Build histogram distribution (20 bins)
        distribution = []
        if req.enable_mc_histogram:
            hist_counts, hist_edges = np.histogram(mc_finals, bins=20)
            for j in range(len(hist_counts)):
                mid = (hist_edges[j] + hist_edges[j+1]) / 2
                distribution.append({
                    "bin": round(float(mid), 0),
                    "count": int(hist_counts[j]),
                    "profitable": bool(mid > req.capital_allocation)
                })

        monte_carlo = {
            "p05": round(float(np.percentile(mc_finals, 5)), 2),
            "p50": round(float(np.percentile(mc_finals, 50)), 2),
            "p95": round(float(np.percentile(mc_finals, 95)), 2),
            "prob_profit": round(prob_profit, 1),
            "ev": round(float(np.mean(mc_finals)), 2),
            "distribution": distribution,
        }

    metrics = {
        "total_trades":       len(trades),
        "win_rate":           round(win_rate, 2),
        "total_pnl":          round(total_pnl, 2),
        "final_equity":       round(req.capital_allocation + total_pnl, 2),
        "avg_pnl":            round(avg_pnl, 2),
        "avg_hold_days":      round(avg_hold_days, 1),
        "sharpe_ratio":       round(sharpe, 2),
        "sortino_ratio":      round(sortino, 2),
        "max_drawdown":       round(max_dd, 2),
        "profit_factor":      profit_factor if profit_factor != float('inf') else 999.99,
        "max_consec_losses":  max_consec_losses,
        "recovery_factor":    recovery_factor,
    }

    return metrics, heatmap_data, monte_carlo


# ── Walk-Forward Validation ────────────────────────────────────────────────
def run_walk_forward(req: BacktestRequest, df: pd.DataFrame):
    """Split data into N windows and run backtest on each."""
    total_bars = len(df) - 200  # usable bars after warm-up
    window_size = total_bars // req.walk_forward_windows
    if window_size < 20:
        return []

    results = []
    for w in range(req.walk_forward_windows):
        w_start = 200 + w * window_size
        w_end   = w_start + window_size if w < req.walk_forward_windows - 1 else len(df)
        window_df = df.iloc[:w_end].copy()  # include warm-up data

        trades, eq_curve, equity = run_backtest_engine(req, window_df, start_idx=w_start)

        start_date = df.iloc[w_start]['Date_str'] if w_start < len(df) else ""
        end_date   = df.iloc[min(w_end - 1, len(df) - 1)]['Date_str']

        wins = sum(1 for t in trades if t['win'])
        total_pnl = sum(t['pnl'] for t in trades)
        wr = (wins / len(trades) * 100) if trades else 0

        results.append({
            "window":     w + 1,
            "start_date": start_date,
            "end_date":   end_date,
            "trades":     len(trades),
            "win_rate":   round(wr, 1),
            "pnl":        round(total_pnl, 2),
            "profitable": bool(total_pnl > 0),
        })

    return results


# ── Main backtest endpoint ──────────────────────────────────────────────────
@app.post("/api/backtest")
def backtest(req: BacktestRequest):
    try:
        raw_df = fetch_historical_data(req.ticker, req.years_history)
        df = compute_indicators(raw_df, req.ema_length)

        if len(df) == 0:
            return {"error": "No data returned from Yahoo Finance."}

        df['Date_str'] = df['Date'].dt.strftime('%Y-%m-%d')

        # Build price_history (vectorized)
        ph = df[['Date_str', 'Open', 'High', 'Low', 'Close']].copy()
        ph.columns = ['time', 'open', 'high', 'low', 'close']
        for c in ['open', 'high', 'low', 'close']:
            ph[c] = ph[c].round(2)
        price_history = ph.to_dict(orient='records')

        # Run main backtest
        trades, equity_curve, equity = run_backtest_engine(req, df)

        # Compute analytics
        metrics, heatmap_data, monte_carlo = compute_analytics(trades, equity_curve, req)

        # Walk-forward validation
        walk_forward = []
        if req.enable_walk_forward:
            walk_forward = run_walk_forward(req, df)

        return {
            "metrics":       metrics,
            "trades":        trades,
            "equity_curve":  equity_curve,
            "price_history": price_history,
            "heatmap":       heatmap_data,
            "monte_carlo":   monte_carlo,
            "walk_forward":  walk_forward,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


# ── Parameter Optimizer endpoint ────────────────────────────────────────────
@app.post("/api/optimize")
def optimize(req: OptimizerRequest):
    try:
        raw_df = fetch_historical_data(req.base_config.ticker, req.base_config.years_history)
        df = compute_indicators(raw_df, req.base_config.ema_length)

        if len(df) == 0:
            return {"error": "No data."}

        df['Date_str'] = df['Date'].dt.strftime('%Y-%m-%d')

        results = []
        for xv in req.x_values:
            for yv in req.y_values:
                cfg = req.base_config.model_copy()
                setattr(cfg, req.param_x, type(getattr(cfg, req.param_x))(xv))
                setattr(cfg, req.param_y, type(getattr(cfg, req.param_y))(yv))

                trades, eq_curve, equity = run_backtest_engine(cfg, df)
                total_pnl = sum(t['pnl'] for t in trades)
                wins = sum(1 for t in trades if t['win'])
                wr = (wins / len(trades) * 100) if trades else 0

                results.append({
                    "x": float(xv),
                    "y": float(yv),
                    "pnl": round(total_pnl, 2),
                    "trades": len(trades),
                    "win_rate": round(wr, 1),
                })

        return {
            "param_x": req.param_x,
            "param_y": req.param_y,
            "results": results,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}
