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
    # New features
    use_vix_filter: bool = False
    vix_min: float = 15.0
    vix_max: float = 35.0
    use_regime_filter: bool = False
    regime_allowed: str = "all"  # "all", "bull", "bear", "sideways"

class OptimizerRequest(BaseModel):
    base_config: BacktestRequest = BacktestRequest()
    param_x: str = "entry_red_days"
    param_y: str = "target_dte"
    x_values: List[float] = [1, 2, 3, 4]
    y_values: List[float] = [7, 14, 21, 30]


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


# ── Black-Scholes ──────────────────────────────────────────────────────────
def bs_call_price(S, K, T, r, sigma):
    if T <= 0: return max(0.0, S - K)
    if sigma <= 0: return max(0.0, S - K * np.exp(-r * T))
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * si.norm.cdf(d1) - K * np.exp(-r * T) * si.norm.cdf(d2)

def bs_put_price(S, K, T, r, sigma):
    if T <= 0: return max(0.0, K - S)
    if sigma <= 0: return max(0.0, K * np.exp(-r * T) - S)
    return bs_call_price(S, K, T, r, sigma) - S + K * np.exp(-r * T)


# ── Indicators ─────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame, ema_length: int) -> pd.DataFrame:
    df = df.copy()
    df['is_green'] = df['Close'] > df['Open']
    df['is_red']   = df['Close'] < df['Open']

    def streak(col):
        s = col.astype(int)
        group = (col != col.shift()).cumsum()
        return s.groupby(group).cumsum().where(col, 0)

    df['greenDays'] = streak(df['is_green'])
    df['redDays']   = streak(df['is_red'])
    df[f'EMA_{ema_length}'] = df['Close'].ewm(span=ema_length, adjust=False).mean()
    df['SMA_200'] = df['Close'].rolling(window=200).mean()
    df['SMA_50']  = df['Close'].rolling(window=50).mean()
    df['Volume_MA'] = df['Volume'].rolling(window=10).mean()

    log_ret = np.log(df['Close'] / df['Close'].shift(1))
    df['HV_21'] = (log_ret.rolling(window=21).std() * np.sqrt(252)).fillna(0.15)

    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(50)

    # Regime detection: SMA-based classification
    df['regime'] = 'sideways'
    bull_mask = (df['Close'] > df['SMA_200']) & (df['SMA_50'] > df['SMA_200'])
    bear_mask = (df['Close'] < df['SMA_200']) & (df['SMA_50'] < df['SMA_200'])
    df.loc[bull_mask, 'regime'] = 'bull'
    df.loc[bear_mask, 'regime'] = 'bear'

    return df


# ── Core backtest engine ───────────────────────────────────────────────────
def run_backtest_engine(req: BacktestRequest, df: pd.DataFrame, start_idx: int = 200):
    RISK_FREE_RATE = 0.045
    is_bear = req.strategy_type == "bear_put"
    price_fn = bs_put_price if is_bear else bs_call_price

    trades, equity_curve = [], []
    equity = req.capital_allocation
    in_trade = False
    entry_idx = -1
    entry_dte = K_long = K_short = 0
    entry_cost = 0.0
    current_entry = {}
    max_eq_seen = req.capital_allocation

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        date = row['Date_str']

        # Mark-to-market
        if in_trade and req.use_mark_to_market:
            dh = i - entry_idx
            T_m = max((entry_dte - dh) / 365.25, 0.0)
            S_m = float(row['Close'])
            sig_m = max(float(row['HV_21']), 0.05)
            sc = current_entry.get("contracts", req.contracts_per_trade)
            mtm_val = (price_fn(S_m, K_long, T_m, RISK_FREE_RATE, sig_m) -
                       price_fn(S_m, K_short, T_m, RISK_FREE_RATE, sig_m)) * 100 * sc
            mtm_equity = equity + mtm_val
        else:
            mtm_equity = equity

        if mtm_equity > max_eq_seen: max_eq_seen = mtm_equity
        dd = ((mtm_equity - max_eq_seen) / max_eq_seen) * 100 if max_eq_seen > 0 else 0
        equity_curve.append({"date": date, "equity": round(mtm_equity, 2), "drawdown": round(dd, 2)})

        if not in_trade:
            # Entry logic
            if is_bear:
                streak_val = int(row['greenDays'])
            else:
                streak_val = int(row['redDays'])
            entry_trigger = (streak_val == req.entry_red_days or streak_val == req.entry_red_days + 1)

            if entry_trigger:
                allow = True

                # Standard filters
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

                # Regime filter
                if req.use_regime_filter and req.regime_allowed != "all":
                    if row.get('regime', 'sideways') != req.regime_allowed: allow = False

                if allow:
                    S = float(row['Close'])
                    sigma = max(float(row['HV_21']), 0.05)
                    T = req.target_dte / 365.25
                    tgt = req.spread_cost_target / 100.0

                    if is_bear:
                        K1 = round(S)
                        p1 = bs_put_price(S, K1, T, RISK_FREE_RATE, sigma)
                        best_K2, best_diff, fp2 = K1 - 5, float('inf'), 0.0
                        for ks in range(K1 - 1, max(K1 - 41, 1), -1):
                            p2 = bs_put_price(S, ks, T, RISK_FREE_RATE, sigma)
                            c = p1 - p2
                            if c < 0: break
                            d = abs(c - tgt)
                            if d < best_diff: best_diff, best_K2, fp2 = d, ks, p2
                        K_long, K_short = K1, best_K2
                        one_cc = (p1 - fp2) * 100
                    else:
                        K1 = round(S)
                        c1 = bs_call_price(S, K1, T, RISK_FREE_RATE, sigma)
                        best_K2, best_diff, fc2 = K1 + 5, float('inf'), 0.0
                        for ks in range(K1 + 1, K1 + 41):
                            c2 = bs_call_price(S, ks, T, RISK_FREE_RATE, sigma)
                            c = c1 - c2
                            if c < 0: break
                            d = abs(c - tgt)
                            if d < best_diff: best_diff, best_K2, fc2 = d, ks, c2
                        K_long, K_short = K1, best_K2
                        one_cc = (c1 - fc2) * 100

                    if one_cc <= 0: continue

                    if req.use_dynamic_sizing:
                        ds = equity * (req.risk_percent / 100.0)
                        if req.max_trade_cap > 0: ds = min(ds, req.max_trade_cap)
                        contracts = int(max(1, ds // one_cc))
                    else:
                        contracts = req.contracts_per_trade

                    entry_cost = one_cc * contracts
                    comm = req.commission_per_contract * contracts * 2
                    if entry_cost + comm > equity: continue

                    equity -= entry_cost + comm
                    in_trade, entry_idx, entry_dte = True, i, req.target_dte
                    current_entry = {"entry_date": date, "entry_spy": S, "spread_cost": entry_cost,
                                     "contracts": contracts, "commission": comm,
                                     "regime": row.get('regime', 'unknown')}
        else:
            # Exit logic
            days_held = i - entry_idx
            new_dte = entry_dte - days_held
            T_c = max(new_dte / 365.25, 0.0)
            S = float(row['Close'])
            sigma = max(float(row['HV_21']), 0.05)
            sc = current_entry.get("contracts", req.contracts_per_trade)
            cv = (price_fn(S, K_long, T_c, RISK_FREE_RATE, sigma) -
                  price_fn(S, K_short, T_c, RISK_FREE_RATE, sigma)) * 100 * sc

            exit_streak = int(row['redDays'] if is_bear else row['greenDays']) >= req.exit_green_days
            stop_exit = req.stop_loss_pct > 0 and cv <= entry_cost * (1 - req.stop_loss_pct / 100)
            expired = new_dte <= 0

            if exit_streak or stop_exit or expired:
                ec = req.commission_per_contract * sc * 2
                equity += cv - ec
                tc = current_entry.get("commission", 0) + ec
                pnl = cv - entry_cost - tc
                trades.append({
                    "entry_date": current_entry["entry_date"], "exit_date": date,
                    "entry_spy": round(current_entry["entry_spy"], 2), "exit_spy": round(S, 2),
                    "spread_cost": round(entry_cost, 2), "spread_exit": round(cv, 2),
                    "pnl": round(float(pnl), 2), "contracts": sc, "days_held": days_held,
                    "commission": round(tc, 2), "win": bool(pnl > 0),
                    "stopped_out": bool(stop_exit and not exit_streak),
                    "expired": bool(expired and not exit_streak and not stop_exit),
                    "regime": current_entry.get("regime", "unknown"),
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


# ── Main endpoint ──────────────────────────────────────────────────────────
@app.post("/api/backtest")
def backtest(req: BacktestRequest):
    try:
        raw_df = fetch_historical_data(req.ticker, req.years_history)
        df = compute_indicators(raw_df, req.ema_length)
        if len(df) == 0: return {"error": "No data returned."}

        # Merge VIX data if filter enabled
        if req.use_vix_filter:
            vix_df = fetch_vix_data(req.years_history)
            if not vix_df.empty:
                df = df.merge(vix_df, on='Date', how='left')
                df['VIX'] = df['VIX'].fillna(method='ffill').fillna(20)

        df['Date_str'] = df['Date'].dt.strftime('%Y-%m-%d')

        ph = df[['Date_str', 'Open', 'High', 'Low', 'Close']].copy()
        ph.columns = ['time', 'open', 'high', 'low', 'close']
        for c in ['open', 'high', 'low', 'close']: ph[c] = ph[c].round(2)
        price_history = ph.to_dict(orient='records')

        # Regime timeline for chart overlay
        regime_timeline = []
        for i in range(200, len(df)):
            regime_timeline.append({"date": df.iloc[i]['Date_str'], "regime": df.iloc[i].get('regime', 'sideways')})

        trades, equity_curve, equity = run_backtest_engine(req, df)
        metrics, heatmap, mc, dur_dist, regime_stats = compute_analytics(trades, equity_curve, req)

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
        df = compute_indicators(raw_df, req.base_config.ema_length)
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


# ── Paper Trading endpoints ────────────────────────────────────────────────
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

