from functools import lru_cache
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
    entry_red_days: int = 2
    exit_green_days: int = 2
    target_dte: int = 14
    stop_loss_pct: float = 0
    use_rsi_filter: bool = True
    rsi_threshold: int = 30
    use_ema_filter: bool = True
    ema_length: int = 10
    use_sma200_filter: bool = False
    use_volume_filter: bool = False


# ── Data fetching (cached on raw OHLCV only, before indicator computation) ──
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
    # Return immutable state — callers must .copy() before mutation
    return df


# ── Black-Scholes European call price ──────────────────────────────────────
def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Returns the Black-Scholes price of a European call option."""
    if T <= 0:
        return max(0.0, S - K)
    if sigma <= 0:
        return max(0.0, S - K * np.exp(-r * T))
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * si.norm.cdf(d1) - K * np.exp(-r * T) * si.norm.cdf(d2)


# ── Indicator computation ───────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame, ema_length: int) -> pd.DataFrame:
    """
    Computes all technical indicators on a COPY of the DataFrame.
    Note: ema_length is passed explicitly so the cache key is correct.
    """
    df = df.copy()

    # Candle direction
    df['is_green'] = df['Close'] > df['Open']
    df['is_red']   = df['Close'] < df['Open']

    # FIX 1: Correct consecutive streak counters that properly reset to 0.
    # Method: for each row, count how many consecutive True values precede+include it.
    def streak(col: pd.Series) -> pd.Series:
        """Returns a series where each value is the current consecutive True-run length."""
        s = col.astype(int)
        # Group by run: each time col flips, create a new group id
        group = (col != col.shift()).cumsum()
        cumulative = s.groupby(group).cumsum()
        # Zero out positions where the value is False (not in a True streak)
        return cumulative.where(col, 0)

    df['greenDays'] = streak(df['is_green'])
    df['redDays']   = streak(df['is_red'])

    # EMA — span=ema_length as per spec
    df[f'EMA_{ema_length}'] = df['Close'].ewm(span=ema_length, adjust=False).mean()

    # SMA 200
    df['SMA_200'] = df['Close'].rolling(window=200).mean()

    # Volume MA (10-day)
    df['Volume_MA'] = df['Volume'].rolling(window=10).mean()

    # Historical volatility (21-day rolling log-return std × √252)
    log_ret = np.log(df['Close'] / df['Close'].shift(1))
    hv = log_ret.rolling(window=21).std() * np.sqrt(252)
    df['HV_21'] = hv.fillna(0.15)  # 15% fallback for the warm-up period

    # FIX 2: Wilder's RSI (exponential smoothing α=1/14), not simple rolling mean.
    delta = df['Close'].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI'] = df['RSI'].fillna(50)  # neutral fill for warm-up bars

    return df


# ── Main backtest endpoint ──────────────────────────────────────────────────
@app.post("/api/backtest")
def backtest(req: BacktestRequest):
    try:
        # FIX 3: fetch cached raw data, then compute indicators on a fresh copy
        # so lru_cache never stores mutated state.
        raw_df = fetch_historical_data(req.ticker, req.years_history)
        df = compute_indicators(raw_df, req.ema_length)

        if len(df) == 0:
            return {"error": "No data returned from Yahoo Finance."}

        RISK_FREE_RATE = 0.045

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

        # Build price_history list for the frontend chart
        df['Date_str'] = df['Date'].dt.strftime('%Y-%m-%d')
        price_history = [
            {
                "time":  row['Date_str'],
                "open":  round(float(row['Open']),  2),
                "high":  round(float(row['High']),  2),
                "low":   round(float(row['Low']),   2),
                "close": round(float(row['Close']), 2),
            }
            for _, row in df.iterrows()
        ]

        # Start loop after warm-up (200 bars for SMA_200)
        max_eq_seen = req.capital_allocation
        for i in range(200, len(df)):
            row  = df.iloc[i]
            date = row['Date_str']

            if equity > max_eq_seen:
                max_eq_seen = equity
            dd = ((equity - max_eq_seen) / max_eq_seen) * 100 if max_eq_seen > 0 else 0

            equity_curve.append({"date": date, "equity": round(equity, 2), "drawdown": round(dd, 2)})

            if not in_trade:
                # ── ENTRY LOGIC ────────────────────────────────────────────
                # FIX 4: Entry fires on EXACTLY entry_red_days OR entry_red_days+1.
                # Using >= would enter on day 5, 6, 7... of a crash indefinitely.
                red = int(row['redDays'])
                entry_trigger = (red == req.entry_red_days or red == req.entry_red_days + 1)

                if entry_trigger:
                    allow_entry = True

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
                        sigma = max(float(row['HV_21']), 0.05)  # floor at 5% to avoid BS degeneracy
                        T     = req.target_dte / 365.25

                        K1 = round(S)  # ATM long strike

                        # FIX 5: target_cost is per-share spread width (divide $250 by 100 shares).
                        # Do NOT divide by contracts_per_trade — that's the per-share target price,
                        # which is instrument-level and doesn't change with position size.
                        target_cost_per_share = req.spread_cost_target / 100.0

                        # FIX 6: Pre-compute c1 once — it's the same for every short-strike scan.
                        c1 = bs_call_price(S, K1, T, RISK_FREE_RATE, sigma)

                        best_K2   = K1 + 5  # sensible fallback
                        best_diff = float('inf')
                        final_c1  = c1
                        final_c2  = 0.0

                        for ks in range(K1 + 1, K1 + 41):
                            c2   = bs_call_price(S, ks, T, RISK_FREE_RATE, sigma)
                            cost = c1 - c2
                            if cost < 0:
                                break  # short call priced above long — stop scanning
                            diff = abs(cost - target_cost_per_share)
                            if diff < best_diff:
                                best_diff = diff
                                best_K2   = ks
                                final_c2  = c2

                        K_long     = K1
                        K_short    = best_K2
                        
                        one_contract_cost = (final_c1 - final_c2) * 100
                        if one_contract_cost <= 0:
                            continue  # bad pricing — skip this signal
                            
                        if req.use_dynamic_sizing:
                             desired_size = equity * (req.risk_percent / 100.0)
                             if req.max_trade_cap > 0:
                                 desired_size = min(desired_size, req.max_trade_cap)
                             contracts = int(max(1, desired_size // one_contract_cost))
                        else:
                             contracts = req.contracts_per_trade

                        entry_cost = one_contract_cost * contracts

                        equity   -= entry_cost
                        in_trade  = True
                        entry_idx  = i
                        entry_dte  = req.target_dte
                        current_entry = {
                            "entry_date": date,
                            "entry_spy":  S,
                            "spread_cost": entry_cost,
                            "contracts": contracts,
                        }

            else:
                # ── EXIT LOGIC ─────────────────────────────────────────────
                days_held   = i - entry_idx
                new_dte     = entry_dte - days_held
                T_current   = max(new_dte / 365.25, 0.0)
                S           = float(row['Close'])
                sigma       = max(float(row['HV_21']), 0.05)

                c1_now = bs_call_price(S, K_long,  T_current, RISK_FREE_RATE, sigma)
                c2_now = bs_call_price(S, K_short, T_current, RISK_FREE_RATE, sigma)
                saved_contracts = current_entry.get("contracts", req.contracts_per_trade)
                current_value = (c1_now - c2_now) * 100 * saved_contracts

                green_exit = int(row['greenDays']) >= req.exit_green_days
                stop_exit  = (req.stop_loss_pct > 0
                              and current_value <= entry_cost * (1 - req.stop_loss_pct / 100))
                expired    = new_dte <= 0

                if green_exit or stop_exit or expired:
                    equity += current_value
                    pnl     = current_value - entry_cost
                    trades.append({
                        "entry_date":  current_entry["entry_date"],
                        "exit_date":   date,
                        "entry_spy":   round(current_entry["entry_spy"], 2),
                        "exit_spy":    round(S, 2),
                        "spread_cost": round(entry_cost, 2),
                        "spread_exit": round(current_value, 2),
                        "pnl":         round(float(pnl), 2),
                        "win":         bool(pnl > 0),
                        "stopped_out": bool(stop_exit and not green_exit),
                        "expired":     bool(expired and not green_exit and not stop_exit),
                    })
                    in_trade = False

        # ── Metrics ────────────────────────────────────────────────────────
        total_pnl = equity - req.capital_allocation
        wins      = sum(1 for t in trades if t['win'])
        win_rate  = (wins / len(trades) * 100) if trades else 0.0

        from datetime import datetime
        avg_pnl = total_pnl / len(trades) if trades else 0.0
        
        hold_days = []
        heatmap_data = []
        if trades:
            from collections import defaultdict
            stats = defaultdict(lambda: {"wins": 0, "total": 0})
            
            for t in trades:
                ed = datetime.strptime(t['entry_date'], '%Y-%m-%d')
                xd = datetime.strptime(t['exit_date'], '%Y-%m-%d')
                hold_days.append((xd - ed).days)
                
                dname = ed.strftime('%a')
                mname = ed.strftime('%b')
                stats[f"{dname}-{mname}"]["total"] += 1
                if t['win']:
                    stats[f"{dname}-{mname}"]["wins"] += 1
                    
            for k, v in stats.items():
                day, month = k.split('-')
                hm_wr = (v["wins"] / v["total"]) * 100
                heatmap_data.append({"day": day, "month": month, "win_rate": round(hm_wr, 2), "total": v["total"]})

        avg_hold_days = sum(hold_days) / len(hold_days) if hold_days else 0.0

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

        monte_carlo = {
            "p05": req.capital_allocation,
            "p50": req.capital_allocation,
            "p95": req.capital_allocation
        }
        if trades:
            trade_pnls = [t['pnl'] for t in trades]
            n_trades = len(trade_pnls)
            mc_finals = []
            for _ in range(1000):
                sample = np.random.choice(trade_pnls, size=n_trades, replace=True)
                mc_finals.append(req.capital_allocation + np.sum(sample))
            
            mc_finals = np.array(mc_finals)
            monte_carlo = {
                "p05": round(float(np.percentile(mc_finals, 5)), 2),
                "p50": round(float(np.percentile(mc_finals, 50)), 2),
                "p95": round(float(np.percentile(mc_finals, 95)), 2)
            }

        return {
            "metrics": {
                "total_trades":   len(trades),
                "win_rate":       round(win_rate, 2),
                "total_pnl":      round(total_pnl, 2),
                "final_equity":   round(equity, 2),
                "avg_pnl":        round(avg_pnl, 2),
                "avg_hold_days":  round(avg_hold_days, 1),
                "sharpe_ratio":   round(sharpe, 2),
                "sortino_ratio":  round(sortino, 2),
                "max_drawdown":   round(max_dd, 2),
            },
            "trades":        trades,
            "equity_curve":  equity_curve,
            "price_history": price_history,
            "heatmap":       heatmap_data,
            "monte_carlo":   monte_carlo,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}
