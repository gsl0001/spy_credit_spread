import contextlib
import functools
import os
import sqlite3
from dataclasses import asdict
import time as _time
from typing import List, Optional, Any

# ── TTL cache (replaces lru_cache on data-fetching functions) ─────────────────
# lru_cache never expires — live scanner would use bars fetched during backtest.
# 300-second TTL ensures live mode always sees fresh data.
_TTL_CACHE: dict = {}

def _ttl_cache(ttl: int = 300):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args):
            key = (fn.__qualname__, args)
            entry = _TTL_CACHE.get(key)
            if entry and (_time.monotonic() - entry[0]) < ttl:
                return entry[1]
            result = fn(*args)
            _TTL_CACHE[key] = (_time.monotonic(), result)
            return result
        wrapper.cache_clear = lambda: _TTL_CACHE.clear()
        return wrapper
    return decorator
import yfinance as yf
import pandas as pd
import numpy as np
import scipy.stats as si
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

# Strategy imports
from strategies.consecutive_days import ConsecutiveDaysStrategy
from strategies.combo_spread import ComboSpreadStrategy
from strategies.dryrun import DryRunStrategy
from strategies.builder import OptionTopologyBuilder, bs_call_price, bs_put_price

# Trading & Scheduler imports
from brokers.ibkr_trading import get_ib_connection
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone
import asyncio

# Initialise structured JSON logging (I1) before anything else logs.
from core.logger import configure_root_logging, log_event
import logging as _logging
configure_root_logging()
_startup_log = _logging.getLogger("main")
log_event(_startup_log, "server_startup", message="FastAPI app initialising")

# Loud warning if uvicorn --reload is active. A file save during an in-flight
# spread kills leg2 journaling and leaves the broker holding both legs with
# no journal record — exactly the failure mode that produced the 2026-05-11
# orphan incident. Detect by inspecting sys.argv (works whether launched via
# `python -m uvicorn ... --reload` or `uvicorn ... --reload`).
import sys as _sys
if "--reload" in _sys.argv or any("--reload" in a for a in _sys.argv):
    log_event(
        _startup_log, "reload_mode_warning",
        level=_logging.WARNING,
        message=(
            "uvicorn --reload is active; file saves will kill in-flight "
            "broker calls and can orphan spreads at the broker. Run without "
            "--reload for live trading."
        ),
    )

_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


@contextlib.asynccontextmanager
async def _lifespan(application):
    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()

    # Startup hardening: raise FD limit + reset corrupted yfinance cache so
    # the long-running scheduler doesn't hit "Too many open files" or stale
    # SQLite "unable to open database file" errors mid-session.
    try:
        from core.yf_safe import raise_fd_limit, reset_cache as _yf_reset
        raise_fd_limit(8192)
        _yf_reset()
    except Exception as exc:  # pragma: no cover - best effort
        _startup_log.warning("startup hardening skipped: %s", exc)

    # I6: Auto-connect to IBKR on startup if configured AND header toggle ON
    from core.settings import SETTINGS
    from core.connection_flags import is_auto_enabled
    ib_cfg = SETTINGS.ibkr.as_dict()
    if ib_cfg.get("host") and ib_cfg.get("port") and is_auto_enabled("ibkr"):
        _startup_log.info("auto-connecting to IBKR at %s:%s...", ib_cfg["host"], ib_cfg["port"])
        asyncio.create_task(get_ib_connection(ib_cfg))
    elif not is_auto_enabled("ibkr"):
        _startup_log.info("IBKR auto-reconnect disabled by header toggle; skipping startup connect")

    # Telegram bot polling: register on startup if configured, so /help etc.
    # work without first calling /api/monitor/start. Idempotent — replaces
    # any existing telegram_bot job.
    try:
        from core.telegram_bot import configured as _tg_configured
        if _tg_configured():
            tg_secs = max(int(SETTINGS.telegram.poll_interval_seconds), 1)
            scheduler.add_job(
                _run_telegram_poll, "interval", seconds=tg_secs,
                id="telegram_bot", replace_existing=True,
                max_instances=1, coalesce=True,
            )
            _startup_log.info("telegram bot polling registered (every %ds)", tg_secs)
    except Exception as exc:  # pragma: no cover - best effort
        _startup_log.warning("telegram startup register skipped: %s", exc)

    # Daily paper-trading-gate evaluator at 16:30 ET (after US RTH close).
    # Iterates active trials, marks pass/fail when sample-size minimums met.
    try:
        scheduler.add_job(
            _run_paper_gate_evaluator, "cron",
            hour=16, minute=30, day_of_week="mon-fri",
            id="paper_gate_evaluator", replace_existing=True,
            max_instances=1, coalesce=True,
        )
        _startup_log.info("paper gate evaluator registered (16:30 ET, Mon-Fri)")
    except Exception as exc:  # pragma: no cover - best effort
        _startup_log.warning("paper gate evaluator register skipped: %s", exc)

    # Boot-time moomoo reconciler. Runs after a 10s grace period so any UI
    # auto-reconnect has time to land; logs broker vs journal drift, auto-
    # records broker orphans as ``single_leg_orphan`` positions for the MTM
    # monitor to manage. Best-effort — errors are logged and ignored.
    async def _boot_reconcile_after_delay():
        try:
            await asyncio.sleep(10)
            from core.moomoo_reconciler import reconcile_once
            result = await reconcile_once()
            _startup_log.info("boot reconcile result: %s", result)
        except Exception as exc:  # pragma: no cover - best effort
            _startup_log.warning("boot reconcile failed: %s", exc)
    asyncio.create_task(_boot_reconcile_after_delay())

    # Auto-start the sole moomoo auto_execute preset scanner on boot. Without this, every
    # server restart silently leaves auto_execute presets idle until someone
    # manually POSTs /api/scanner/preset/start. Discovered 2026-05-12 when
    # the bot ran all day without firing because the scanner was never
    # started after run-live.sh booted the server.
    #
    # Safety rule: when AUTOSTART_PRESET is not set, exactly one moomoo preset
    # may have auto_execute=True. This prevents a stale test preset and a real
    # strategy preset from both being candidates after config edits.
    async def _autostart_preset_after_delay():
        try:
            await asyncio.sleep(12)  # after reconcile
            import os
            override = os.environ.get("AUTOSTART_PRESET", "").strip()
            target_name = None
            if override:
                target_name = override
            else:
                from core.presets import single_moomoo_auto_execute_preset
                preset_candidate, auto_names = single_moomoo_auto_execute_preset()
                if not auto_names:
                    _startup_log.info("autostart_preset: none configured (no moomoo auto_execute)")
                    return
                if preset_candidate is None:
                    log_event(_startup_log, "autostart_preset_blocked",
                              reason="multiple_moomoo_auto_execute",
                              presets=auto_names)
                    return
                target_name = preset_candidate.name
            if not target_name:
                _startup_log.info("autostart_preset: none configured (no moomoo auto_execute)")
                return
            # Idempotent — replaces any existing scanner job.
            preset = _preset_scanner.load_preset(target_name)
            scanner_state["active"] = True
            scanner_state["config"] = preset.to_dict()
            scanner_state["timing_mode"] = preset.timing_mode
            scanner_state["timing_value"] = preset.timing_value
            _remove_market_scan_jobs()
            secs = max(int(preset.timing_value), 10)
            scheduler.add_job(_run_preset_tick, "interval", seconds=secs,
                              id="market_scan", replace_existing=True)
            _startup_log.info("autostart_preset: scanner armed for %s (every %ds)",
                              target_name, secs)
        except Exception as exc:  # noqa: BLE001
            _startup_log.warning("autostart_preset failed: %s", exc)
    asyncio.create_task(_autostart_preset_after_delay())

    yield
    _MAIN_LOOP = None


app = FastAPI(title="SPY Options Backtesting Engine", lifespan=_lifespan)


@app.get("/", include_in_schema=False)
def dashboard_redirect():
    return RedirectResponse("http://127.0.0.1:5173/")


def _safe_json(obj):
    """Recursively replace NaN/Inf floats with None so JSONResponse doesn't crash.

    IBKR returns IEEE-754 NaN for quotes when the market is closed.  Python's
    ``json`` module serialises NaN as the bare token ``NaN`` which is not valid
    JSON-RFC-8259, and FastAPI's JSONResponse raises ``ValueError`` on it.
    """
    import math
    if isinstance(obj, dict):
        return {k: _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_json(v) for v in obj]
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    return obj


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

# I11: hydrate scanner log buffer from SQLite so history survives restarts.
try:
    from core.journal import get_journal as _get_journal_boot
    _persisted = _get_journal_boot().list_scan_logs(limit=50)
    if _persisted:
        scanner_state["logs"] = _persisted
        scanner_state["last_run"] = _persisted[0].get("time")
        log_event(
            _startup_log,
            "scanner_logs_hydrated",
            count=len(_persisted),
            message="restored scanner history from journal",
        )
except Exception as _hydrate_err:  # pragma: no cover - best effort
    _startup_log.warning("scanner log hydrate failed: %s", _hydrate_err)

class BacktestRequest(BaseModel):
    ticker: str = "SPY"
    years_history: int = 2
    capital_allocation: float = 10000.0
    contracts_per_trade: int = 1
    # use_request §2 — single dropdown:
    #   "fixed" | "dynamic_risk" | "targeted_spread" | "" (legacy/derive)
    position_size_method: str = ""
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

    # I9: bid-ask haircut — fraction of mid-price paid as spread on each fill.
    # 0.02 = 2% adverse fill (mid ± half spread).  Default 0.0 for backward compat.
    bid_ask_haircut: float = 0.0
    strategy_params: dict = Field(default_factory=dict)

    # Intraday / ORB params. The frontend's schema form sends these flat so
    # keep them first-class while also accepting nested strategy_params.
    or_minutes: int = 5
    offset: float = 1.50
    width: int = 5
    min_range_pct: float = 0.05
    time_exit_hhmm: str = "15:30"
    allowed_days: str = "MWF"
    skip_news_days: bool = True

class OptimizerRequest(BaseModel):
    base_config: BacktestRequest = BacktestRequest()
    param_x: str = "entry_red_days"
    param_y: str = "target_dte"
    x_values: List[float] = [1, 2, 3, 4]
    y_values: List[float] = [7, 14, 21, 30]


class StrategyFactory:
    """Backtest-side strategy lookup. Delegates to core.scanner's registry
    so adding a new strategy only requires editing one dict."""

    @staticmethod
    def _registry() -> dict:
        from core.scanner import list_strategy_classes
        return list_strategy_classes()

    @staticmethod
    def get_strategy(strategy_id: str):
        registry = StrategyFactory._registry()
        strat_cls = registry.get(strategy_id) or registry.get("consecutive_days") or ConsecutiveDaysStrategy
        return strat_cls()

    @staticmethod
    def get_all_strategies():
        return [
            {
                "id": k,
                "name": v().name,
                "schema": v.get_schema(),
                "bar_size": getattr(v, "BAR_SIZE", "1 day"),
                "history_period": getattr(v, "HISTORY_PERIOD", "1mo"),
                "vetting_result": getattr(v, "VETTING_RESULT", "pending"),
            }
            for k, v in StrategyFactory._registry().items()
        ]


# ── Data fetching ──────────────────────────────────────────────────────────
@_ttl_cache(300)
def fetch_historical_data(ticker: str, years: int, strategy_id: str = ""):
    bar_size, history_period = _resolve_bar_spec(strategy_id) if strategy_id else ("1 day", "1mo")
    is_intraday = bar_size != "1 day"
    interval = _YF_INTERVAL_MAP.get(bar_size, "1d") if is_intraday else "1d"
    if is_intraday:
        period = _YF_INTRADAY_CAP.get(interval, history_period or "60d")
    else:
        period = f"{years}y"
    df = yf.download(ticker, period=period, interval=interval, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.reset_index(inplace=True)
    if 'Date' not in df.columns:
        if 'Datetime' in df.columns:
            df.rename(columns={'Datetime': 'Date'}, inplace=True)
        elif 'index' in df.columns:
            df.rename(columns={'index': 'Date'}, inplace=True)
    if hasattr(df['Date'].dt, 'tz') and df['Date'].dt.tz is not None:
        if is_intraday:
            try:
                df['Date'] = df['Date'].dt.tz_convert("America/New_York")
            except Exception:
                pass
        df['Date'] = df['Date'].dt.tz_localize(None)
    return df


@_ttl_cache(300)
def fetch_risk_free_rate():
    """Fetch 13-week T-Bill (^IRX) as a risk-free rate proxy."""
    try:
        df = yf.download("^IRX", period="5y", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return float(df['Close'].iloc[-1]) / 100.0
    except Exception:
        return 0.045

@_ttl_cache(300)
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

                    # I9: bid-ask haircut — you always fill at a price worse than
                    # theoretical mid.  For a debit you pay more; for a credit you
                    # receive less.  Formula: one_cc += |one_cc| * haircut preserves
                    # sign while making the fill adversarially worse.
                    if req.bid_ask_haircut > 0:
                        one_cc = one_cc + abs(one_cc) * req.bid_ask_haircut

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
                # I9: apply bid-ask haircut at exit — you close at a price worse
                # than mid.  cv_exit < cv_mid for debits (selling at bid) and
                # cv_exit > cv_mid for credits (buying back at ask).
                cv_exit = cv - abs(cv) * req.bid_ask_haircut if req.bid_ask_haircut > 0 else cv
                equity += cv_exit - ec
                tc = current_entry.get("commission", 0) + ec
                pnl = cv_exit - current_entry["entry_cost"] - tc
                
                final_reason = reason
                if stop_exit: final_reason = "stop_loss"
                if tp_exit: final_reason = "take_profit"
                if ts_exit: final_reason = "trailing_stop"

                trades.append({
                    "entry_date": current_entry["entry_date"], "exit_date": date,
                    "entry_spy": round(current_entry["entry_spy"], 2), "exit_spy": round(S, 2),
                    "side": "BUY" if current_entry["entry_cost"] > 0 else "SELL",
                    "spread_cost": round(current_entry["entry_cost"], 2), "spread_exit": round(cv_exit, 2),
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


def _param(req: BacktestRequest, key: str, default):
    params = getattr(req, "strategy_params", {}) or {}
    return params.get(key, getattr(req, key, default))


def _parse_hhmm(value: str):
    from datetime import time as _time_cls
    try:
        hour, minute = (int(part) for part in str(value).split(":", 1))
        return _time_cls(hour, minute)
    except Exception:
        return _time_cls(15, 30)


def _allowed_days_to_weekdays(value: str) -> tuple[int, ...]:
    value = str(value or "MWF").upper()
    if value in {"ALL", "MON-FRI", "WEEKDAYS"}:
        return (0, 1, 2, 3, 4)
    mapping = {"M": 0, "T": 1, "W": 2, "R": 3, "F": 4}
    days = tuple(mapping[ch] for ch in value if ch in mapping)
    return days or (0, 2, 4)


def _orb_config_from_request(req: BacktestRequest):
    from core.backtest_orb import OrbBacktestConfig
    is_ldm = req.strategy_id in ("ldm_0dte", "ldm_fade_0dte")
    is_fade = req.strategy_id == "ldm_fade_0dte"
    default_or_minutes = 15 if is_ldm else 5
    default_or_start = "14:00" if is_ldm else "09:30"
    default_offset = 0.50 if is_ldm else 1.50
    default_allowed = "MTWRF" if is_ldm else "MWF"
    default_min_range = 0.05
    default_entry_cutoff = "15:30"
    default_time_exit = "15:55" if is_ldm else "15:30"
    default_vix_min = 12.0 if is_ldm else float(req.vix_min)
    default_vix_max = 30.0 if is_ldm else float(req.vix_max)
    return OrbBacktestConfig(
        or_minutes=int(_param(req, "or_minutes", default_or_minutes)),
        or_start_time=_parse_hhmm(_param(req, "or_start_hhmm", default_or_start)),
        fade_mode=is_fade,
        offset_points=float(_param(req, "offset", default_offset)),
        width_points=int(_param(req, "width", req.strike_width or 5)),
        min_range_pct=float(_param(req, "min_range_pct", default_min_range)),
        vix_min=float(_param(req, "vix_min", default_vix_min)),
        vix_max=float(_param(req, "vix_max", default_vix_max)),
        allowed_weekdays=_allowed_days_to_weekdays(_param(req, "allowed_days", default_allowed)),
        skip_news_days=bool(_param(req, "skip_news_days", True)),
        take_profit_pct=float(req.take_profit_pct),
        stop_loss_pct=float(req.stop_loss_pct),
        time_exit=_parse_hhmm(_param(req, "time_exit_hhmm", default_time_exit)),
        entry_cutoff=_parse_hhmm(_param(req, "entry_cutoff_hhmm", default_entry_cutoff)),
        capital=float(req.capital_allocation),
        contracts_per_trade=int(req.contracts_per_trade),
        spread_cost_per_contract=float(req.spread_cost_target),
        commission_per_contract=float(req.commission_per_contract),
    )


def _raw_bars_to_orb_frame(raw_df: pd.DataFrame) -> pd.DataFrame:
    bars = raw_df.copy()
    if "Date" not in bars.columns and isinstance(bars.index, pd.DatetimeIndex):
        bars["Date"] = bars.index
    rename = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    bars = bars.rename(columns=rename)
    required = ["Date", "open", "high", "low", "close"]
    missing = [col for col in required if col not in bars.columns]
    if missing:
        raise ValueError(f"ORB backtest missing columns: {', '.join(missing)}")
    bars["Date"] = pd.to_datetime(bars["Date"])
    bars = bars.set_index("Date", drop=False)
    keep = ["open", "high", "low", "close"]
    if "volume" in bars.columns:
        keep.append("volume")
    return bars[keep]


def _orb_vix_series(req: BacktestRequest):
    if not req.use_vix_filter:
        return None
    vix_df = fetch_vix_data(req.years_history)
    if vix_df.empty:
        return pd.Series(dtype=float)
    series = vix_df.copy()
    series["Date"] = pd.to_datetime(series["Date"]).dt.normalize()
    return series.set_index("Date")["VIX"]


def _orb_events(req: BacktestRequest) -> set[str] | None:
    if not bool(_param(req, "skip_news_days", True)):
        return None
    from core.calendar import load_event_calendar
    return {ev.date for ev in load_event_calendar() if ev.severity in ("high", "medium")}


def _adapt_orb_report(report: dict, raw_df: pd.DataFrame, req: BacktestRequest) -> dict:
    stats = report.get("stats", {})
    trades = []
    for t in report.get("trades", []):
        entry_date = str(t.get("date") or t.get("entry_time", ""))[:10]
        exit_date = str(t.get("exit_time", ""))[:10] or entry_date
        trades.append({
            "entry_date": entry_date,
            "exit_date": exit_date,
            "entry_spy": t.get("entry_price", 0),
            "exit_spy": t.get("exit_price", 0),
            "side": "BUY",
            "spread_cost": req.spread_cost_target * req.contracts_per_trade,
            "spread_exit": t.get("spread_value_at_exit", 0),
            "pnl": t.get("pnl_dollars", 0),
            "contracts": req.contracts_per_trade,
            "days_held": 0,
            "commission": req.commission_per_contract * 4 * req.contracts_per_trade,
            "win": (t.get("pnl_dollars", 0) > 0),
            "stopped_out": t.get("exit_reason") == "stop_loss",
            "reason": t.get("exit_reason", "time_exit"),
            "regime": "intraday",
            "topology": req.topology,
        })

    price_history = raw_df.copy()
    if "Date" not in price_history.columns and isinstance(price_history.index, pd.DatetimeIndex):
        price_history["Date"] = price_history.index
    if not price_history.empty:
        price_history["time"] = pd.to_datetime(price_history["Date"]).dt.strftime("%Y-%m-%d %H:%M")
        price_history = price_history.rename(columns={
            "Open": "open", "High": "high", "Low": "low", "Close": "close",
        })
        price_history = price_history[["time", "open", "high", "low", "close"]]
        for col in ["open", "high", "low", "close"]:
            price_history[col] = price_history[col].astype(float).round(2)

    # Compute profit_factor / avg_hold_days / max_consec_losses from the
    # trade list so intraday strategies can be evaluated against the same
    # skill bar as daily strategies (skill Step 7).
    pnls = [float(t.get("pnl", 0) or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_win / gross_loss, 3) if gross_loss > 0 else (
        float("inf") if gross_win > 0 else 0.0
    )
    # Intraday hold reported as fraction of a session day for legibility
    # (e.g. 0.25 = ~1.6h held). The trade rows from the ORB engine carry
    # entry_time/exit_time inside the original report; pull from there.
    raw_trades = report.get("trades", [])
    hold_fracs = []
    for t in raw_trades:
        try:
            entry_ts = pd.to_datetime(t.get("entry_time"))
            exit_ts = pd.to_datetime(t.get("exit_time"))
            if pd.notna(entry_ts) and pd.notna(exit_ts):
                hours = (exit_ts - entry_ts).total_seconds() / 3600.0
                if hours >= 0:
                    hold_fracs.append(hours / 6.5)  # 6.5h regular session
        except Exception:
            continue
    avg_hold_days = round(sum(hold_fracs) / len(hold_fracs), 3) if hold_fracs else 0.0
    # Max consecutive losses
    max_consec, cur_consec = 0, 0
    for p in pnls:
        if p < 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0
    # Recovery factor: |total_pnl| / |max_drawdown_dollars|. Engine reports
    # max_drawdown_pct (% of starting equity), so reconstruct dollar DD.
    capital = float(getattr(req, "capital_allocation", 10000) or 10000)
    dd_pct = float(stats.get("max_drawdown_pct", 0) or 0)
    dd_dollars = abs(dd_pct) / 100.0 * capital if capital > 0 else 0.0
    total_pnl = float(stats.get("total_pnl", 0) or 0)
    recovery_factor = round(total_pnl / dd_dollars, 3) if dd_dollars > 0 else 0.0

    metrics = {
        "total_trades": stats.get("total_trades", 0),
        "win_rate": round(float(stats.get("win_rate", 0)) * 100, 2),
        "total_pnl": total_pnl,
        "avg_pnl": round(total_pnl / max(int(stats.get("total_trades", 0)), 1), 2),
        "sharpe_ratio": stats.get("sharpe", 0),
        "max_drawdown": stats.get("max_drawdown_pct", 0),
        "profit_factor": profit_factor,
        "recovery_factor": recovery_factor,
        "avg_hold_days": avg_hold_days,
        "max_consec_losses": max_consec,
    }

    return _safe_json({
        "metrics": metrics,
        "trades": trades,
        "equity_curve": report.get("equity_curve", []),
        "price_history": price_history.to_dict(orient="records") if not price_history.empty else [],
        "heatmap": [],
        "monte_carlo": None,
        "walk_forward": [],
        "duration_dist": [],
        "regime_stats": {},
        "regime_timeline": [],
    })


def _run_orb_endpoint_backtest(req: BacktestRequest, raw_df: pd.DataFrame) -> dict:
    from core.backtest_orb import run_orb_backtest
    bars = _raw_bars_to_orb_frame(raw_df)
    report = run_orb_backtest(
        bars,
        vix=_orb_vix_series(req),
        events=_orb_events(req),
        config=_orb_config_from_request(req),
    )
    return _adapt_orb_report(report, raw_df, req)


def _run_generic_intraday_backtest(req: BacktestRequest, raw_df: pd.DataFrame) -> dict:
    """Generic 1m/5m intraday harness for non-ORB strategies.

    Used when ``cls.INTRADAY_ENGINE == "generic"``. Drives entries/exits
    through ``strategy.check_entry`` / ``strategy.check_exit`` on intraday
    bars; force-flats at session close. Reuses the ORB report shape so
    the same adapter renders both.
    """
    from core.backtest_intraday import run_intraday_backtest, IntradayBacktestConfig
    bars = _raw_bars_to_orb_frame(raw_df)
    strategy = StrategyFactory.get_strategy(req.strategy_id)
    report = run_intraday_backtest(
        bars,
        req=req,
        strategy=strategy,
        config=IntradayBacktestConfig(
            entry_cutoff=_parse_hhmm(_param(req, "entry_cutoff_hhmm", "15:30")),
            session_close=_parse_hhmm(_param(req, "time_exit_hhmm", "15:55")),
        ),
    )
    return _adapt_orb_report(report, raw_df, req)


@app.get("/api/strategies")
def get_strategies():
    all_strats = StrategyFactory.get_all_strategies()
    log_event(_startup_log, "fetch_strategies", count=len(all_strats))
    return all_strats


# ── Main endpoint ──────────────────────────────────────────────────────────
@app.post("/api/backtest")
def backtest(req: BacktestRequest):
    try:
        raw_df = fetch_historical_data(req.ticker, req.years_history, req.strategy_id)
        df = raw_df.copy() # Indicators computed inside engine now per strategy
        if len(df) == 0: return {"error": "No data returned."}

        # Route any intraday strategy through the ORB intraday harness.
        # The harness handles "5 mins"/"15 mins"/"1 hour" via or_minutes
        # and or_start_time knobs; daily strategies fall through to
        # run_backtest_engine. Dispatch on the class invariant rather
        # than a hardcoded id list so adding a new intraday strategy
        # doesn't require touching this branch.
        from core.scanner import resolve_strategy_class as _resolve_cls
        _cls = _resolve_cls(req.strategy_id)
        _bar_size = getattr(_cls, "BAR_SIZE", "1 day") if _cls else "1 day"
        if _bar_size != "1 day":
            _engine = getattr(_cls, "INTRADAY_ENGINE", "orb") if _cls else "orb"
            if _engine == "generic":
                return _run_generic_intraday_backtest(req, raw_df)
            return _run_orb_endpoint_backtest(req, raw_df)

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

        return _safe_json({
            "metrics": metrics, "trades": trades, "equity_curve": equity_curve,
            "price_history": price_history, "heatmap": heatmap, "monte_carlo": mc,
            "walk_forward": wf, "duration_dist": dur_dist, "regime_stats": regime_stats,
            "regime_timeline": regime_timeline,
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": str(e)}


# ── SPY Intraday Sparkline ─────────────────────────────────────────────────
@app.get("/api/spy/intraday")
async def spy_intraday(host: str = None, port: int = None, client_id: int = None):
    """Fetch today's SPY 1-min bars, with live IBKR price if available."""
    out = {"data": [], "current": 0, "change": 0, "change_pct": 0, "source": "yfinance"}
    
    # 1. Try to get live price from IBKR if creds provided and connection exists
    if host and port and client_id:
        from brokers.ibkr_trading import _ib_instances
        key = f"{host}:{port}:{client_id}"
        trader = _ib_instances.get(key)
        if trader and trader.is_alive():
            try:
                live = await trader.get_live_price("SPY")
                if live.get("last"):
                    out["current"] = live["last"]
                    out["source"] = "ibkr"
            except Exception:
                pass

    # 2. Fetch history from yfinance (with prepost=True for extended hours)
    try:
        from core.yf_safe import safe_download, safe_fast_info
        # Use 2d period to ensure we have context for overnight/pre-market moves
        df = safe_download("SPY", period="2d", interval="1m", progress=False, prepost=True)
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
        
        out["data"] = data
        if data:
            if out["current"] == 0:
                out["current"] = data[-1]["close"]
            else:
                out["data"].append({"time": "live", "close": out["current"]})
            
            # Use yfinance ticker info to get an accurate previous close
            try:
                ticker_info = safe_fast_info("SPY")
                prev_close = ticker_info.get("previousClose") or data[0]["close"]
            except Exception:
                prev_close = data[0]["close"]

            out["change"] = round(out["current"] - prev_close, 2)
            out["change_pct"] = round((out["change"] / prev_close) * 100, 2) if prev_close else 0
            
    except Exception as e:
        if out["current"] == 0:
            return {"error": str(e), "data": [], "current": 0, "change": 0, "change_pct": 0}

    return out


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
@app.get("/api/strategies/{strategy_id}/schema")
def get_strategy_schema(strategy_id: str):
    cls = StrategyFactory._registry().get(strategy_id)
    if cls is None:
        return {"error": "unknown_strategy", "strategy_id": strategy_id}
    try:
        return {"strategy_id": strategy_id, "schema": cls.get_schema()}
    except Exception as e:  # noqa: BLE001
        return {"error": "schema_failed", "detail": str(e)}


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
    from brokers.paper_trading import check_connection
    return check_connection(creds.api_key, creds.api_secret)

@app.post("/api/paper/positions")
def paper_positions(creds: PaperCredentials):
    from brokers.paper_trading import get_positions
    return {"positions": get_positions(creds.api_key, creds.api_secret)}

@app.post("/api/paper/orders")
def paper_orders(creds: PaperCredentials):
    from brokers.paper_trading import get_orders
    return {"orders": get_orders(creds.api_key, creds.api_secret)}

@app.post("/api/paper/execute")
def paper_execute(req: PaperOrderRequest):
    from brokers.paper_trading import place_equity_order
    return place_equity_order(req.api_key, req.api_secret, req.symbol, req.qty, req.side)

@app.post("/api/paper/scan")
def paper_scan(req: PaperScanRequest):
    from brokers.paper_trading import scan_signal
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
    """Background task for scanning market based on frequency.

    C8: Uses core.filters.apply_filters for parity with backtest engine.
    """
    if not scanner_state["active"]: return

    current_time = datetime.now().isoformat()
    config = scanner_state.get("config", {})
    mode = scanner_state.get("mode", "paper")

    try:
        from brokers.paper_trading import scan_signal
        from core.filters import apply_filters
        # Perform the scan using the logic in paper_trading (which is generic)
        res = scan_signal("", "", config) # Alpaca keys not needed for just scanning YF data

        signal = res.get("signal", False)
        price = res.get("price", 0)
        rsi = res.get("rsi", 0)

        # C8: Apply the FULL filter set (SMA200, Volume, VIX, regime) that
        # the backtest engine uses. scan_signal only checks RSI + EMA.
        filter_rejected = ""
        if signal:
            # Build a row-like dict from the scan result + config
            row = res.get("row_data", {})
            if row:
                # Wrap config as a namespace so apply_filters can read attrs
                class _Cfg:
                    pass
                cfg = _Cfg()
                for k, v in config.items():
                    setattr(cfg, k, v)
                allowed, filter_rejected = apply_filters(row, cfg)
                if not allowed:
                    signal = False

        log_entry = {
            "time": current_time,
            "signal": signal,
            "price": price,
            "rsi": rsi,
            "msg": f"Scan completed. Signal: {'YES' if signal else 'NO'}"
                   + (f" (filtered: {filter_rejected})" if filter_rejected else ""),
            "details": res,
        }

        # Limit logs to last 50 (in-memory)
        scanner_state["logs"].insert(0, log_entry)
        if len(scanner_state["logs"]) > 50:
            scanner_state["logs"].pop()

        # I11: persist to SQLite so scanner history survives restarts.
        try:
            from core.journal import get_journal
            get_journal().record_scan_log(
                time=current_time,
                signal=bool(signal),
                price=float(price) if price is not None else None,
                rsi=float(rsi) if rsi is not None else None,
                msg=log_entry["msg"],
                details=res if isinstance(res, dict) else {"raw": str(res)},
            )
        except Exception as _persist_err:
            import logging as _pl
            _pl.getLogger(__name__).warning(
                "scanner log persist failed: %s", _persist_err
            )

        scanner_state["last_run"] = current_time

        # Auto-execute when enabled
        if signal and scanner_state.get("auto_execute", False):
            mode = scanner_state.get("mode", "paper")
            creds = scanner_state.get("creds") or {}
            if mode == "paper" and creds.get("api_key"):
                # I5: Generate an idempotency key scoped to date + symbol + strategy
                # so rapid scanner fires on the same day are suppressed as duplicates.
                _scan_date = datetime.now().strftime("%Y-%m-%d")
                _scan_symbol = config.get("ticker", "SPY")
                _scan_strategy = config.get("strategy_id", "default")
                _idem_key = f"scan:{_scan_date}:{_scan_symbol}:{_scan_strategy}"

                from core.journal import get_journal as _get_j, Order as _Order
                import uuid as _uuid_scan
                _jnl = _get_j()
                _existing = _jnl.get_order_by_idempotency(_idem_key)
                if _existing is not None:
                    import logging as _scan_log
                    _scan_log.getLogger(__name__).info(
                        "duplicate scan signal suppressed (key=%s)", _idem_key
                    )
                else:
                    from brokers.paper_trading import place_equity_order
                    side = "sell" if config.get("direction", "bull") == "bear" else "buy"
                    place_equity_order(creds["api_key"], creds["api_secret"],
                                       _scan_symbol,
                                       config.get("contracts_per_trade", 1) * 100, side)
                    _now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    _jnl.record_order(_Order(
                        id=str(_uuid_scan.uuid4()),
                        position_id=None,
                        broker="paper",
                        broker_order_id=None,
                        side=side.upper(),
                        limit_price=None,
                        status="submitted",
                        submitted_at=_now_iso,
                        kind="entry",
                        idempotency_key=_idem_key,
                    ))

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Background scan error: %s", e)
        err_entry = {
            "time": current_time,
            "signal": False,
            "msg": f"Error: {str(e)}"
        }
        scanner_state["logs"].insert(0, err_entry)
        if len(scanner_state["logs"]) > 50:
            scanner_state["logs"].pop()
        # I11: persist error events too, so operators can see failures post-hoc.
        try:
            from core.journal import get_journal
            get_journal().record_scan_log(
                time=current_time,
                signal=False,
                price=None,
                rsi=None,
                msg=err_entry["msg"],
                details={"error": str(e)},
            )
        except Exception:
            pass

@app.post("/api/scanner/start")
def start_scanner(req: ScannerConfigRequest):
    from core.leader import try_acquire_leadership
    # I6: Claim leadership when starting automated background work
    try_acquire_leadership("data/monitor.lock")

    scanner_state["active"] = True
    scanner_state["timing_mode"] = req.timing_mode
    scanner_state["timing_value"] = req.timing_value
    scanner_state["mode"] = req.mode
    scanner_state["config"] = req.config
    scanner_state["auto_execute"] = req.auto_execute

    # Remove existing scanner jobs while preserving monitor/telegram jobs.
    _remove_market_scan_jobs()

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
    _remove_market_scan_jobs()
    return {"status": "stopped"}

@app.get("/api/scanner/status")
def get_scanner_status():
    return scanner_state


# ── Scanner presets (use_request §4) ──────────────────────────────────────

from core.presets import PresetStore as _PresetStore, ScannerPreset as _ScannerPreset

_preset_store = _PresetStore()


@app.get("/api/presets")
def list_presets():
    return {"presets": [p.to_dict() for p in _preset_store.list()]}


@app.get("/api/presets/{name}")
def get_preset(name: str):
    p = _preset_store.get(name)
    if p is None:
        return {"error": "not_found", "name": name}
    return p.to_dict()


@app.post("/api/presets")
def save_preset(payload: dict):
    try:
        preset = _ScannerPreset.from_dict(payload)
    except (KeyError, ValueError, TypeError) as e:
        return {"error": "invalid_preset", "detail": str(e)}
    saved = _preset_store.save(preset)
    return {"saved": True, "preset": saved.to_dict()}


@app.delete("/api/presets/{name}")
def delete_preset(name: str):
    ok = _preset_store.delete(name)
    return {"deleted": ok, "name": name}


# ── Preset-driven scanner (use_request §4) ────────────────────────────────

from core.scanner import (
    Scanner as _Scanner,
    PresetRequired as _PresetRequired,
    resolve_strategy_class as _resolve_strategy_class,
)


# IBKR canonical bar-size string → yfinance interval string. Recognised
# values are the ones declared via BaseStrategy.BAR_SIZE; anything else
# falls back to daily.
_YF_INTERVAL_MAP = {
    "1 min":   "1m",
    "5 mins":  "5m",
    "15 mins": "15m",
    "30 mins": "30m",
    "1 hour":  "60m",
    "1 day":   "1d",
}

# yfinance period caps depending on intraday granularity. yfinance
# rejects e.g. period="1mo" with interval="1m" — these are the safe
# defaults used when the strategy didn't declare HISTORY_PERIOD or
# declared one that's too long for the requested interval.
_YF_INTRADAY_CAP = {
    "1m":  "7d",
    "5m":  "60d",
    "15m": "60d",
    "30m": "60d",
    "60m": "730d",
}

# IBKR durationStr defaults paired to bar size. IBKR enforces strict
# (barSize, duration) pairs — these are conservative working values.
_IB_DURATION_MAP = {
    "1 min":   "2 D",
    "5 mins":  "5 D",
    "15 mins": "15 D",
    "30 mins": "30 D",
    "1 hour":  "30 D",
    "1 day":   "30 D",
}


def _history_period_to_days(period: str) -> int:
    """Convert yfinance-style periods (``60d``, ``1mo``) to moomoo days."""
    raw = str(period or "").strip().lower()
    try:
        if raw.endswith("d"):
            return max(1, int(raw[:-1]))
        if raw.endswith("mo"):
            return max(1, int(raw[:-2]) * 30)
        if raw.endswith("y"):
            return max(1, int(raw[:-1]) * 365)
    except ValueError:
        pass
    return 30


def _resolve_bar_spec(preset_or_strategy):
    """Resolve (bar_size, history_period) for a preset OR a bare strategy name.

    Reads the strategy class's ``BAR_SIZE`` / ``HISTORY_PERIOD`` class
    attributes (Option B — strategy-declared). Falls back to daily if
    the strategy can't be resolved.

    Accepts either a ``ScannerPreset`` (uses ``preset.strategy_name``) or
    a string strategy id directly — so the monitor can ask for bars
    sized to a position's recorded strategy without needing the preset.
    """
    bar_size = "1 day"
    history_period = "1mo"
    strategy_name = None
    if isinstance(preset_or_strategy, str):
        strategy_name = preset_or_strategy
    elif preset_or_strategy is not None:
        strategy_name = getattr(preset_or_strategy, "strategy_name", None)
    if strategy_name:
        cls = _resolve_strategy_class(strategy_name)
        if cls is not None:
            bar_size = getattr(cls, "BAR_SIZE", bar_size)
            history_period = getattr(cls, "HISTORY_PERIOD", history_period)
    return bar_size, history_period


def _preset_bars_fetcher(symbol: str, strategy_name: Optional[str] = None):
    """Fetcher for the preset-driven scanner AND the live-monitor's
    strategy-exit checks.

    Honours the strategy's declared ``BAR_SIZE`` / ``HISTORY_PERIOD``
    so intraday strategies (e.g. dryrun) get 5-minute bars while daily
    strategies (consecutive_days, combo_spread) get daily bars.

    Resolution order for the strategy:
      1. Explicit ``strategy_name`` argument (used by the monitor — picks
         the bar size based on the position being checked, regardless of
         which preset is currently active in the scanner).
      2. The currently active scanner preset's strategy.
      3. Daily defaults.
    """
    import yfinance as yf
    from core.settings import SETTINGS
    import asyncio

    # The active preset is consulted for two things: bar-size resolution
    # (when no explicit ``strategy_name`` is passed) and the
    # ``fetch_only_live`` flag below.
    p = _preset_scanner.active_preset
    if strategy_name:
        bar_size, history_period = _resolve_bar_spec(strategy_name)
    else:
        bar_size, history_period = _resolve_bar_spec(p)
    is_intraday = bar_size != "1 day"

    yf_interval = _YF_INTERVAL_MAP.get(bar_size, "1d")
    # Cap the period for intraday intervals so yfinance doesn't reject the request.
    if is_intraday:
        cap = _YF_INTRADAY_CAP.get(yf_interval, "5d")
        # Take whichever is shorter — strategy's declared period or the cap.
        # Simple approach: trust the cap unless strategy explicitly declared shorter.
        yf_period = history_period if history_period in {"1d", "2d", "5d", "7d"} else cap
    else:
        yf_period = history_period

    # ── 1. Live anchor (only useful for daily bars) ────────────────────
    live_price = None
    trader = None
    try:
        from brokers.ibkr_trading import _ib_instances
        creds = SETTINGS.ibkr.as_dict()
        key = f"{creds.get('host', '127.0.0.1')}:{creds.get('port', 7497)}:{creds.get('client_id', 1)}"
        trader = _ib_instances.get(key)
        if trader and trader.is_alive() and not is_intraday:
            loop = _MAIN_LOOP
            if loop and loop.is_running():
                live = asyncio.run_coroutine_threadsafe(
                    trader.get_live_price(symbol), loop,
                ).result(timeout=5)
                live_price = live.get("last")
    except Exception:
        pass

    if not live_price and not is_intraday:
        try:
            from core.yf_safe import safe_fast_info
            live_price = safe_fast_info(symbol).get("lastPrice")
        except Exception:
            pass

    # ── 2. Pure-Live mode via IBKR historical (requires Historical Data entitlement) ──
    if p and getattr(p, "fetch_only_live", False) and trader and trader.is_alive():
        try:
            loop = _MAIN_LOOP
            if loop and loop.is_running():
                ib_duration = _IB_DURATION_MAP.get(bar_size, "30 D")
                df = asyncio.run_coroutine_threadsafe(
                    trader.get_historical_bars(symbol, duration=ib_duration, bar_size=bar_size),
                    loop,
                ).result(timeout=10)
                if df is not None and not df.empty:
                    if live_price and not is_intraday:
                        df.loc[df.index[-1], "Close"] = live_price
                    return df
        except Exception:
            pass

    # ── 2b. Pure-Live mode via moomoo historical bars for moomoo presets ──
    if p and getattr(p, "fetch_only_live", False) and getattr(p, "broker", "") == "moomoo":
        try:
            from core.broker import get_broker
            mm = get_broker("moomoo")
            loop = _MAIN_LOOP
            if mm and mm.is_alive() and loop and loop.is_running():
                rows = asyncio.run_coroutine_threadsafe(
                    mm.get_historical_bars(
                        symbol,
                        duration_days=_history_period_to_days(yf_period),
                        bar_size=yf_interval,
                    ),
                    loop,
                ).result(timeout=10)
                df = pd.DataFrame(rows or [])
                if df is not None and not df.empty and "Date" in df.columns:
                    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
                    df = df.dropna(subset=["Date"])
                    if not df.empty:
                        if hasattr(df["Date"].dt, "tz") and df["Date"].dt.tz is not None:
                            if is_intraday:
                                df["Date"] = df["Date"].dt.tz_convert("America/New_York")
                            df["Date"] = df["Date"].dt.tz_localize(None)
                        df.set_index("Date", drop=False, inplace=True)
                        if live_price and not is_intraday:
                            df.loc[df.index[-1], "Close"] = live_price
                        return df
        except Exception as exc:
            _startup_log.warning("moomoo historical bars fallback failed: %s", exc)

    # ── 3. yfinance fallback (free, no subscription) ──────────────────
    from core.yf_safe import safe_download as _yf_safe_download
    df = _yf_safe_download(symbol, period=yf_period, interval=yf_interval, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if len(df) == 0:
        return df

    df.reset_index(inplace=True)
    # yfinance puts the timestamp column under different names depending on interval.
    for cand in ("Date", "Datetime", "index"):
        if cand in df.columns:
            if cand != "Date":
                df.rename(columns={cand: "Date"}, inplace=True)
            break

    # Timezone handling: intraday strategies compare ts.time() against ET
    # wall-clock windows (e.g. 09:35), so convert to America/New_York
    # *before* stripping the tz. Daily bars don't need this.
    if hasattr(df["Date"].dt, "tz") and df["Date"].dt.tz is not None:
        if is_intraday:
            try:
                df["Date"] = df["Date"].dt.tz_convert("America/New_York")
            except Exception:
                pass
        df["Date"] = df["Date"].dt.tz_localize(None)

    # Mirror the index to "Date" so strategies that read either work.
    df.set_index("Date", drop=False, inplace=True)

    # ── 4. Live anchor injection (daily-bar hack only) ────────────────
    if live_price and not is_intraday:
        last_dt = df["Date"].iloc[-1].date()
        today_dt = datetime.now().date()
        if last_dt == today_dt:
            df.loc[df.index[-1], "Close"] = live_price
        else:
            new_row = df.iloc[-1].copy()
            new_row["Date"] = datetime.now().replace(microsecond=0)
            new_row["Open"] = live_price
            new_row["High"] = live_price
            new_row["Low"] = live_price
            new_row["Close"] = live_price
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    return df


_preset_scanner = _Scanner(
    store=_preset_store,
    bars_fetcher=_preset_bars_fetcher,
)


def _run_preset_tick():
    """Background wrapper to call tick and update shared logs."""
    try:
        signals = _preset_scanner.tick()
        if signals:
            # Update the main scanner logs so the UI shows the new ticks
            recent = _preset_scanner.history(limit=50)
            scanner_state["logs"] = recent
            if recent:
                scanner_state["last_run"] = recent[0]["time"]

            # I5: Auto-execute signals if preset allows it
            p = _preset_scanner.active_preset
            if p and p.auto_execute:
                from core.settings import SETTINGS
                from core.journal import get_journal as _gj
                for sig in signals:
                    if not (sig.fired and sig.signal_type == "entry"):
                        continue

                    # Per-bar idempotency: same symbol + preset can only fire once
                    # per entry bar. sig.time is the bar timestamp from the
                    # strategy, so re-ticks within the same bar are deduped
                    # but the next bar can fire normally.
                    _bar_ts = (sig.time or datetime.now().strftime("%Y-%m-%d"))[:19]
                    _idem_key = f"scan:{_bar_ts}:{sig.symbol}:{p.name}"
                    if _gj().get_order_by_idempotency(_idem_key):
                        log_event(_startup_log, "preset_scan_duplicate_suppressed",
                                  key=_idem_key)
                        continue

                    log_event(_startup_log, "preset_scan_signal_fired",
                              symbol=sig.symbol, preset=p.name, key=_idem_key,
                              broker=p.broker)

                    broker_name = getattr(p, "broker", "ibkr")

                    if broker_name == "moomoo":
                        # Moomoo path: build MoomooOrderRequest and route to moomoo impl
                        try:
                            order_req = MoomooOrderRequest(
                                symbol=sig.symbol or p.ticker,
                                # direction = "bull_call" | "bear_put"; threaded
                                # through to _moomoo_execute_impl which branches
                                # the strike picker (pick_bull_call_strikes vs
                                # pick_bear_put_strikes) on "bear" in direction.
                                direction=p.strategy_type,
                                contracts=int(getattr(sig, "contracts", 0) or 0),
                                strike_width=int(p.strike_width),
                                target_dte=int(p.target_dte),
                                spread_cost_target=float(p.spread_cost_target),
                                position_size_method=p.position_size_method or "fixed",
                                risk_percent=float(p.sizing_params.get("risk_percent", 1.0)),
                                max_allocation_cap=float(p.sizing_params.get("max_allocation_cap", 500.0)),
                                stop_loss_pct=float(p.stop_loss_pct),
                                take_profit_pct=float(p.take_profit_pct),
                                trailing_stop_pct=float(p.trailing_stop_pct),
                                otm_offset=float(p.strategy_params.get("offset", 0.0)),
                                client_order_id=_idem_key,
                                chain_max_bid_ask_pct=float(p.strategy_params.get("chain_max_bid_ask_pct", 0.25)),
                                chain_min_volume=int(p.strategy_params.get("chain_min_volume", 0) or 0),
                                chain_min_open_interest=int(p.strategy_params.get("chain_min_open_interest", 0) or 0),
                                # Smoke-test presets (dryrun) need to bypass the
                                # event-blackout gate; production strategies must
                                # leave this False so CPI/FOMC/NFP keep blocking.
                                # Server-side opt-in gate: preset's flag is
                                # only honored when ALLOW_BYPASS_EVENT_BLACKOUT=1.
                                # Same env gate as the public API so a stray
                                # preset can't quietly disable news filters.
                                bypass_event_blackout=(
                                    bool(getattr(p, "bypass_event_blackout", False))
                                    and os.environ.get("ALLOW_BYPASS_EVENT_BLACKOUT", "0") == "1"
                                ),
                                preset_name=p.name,
                            )
                        except Exception as e:
                            log_event(_startup_log, "preset_auto_execute_build_failed",
                                      error=str(e), preset=p.name)
                            continue
                        # Paper-trading-gate pre-fire check. If the preset is
                        # under an active trial, enforce per-trial position
                        # cap and conservative sizing so the trial sample
                        # stays apples-to-apples with the backtest.
                        try:
                            from core.paper_gate import (
                                get_paper_gate_store, trial_allows_fire,
                                STATUS_TRIALING,
                            )
                            allowed, reason = trial_allows_fire(p.name)
                            if not allowed:
                                log_event(_startup_log, "paper_trial_blocked",
                                          preset=p.name, reason=reason)
                                continue
                            _trial = get_paper_gate_store().get(p.name)
                            if _trial and _trial.status == STATUS_TRIALING:
                                # Override sizing to fixed 1 contract — skill
                                # rule: "fixed_contracts: 1 for first-time
                                # presets; switch to dynamic_risk only after
                                # the live paper run confirms".
                                order_req = order_req.model_copy(update={
                                    "contracts": 1,
                                    "position_size_method": "fixed",
                                })
                        except Exception as _exc:  # pragma: no cover
                            _startup_log.warning(
                                "paper gate pre-fire check failed: %s", _exc)
                        try:
                            from core.broker import get_broker
                            broker = get_broker("moomoo")
                            trd_env_int = int(getattr(broker, "_trd_env_int", 1))
                        except Exception as _exc:  # noqa: BLE001
                            log_event(_startup_log, "preset_auto_execute_skipped",
                                      reason="moomoo_broker_not_ready",
                                      preset=p.name, error=str(_exc))
                            continue
                        if (
                            trd_env_int != 0
                            and os.environ.get("ALLOW_MOOMOO_REAL_AUTO_EXECUTE", "0") != "1"
                        ):
                            log_event(_startup_log, "preset_auto_execute_skipped",
                                      reason="real_moomoo_auto_execute_blocked",
                                      preset=p.name, trd_env=trd_env_int)
                            continue
                        coro = _moomoo_execute_impl(order_req)
                    else:
                        # IBKR path (default)
                        ib_creds = SETTINGS.ibkr.as_dict()
                        if not (ib_creds.get("host") and ib_creds.get("port")):
                            log_event(_startup_log, "preset_auto_execute_skipped",
                                      reason="no_ibkr_creds", preset=p.name)
                            continue
                        try:
                            order_req = IBKROrderRequest(
                                creds=IBKRConnectRequest(
                                    host=ib_creds["host"],
                                    port=int(ib_creds["port"]),
                                    client_id=int(ib_creds.get("client_id", 1)),
                                ),
                                symbol=sig.symbol or p.ticker,
                                topology=p.topology,
                                direction=p.strategy_type,
                                contracts=int(getattr(sig, "contracts", 0) or 0),
                                strike_width=int(p.strike_width),
                                target_dte=int(p.target_dte),
                                spread_cost_target=float(p.spread_cost_target),
                                position_size_method=p.position_size_method or "",
                                risk_percent=float(p.sizing_params.get("risk_percent", 5.0)),
                                max_trade_cap=float(p.sizing_params.get("max_trade_cap", 0.0)),
                                target_spread_pct=float(p.sizing_params.get("target_spread_pct", 2.0)),
                                max_allocation_cap=float(p.sizing_params.get("max_allocation_cap", 2500.0)),
                                stop_loss_pct=float(p.stop_loss_pct),
                                take_profit_pct=float(p.take_profit_pct),
                                trailing_stop_pct=float(p.trailing_stop_pct),
                                otm_offset=float(p.strategy_params.get("offset", 0.0)),
                                client_order_id=_idem_key,
                            )
                        except Exception as e:
                            log_event(_startup_log, "preset_auto_execute_build_failed",
                                      error=str(e), preset=p.name)
                            continue
                        coro = _ibkr_execute_impl(order_req)

                    # Schedule the async order placement on the main event loop.
                    # _run_preset_tick runs in the APScheduler thread, so we
                    # need run_coroutine_threadsafe to cross threads safely.
                    loop = _MAIN_LOOP
                    if loop is None or not loop.is_running():
                        log_event(_startup_log, "preset_auto_execute_skipped",
                                  reason="loop_not_running", preset=p.name)
                        continue
                    try:
                        future = asyncio.run_coroutine_threadsafe(coro, loop)
                        result = future.result(timeout=60)
                    except Exception as e:
                        log_event(_startup_log, "preset_auto_execute_failed",
                                  error=str(e), preset=p.name, key=_idem_key)
                        continue

                    if isinstance(result, dict) and result.get("error"):
                        log_event(_startup_log, "preset_auto_execute_rejected",
                                  preset=p.name, key=_idem_key,
                                  error=result.get("error"),
                                  reason=result.get("reason"))
                    else:
                        log_event(_startup_log, "preset_auto_execute_submitted",
                                  preset=p.name, key=_idem_key,
                                  order_id=result.get("order_id"),
                                  position_id=result.get("position_id"),
                                  contracts=result.get("contracts"),
                                  limit=result.get("limit_price"))
    except Exception as e:
        log_event(_startup_log, "preset_tick_failed", error=str(e))


def _remove_market_scan_jobs() -> None:
    """Remove scanner jobs without touching monitor/telegram/digest jobs."""
    for job in scheduler.get_jobs():
        if str(job.id).startswith("market_scan"):
            job.remove()


@app.post("/api/scanner/preset/start")
def start_preset_scanner(payload: dict):
    name = (payload or {}).get("name")
    if not name:
        return {"error": "missing_preset_name"}
    try:
        from core.leader import try_acquire_leadership
        # I6: Claim leadership when starting automated background work
        try_acquire_leadership("data/monitor.lock")

        preset = _preset_scanner.load_preset(name)

        # Deactivate standard scanner to avoid duplicate jobs
        scanner_state["active"] = True  # Mark as active for HUD
        scanner_state["config"] = preset.to_dict()
        scanner_state["timing_mode"] = preset.timing_mode
        scanner_state["timing_value"] = preset.timing_value
        _remove_market_scan_jobs()

        if preset.timing_mode == "interval":
            secs = max(preset.timing_value, 10)
            scheduler.add_job(_run_preset_tick, "interval", seconds=secs,
                              id="market_scan", replace_existing=True)
        elif preset.timing_mode == "after_open":
            total_mins = 30 + preset.timing_value
            hour = 9 + total_mins // 60
            minute = total_mins % 60
            scheduler.add_job(_run_preset_tick, "cron", hour=hour, minute=minute,
                              day_of_week="mon-fri", timezone="America/New_York", id="market_scan")
        elif preset.timing_mode == "before_close":
            total_mins = 16 * 60 - preset.timing_value
            hour = total_mins // 60
            minute = total_mins % 60
            scheduler.add_job(_run_preset_tick, "cron", hour=hour, minute=minute,
                              day_of_week="mon-fri", timezone="America/New_York", id="market_scan")
        elif preset.timing_mode == "on_open":
            scheduler.add_job(_run_preset_tick, "cron", hour=9, minute=30,
                              day_of_week="mon-fri", timezone="America/New_York", id="market_scan")
        elif preset.timing_mode == "on_close":
            scheduler.add_job(_run_preset_tick, "cron", hour=16, minute=0,
                              day_of_week="mon-fri", timezone="America/New_York", id="market_scan")

    except KeyError as e:
        return {"error": "preset_not_found", "detail": str(e)}
    return {"started": True, "preset": preset.to_dict()}


@app.post("/api/scanner/preset/tick")
def tick_preset_scanner():
    """Manual tick — useful for tests + UI 'Scan Now' button."""
    try:
        signals = _preset_scanner.tick()
        # Sync logs for HUD
        recent = _preset_scanner.history(limit=50)
        scanner_state["logs"] = recent
        if recent:
            scanner_state["last_run"] = recent[0]["time"]
    except _PresetRequired as e:
        return {"error": "preset_required", "detail": str(e)}
    return {
        "signals": [s.to_dict() for s in signals],
        "history": _preset_scanner.history(limit=20),
    }


@app.post("/api/scanner/preset/stop")
def stop_preset_scanner():
    _preset_scanner.stop()
    scanner_state["active"] = False
    _remove_market_scan_jobs()
    return {"stopped": True}


@app.get("/api/scanner/preset/status")
def preset_scanner_status():
    p = _preset_scanner.active_preset
    return {
        "active": _preset_scanner.is_active,
        "preset": p.to_dict() if p else None,
        "history": _preset_scanner.history(limit=20),
    }


class IBKRConnectRequest(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1


# ── Connection auto-reconnect toggles (header pills) ──────────────────────
class ConnectionAutoRequest(BaseModel):
    broker: str  # "ibkr" or "moomoo"
    enabled: bool


@app.get("/api/connection/auto")
async def connection_auto_get():
    from core.connection_flags import snapshot
    return snapshot()


@app.post("/api/connection/auto")
async def connection_auto_set(req: ConnectionAutoRequest):
    from core.connection_flags import set_auto_enabled, snapshot
    try:
        set_auto_enabled(req.broker, req.enabled)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    # Side-effects when toggling off: cancel any in-flight reconnect work,
    # disconnect the live trader so the strategy doesn't keep dialing.
    if not req.enabled:
        if req.broker == "ibkr":
            try:
                from brokers.ibkr_trading import _ib_instances
                for trader in list(_ib_instances.values()):
                    try:
                        trader.disconnect()
                    except Exception:
                        pass
            except Exception:
                pass
        elif req.broker == "moomoo":
            try:
                global _moomoo_trader
                from core.broker import unregister_broker
                if _moomoo_trader is not None:
                    try:
                        _moomoo_trader.disconnect()
                    except Exception:
                        pass
                    _moomoo_trader = None
                try:
                    unregister_broker("moomoo")
                except Exception:
                    pass
            except Exception:
                pass

    # Side-effect when toggling on: kick a fresh connect attempt so the user
    # gets immediate feedback instead of waiting for the next scheduler tick.
    if req.enabled:
        if req.broker == "ibkr":
            try:
                from core.settings import SETTINGS
                ib_cfg = SETTINGS.ibkr.as_dict()
                if ib_cfg.get("host") and ib_cfg.get("port"):
                    asyncio.create_task(get_ib_connection(ib_cfg))
            except Exception:
                pass

    return {"ok": True, "flags": snapshot()}


@app.post("/api/ibkr/connect")
async def ibkr_connect(req: IBKRConnectRequest):
    # No force=True: this endpoint is also the frontend's 10s heartbeat poll
    # (useBackendData.positions). When the header IBKR toggle is OFF the gate
    # in get_ib_connection() must short-circuit so we don't dial TWS.
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
    symbol: str = "SPY"
    topology: str = "vertical_spread"
    direction: str = "bull_call"
    contracts: int = 0               # 0 = auto-size from config
    strike_width: int = 5
    target_dte: int = 14
    spread_cost_target: float = 250.0
    # Sizing overrides (0 = use SETTINGS defaults)
    position_size_method: str = ""  # "fixed"|"dynamic_risk"|"targeted_spread"|""
    use_dynamic_sizing: bool = False
    use_targeted_spread: bool = False
    risk_percent: float = 5.0
    target_spread_pct: float = 2.0
    max_allocation_cap: float = 2500.0
    max_trade_cap: float = 0.0
    # Risk overrides stored per-position
    stop_loss_pct: float = 50.0
    take_profit_pct: float = 50.0
    trailing_stop_pct: float = 0.0
    # Strike offset: points above underlying for K_long (SSRN 6355218: 0.96–2.00, default 1.50)
    otm_offset: float = 0.0
    # I5: optional client-provided ID for idempotency
    client_order_id: Optional[str] = None


async def _ibkr_execute_impl(req: IBKROrderRequest) -> dict:
    """Live order path — used by both /api/ibkr/execute and the preset
    auto-execute scheduler. Performs idempotency check, chain resolution,
    sizing, pre-trade risk gate, order submission, and journaling.

    Returns a JSON-serialisable dict with either ``success: True`` (and
    order details) or ``error: "..."`` so callers can surface the result
    uniformly.
    """
    import uuid as _uuid
    from core.journal import Position, Order, get_journal
    from core.risk import (
        AccountSnapshot, RiskContext, RiskLimits, evaluate_pre_trade,
        size_position, sizing_mode_from_request,
    )
    from core.chain import resolve_bull_call_spread
    from core.calendar import load_event_calendar
    from core.settings import SETTINGS

    journal = get_journal()

    # I5: Check for existing idempotency key BEFORE doing any work
    idem_key = req.client_order_id if req.client_order_id else None
    if idem_key:
        existing = journal.get_order_by_idempotency(idem_key)
        if existing:
            _startup_log.info("duplicate order suppressed (key=%s)", idem_key)
            return {
                "success": True,
                "duplicate": True,
                "order_id": existing.id,
                "position_id": existing.position_id,
                "status": existing.status,
            }

    # 1. Connect to IBKR
    trader, msg = await get_ib_connection(req.creds.model_dump())
    if not trader:
        return {"error": msg}

    # 2. Fetch account snapshot for risk checks
    try:
        acct = await trader.get_account_summary()
    except Exception as e:
        return {"error": f"account_summary_failed: {e}"}

    account = AccountSnapshot(
        equity=acct.get("equity", 0),
        buying_power=acct.get("buying_power", 0),
        excess_liquidity=acct.get("excess_liquidity", 0),
        daily_pnl=acct.get("daily_pnl", 0),
    )

    # 3. Resolve real option chain from IBKR (C4 + C5 fixes)
    spread = await resolve_bull_call_spread(
        trader, req.symbol, req.target_dte, req.spread_cost_target,
        max_width=req.strike_width + 35,
        otm_offset=req.otm_offset,
    )
    if spread is None:
        journal.log_event("chain_resolve_failed", subject=req.symbol)
        return {"error": "Could not resolve live option chain. Check TWS data subscriptions."}

    # 4. Position sizing (C9)
    mode = sizing_mode_from_request(req)
    if req.contracts > 0:
        contracts = req.contracts
    else:
        contracts = size_position(
            equity=account.equity,
            debit_per_contract=spread.net_debit,
            margin_per_contract=spread.margin_req,
            mode=mode,
            fixed_contracts=max(req.contracts, 1),
            risk_percent=req.risk_percent,
            max_trade_cap=req.max_trade_cap,
            target_spread_pct=req.target_spread_pct,
            max_allocation_cap=req.max_allocation_cap,
            excess_liquidity=account.excess_liquidity,
        )
    if contracts <= 0:
        return {"error": "sizing_zero", "detail": "Position size computed to 0 contracts."}

    # 5. Pre-trade risk check (C3)
    events = load_event_calendar()
    ctx = RiskContext(
        account=account,
        open_positions=len(journal.list_open()),
        today_realized_pnl=journal.today_realized_pnl(),
        debit_per_contract=spread.net_debit,
        margin_per_contract=spread.margin_req,
        contracts=contracts,
        target_dte=req.target_dte,
        limits=RiskLimits.from_settings(),
        events=events,
    )
    decision = evaluate_pre_trade(ctx)
    if not decision.allowed:
        journal.log_event("risk_rejected", subject=req.symbol, payload={
            "reason": decision.reason, **decision.details,
        })
        # Telegram: only buzz on risk-gate rejections; sizing_zero / chain
        # failures are noisier (every tick of a misconfigured preset would
        # ping the phone).
        try:
            from core.telegram_bot import notify_entry_rejected
            notify_entry_rejected(
                req.symbol, decision.reason,
                detail=", ".join(f"{k}={v}" for k, v in (decision.details or {}).items()),
            )
        except Exception:  # noqa: BLE001
            pass
        return {"error": "risk_rejected", "reason": decision.reason, "details": decision.details}

    # 6. Determine order side from spread type (C5 fix — no more hardcoded BUY)
    side = "BUY" if spread.net_debit > 0 else "SELL"

    # 7. Compute limit price with haircut
    haircut = SETTINGS.risk.limit_price_haircut
    ib_legs = spread.as_ib_legs()
    midpoint = await trader.get_combo_midpoint(req.symbol, ib_legs)
    if midpoint is None or midpoint <= 0:
        # Fall back to chain-derived mid
        midpoint = abs(spread.net_debit) / 100.0
    hc = haircut * abs(midpoint)
    limit_price = round(midpoint - hc if side == "BUY" else midpoint + hc, 2)

    # 8. Submit order
    pos_id = str(_uuid.uuid4())
    order_id = str(_uuid.uuid4())
    # I5: use client_order_id if provided, else fall back to position-based key
    idem_key = req.client_order_id if req.client_order_id else f"entry:{pos_id}"
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        res = await trader.place_combo_order(
            req.symbol, ib_legs, contracts, side=side, lmtPrice=limit_price,
        )
    except Exception as e:
        journal.log_event("order_submit_failed", subject=req.symbol, payload={"error": str(e)})
        return {"error": f"order_submit_failed: {e}"}

    broker_order_id = str(res.get("orderId", "")) if isinstance(res, dict) else ""

    # 9. Journal the position + entry order (C1)
    journal.open_position(Position(
        id=pos_id,
        symbol=req.symbol,
        topology=spread.topology,
        direction=spread.direction,
        contracts=contracts,
        entry_cost=spread.net_debit * contracts,
        entry_time=now_iso,
        expiry=spread.expiry,
        state="pending",
        high_water_mark=abs(spread.net_debit) / 100.0,
        broker="ibkr",
        legs=tuple(leg.__dict__ if hasattr(leg, '__dict__') else leg for leg in ib_legs),
        meta={
            "combo_type": "debit" if spread.net_debit > 0 else "credit",
            "stop_loss_pct": req.stop_loss_pct,
            "take_profit_pct": req.take_profit_pct,
            "trailing_stop_pct": req.trailing_stop_pct,
            "underlying_price": spread.underlying_price,
            "implied_vol": spread.implied_vol,
        },
    ))
    journal.record_order(Order(
        id=order_id,
        position_id=pos_id,
        broker="ibkr",
        broker_order_id=broker_order_id or None,
        side=side,
        limit_price=limit_price,
        status="submitted",
        submitted_at=now_iso,
        kind="entry",
        idempotency_key=idem_key,
    ))
    journal.log_event("entry_submitted", subject=pos_id, payload={
        "symbol": req.symbol, "contracts": contracts, "side": side,
        "limit": limit_price, "mid": midpoint, "expiry": spread.expiry,
        "K_long": spread.meta.get("K_long"), "K_short": spread.meta.get("K_short"),
        "broker_order_id": broker_order_id,
    })

    # Telegram: ping the operator that an order is in flight.
    try:
        from core.telegram_bot import notify_entry_submitted
        notify_entry_submitted(
            req.symbol, side, contracts, limit_price,
            idem_key=idem_key,
        )
    except Exception:  # noqa: BLE001
        pass  # best-effort; never let notify failure rollback an order

    return {
        "success": True,
        "position_id": pos_id,
        "order_id": order_id,
        "broker_order_id": broker_order_id,
        "symbol": req.symbol,
        "direction": spread.direction,
        "expiry": spread.expiry,
        "contracts": contracts,
        "side": side,
        "limit_price": limit_price,
        "midpoint": midpoint,
        "net_debit": spread.net_debit,
        "K_long": spread.meta.get("K_long"),
        "K_short": spread.meta.get("K_short"),
        "underlying_price": spread.underlying_price,
    }


@app.post("/api/ibkr/execute")
async def ibkr_execute(req: IBKROrderRequest):
    """Public order endpoint — thin wrapper around _ibkr_execute_impl."""
    return await _ibkr_execute_impl(req)


# Haircut percentages for exit paths.
# - Manual exit: moderate (0.05) — same as the algorithmic monitor.
# - Flatten-all (panic button): aggressive (0.15) — user wants out NOW,
#   accept slippage to ensure fills cross any reasonable spread.
_EXIT_HAIRCUT_MANUAL = 0.05
_EXIT_HAIRCUT_PANIC = 0.15


@app.post("/api/ibkr/exit")
async def ibkr_exit(req: dict):
    """Manual exit for a single position. JSON: { "creds": {...}, "position_id": "..." }"""
    from core.journal import get_journal
    from core.monitor import submit_exit_order

    journal = get_journal()
    trader, msg = await get_ib_connection(req.get("creds", {}))
    if not trader:
        return {"error": msg}

    pos_id = req.get("position_id")
    pos = journal.get_position(pos_id)
    if not pos:
        return {"error": "position_not_found", "id": pos_id}

    if pos.state not in ("open", "closing"):
        return {"error": "position_not_open", "state": pos.state}

    try:
        legs_list = [dict(leg) for leg in pos.legs]
        mid = await trader.get_combo_midpoint(pos.symbol, legs_list)
        # No quote → don't fabricate a $0.01 limit that can never fill.
        # Tell the caller the real situation so they can retry, escalate
        # to flatten-all (which uses a market-order fallback), or wait.
        if mid is None or mid <= 0 or (isinstance(mid, float) and np.isnan(mid)):
            journal.log_event("exit_no_quote", subject=pos_id, payload={
                "symbol": pos.symbol, "reason": "manual_close",
            })
            return {
                "error": "no_quote",
                "detail": "Could not fetch a combo midpoint for this position. "
                          "Retry once a quote is available, or use FLATTEN ALL "
                          "to escalate to a market-order close.",
                "position_id": pos_id,
            }
        res = await submit_exit_order(
            trader, pos, float(mid), "manual_close", journal,
            haircut_pct=_EXIT_HAIRCUT_MANUAL,
        )
        # ``submit_exit_order`` never raises — surface its real outcome
        # (ok / error) instead of an unconditional success: True.
        return {"success": bool(res.get("ok")), **res}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ibkr/flatten_all")
async def ibkr_flatten_all(req: IBKRConnectRequest):
    """C6: Kill switch — close every open position aggressively.

    Uses a wide haircut so limit orders cross any reasonable spread, and
    falls back to a market order when no quote is available. The user
    clicked panic for a reason; getting filled matters more than slippage.
    """
    from core.journal import get_journal
    from core.monitor import submit_exit_order

    journal = get_journal()
    trader, msg = await get_ib_connection(req.model_dump())
    if not trader:
        return {"error": msg}

    open_positions = journal.list_open()
    if not open_positions:
        return {"closed": 0, "msg": "No open positions."}

    results = []
    for pos in open_positions:
        if pos.state not in ("open", "closing"):
            continue
        try:
            legs_list = [dict(leg) for leg in pos.legs]
            mid = await trader.get_combo_midpoint(pos.symbol, legs_list)
            if mid is None or mid <= 0 or (isinstance(mid, float) and np.isnan(mid)):
                # No quote — fall back to a market-order close so the
                # position actually exits. ``_market_close_position`` posts
                # the same journal entries as ``submit_exit_order`` but
                # with ``lmtPrice=None`` so IBKR routes it as a market order.
                res = await _market_close_position(trader, pos, journal,
                                                    reason="manual_flatten_market")
            else:
                res = await submit_exit_order(
                    trader, pos, float(mid), "manual_flatten", journal,
                    haircut_pct=_EXIT_HAIRCUT_PANIC,
                )
            results.append({"position_id": pos.id, **res})
        except Exception as e:
            results.append({"position_id": pos.id, "error": str(e)})

    journal.log_event("flatten_all", payload={"count": len(results)})
    try:
        from core.telegram_bot import notify_alert
        notify_alert("critical", f"FLATTEN ALL fired — {len(results)} position(s) closing")
    except Exception:  # noqa: BLE001
        pass
    return {"closed": len(results), "results": results}


async def _market_close_position(trader, pos, journal, *, reason: str) -> dict:
    """Fallback close path used when no combo quote is available.

    Submits the close as a market order (no ``lmtPrice``) so it crosses
    any spread. Mirrors ``submit_exit_order``'s journaling so the fill
    watcher reconciles the resulting fill the same way as a normal exit.
    """
    import uuid as _uuid
    from datetime import timezone as _tz
    from core.journal import Order

    # Closing side: debit spreads (entered BUY) close SELL; credits the inverse.
    combo_type = (pos.meta or {}).get("combo_type", "debit")
    close_side = "SELL" if combo_type == "debit" else "BUY"

    # Reverse the legs so place_combo_order interprets the close correctly
    # when we pass side=close_side.
    legs = []
    for leg in pos.legs:
        flipped = dict(leg)
        side = leg.get("side", "long")
        flipped["side"] = "short" if side == "long" else "long"
        legs.append(flipped)
    try:
        res = await trader.place_combo_order(
            pos.symbol, legs, int(pos.contracts), side=close_side, lmtPrice=None,
        )
    except Exception as e:
        journal.log_event("exit_failed", subject=pos.id, payload={
            "reason": reason, "error": f"{type(e).__name__}: {e}",
        })
        return {"ok": False, "error": str(e), "reason": reason}

    broker_order_id = str(res.get("orderId", "")) if isinstance(res, dict) else ""
    order_id = str(_uuid.uuid4())
    journal.record_order(Order(
        id=order_id,
        position_id=pos.id,
        broker=pos.broker,
        broker_order_id=broker_order_id or None,
        side=close_side,
        limit_price=None,
        status="submitted",
        submitted_at=datetime.now(_tz.utc).isoformat(timespec="seconds"),
        kind="exit",
        idempotency_key=f"exit:{pos.id}:{reason}",
    ))
    journal.update_position(pos.id, state="closing")
    journal.log_event("exit_submitted", subject=pos.id, payload={
        "reason": reason, "order_type": "MARKET",
        "broker_order_id": broker_order_id,
    })
    return {"ok": True, "order_id": order_id, "reason": reason,
            "order_type": "MARKET"}


@app.post("/api/ibkr/chain_debug")
async def ibkr_chain_debug(
    req: IBKRConnectRequest,
    symbol: str = "SPY",
    target_dte: int = 7,
    target_cost: float = 150.0,
):
    """Diagnose chain resolution step-by-step without placing an order.

    Returns a detailed breakdown of each resolution step plus a synthetic
    fallback so you can see what the paper_entry would use.  Useful for
    verifying TWS data subscriptions and market-hours constraints.
    """
    import traceback as _tb
    import math as _math
    try:
        from core.chain import (
            resolve_bull_call_spread_with_diagnostics,
            build_synthetic_spread,
        )
        trader, msg = await get_ib_connection(req.model_dump())
        if not trader:
            return {"error": msg}

        spread, diag = await resolve_bull_call_spread_with_diagnostics(
            trader, symbol, target_dte, target_cost, quote_wait=2.0
        )

        # NaN guard: IBKR returns IEEE-754 NaN for closed-market quotes.
        # NaN is truthy so `nan or 0` == nan, and nan<=0 is False — yfinance
        # branch would be skipped.  Force to 0.0 so synthetic fetches price.
        _raw_und = diag.get("underlying") or 0.0
        _underlying_hint = 0.0 if (
            _raw_und is None or not isinstance(_raw_und, (int, float))
            or _math.isnan(_raw_und) or _math.isinf(_raw_und)
        ) else float(_raw_und)
        synthetic = build_synthetic_spread(
            symbol=symbol,
            target_dte=target_dte,
            target_cost=target_cost,
            underlying=_underlying_hint,
        )

        return _safe_json({
            "live_chain": {
                "resolved": spread is not None,
                "net_debit": spread.net_debit if spread else None,
                "K_long": spread.meta.get("K_long") if spread else None,
                "K_short": spread.meta.get("K_short") if spread else None,
                "expiry": spread.expiry if spread else None,
            },
            "diagnostics": diag,
            "synthetic_chain": {
                "available": synthetic is not None,
                "net_debit": synthetic.net_debit if synthetic else None,
                "K_long": synthetic.meta.get("K_long") if synthetic else None,
                "K_short": synthetic.meta.get("K_short") if synthetic else None,
                "underlying": synthetic.underlying_price if synthetic else None,
                "hv_sigma": synthetic.meta.get("hv_sigma") if synthetic else None,
            },
            "recommendation": (
                "live chain available — use paper_entry normally"
                if spread else
                "live chain unavailable (market closed or no subscription) — "
                "paper_entry will use synthetic_bs pricing"
            ),
        })
    except BaseException as _exc:
        return {"error": str(_exc), "traceback": _tb.format_exc()}


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
    """C11 + I10: Extended liveness check.

    Returns socket state, journal / scheduler health, leader state, plus I10
    UI-facing alert flags: daily-loss consumption, monitor-tick staleness,
    and dropped-socket signal. The UI surfaces these as banners.
    """
    from brokers.ibkr_trading import _ib_instances, HAS_IBSYNC
    from core.journal import get_journal
    from core.leader import is_leader, current_leader_info
    from core.risk import RiskLimits
    from dataclasses import asdict as _asdict

    leader_info = current_leader_info()
    result = {
        "alive": False,
        "status": "unavailable",
        "ibkr_available": HAS_IBSYNC,
        "journal_ok": False,
        "open_positions": 0,
        "today_pnl": 0.0,
        "today_trades": 0,
        "scheduler_jobs": [],
        "monitor_registered": False,
        "is_leader": is_leader(),
        "leader_info": _asdict(leader_info) if leader_info else None,
        # I10 defaults (populated below).
        "daily_loss_pct_used": 0.0,
        "daily_loss_warning": False,
        "daily_loss_limit_pct": 0.0,
        "monitor_last_tick_iso": _last_monitor_tick_iso,
        "monitor_seconds_since_tick": None,
        "monitor_stalled": False,
        "ibkr_dropped": False,
        "moomoo_connected": False,
        "moomoo_last_healthy_iso": None,
        "moomoo_seconds_since_healthy": None,
        "moomoo_stalled": False,
        "moomoo_reconnecting": False,
        "moomoo_reconnect_attempt": 0,
        "zero_dte_bot": None,
        "market_open": False,
        "market_reason": "unknown",
        "minutes_to_close": None,
        "next_close": "unknown",
        "alerts": [],
    }

    # Market-hours status for the header/status bar. Keep this independent of
    # broker connectivity so the UI can explain closed-market states cleanly.
    try:
        from core.calendar import is_market_open, minutes_to_close
        market_open, market_reason = is_market_open()
        mtc = minutes_to_close()
        result["market_open"] = bool(market_open)
        result["market_reason"] = market_reason
        result["minutes_to_close"] = mtc
        result["next_close"] = f"{mtc}m to close" if market_open and mtc >= 0 else market_reason.replace("_", " ")
    except Exception:
        pass

    # Journal health + daily-loss consumption (I10).
    try:
        journal = get_journal()
        result["journal_ok"] = True
        result["open_positions"] = len(journal.list_open())
        today_pnl = journal.today_realized_pnl()
        result["today_pnl"] = today_pnl
        result["today_trades"] = journal.today_trade_count()

        # Compute % of daily-loss limit consumed.
        try:
            limits = RiskLimits.from_settings()
            result["daily_loss_limit_pct"] = limits.daily_loss_limit_pct
            # Only losses (negative P&L) count against the limit.
            loss = -today_pnl if today_pnl < 0 else 0.0
            # Use a coarse equity estimate — either last known snapshot or 1.
            equity_est = 0.0
            try:
                from core.settings import SETTINGS as _S
                equity_est = float(
                    getattr(_S.risk, "assumed_equity_for_alerts", 0.0) or 0.0
                )
            except Exception:
                equity_est = 0.0
            if equity_est > 0 and limits.daily_loss_limit_pct > 0:
                pct_used = (loss / equity_est * 100.0) / limits.daily_loss_limit_pct
                result["daily_loss_pct_used"] = round(pct_used * 100.0, 2)
                if pct_used >= 0.8:
                    result["daily_loss_warning"] = True
                    result["alerts"].append({
                        "level": "warning",
                        "code": "daily_loss_approaching",
                        "message": (
                            f"Daily loss at {result['daily_loss_pct_used']}% "
                            f"of {limits.daily_loss_limit_pct}% limit"
                        ),
                    })
        except Exception:
            pass
    except Exception:
        pass

    # Scheduler jobs
    try:
        jobs = scheduler.get_jobs()
        result["scheduler_jobs"] = [j.id for j in jobs]
        result["monitor_registered"] = any("monitor" in j.id for j in jobs)
    except Exception:
        pass

    # 0DTE Bot status
    try:
        global _zero_dte_bot
        if _zero_dte_bot is not None:
            result["zero_dte_bot"] = _zero_dte_bot.status()
    except Exception:
        pass

    # I10: monitor-tick staleness. Only meaningful when monitor is registered.
    if result["monitor_registered"] and _last_monitor_tick_iso:
        try:
            last_dt = datetime.fromisoformat(_last_monitor_tick_iso)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            delta = (datetime.now(timezone.utc) - last_dt).total_seconds()
            result["monitor_seconds_since_tick"] = round(delta, 1)
            if delta > 30.0:
                result["monitor_stalled"] = True
                result["alerts"].append({
                    "level": "critical",
                    "code": "monitor_stalled",
                    "message": f"No monitor tick for {int(delta)}s",
                })
        except Exception:
            pass
    elif result["monitor_registered"] and _last_monitor_tick_iso is None:
        # Registered but never ticked — flag once interval has had a chance.
        result["monitor_stalled"] = True
        result["alerts"].append({
            "level": "warning",
            "code": "monitor_never_ticked",
            "message": "Monitor registered but has not ticked yet",
        })

    # Moomoo OpenD health.  Surface connection state + staleness so
    # heartbeat consumers (UI, /api/health watchers) can alert on a
    # silently-stalled OpenD connection.
    try:
        if _moomoo_trader is not None and _moomoo_trader.is_alive():
            result["moomoo_connected"] = True
            result["moomoo_acc_id"] = getattr(_moomoo_trader, "_acc_id", None)
            last_iso = getattr(_moomoo_trader, "last_healthy_iso", None)
            result["moomoo_last_healthy_iso"] = last_iso
            if last_iso:
                last_dt = datetime.fromisoformat(last_iso)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                delta = (datetime.now(timezone.utc) - last_dt).total_seconds()
                result["moomoo_seconds_since_healthy"] = round(delta, 1)
                # 5 minutes without a successful broker call → stale.
                # (account/positions/snapshot polling at 15s should keep
                # this fresh whenever the scanner or monitor is active.)
                if delta > 300:
                    result["moomoo_stalled"] = True
                    result["alerts"].append({
                        "level": "warning" if delta < 900 else "critical",
                        "code": "moomoo_stalled",
                        "message": f"OpenD silent for {int(delta)}s — connection may be dead",
                    })
        elif _moomoo_trader is not None and not _moomoo_trader.is_alive():
            # Connection dropped — trigger auto-reconnect if header toggle ON
            result["moomoo_connected"] = False
            from core.connection_flags import is_auto_enabled
            if is_auto_enabled("moomoo") and hasattr(_moomoo_trader, "schedule_reconnect"):
                _moomoo_trader.schedule_reconnect(_MAIN_LOOP)
            if hasattr(_moomoo_trader, "reconnecting") and _moomoo_trader.reconnecting:
                result["moomoo_reconnecting"] = True
                result["moomoo_reconnect_attempt"] = getattr(
                    _moomoo_trader, "reconnect_attempt", 0
                )
    except Exception:  # noqa: BLE001
        pass

    # IBKR socket
    if not HAS_IBSYNC:
        return result
    key = f"{req.host}:{req.port}:{req.client_id}"
    trader = _ib_instances.get(key)
    if not trader:
        result["status"] = "not_connected"
        return result
    try:
        alive = trader.ib.isConnected()
        if not alive:
            trader.connected = False
            result["ibkr_dropped"] = True
            result["alerts"].append({
                "level": "critical",
                "code": "ibkr_dropped",
                "message": "IBKR socket dropped — no order management possible",
            })
        result["alive"] = alive
        result["status"] = "online" if alive else "dropped"
    except Exception as e:
        trader.connected = False
        result["status"] = "error"
        result["detail"] = str(e)
        result["ibkr_dropped"] = True
        result["alerts"].append({
            "level": "critical",
            "code": "ibkr_error",
            "message": f"IBKR socket error: {e}",
        })

    return result


@app.post("/api/ibkr/reconnect")
async def ibkr_reconnect(req: IBKRConnectRequest):
    """Force-reconnect an existing IBKR session."""
    from brokers.ibkr_trading import _ib_instances
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


# ── Moomoo API endpoints ──────────────────────────────────────────────────


class MoomooConnectRequest(BaseModel):
    host: str = "127.0.0.1"
    port: int = 11111
    trade_password: str = ""
    trd_env: int = 0                  # 0=simulate, 1=real
    security_firm: str = "NONE"       # NONE=auto, FUTUINC=US, FUTUCA=Canada, FUTUSECURITIES=HK
    filter_trdmarket: str = "NONE"    # NONE=auto (recommended), US, HK, CA, etc.


class MoomooProbeRequest(BaseModel):
    host: str = "127.0.0.1"
    port: int = 11111


class MoomooOrderRequest(BaseModel):
    host: str = "127.0.0.1"
    port: int = 11111
    trade_password: str = ""
    symbol: str = "SPY"
    direction: str = "bull_call"        # "bull_call" | "bear_put"
    contracts: int = 1
    strike_width: int = 5
    target_dte: int = 0
    spread_cost_target: float = 250.0
    otm_offset: float = 0.0
    position_size_method: str = "fixed"
    risk_percent: float = 1.0
    max_allocation_cap: float = 500.0
    stop_loss_pct: float = 50.0
    take_profit_pct: float = 50.0
    trailing_stop_pct: float = 0.0
    client_order_id: Optional[str] = None
    # Test-only escape hatch: skip the event-blackout gate so smoke
    # tests can fire orders during news days.  NEVER set True in
    # production scanner code paths — the news filter exists for a
    # reason (NFP/FOMC/CPI move SPY 2-3% in seconds).
    #
    # Security: this flag arrives over the wire but is honored ONLY when the
    # server-side env var ALLOW_BYPASS_EVENT_BLACKOUT=1 is also set. A
    # field-level validator (below) zeroes it otherwise so a malicious or
    # misconfigured client can't disable the blackout gate.
    bypass_event_blackout: bool = False

    @field_validator("bypass_event_blackout", mode="before")
    @classmethod
    def _gate_bypass_event_blackout(cls, v: Any) -> bool:
        # Server-side opt-in via env var. Defaults to "0" → False.
        import os as _os
        if _os.environ.get("ALLOW_BYPASS_EVENT_BLACKOUT", "0") != "1":
            return False
        return bool(v)
    # Chain-quality gate tolerance overrides. moomoo OpenD NBBO is sparser
    # than IBKR's, so the per-leg bid/ask % default is more permissive than
    # the validate_spread_quality() default. Set via preset.strategy_params.
    chain_max_bid_ask_pct: float = 0.25
    chain_min_volume: int = 0
    chain_min_open_interest: int = 0
    # Skill Step 10 / paper-validation hook: when the auto-execute path
    # builds this request from a preset, it threads the preset name so the
    # journal can later filter positions per-preset for live-vs-backtest
    # comparison in /api/paper_validation.
    preset_name: Optional[str] = None


_moomoo_trader = None


@app.post("/api/moomoo/connect")
async def moomoo_connect(req: MoomooConnectRequest):
    global _moomoo_trader
    from brokers.moomoo_trading import MoomooTrader
    from core.broker import register_broker
    trader = MoomooTrader(
        host=req.host,
        port=req.port,
        trade_password=req.trade_password,
        trd_env=req.trd_env,
        security_firm=req.security_firm,
        filter_trdmarket=req.filter_trdmarket,
    )
    try:
        result = await trader.connect()
        # H6: cleanly disconnect any prior trader before swapping in the new
        # one so we don't leak OpenD socket contexts on reconnect.
        if _moomoo_trader is not None and _moomoo_trader is not trader:
            try:
                _moomoo_trader.disconnect()
            except Exception:
                pass
        _moomoo_trader = trader
        register_broker("moomoo", trader)
        # Post-reconnect drift check: catch positions that opened/closed at
        # the broker while we were disconnected. Fire-and-forget so the
        # /connect response isn't blocked.
        async def _reconcile_after_connect():
            try:
                await asyncio.sleep(2)
                from core.moomoo_reconciler import reconcile_once
                rec = await reconcile_once()
                if rec.get("orphans") or rec.get("phantoms"):
                    _startup_log.info("post-reconnect reconcile: %s", rec)
            except Exception as exc:
                _startup_log.warning("post-reconnect reconcile failed: %s", exc)
        asyncio.create_task(_reconcile_after_connect())
        return result
    except Exception as exc:
        return {"connected": False, "error": str(exc)}


@app.post("/api/moomoo/probe")
async def moomoo_probe(req: MoomooProbeRequest):
    """Diagnostic: list every account OpenD has, regardless of filters.

    Lets the UI show the user which accounts are visible and what trd_env /
    security_firm / trdmarket_auth values they have, so they can pick the
    right combo before calling /connect.
    """
    from brokers.moomoo_trading import MoomooTrader
    return await MoomooTrader.probe(host=req.host, port=req.port)


@app.post("/api/moomoo/disconnect")
async def moomoo_disconnect():
    global _moomoo_trader
    from core.broker import unregister_broker
    if _moomoo_trader:
        _moomoo_trader.disconnect()
        _moomoo_trader = None
    unregister_broker("moomoo")
    return {"status": "disconnected"}


@app.get("/api/moomoo/account")
async def moomoo_account():
    from core.broker import get_broker, BrokerNotConnected
    try:
        broker = get_broker("moomoo")
        return await broker.get_account_summary()
    except BrokerNotConnected as exc:
        return {"error": str(exc)}


@app.get("/api/moomoo/positions")
async def moomoo_positions():
    from core.broker import get_broker, BrokerNotConnected
    try:
        broker = get_broker("moomoo")
        return {"positions": await broker.get_positions()}
    except BrokerNotConnected as exc:
        return {"error": str(exc)}


@app.get("/api/moomoo/chain")
async def moomoo_chain(symbol: str = "SPY", date: str = ""):
    from core.broker import get_broker, BrokerNotConnected
    if not date:
        from datetime import date as _date
        date = _date.today().strftime("%Y-%m-%d")
    try:
        broker = get_broker("moomoo")
        df = await broker.get_option_chain(symbol, date)
        return {"chain": df.to_dict(orient="records") if hasattr(df, "to_dict") else []}
    except BrokerNotConnected as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/moomoo/execute")
async def moomoo_execute(req: MoomooOrderRequest):
    """Public moomoo order entry point.  Wraps the impl with a catch-all so
    unexpected exceptions surface as JSON instead of HTTP 500 — the UI can't
    show a useful error from a plain 'Internal Server Error' string body.
    """
    try:
        return await _moomoo_execute_impl(req)
    except Exception as exc:  # noqa: BLE001
        import traceback
        from core.journal import get_journal
        tb = traceback.format_exc()
        try:
            get_journal().log_event(
                "moomoo_execute_unhandled",
                payload={"error": str(exc), "type": type(exc).__name__, "traceback": tb[:4000]},
            )
        except Exception:
            pass
        return {"error": "unhandled_exception", "reason": f"{type(exc).__name__}: {exc}"}


@app.post("/api/moomoo/exit")
async def moomoo_exit(payload: dict):
    position_id = (payload or {}).get("position_id")
    reason = (payload or {}).get("reason") or "manual_exit"
    if not position_id:
        return {"error": "missing_position_id"}
    from core.journal import get_journal, Order
    from core.broker import get_broker, BrokerNotConnected
    import uuid
    journal = get_journal()
    pos = journal.get_position(position_id)
    if not pos:
        return {"error": "position_not_found"}
    if pos.broker != "moomoo":
        return {"error": f"position broker is '{pos.broker}', not 'moomoo'"}
    if pos.state != "open":
        return {"error": f"position state is '{pos.state}', not 'open'"}
    try:
        broker = get_broker("moomoo")
    except BrokerNotConnected as exc:
        return {"error": f"moomoo not connected: {exc}"}

    # Capture spread mid BEFORE close so we have a P&L reference if leg fills
    # don't reconcile cleanly (best-effort; None means no quote available).
    pre_close_mid = None
    try:
        pre_close_mid = await broker.get_spread_mid(list(pos.legs))
    except Exception:
        pass

    try:
        result = await broker.close_position(position_id, list(pos.legs))
    except Exception as exc:
        journal.log_event("exit_failed", subject=position_id, payload={
            "error": str(exc), "broker": "moomoo", "reason": reason,
        })
        return {"error": f"close_position failed: {exc}"}

    # Record an exit order per leg so fill_watcher can reconcile each fill
    # individually.  Mark position state='closing' — fill_watcher will close
    # it (with real realized_pnl) once both leg fills are confirmed.
    close_orders = result.get("close_orders", []) if isinstance(result, dict) else []
    submitted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    recorded_order_ids = []
    for entry in close_orders:
        leg = entry.get("leg") if isinstance(entry, dict) else None
        broker_order_id = entry.get("order_id") if isinstance(entry, dict) else None
        side = "SELL" if (leg or {}).get("side") == "long" else "BUY"
        order_id = str(uuid.uuid4())
        try:
            journal.record_order(Order(
                id=order_id,
                position_id=position_id,
                broker="moomoo",
                broker_order_id=str(broker_order_id) if broker_order_id is not None else "",
                side=side,
                limit_price=None,
                status="submitted",
                submitted_at=submitted_at,
                kind="exit",
                idempotency_key=f"exit:{position_id}:{reason}:{order_id[:8]}",
            ))
            recorded_order_ids.append(order_id)
        except Exception as exc:  # noqa: BLE001
            journal.log_event("exit_order_record_failed", subject=position_id,
                              payload={"error": str(exc), "broker_order_id": broker_order_id})

    journal.update_position(position_id, state="closing", exit_reason=reason)
    journal.log_event("exit_submitted", subject=position_id, payload={
        "broker": "moomoo", "reason": reason,
        "pre_close_spread_mid": pre_close_mid,
        "leg_orders": recorded_order_ids,
        "result": result,
    })
    return {**result, "pre_close_spread_mid": pre_close_mid, "exit_orders": recorded_order_ids}


@app.post("/api/moomoo/close_one")
async def moomoo_close_one(req: dict):
    """Single-leg broker-direct market close. Body: {code, qty, side: 'buy'|'sell'}."""
    from core.broker import get_broker, BrokerNotConnected
    try:
        broker = get_broker("moomoo")
    except BrokerNotConnected as exc:
        return {"error": "broker_not_connected", "reason": str(exc)}
    import asyncio as _aio_local
    loop = _aio_local.get_event_loop()
    code = req["code"]; qty = int(req["qty"]); side = req["side"]

    def _go():
        import moomoo as ft
        from brokers.moomoo_trading import _close_session_kwargs
        trd_side = ft.TrdSide.BUY if side == "buy" else ft.TrdSide.SELL
        ret, data = broker._trd_ctx.place_order(
            price=0.0, qty=qty, code=code, trd_side=trd_side,
            order_type=ft.OrderType.MARKET,
            trd_env=broker._ft_trd_env, acc_id=broker._acc_id,
            remark="close_one",
            **_close_session_kwargs(ft),
        )
        if ret != ft.RET_OK:
            return {"error": str(data)}
        return {"order_id": str(data["order_id"].iloc[0])}

    try:
        return await loop.run_in_executor(None, _go)
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/moomoo/flatten_broker")
async def moomoo_flatten_broker():
    """Emergency broker-direct flatten: pulls live positions from moomoo OpenD
    (bypassing the journal) and submits MARKET close orders for each. Used
    when the journal has drifted from broker reality."""
    from core.broker import get_broker, BrokerNotConnected
    try:
        broker = get_broker("moomoo")
    except BrokerNotConnected as exc:
        return {"error": "broker_not_connected", "reason": str(exc)}

    import asyncio as _aio_local
    loop = _aio_local.get_event_loop()

    def _list_broker_positions():
        import moomoo as ft
        ret, data = broker._trd_ctx.position_list_query(
            trd_env=broker._ft_trd_env, acc_id=broker._acc_id,
        )
        if ret != ft.RET_OK:
            raise RuntimeError(f"position_list_query failed: {data}")
        return data.to_dict(orient="records")

    rows = await loop.run_in_executor(None, _list_broker_positions)
    spy_legs = [r for r in rows if str(r.get("code", "")).startswith("US.SPY")]

    def _close_leg(row):
        import moomoo as ft
        from brokers.moomoo_trading import _close_session_kwargs
        qty = int(abs(float(row["qty"])))
        if qty == 0:
            return {"code": row["code"], "skipped": "zero qty"}
        # qty > 0 → long → SELL to close. qty < 0 → short → BUY to close.
        side = ft.TrdSide.SELL if float(row["qty"]) > 0 else ft.TrdSide.BUY
        ret, data = broker._trd_ctx.place_order(
            price=0.0, qty=qty, code=row["code"], trd_side=side,
            order_type=ft.OrderType.MARKET,
            trd_env=broker._ft_trd_env, acc_id=broker._acc_id,
            remark="flatten_broker",
            **_close_session_kwargs(ft),
        )
        if ret != ft.RET_OK:
            return {"code": row["code"], "qty": qty, "error": str(data)}
        return {"code": row["code"], "qty": qty, "order_id": str(data["order_id"].iloc[0])}

    results = []
    for row in spy_legs:
        try:
            res = await loop.run_in_executor(None, _close_leg, row)
        except Exception as exc:
            res = {"code": row.get("code"), "error": str(exc)}
        results.append(res)

    return {"closed": len([r for r in results if r.get("order_id")]), "results": results}


@app.post("/api/moomoo/flatten_all")
async def moomoo_flatten_all():
    """Kill switch — market-close every open moomoo position.

    Mirrors /api/ibkr/flatten_all: iterates the journal's open moomoo
    positions and fires broker.close_position on each.  Best-effort —
    individual failures are returned in the results list but don't abort
    the loop.  Telegram alert fires regardless of partial failures so
    the operator gets a notification.
    """
    from core.broker import get_broker, BrokerNotConnected
    from core.journal import get_journal, Order
    import uuid

    journal = get_journal()
    try:
        broker = get_broker("moomoo")
    except BrokerNotConnected as exc:
        return {"error": "broker_not_connected", "reason": str(exc)}

    open_positions = [p for p in journal.list_open() if p.broker == "moomoo"]
    if not open_positions:
        return {"closed": 0, "msg": "No open moomoo positions."}

    submitted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = []
    for pos in open_positions:
        if pos.state not in ("open", "closing"):
            continue
        try:
            legs_list = [dict(leg) for leg in pos.legs]
            # Best-effort pre-close mid for the audit trail.
            try:
                pre_mid = await broker.get_spread_mid(legs_list)
            except Exception:
                pre_mid = None
            close_result = await broker.close_position(pos.id, legs_list)
            recorded = []
            for entry in (close_result.get("close_orders") or []):
                leg = entry.get("leg") if isinstance(entry, dict) else None
                broker_order_id = entry.get("order_id") if isinstance(entry, dict) else None
                side = "SELL" if (leg or {}).get("side") == "long" else "BUY"
                order_id = str(uuid.uuid4())
                journal.record_order(Order(
                    id=order_id,
                    position_id=pos.id,
                    broker="moomoo",
                    broker_order_id=str(broker_order_id) if broker_order_id is not None else "",
                    side=side,
                    limit_price=None,
                    status="submitted",
                    submitted_at=submitted_at,
                    kind="exit",
                    idempotency_key=f"flatten:{pos.id}:{order_id[:8]}",
                ))
                recorded.append(order_id)
            journal.update_position(pos.id, state="closing", exit_reason="manual_flatten")
            journal.log_event("exit_submitted", subject=pos.id, payload={
                "broker": "moomoo", "reason": "manual_flatten",
                "pre_close_spread_mid": pre_mid, "leg_orders": recorded,
            })
            results.append({
                "position_id": pos.id, "ok": True,
                "leg_orders": recorded, "pre_close_mid": pre_mid,
            })
        except Exception as exc:  # noqa: BLE001
            journal.log_event("exit_failed", subject=pos.id, payload={
                "error": str(exc), "broker": "moomoo", "reason": "manual_flatten",
            })
            results.append({"position_id": pos.id, "ok": False, "error": str(exc)})

    journal.log_event("flatten_all", payload={
        "broker": "moomoo", "count": len(results),
    })
    try:
        from core.telegram_bot import notify_alert
        notify_alert("critical", f"MOOMOO FLATTEN ALL — {len(results)} position(s) closing")
    except Exception:  # noqa: BLE001
        pass
    return {"closed": len(results), "results": results}


@app.post("/api/moomoo/reconcile")
async def moomoo_reconcile():
    """Manually trigger the orphan-leg reconciler.

    Compares moomoo broker positions against journal open positions.
    Returns counts + any orphans (broker has, journal doesn't) and
    phantoms (journal has, broker doesn't).  Phantoms older than 60s
    are auto-closed with reason='reconcile_phantom'.
    """
    from core.moomoo_reconciler import reconcile_once
    return await reconcile_once()


@app.post("/api/moomoo/cancel")
async def moomoo_cancel(payload: dict):
    order_id = (payload or {}).get("order_id")
    if not order_id:
        return {"error": "missing_order_id"}
    from core.broker import get_broker, BrokerNotConnected
    try:
        broker = get_broker("moomoo")
        return await broker.cancel_order(order_id)
    except BrokerNotConnected as exc:
        return {"error": str(exc)}


# ── Moomoo 0DTE Bot ─────────────────────────────────────────────────────────

_zero_dte_bot = None

@app.post("/api/moomoo/0dte/start")
async def moomoo_0dte_start():
    global _zero_dte_bot
    from core.broker import get_broker, BrokerNotConnected
    from strategies.order_flow_0dte import ZeroDTEOrderFlowBot, ZeroDTEConfig

    try:
        broker = get_broker("moomoo")
    except BrokerNotConnected as exc:
        return {"error": str(exc)}

    if _zero_dte_bot is None:
        _zero_dte_bot = ZeroDTEOrderFlowBot(broker, ZeroDTEConfig())
    
    return await _zero_dte_bot.start()

@app.post("/api/moomoo/0dte/stop")
async def moomoo_0dte_stop():
    global _zero_dte_bot
    if _zero_dte_bot is None:
        return {"error": "Bot not initialized"}
    return await _zero_dte_bot.stop()

@app.get("/api/moomoo/0dte/status")
async def moomoo_0dte_status():
    global _zero_dte_bot
    if _zero_dte_bot is None:
        return {"running": False, "msg": "Not initialized"}
    return _zero_dte_bot.status()


async def _moomoo_execute_impl(req: MoomooOrderRequest) -> dict:
    """Live moomoo order path — mirrors _ibkr_execute_impl.

    Steps: idempotency → connect check → account snapshot → chain resolution
    → strike picking → sizing → risk gate → place_spread → journal → notify.
    """
    from core.broker import get_broker, BrokerNotConnected
    from core.journal import get_journal
    from core.risk import evaluate_pre_trade, AccountSnapshot
    from core.chain import pick_bull_call_strikes, pick_bear_put_strikes, pick_nearest_expiry
    from core.settings import SETTINGS
    from core.broker import LegSpec, SpreadRequest
    import uuid

    client_id = req.client_order_id or str(uuid.uuid4())

    # Idempotency guard — claim the key in an `events` row BEFORE the broker
    # call so that retries with the same key (timeout retries from the UI,
    # scanner re-ticks within the same bar, etc.) are deduped even when the
    # first attempt got rejected before journaling a position. The events
    # table is append-only; a successful position later writes the orders
    # row too, but the events row alone is enough to claim the key.
    journal = get_journal()
    if journal.get_order_by_idempotency(client_id):
        return {"error": "duplicate", "reason": "idempotency_key_exists", "client_order_id": client_id}
    # Indexed dedup against events.claim_key — catches failed prior attempts
    # that never wrote an orders row. UNIQUE index on claim_key turns a former
    # full-table LIKE-scan into an O(log n) equality probe.
    try:
        if journal.has_event_claim(client_id, kind="moomoo_execute_claim"):
            return {"error": "duplicate",
                    "reason": "idempotency_key_claimed",
                    "client_order_id": client_id}
        journal.log_event(
            "moomoo_execute_claim",
            subject=req.symbol,
            claim_key=client_id,
            payload={
                "client_order_id": client_id,
                "symbol": req.symbol,
                "direction": req.direction,
            },
        )
    except sqlite3.IntegrityError:
        # UNIQUE(claim_key) violation = parallel submitter beat us; defer.
        return {"error": "duplicate",
                "reason": "idempotency_key_claimed",
                "client_order_id": client_id}
    except Exception as exc:  # pragma: no cover - fail open, but log it
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "idempotency claim failed for %s: %s", client_id, exc,
        )

    # Broker check
    try:
        broker = get_broker("moomoo")
    except BrokerNotConnected as exc:
        return {"error": "broker_not_connected", "reason": str(exc)}

    if not broker.is_alive():
        return {"error": "broker_not_connected", "reason": "moomoo trader reports not alive"}

    # Account snapshot
    try:
        acct = await broker.get_account_summary()
    except Exception as exc:
        return {"error": "account_fetch_failed", "reason": str(exc)}

    snapshot = AccountSnapshot(
        equity=acct.get("equity", 0.0),
        buying_power=acct.get("buying_power", 0.0),
        excess_liquidity=acct.get("excess_liquidity", 0.0),
        daily_pnl=acct.get("realized_pnl", 0.0),
    )

    # Live price
    try:
        price_data = await broker.get_live_price(req.symbol)
        underlying = float(price_data.get("last", 0))
        if underlying <= 0:
            return {"error": "bad_price", "reason": "live price <= 0"}
    except Exception as exc:
        return {"error": "price_fetch_failed", "reason": str(exc)}

    # Option chain — pick the expiry closest to (today + target_dte) using
    # moomoo's authoritative expiry list (handles weekends + market holidays
    # automatically). Falls back to the calendar-walk hack if the SDK call
    # fails so we don't regress.
    from datetime import date as _date, timedelta
    chain_df = None
    expiry_str = None
    last_err = None
    target_anchor = _date.today() + timedelta(days=max(0, req.target_dte))
    expiries: list[str] = []
    try:
        expiries = await broker.list_option_expiries(req.symbol)
    except Exception as exc:
        last_err = f"list_option_expiries: {exc}"

    candidates: list[str] = []
    if expiries:
        # Score each available expiry by abs(days from target anchor); prefer
        # >= target_anchor when ties (don't go shorter-DTE than asked).
        scored = []
        for e in expiries:
            try:
                d = _date.fromisoformat(e)
            except ValueError:
                continue
            if d < _date.today():
                continue  # already-expired
            delta = (d - target_anchor).days
            scored.append((abs(delta), -delta, e))  # tie-break: prefer >= target
        scored.sort()
        candidates = [e for _, _, e in scored]
    else:
        # Fallback: original weekend-walk loop — still works, just doesn't
        # know about market holidays.
        for skip in range(0, 8):
            d = _date.today() + timedelta(days=req.target_dte + skip)
            if d.weekday() >= 5:
                continue
            candidates.append(d.strftime("%Y-%m-%d"))

    for candidate in candidates[:8]:  # cap at 8 chain fetches per call
        try:
            df = await broker.get_option_chain(req.symbol, candidate)
        except Exception as exc:
            last_err = str(exc)
            continue
        if df is not None and hasattr(df, "empty") and not df.empty:
            chain_df = df
            expiry_str = candidate
            break

    if chain_df is None or expiry_str is None:
        return {"error": "chain_fetch_failed",
                "reason": last_err or "no chain found near target_dte"}

    is_bear = "bear" in req.direction
    right = "P" if is_bear else "C"
    chain_sub = chain_df[chain_df["option_type"] == ("PUT" if is_bear else "CALL")]
    strike_grid = sorted(chain_sub["strike_price"].unique().tolist())
    leg_prices = {
        float(row["strike_price"]): (float(row["bid_price"]), float(row["ask_price"]))
        for _, row in chain_sub.iterrows()
    }

    # Strike selection — branch by direction. Bull-call lifts K_long ≈ ATM +
    # offset and shorts a higher strike; bear-put drops K_long ≈ ATM − offset
    # and shorts a lower strike. Same target_debit / max_width / OTM offset
    # semantics in both directions.
    if is_bear:
        spread = pick_bear_put_strikes(
            strike_grid=strike_grid,
            underlying=underlying,
            put_prices=leg_prices,
            target_debit=req.spread_cost_target,
            otm_offset=req.otm_offset,
        )
        picker_name = "pick_bear_put_strikes"
    else:
        spread = pick_bull_call_strikes(
            strike_grid=strike_grid,
            underlying=underlying,
            call_prices=leg_prices,
            target_debit=req.spread_cost_target,
            otm_offset=req.otm_offset,
        )
        picker_name = "pick_bull_call_strikes"
    if not spread:
        return {"error": "no_spread_found", "reason": f"{picker_name} returned None"}

    # Pre-trade chain quality check — reject degenerate quotes BEFORE order
    # submission.  Catches: zero bid/ask, crossed markets, blown-out spreads,
    # near-zero debit, and (where data is available) low volume/OI.
    from core.chain import validate_spread_quality
    quality_lookup = {
        float(row["strike_price"]): {
            "volume": int(row.get("volume", 0) or 0),
            "open_interest": int(row.get("open_interest", 0) or 0),
        }
        for _, row in chain_sub.iterrows()
    }
    # Tolerances default to the moomoo-friendly 0.25 (OpenD NBBO is wider than
    # IBKR's). Volume/OI gates default to 0 because moomoo chains often report
    # zero for both — relying on them silently rejects every signal.
    quality_ok, quality_reason = validate_spread_quality(
        spread,
        max_bid_ask_pct=float(getattr(req, "chain_max_bid_ask_pct", 0.25) or 0.25),
        min_mid=0.05,
        quality_lookup=quality_lookup,
        min_volume=int(getattr(req, "chain_min_volume", 0) or 0) or None,
        min_open_interest=int(getattr(req, "chain_min_open_interest", 0) or 0) or None,
    )
    if not quality_ok:
        journal.log_event("chain_quality_rejected", subject=req.symbol, payload={
            "reason": quality_reason, "broker": "moomoo",
            "K_long": spread.get("K_long"), "K_short": spread.get("K_short"),
            "long_bid": spread.get("long_bid"), "long_ask": spread.get("long_ask"),
            "short_bid": spread.get("short_bid"), "short_ask": spread.get("short_ask"),
        })
        return {
            "error": "chain_quality_rejected",
            "reason": quality_reason,
            "K_long": spread.get("K_long"), "K_short": spread.get("K_short"),
            "long_bid": spread.get("long_bid"), "long_ask": spread.get("long_ask"),
            "short_bid": spread.get("short_bid"), "short_ask": spread.get("short_ask"),
        }

    # Position sizing — debit-per-contract is in $ (mid-leg dollars * 100).
    from core.risk import size_position, RiskContext, RiskLimits
    debit_per_contract_dollars = float(spread["debit_per_contract"]) * 100.0
    margin_per_contract_dollars = debit_per_contract_dollars  # debit spread: max loss = debit
    if req.contracts > 0:
        contracts = req.contracts
    else:
        contracts = size_position(
            equity=snapshot.equity,
            debit_per_contract=debit_per_contract_dollars,
            margin_per_contract=margin_per_contract_dollars,
            mode=req.position_size_method or "fixed",
            fixed_contracts=1,
            risk_percent=req.risk_percent,
            max_allocation_cap=req.max_allocation_cap,
            excess_liquidity=snapshot.excess_liquidity,
        )

    if contracts <= 0:
        return {"error": "sizing_zero", "reason": "position sizer returned 0 contracts"}

    # Pre-trade risk gate.  Disable market-hours check for moomoo simulate
    # accounts (they accept orders 24/7) but keep all other gates active.
    # Allow callers to opt into bypassing the event-blackout gate for
    # smoke-testing only (req.bypass_event_blackout); never set in scanner.
    from core.calendar import load_event_calendar
    is_simulate = getattr(broker, "_trd_env_int", 1) == 0
    limits = RiskLimits.from_settings()
    from dataclasses import replace
    if is_simulate:
        limits = replace(limits, require_market_open=False)
    if req.bypass_event_blackout:
        limits = replace(limits, block_on_events=False)
        journal.log_event("bypass_event_blackout", subject=req.symbol, payload={
            "broker": "moomoo", "client_order_id": client_id,
            "warning": "test-only flag — production scanner must NOT set this",
        })
    ctx = RiskContext(
        account=snapshot,
        open_positions=len(journal.list_open()),
        today_realized_pnl=journal.today_realized_pnl(),
        today_trade_count=journal.today_entry_count(broker="moomoo"),
        debit_per_contract=debit_per_contract_dollars,
        margin_per_contract=margin_per_contract_dollars,
        contracts=contracts,
        target_dte=req.target_dte,
        limits=limits,
        events=load_event_calendar(),
    )
    decision = evaluate_pre_trade(ctx)
    if not decision.allowed:
        journal.log_event("risk_rejected", subject=req.symbol, payload={
            "reason": decision.reason, "broker": "moomoo", **decision.details,
        })
        return {"error": "risk_gate_blocked", "reason": decision.reason, **decision.details}

    # Build spread request — expiry_str ('YYYY-MM-DD') was set by the chain
    # picker above; the moomoo broker code wants the dash-less form.
    expiry_ymd = expiry_str.replace("-", "")
    spread_req = SpreadRequest(
        symbol=req.symbol,
        long_leg=LegSpec(expiry=expiry_ymd, strike=spread["K_long"], right=right,
                         price=round(spread["long_ask"], 2)),
        short_leg=LegSpec(expiry=expiry_ymd, strike=spread["K_short"], right=right,
                          price=round(spread["short_bid"], 2)),
        qty=contracts,
        net_debit_limit=round(spread["debit_per_contract"] * 100 * contracts, 2),
        position_id=client_id,
        client_order_id=client_id,
        max_bid_ask_pct=float(getattr(req, "chain_max_bid_ask_pct", 0.25) or 0.25),
    )

    # Pre-journal an in-flight ('pending') position so a process crash between
    # leg1 fill and the post-success journal write doesn't strand the spread
    # as an unjournaled orphan at the broker. The reconciler treats 'pending'
    # rows as covering the legs, so it won't manufacture single_leg_orphan
    # entries. On success we update to state='open' below; on failure we mark
    # it closed/cancelled.
    from core.journal import Position as _Position
    _now_pre_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _pending_legs = [
        {"expiry": expiry_ymd, "strike": spread["K_long"], "right": right,
         "side": "long", "qty": contracts},
        {"expiry": expiry_ymd, "strike": spread["K_short"], "right": right,
         "side": "short", "qty": contracts},
    ]
    _pre_journaled = False
    try:
        journal.open_position(_Position(
            id=client_id,
            symbol=req.symbol,
            topology="vertical_spread",
            direction=req.direction,
            contracts=contracts,
            entry_cost=spread["debit_per_contract"] * 100 * contracts,
            entry_time=_now_pre_iso,
            expiry=expiry_ymd,
            state="pending",
            legs=tuple(_pending_legs),
            broker="moomoo",
            high_water_mark=spread["debit_per_contract"] * 100,
            meta={
                "broker": "moomoo",
                "legs": _pending_legs,
                "stop_loss_pct": req.stop_loss_pct,
                "take_profit_pct": req.take_profit_pct,
                "trailing_stop_pct": req.trailing_stop_pct,
                "idempotency_key": client_id,
                "preset_name": getattr(req, "preset_name", None),
                "in_flight": True,
            },
        ))
        _pre_journaled = True
    except Exception as exc:  # noqa: BLE001
        # Most likely cause: duplicate id from a retry. That's fine; the
        # existing row already covers the legs. Continue without raising.
        logger.warning("pre-journal pending position failed (id=%s): %s",
                       client_id, exc)

    # Execute
    try:
        order_result = await broker.place_spread(spread_req)
    except Exception as exc:
        journal.log_event("order_failed", subject=req.symbol, payload={
            "broker": "moomoo", "client_order_id": client_id, "error": str(exc),
            "K_long": spread["K_long"], "K_short": spread["K_short"],
        })
        if _pre_journaled:
            try:
                journal.close_position(
                    client_id, exit_cost=0.0,
                    reason="order_failed", realized_pnl=0.0,
                )
            except Exception:  # noqa: BLE001
                pass
        return {"error": "order_failed", "reason": str(exc)}

    if order_result.get("status") != "ok":
        # Log the broker's rejection (e.g., long_leg_timeout) so the audit
        # trail shows what was attempted even when no position was opened.
        evt_kind = "broken_spread" if order_result.get("status") == "broken_spread" else "order_rejected"
        journal.log_event(evt_kind, subject=req.symbol, payload={
            "broker": "moomoo", "client_order_id": client_id,
            "K_long": spread["K_long"], "K_short": spread["K_short"],
            **order_result,
        })
        # Tear down the pre-journaled pending row. The leg1-orphan handler
        # below will record its own orphan position if needed.
        if _pre_journaled:
            try:
                journal.close_position(
                    client_id, exit_cost=0.0,
                    reason=f"order_{order_result.get('status', 'rejected')}",
                    realized_pnl=0.0,
                )
            except Exception:  # noqa: BLE001
                pass
        # If leg1 actually filled and the flatten didn't take, we have an
        # orphan long at the broker. Record it so the monitor can MTM / exit
        # it instead of letting it bleed. Skip recording when the flatten
        # succeeded (long is already closed) or when leg1 itself never filled.
        flatten_ok = (
            order_result.get("flatten_order_id") is not None
            and order_result.get("reason") != "broken_spread_unflattened"
        )
        if (
            order_result.get("leg1_order_id")
            and order_result.get("reason") not in ("long_leg_timeout", None)
            and not flatten_ok
        ):
            from core.journal import Position
            orphan_legs = [
                {"expiry": expiry_ymd, "strike": spread["K_long"], "right": right,
                 "side": "long", "qty": contracts},
            ]
            try:
                now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
                orphan_id = journal.open_position(Position(
                    id=f"{client_id}:orphan",
                    symbol=req.symbol,
                    topology="single_leg_orphan",
                    direction=req.direction,
                    contracts=contracts,
                    entry_cost=spread["long_bid"] * 100,  # ~mid; refined later
                    entry_time=now_iso,
                    expiry=expiry_ymd,
                    state="open",
                    legs=tuple(orphan_legs),
                    broker="moomoo",
                    high_water_mark=spread["long_bid"] * 100,
                    meta={
                        "broker": "moomoo",
                        "orphan": True,
                        "broker_order_id": order_result.get("leg1_order_id", ""),
                        "stop_loss_pct": req.stop_loss_pct,
                        "take_profit_pct": req.take_profit_pct,
                        "trailing_stop_pct": req.trailing_stop_pct,
                        "idempotency_key": client_id + ":orphan",
                        **order_result,
                    },
                ))
                journal.log_event("orphan_long_recorded", subject=req.symbol, payload={
                    "broker": "moomoo", "position_id": orphan_id,
                    "K_long": spread["K_long"], "leg1_order_id": order_result.get("leg1_order_id"),
                    "reason": order_result.get("reason"),
                })
            except Exception as exc:
                journal.log_event("orphan_journal_failed", subject=req.symbol, payload={
                    "broker": "moomoo", "error": str(exc), **order_result,
                })
        return {"error": "order_rejected", **order_result}

    # Journal
    from core.journal import Order, Position
    import uuid as _uuid

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    legs = [
        {"expiry": expiry_ymd, "strike": spread["K_long"], "right": right,
         "side": "long", "qty": contracts},
        {"expiry": expiry_ymd, "strike": spread["K_short"], "right": right,
         "side": "short", "qty": contracts},
    ]
    # Use real fill prices from the broker when available (live trading
    # slippage is real and the quoted long_ask/short_bid is what we ASKED,
    # not what we got). Falls back to quoted prices when the broker didn't
    # return avg_fill_price (paper/dryrun or older code paths).
    _leg1_fp = float(order_result.get("leg1_avg_fill_price") or 0.0) or float(spread["long_ask"])
    _leg2_fp = float(order_result.get("leg2_avg_fill_price") or 0.0) or float(spread["short_bid"])
    _real_debit_per_contract = max(_leg1_fp - _leg2_fp, 0.0)
    _real_entry_cost = _real_debit_per_contract * 100 * contracts

    # Promote the pre-journaled pending row to 'open'. If pre-journal failed
    # (rare — typically a retry collision), fall back to a fresh insert.
    if _pre_journaled:
        journal.update_position(
            client_id,
            state="open",
            entry_cost=_real_entry_cost,
            high_water_mark=_real_debit_per_contract * 100,
        )
        pos_id = client_id
    else:
        pos_id = journal.open_position(Position(
            id=client_id,
            symbol=req.symbol,
            topology="vertical_spread",
            direction=req.direction,
            contracts=contracts,
            entry_cost=spread["debit_per_contract"] * 100 * contracts,
            entry_time=now_iso,
            expiry=expiry_ymd,
            state="open",
            legs=tuple(legs),
            broker="moomoo",
            high_water_mark=spread["debit_per_contract"] * 100,
            meta={
                "broker": "moomoo",
                "legs": legs,
                "broker_order_id": order_result.get("leg1_order_id", ""),
                "stop_loss_pct": req.stop_loss_pct,
                "take_profit_pct": req.take_profit_pct,
                "trailing_stop_pct": req.trailing_stop_pct,
                "idempotency_key": client_id,
                "preset_name": getattr(req, "preset_name", None),
                **order_result,
            },
        ))
    long_order_id = str(_uuid.uuid4())
    short_order_id = str(_uuid.uuid4())
    journal.record_order(Order(
        id=long_order_id,
        position_id=pos_id,
        broker="moomoo",
        broker_order_id=str(order_result.get("leg1_order_id") or ""),
        side="BUY",
        limit_price=round(spread["long_ask"], 2),
        status="filled",
        submitted_at=now_iso,
        filled_at=now_iso,
        fill_price=round(_leg1_fp, 2),
        kind="entry",
        idempotency_key=client_id,
    ))
    journal.record_order(Order(
        id=short_order_id,
        position_id=pos_id,
        broker="moomoo",
        broker_order_id=str(order_result.get("leg2_order_id") or ""),
        side="SELL",
        limit_price=round(spread["short_bid"], 2),
        status="filled",
        submitted_at=now_iso,
        filled_at=now_iso,
        fill_price=round(_leg2_fp, 2),
        kind="entry",
        idempotency_key=f"{client_id}:short",
    ))

    # Persist a Fill row per leg so the fills table is no longer empty for
    # moomoo trades. Before this, the success path wrote Order rows with
    # status='filled' directly, bypassing fill_watcher's record_fill call,
    # so any report/PnL that joins on fills was silently empty for moomoo.
    from core.journal import Fill as _Fill
    for _oid, _qty, _price in (
        (long_order_id, int(order_result.get("leg1_filled_qty") or contracts),
         round(_leg1_fp, 2)),
        (short_order_id, int(order_result.get("leg2_filled_qty") or contracts),
         round(_leg2_fp, 2)),
    ):
        try:
            journal.record_fill(_Fill(
                id=None, order_id=_oid, qty=_qty, price=_price,
                time=now_iso, exec_id=None, commission=0.0,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("record_fill failed for order %s: %s", _oid, exc)

    journal.log_event("entry_submitted", subject=pos_id, payload={
        "symbol": req.symbol,
        "broker": "moomoo",
        "contracts": contracts,
        "K_long": spread["K_long"],
        "K_short": spread["K_short"],
        "leg1_order_id": order_result.get("leg1_order_id"),
        "leg2_order_id": order_result.get("leg2_order_id"),
        "entry_order_ids": [long_order_id, short_order_id],
    })

    # Notify
    try:
        from core.notifier import send_trade_alert
        send_trade_alert(
            symbol=req.symbol,
            direction=req.direction,
            contracts=contracts,
            entry_cost=spread["debit_per_contract"] * 100,
            broker="moomoo",
        )
    except Exception:
        pass

    return {
        "success": True,
        "position_id": pos_id,
        "contracts": contracts,
        "K_long": spread["K_long"],
        "K_short": spread["K_short"],
        "debit_per_contract": spread["debit_per_contract"],
        "leg1_order_id": order_result.get("leg1_order_id"),
        "leg2_order_id": order_result.get("leg2_order_id"),
        "client_order_id": client_id,
    }


# ── Journal API endpoints ─────────────────────────────────────────────────

@app.get("/api/journal/positions")
def journal_positions(state: str = "open"):
    """List positions. state=open returns pending/open/closing; state=all returns everything."""
    from core.journal import get_journal
    journal = get_journal()
    if state == "all":
        return {"positions": [_pos_to_dict(p) for p in journal.list_all()]}
    return {"positions": [_pos_to_dict(p) for p in journal.list_open()]}


@app.get("/api/journal/orders")
def journal_orders(broker: Optional[str] = None, limit: int = 50):
    """Recent orders from the broker journal.

    Used by the per-broker view's Order Log so scanner-initiated orders
    (auto_execute presets) and historical orders show up even when the
    in-memory session log is empty. Optionally filter by ``broker``
    (``moomoo`` / ``ibkr`` / ``paper``); ``limit`` caps the result.

    Also includes recent ``order_rejected`` events for the same broker so
    chain-quality / preflight rejections are visible to the operator.
    """
    from core.journal import get_journal
    journal = get_journal()
    orders = journal.list_recent_orders(broker=broker, limit=int(limit))

    # Hydrate parent-position strikes onto each order row so the UI can
    # display K_long/K_short for journal-backed orders (Order entities only
    # carry the leg, not the spread). Single SELECT per unique position_id.
    pos_strikes: dict[str, dict] = {}
    for o in orders:
        if o.position_id and o.position_id not in pos_strikes:
            try:
                pos = journal.get_position(o.position_id)
            except Exception:  # noqa: BLE001
                pos = None
            if pos and pos.legs:
                longs = [l for l in pos.legs if l.get("side") == "long"]
                shorts = [l for l in pos.legs if l.get("side") == "short"]
                pos_strikes[o.position_id] = {
                    "symbol": pos.symbol,
                    "K_long": longs[0].get("strike") if longs else None,
                    "K_short": shorts[0].get("strike") if shorts else None,
                }

    def _order_to_dict(o) -> dict:
        ps = pos_strikes.get(o.position_id or "", {})
        return {
            "id": o.id,
            "position_id": o.position_id,
            "broker": o.broker,
            "broker_order_id": o.broker_order_id,
            "side": o.side,
            "limit_price": o.limit_price,
            "status": o.status,
            "submitted_at": o.submitted_at,
            "filled_at": o.filled_at,
            "fill_price": o.fill_price,
            "commission": o.commission,
            "symbol": ps.get("symbol"),
            "K_long": ps.get("K_long"),
            "K_short": ps.get("K_short"),
            "kind": o.kind,
            "idempotency_key": o.idempotency_key,
        }

    rejections: list[dict] = []
    try:
        # Surface every flavor of "your signal didn't make it to the broker"
        # so the Order Log explains silent gaps. Before this, ``risk_rejected``
        # (e.g. max_concurrent_positions) didn't appear in the UI even though
        # it accounted for ~99% of today's blocked signals.
        rejection_kinds = {
            "order_rejected", "chain_quality_rejected", "risk_rejected",
            "broken_spread", "order_failed",
        }
        for ev in journal.recent_events(limit=int(limit) * 5):
            if ev.get("kind") not in rejection_kinds:
                continue
            payload = ev.get("payload") or {}
            if broker and payload.get("broker") != broker:
                continue
            rejections.append({
                "time": ev.get("time"),
                "kind": ev.get("kind"),
                "subject": ev.get("subject"),
                "reason": payload.get("reason"),
                "error": payload.get("error"),
                "client_order_id": payload.get("client_order_id"),
                "K_long": payload.get("K_long"),
                "K_short": payload.get("K_short"),
            })
            if len(rejections) >= int(limit):
                break
    except Exception:
        rejections = []

    return {
        "orders": [_order_to_dict(o) for o in orders],
        "rejections": rejections,
    }


@app.get("/api/journal/daily_pnl")
def journal_daily_pnl(days: int = 30):
    from core.journal import get_journal
    journal = get_journal()
    return {
        "today_pnl": journal.today_realized_pnl(),
        "today_trades": journal.today_trade_count(),
        "history": journal.history_pnl(days),
    }


@app.get("/api/journal/events")
def journal_events(limit: int = 50):
    from core.journal import get_journal
    return _safe_json({"events": get_journal().recent_events(limit)})


@app.get("/api/journal/reconciliation")
def journal_reconciliation(date: Optional[str] = None):
    """EOD commission/slippage reconciliation report.

    Optional query param ``?date=YYYY-MM-DD`` selects the day; defaults to today.
    """
    from core.journal import get_journal
    return _safe_json(get_journal().daily_reconciliation_report(date))


class PaperValidationRequest(BaseModel):
    """Compares live (paper) trade stats against backtest expectations.

    Implements skill Step 10: "run preset on moomoo paper for one full
    trading week ... compare live-paper stats to the backtest". The
    caller supplies the backtest stats from /api/backtest (or any prior
    run); this endpoint reads the journal for live positions opened
    under the preset and computes deltas.
    """
    preset_name: str
    days_back: int = 7
    expected_win_rate_pct: float = 0.0     # backtest WR%, e.g. 75 for rsi2
    expected_trades_per_week: float = 0.0  # 3y / 156 weeks for that strategy
    expected_max_drawdown_pct: float = 0.0  # backtest max DD %, negative number


@app.post("/api/paper_validation")
def paper_validation(req: PaperValidationRequest):
    """Skill Step 10 paper-validation gate.

    Returns a verdict {pass | warn | fail} plus per-criterion deltas:
      - trade-count vs expected cadence (warn if < 25% of expected)
      - win-rate within 10 percentage points of backtest WR
      - any single losing trade larger than backtest max drawdown ($)

    Read-only — does not mutate the journal or block any flow. Surfaces
    the comparison the skill asks the human to do by hand.
    """
    from datetime import datetime, timedelta, timezone
    from core.journal import get_journal
    from core.presets import PresetStore
    from dataclasses import replace

    store = PresetStore()
    preset = store.get(req.preset_name)
    if preset is None:
        return {"error": "preset_not_found", "preset_name": req.preset_name}

    journal = get_journal()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, req.days_back))).isoformat()
    positions = [p for p in journal.list_all(limit=1000)
                 if p.entry_time >= cutoff
                 and (p.meta or {}).get("preset_name") == req.preset_name
                 and p.state in ("closed", "open")]

    closed = [p for p in positions if p.state == "closed" and p.realized_pnl is not None]
    wins = [p for p in closed if (p.realized_pnl or 0) > 0]
    losses = [p for p in closed if (p.realized_pnl or 0) <= 0]
    n_closed = len(closed)
    live_wr = (len(wins) / n_closed * 100.0) if n_closed > 0 else 0.0
    biggest_loss_dollars = min((p.realized_pnl or 0) for p in closed) if closed else 0.0
    weeks_observed = max(1.0, req.days_back / 7.0)
    live_trades_per_week = len(positions) / weeks_observed

    # Equity-based DD threshold: backtest max DD% × preset capital target
    # (rough — moomoo doesn't expose backtest capital, so use 10k baseline).
    expected_max_loss_dollars = (req.expected_max_drawdown_pct / 100.0) * 10000.0
    findings = []
    verdict = "pass"

    # 1. trade count cadence
    if req.expected_trades_per_week > 0:
        ratio = live_trades_per_week / req.expected_trades_per_week
        if ratio < 0.25:
            findings.append({
                "criterion": "trade_count",
                "severity": "warn",
                "message": f"live cadence {live_trades_per_week:.2f}/wk is {ratio:.0%} of backtest {req.expected_trades_per_week:.2f}/wk",
            })
            verdict = "warn" if verdict == "pass" else verdict
        elif ratio > 4.0:
            findings.append({
                "criterion": "trade_count",
                "severity": "warn",
                "message": f"live cadence {live_trades_per_week:.2f}/wk is {ratio:.0%} of backtest — overtrading?",
            })
            verdict = "warn" if verdict == "pass" else verdict

    # 2. win-rate band (±10pp per skill)
    if req.expected_win_rate_pct > 0 and n_closed >= 3:
        delta = live_wr - req.expected_win_rate_pct
        if abs(delta) > 10:
            findings.append({
                "criterion": "win_rate",
                "severity": "fail" if abs(delta) > 20 else "warn",
                "message": f"live WR {live_wr:.1f}% deviates {delta:+.1f}pp from backtest {req.expected_win_rate_pct:.1f}%",
            })
            verdict = "fail" if abs(delta) > 20 else ("warn" if verdict == "pass" else verdict)

    # 3. single-trade loss vs backtest max DD$
    if expected_max_loss_dollars < 0 and biggest_loss_dollars < expected_max_loss_dollars:
        findings.append({
            "criterion": "max_single_loss",
            "severity": "fail",
            "message": f"a single loss of ${biggest_loss_dollars:.2f} exceeds backtest max DD ${expected_max_loss_dollars:.2f}",
        })
        verdict = "fail"

    return _safe_json({
        "preset_name": req.preset_name,
        "verdict": verdict,
        "days_observed": req.days_back,
        "live": {
            "positions_total": len(positions),
            "positions_closed": n_closed,
            "win_rate_pct": round(live_wr, 2),
            "trades_per_week": round(live_trades_per_week, 2),
            "biggest_loss_dollars": round(biggest_loss_dollars, 2),
            "wins": len(wins),
            "losses": len(losses),
        },
        "expected": {
            "win_rate_pct": req.expected_win_rate_pct,
            "trades_per_week": req.expected_trades_per_week,
            "max_drawdown_pct": req.expected_max_drawdown_pct,
        },
        "findings": findings,
    })


class PaperTrialStartRequest(BaseModel):
    preset_name: str
    expected_win_rate_pct: float = 0.0
    expected_trades_per_week: float = 0.0
    expected_max_drawdown_pct: float = 0.0
    min_trades: int = 20
    min_days: int = 7
    max_open_positions: int = 1
    allow_overlap: bool = True
    fast_fail_dd_multiplier: float = 2.0
    notes: str = ""


@app.post("/api/paper_trials/start")
def paper_trial_start(req: PaperTrialStartRequest):
    """Begin a paper-trading trial for a preset.

    The preset must exist and its strategy must not be VETTING_RESULT=rejected
    (the PresetStore guard already enforces this at save time). The trial
    row drives both the per-fire concurrency gate and the daily evaluator.
    """
    from datetime import datetime, timezone
    from core.paper_gate import (
        get_paper_gate_store, PaperTrial, STATUS_TRIALING,
    )
    from core.presets import PresetStore
    pstore = PresetStore()
    preset = pstore.get(req.preset_name)
    if preset is None:
        return {"error": "preset_not_found", "preset_name": req.preset_name}
    store = get_paper_gate_store()
    trial = PaperTrial(
        preset_name=req.preset_name,
        started_at=datetime.now(timezone.utc).isoformat(),
        status=STATUS_TRIALING,
        expected_win_rate_pct=req.expected_win_rate_pct,
        expected_trades_per_week=req.expected_trades_per_week,
        expected_max_drawdown_pct=req.expected_max_drawdown_pct,
        min_trades=req.min_trades,
        min_days=req.min_days,
        notes=req.notes,
        max_open_positions=req.max_open_positions,
        allow_overlap=req.allow_overlap,
        fast_fail_dd_multiplier=req.fast_fail_dd_multiplier,
    )
    store.upsert(trial)
    # Ensure exactly one moomoo preset is armed. Starting a paper trial should
    # not leave older engine-test or trial presets eligible to auto-fire after
    # restart.
    if preset.broker == "moomoo":
        try:
            for other in pstore.list():
                if other.broker == "moomoo" and other.name != preset.name and other.auto_execute:
                    pstore.save(replace(other, auto_execute=False))
            if not preset.auto_execute:
                pstore.save(replace(preset, auto_execute=True))
        except Exception as exc:
            return {
                "error": "preset_auto_execute_update_failed",
                "preset_name": req.preset_name,
                "reason": str(exc),
            }
    elif not preset.auto_execute:
        try:
            pstore.save(replace(preset, auto_execute=True))
        except Exception as exc:
            return {
                "error": "preset_auto_execute_update_failed",
                "preset_name": req.preset_name,
                "reason": str(exc),
            }
    return _safe_json({"trial": asdict(trial)})


@app.get("/api/paper_trials")
def paper_trials_list(status: Optional[str] = None):
    """List trials with live deltas folded in.

    Each entry combines the persisted trial row with a fresh evaluation
    (live WR / cadence / max-loss vs expected). Use ``?status=trialing``
    to filter to active rows.
    """
    from core.paper_gate import get_paper_gate_store, evaluate_trial
    store = get_paper_gate_store()
    trials = store.list(status=status) if status else store.list()
    out = []
    for trial in trials:
        try:
            ev = evaluate_trial(trial)
        except Exception as exc:
            ev = {"error": str(exc)}
        out.append({"trial": asdict(trial), "evaluation": ev})
    return _safe_json({"trials": out})


@app.post("/api/paper_trials/evaluate_now")
def paper_trial_evaluate_now():
    """Manually trigger the daily evaluator (also runs on cron 16:30 ET)."""
    from core.paper_gate import run_daily_evaluator
    return _safe_json(run_daily_evaluator())


@app.post("/api/paper_trials/{preset_name}/stop")
def paper_trial_stop(preset_name: str):
    """Manually demote a trial — disables auto_execute on the preset."""
    from core.paper_gate import get_paper_gate_store, STATUS_DEMOTED
    from core.presets import PresetStore
    store = get_paper_gate_store()
    ok = store.set_status(preset_name, STATUS_DEMOTED, notes="manual_stop")
    if not ok:
        return {"error": "trial_not_found", "preset_name": preset_name}
    try:
        pstore = PresetStore()
        preset = pstore.get(preset_name)
        if preset and preset.auto_execute:
            from dataclasses import replace
            pstore.save(replace(preset, auto_execute=False))
    except Exception:
        pass
    return {"ok": True, "preset_name": preset_name, "status": STATUS_DEMOTED}


@app.post("/api/paper_trials/{preset_name}/promote")
def paper_trial_promote(preset_name: str):
    """Mark a passed trial as promoted.

    Does NOT move funds or change broker accounts — promotion is an
    audit-trail flag. The caller is expected to clone the preset to a
    real-money config separately via the normal preset save flow.
    """
    from core.paper_gate import get_paper_gate_store, STATUS_PASSED, STATUS_PROMOTED
    store = get_paper_gate_store()
    trial = store.get(preset_name)
    if trial is None:
        return {"error": "trial_not_found", "preset_name": preset_name}
    if trial.status != STATUS_PASSED:
        return {
            "error": "trial_not_passed",
            "current_status": trial.status,
            "hint": "trial must be in 'passed' state — wait for evaluator or call /evaluate_now",
        }
    store.set_status(preset_name, STATUS_PROMOTED, notes="manual_promote")
    return {"ok": True, "preset_name": preset_name, "status": STATUS_PROMOTED}


@app.delete("/api/paper_trials/{preset_name}")
def paper_trial_delete(preset_name: str):
    """Remove the trial row entirely (audit-trail erase)."""
    from core.paper_gate import get_paper_gate_store
    ok = get_paper_gate_store().delete(preset_name)
    return {"ok": ok, "preset_name": preset_name}


@app.post("/api/orders/cleanup-stale")
def cleanup_stale_orders(max_age_hours: int = 24):
    """Mark stale orders + their positions as cancelled.

    Cleans:
    - orders stuck in submitted/submitting/pending older than max_age_hours → cancelled
    - positions in 'pending' state with no live order older than max_age_hours → closed

    Useful when IBKR/moomoo was disconnected mid-fill, leaving rows that block
    fresh trades (fill_watcher keeps polling, max_concurrent_positions blocks
    new entries).
    """
    from core.journal import get_journal
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
    j = get_journal()
    with j._lock:
        cur = j._conn.cursor()

        # Stale orders
        cur.execute(
            "SELECT id, broker, broker_order_id, position_id, submitted_at FROM orders "
            "WHERE status IN ('submitted','submitting','pending') AND submitted_at < ?",
            (cutoff,),
        )
        stale_orders = [dict(r) for r in cur.fetchall()]

        # Stale pending/closing positions
        cur.execute(
            "SELECT id, symbol, broker, state, entry_time FROM positions "
            "WHERE state IN ('pending','closing') AND entry_time < ?",
            (cutoff,),
        )
        stale_positions = [dict(r) for r in cur.fetchall()]

        if stale_orders or stale_positions:
            j._conn.execute("BEGIN")
            try:
                for r in stale_orders:
                    j._conn.execute(
                        "UPDATE orders SET status='cancelled' WHERE id=?",
                        (r["id"],),
                    )
                for r in stale_positions:
                    j._conn.execute(
                        "UPDATE positions SET state='closed', "
                        "exit_reason=COALESCE(exit_reason,'stale_cleanup'), "
                        "exit_time=COALESCE(exit_time, ?) "
                        "WHERE id=?",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"), r["id"]),
                    )
                j._conn.execute("COMMIT")
            except Exception:
                j._conn.execute("ROLLBACK")
                raise
    return {
        "cleaned_orders": len(stale_orders),
        "cleaned_positions": len(stale_positions),
        "orders": stale_orders,
        "positions": stale_positions,
        "cutoff": cutoff,
    }


def _pos_to_dict(p) -> dict:
    return {
        "id": p.id, "symbol": p.symbol, "topology": p.topology,
        "direction": p.direction, "contracts": p.contracts,
        "entry_cost": p.entry_cost, "entry_time": p.entry_time,
        "expiry": p.expiry, "state": p.state,
        "exit_cost": p.exit_cost, "exit_time": p.exit_time,
        "exit_reason": p.exit_reason, "realized_pnl": p.realized_pnl,
        "high_water_mark": p.high_water_mark, "broker": p.broker,
        "legs": list(p.legs), "meta": p.meta,
    }


# ── Monitor & Fill-Watcher scheduler registration ────────────────────────

# I10: track last successful monitor tick for heartbeat staleness alerts.
_last_monitor_tick_iso: Optional[str] = None


def _has_ibkr_work() -> bool:
    """True if the journal has any IBKR position open or order pending.

    Used by monitor/fill_watcher schedulers to skip the IBKR connection
    attempt when only moomoo work exists — avoids flooding the log with
    'Connection refused' errors when TWS isn't running.
    """
    try:
        from core.journal import get_journal
        j = get_journal()
        # Check for any open IBKR position or pending IBKR order.
        with j._lock:
            cur = j._conn.cursor()
            cur.execute(
                "SELECT 1 FROM positions WHERE state='open' AND broker='ibkr' LIMIT 1"
            )
            if cur.fetchone():
                return True
            cur.execute(
                "SELECT 1 FROM orders WHERE broker='ibkr' "
                "AND status IN ('submitted','submitting','pending') LIMIT 1"
            )
            return cur.fetchone() is not None
    except Exception:
        # If we can't query, fall back to "yes, try" so we don't lose orders.
        return True


def _run_monitor_tick():
    """Sync wrapper that drives the async monitor tick from APScheduler.

    Builds the ``defaults`` dict explicitly — including the strategy-aware
    ``bars_fetcher`` — so the monitor's ``_resolve_strategy_exit`` actually
    has bars to evaluate. Without this, only stop/profit/trailing/expiry
    gates fire; the strategy's own ``check_exit`` (e.g. consecutive-day
    reversal) is dead code.
    """
    import asyncio as _aio
    from core.monitor import tick
    from core.settings import SETTINGS
    from core.leader import is_leader

    # I6: no-op if another instance holds the leader lock.
    if not is_leader():
        return

    # Skip IBKR connection entirely when no IBKR work is queued.  Monitor
    # still runs (so moomoo positions are evaluated) but with a None trader.
    skip_ibkr = not _has_ibkr_work()

    async def _factory():
        if skip_ibkr:
            return None
        creds = SETTINGS.ibkr.as_dict()
        trader, _ = await get_ib_connection(creds)
        return trader

    defaults = {
        "stop_loss_pct": SETTINGS.risk.default_stop_loss_pct,
        "take_profit_pct": SETTINGS.risk.default_take_profit_pct,
        "trailing_stop_pct": SETTINGS.risk.default_trailing_stop_pct,
        "dte_exit_at": 0,
        "haircut_pct": SETTINGS.risk.limit_price_haircut,
        # Wire the strategy-aware bars fetcher so check_exit() has data.
        "bars_fetcher": _preset_bars_fetcher,
    }

    global _last_monitor_tick_iso
    loop = _MAIN_LOOP
    if loop is None or not loop.is_running():
        return  # app not fully started yet
    try:
        future = _aio.run_coroutine_threadsafe(
            tick(_factory, defaults=defaults), loop,
        )
        future.result(timeout=55)  # block until done; propagates exceptions
        _last_monitor_tick_iso = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("monitor tick failed: %s", e)


def _run_moomoo_reconcile():
    """Sync wrapper that drives the moomoo orphan-leg reconciler.

    Detects mismatches between OpenD's position_list_query and our journal:
    - Orphan broker legs (broker has, journal doesn't) → CRITICAL alert
    - Phantom journal positions (journal has, broker doesn't) → auto-close
    """
    import asyncio as _aio
    from core.leader import is_leader
    if not is_leader():
        return
    # No moomoo work? skip.
    try:
        from core.broker import _registry
        if "moomoo" not in _registry:
            return
    except Exception:  # noqa: BLE001
        return

    async def _go():
        from core.moomoo_reconciler import reconcile_once
        return await reconcile_once()

    loop = _MAIN_LOOP
    if loop is None or not loop.is_running():
        return
    try:
        future = _aio.run_coroutine_threadsafe(_go(), loop)
        future.result(timeout=30)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("moomoo reconcile failed: %s", e)


def _run_fill_reconcile():
    """Sync wrapper that drives the async fill reconciler from APScheduler."""
    import asyncio as _aio
    from core.fill_watcher import reconcile_once
    from core.settings import SETTINGS
    from core.leader import is_leader

    # I6: no-op if another instance holds the leader lock.
    if not is_leader():
        return

    # Skip entirely when no broker reconciliation work is pending.
    # fill_watcher routes per-order to the right broker registry so a None
    # IBKR trader is fine — moomoo orders still reconcile via register_broker.
    if not _has_ibkr_work():
        # Still call reconcile_once with a None trader so any moomoo orders
        # in the queue get reconciled via the broker registry path.
        async def _go():
            await reconcile_once(None, timeout_seconds=SETTINGS.risk.fill_timeout_seconds)
        loop = _MAIN_LOOP
        if loop is None or not loop.is_running():
            return
        try:
            future = _aio.run_coroutine_threadsafe(_go(), loop)
            future.result(timeout=55)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("fill reconcile (moomoo-only) failed: %s", e)
        return

    async def _go():
        creds = SETTINGS.ibkr.as_dict()
        trader, _ = await get_ib_connection(creds)
        if trader:
            await reconcile_once(trader, timeout_seconds=SETTINGS.risk.fill_timeout_seconds)

    loop = _MAIN_LOOP
    if loop is None or not loop.is_running():
        return  # app not fully started yet
    try:
        future = _aio.run_coroutine_threadsafe(_go(), loop)
        future.result(timeout=55)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("fill reconcile failed: %s", e)


@app.post("/api/monitor/start")
def start_monitor(interval: int = 15):
    """Register the monitor loop + fill watcher as scheduler jobs.

    I6: acquires an advisory file lock so only one server instance runs
    the monitor tick. If another instance already holds the lock, returns
    ``{"status": "not_leader", "holder": {...}}`` and registers no jobs.
    """
    from core.leader import try_acquire_leadership, _peek_lock_file
    from pathlib import Path

    secs = max(interval, 5)
    lock_path = "data/monitor.lock"

    acquired = try_acquire_leadership(lock_path)
    if not acquired:
        holder = _peek_lock_file(Path(lock_path))
        log_event(_startup_log, "monitor_start_rejected_not_leader",
                  level=_logging.WARNING, holder=holder)
        return {
            "status": "not_leader",
            "holder": holder,
            "message": "Another instance holds the monitor lock; "
                       "this server will not register monitor jobs.",
        }

    # Remove existing monitor jobs if re-registering
    for job in scheduler.get_jobs():
        if job.id in ("live_monitor", "fill_watcher", "moomoo_reconcile"):
            job.remove()

    scheduler.add_job(_run_monitor_tick, "interval", seconds=secs,
                      id="live_monitor", replace_existing=True)
    scheduler.add_job(_run_fill_reconcile, "interval", seconds=secs,
                      id="fill_watcher", replace_existing=True)
    # Moomoo orphan-leg reconciler — runs at 4× monitor interval (e.g. every
    # 60s if monitor is 15s) since it's read-only and doesn't need to be
    # synchronous with fill polling.
    scheduler.add_job(_run_moomoo_reconcile, "interval", seconds=max(secs * 4, 60),
                      id="moomoo_reconcile", replace_existing=True)

    # I2: daily digest at 16:05 ET (after market close) — best-effort, no-op
    # when NOTIFY_WEBHOOK_URL is not configured.
    for job in scheduler.get_jobs():
        if job.id == "daily_digest":
            job.remove()
    scheduler.add_job(
        _run_daily_digest,
        "cron",
        hour=21,       # 16:05 ET = 21:05 UTC (no DST correction; adjust via env)
        minute=5,
        id="daily_digest",
        replace_existing=True,
    )

    # Telegram bot polling — dormant unless TELEGRAM_BOT_TOKEN + CHAT_ID
    # are set. Stateless poll; 3s default interval is responsive enough
    # for human commands without hammering Telegram's API.
    try:
        from core.telegram_bot import configured as _tg_configured
        from core.settings import SETTINGS
        if _tg_configured():
            tg_secs = max(int(SETTINGS.telegram.poll_interval_seconds), 1)
            for job in scheduler.get_jobs():
                if job.id == "telegram_bot":
                    job.remove()
            scheduler.add_job(_run_telegram_poll, "interval", seconds=tg_secs,
                              id="telegram_bot", replace_existing=True,
                              max_instances=1, coalesce=True)
            log_event(_startup_log, "telegram_bot_registered",
                      poll_interval_seconds=tg_secs)
    except Exception as e:  # noqa: BLE001
        log_event(_startup_log, "telegram_bot_register_failed",
                  level=_logging.WARNING, error=str(e))

    log_event(_startup_log, "monitor_started", interval_seconds=secs)
    return {"status": "started", "interval_seconds": secs, "leader": True}


def _run_paper_gate_evaluator():
    """APScheduler entry — runs the daily paper-gate evaluator."""
    try:
        from core.paper_gate import run_daily_evaluator
        result = run_daily_evaluator()
        if result.get("transitions"):
            log_event(_startup_log, "paper_gate_transitions",
                      transitions=result["transitions"])
    except Exception as exc:
        _startup_log.warning("paper gate evaluator failed: %s", exc)


def _run_telegram_poll():
    """APScheduler wrapper — drain pending Telegram updates and dispatch
    any registered slash-command handlers. No-op when not configured."""
    try:
        from core.telegram_bot import poll_once, configured as _tg_configured
        if not _tg_configured():
            return
        poll_once()
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("telegram poll failed: %s", e)


def _run_daily_digest():
    """APScheduler wrapper — send the daily digest webhook (I2)."""
    from core.notifier import send_daily_digest
    from core.journal import get_journal
    try:
        send_daily_digest(get_journal())
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("daily digest send failed: %s", e)


class PaperEntryRequest(BaseModel):
    """Request body for /api/monitor/paper_entry (dry-run forced entry)."""
    creds: IBKRConnectRequest
    symbol: str = "SPY"
    contracts: int = 1
    target_dte: int = 7          # short DTE so exit happens quickly during dry-run
    spread_cost_target: float = 150.0
    strike_width: int = 5
    stop_loss_pct: float = 40.0
    take_profit_pct: float = 40.0
    # Skip the market-hours check for out-of-hours dry runs.
    skip_market_hours_check: bool = True
    # Skip event-calendar blackouts (e.g. CPI/FOMC days) for dry-run testing.
    skip_event_blackout: bool = False
    # When live option quotes aren't available (market closed / no subscription),
    # fall back to Black-Scholes synthetic pricing so the full journaling / order
    # path can still be exercised.
    allow_synthetic_chain: bool = True


@app.post("/api/monitor/paper_entry")
async def paper_entry(req: PaperEntryRequest):
    """Dry-run helper: fire one entry immediately, bypassing strategy signal.

    Runs the *full* live path — chain resolution, risk gate (market-hours
    check skippable), sizing, journal, order — so all infrastructure is
    exercised on demand.  Use this when the underlying strategy would only
    fire 2×/month and you need faster paper-trading validation.

    Typical use::

        POST /api/monitor/paper_entry
        {"creds": {"port": 7497}, "target_dte": 7, "skip_market_hours_check": true}
    """
    import traceback as _tb
    try:
        return await _paper_entry_inner(req)
    except BaseException as _exc:
        return {"error": str(_exc), "traceback": _tb.format_exc()}


async def _paper_entry_inner(req: PaperEntryRequest):
    """Inner implementation — wrapped by paper_entry for top-level error capture."""
    from core.journal import Position, Order, get_journal
    from core.risk import (
        AccountSnapshot, RiskContext, RiskLimits, evaluate_pre_trade,
        size_position,
    )
    from core.chain import (
        resolve_bull_call_spread_with_diagnostics,
        build_synthetic_spread,
    )
    from core.calendar import load_event_calendar
    from core.settings import SETTINGS
    import uuid as _uuid

    journal = get_journal()

    # 1. Connect
    trader, msg = await get_ib_connection(req.creds.model_dump())
    if not trader:
        return {"error": msg}

    # 2. Account snapshot
    try:
        acct = await trader.get_account_summary()
    except Exception as e:
        return {"error": f"account_summary_failed: {e}"}

    account = AccountSnapshot(
        equity=acct.get("equity", 0),
        buying_power=acct.get("buying_power", 0),
        excess_liquidity=acct.get("excess_liquidity", 0),
        daily_pnl=acct.get("daily_pnl", 0),
    )

    # 3. Option chain — try live first, fall back to synthetic when market is closed
    spread, chain_diag = await resolve_bull_call_spread_with_diagnostics(
        trader, req.symbol, req.target_dte, req.spread_cost_target,
        max_width=req.strike_width + 35,
    )
    chain_source = "live"

    if spread is None and req.allow_synthetic_chain:
        # Live quotes unavailable (market closed, no subscription, etc).
        # Build a Black-Scholes priced spread so the rest of the pipeline
        # (risk gate, journal, order path) can still be exercised.
        # NaN guard: IEEE-754 NaN is truthy, so `nan or 0.0` == nan, and
        # nan <= 0 is False — which would skip the yfinance fallback inside
        # build_synthetic_spread.  Sanitise to 0.0 so yfinance is called.
        import math as _math
        _raw_und = chain_diag.get("underlying") or 0.0
        _und_hint = 0.0 if (
            _raw_und is None or not isinstance(_raw_und, (int, float))
            or _math.isnan(float(_raw_und)) or _math.isinf(float(_raw_und))
        ) else float(_raw_und)
        spread = build_synthetic_spread(
            symbol=req.symbol,
            target_dte=req.target_dte,
            target_cost=req.spread_cost_target,
            strike_width=req.strike_width,
            underlying=_und_hint,
        )
        chain_source = "synthetic_bs"

    if spread is None:
        journal.log_event("paper_entry_chain_failed", subject=req.symbol,
                          payload=chain_diag)
        return _safe_json({
            "error": "Chain resolution failed",
            "diagnostics": chain_diag,
            "hint": (
                "Live quotes unavailable and synthetic fallback failed. "
                "Check: (1) TWS is running, (2) market data subscriptions for SPY options, "
                "(3) try during RTH (9:30–16:00 ET). "
                "Or pass allow_synthetic_chain=true (default) for out-of-hours testing."
            ),
        })

    contracts = max(req.contracts, 1)

    # 4. Risk gate — optionally skip market-hours and event-blackout checks
    #    so dry-run tests can be forced on any day.
    limits = RiskLimits.from_settings()
    from dataclasses import replace as _replace
    if req.skip_market_hours_check:
        limits = _replace(limits, require_market_open=False)
    if req.skip_event_blackout:
        limits = _replace(limits, block_on_events=False)

    events = load_event_calendar() if not req.skip_event_blackout else []
    ctx = RiskContext(
        account=account,
        open_positions=len(journal.list_open()),
        today_realized_pnl=journal.today_realized_pnl(),
        debit_per_contract=spread.net_debit,
        margin_per_contract=spread.margin_req,
        contracts=contracts,
        target_dte=req.target_dte,
        limits=limits,
        events=events,
    )
    decision = evaluate_pre_trade(ctx)
    if not decision.allowed:
        return {
            "risk_rejected": True,
            "reason": decision.reason,
            "details": decision.details,
        }

    # 5. Place order (same path as /api/ibkr/execute)
    pos_id = f"dry-{_uuid.uuid4().hex[:8]}"
    expiry_str = spread.expiry
    side = "BUY" if spread.net_debit > 0 else "SELL"
    midpoint = abs(spread.net_debit) / 100.0
    haircut = SETTINGS.risk.limit_price_haircut
    limit_price = round(midpoint * (1 + haircut) if side == "BUY" else midpoint * (1 - haircut), 2)

    try:
        # SpreadSpec.as_ib_legs() converts ChainLeg objects → List[Dict]
        ib_legs = spread.as_ib_legs()
        order_result = await trader.place_combo_order(
            symbol=req.symbol,
            legs=ib_legs,
            sc=contracts,
            side=side,
            lmtPrice=limit_price,
        )
    except Exception as e:
        return {"error": f"order_placement_failed: {e}"}

    # 6. Journal
    pos = Position(
        id=pos_id,
        symbol=req.symbol,
        topology="vertical_spread",
        direction="bull",
        contracts=contracts,
        entry_cost=spread.net_debit * contracts,
        entry_time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        expiry=expiry_str,
        # Convert ChainLeg dataclass instances to dicts for JSON serialisation.
        legs=tuple(
            (__import__("dataclasses").asdict(leg)
             if hasattr(leg, "__dataclass_fields__") else leg)
            for leg in spread.legs
        ),
        state="pending",
        high_water_mark=abs(spread.net_debit) / 100.0,
        broker="ibkr",
        meta={
            "stop_loss_pct": req.stop_loss_pct,
            "take_profit_pct": req.take_profit_pct,
            "dry_run": True,
        },
    )
    journal.open_position(pos)
    order = Order(
        id=f"ord-{_uuid.uuid4().hex[:8]}",
        position_id=pos_id,
        broker="ibkr",
        broker_order_id=str(order_result.get("orderId", order_result.get("order_id", ""))),
        side=side,
        limit_price=limit_price,
        status="submitted",
        submitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        kind="entry",
    )
    journal.record_order(order)
    journal.log_event("paper_entry_placed", subject=pos_id, payload={
        "symbol": req.symbol, "contracts": contracts,
        "net_debit": spread.net_debit, "expiry": expiry_str,
    })

    return _safe_json({
        "status": "placed",
        "position_id": pos_id,
        "contracts": contracts,
        "net_debit": spread.net_debit,
        "limit_price": limit_price,
        "expiry": expiry_str,
        "K_long": spread.meta.get("K_long"),
        "K_short": spread.meta.get("K_short"),
        "underlying_price": spread.underlying_price,
        "chain_source": chain_source,          # "live" or "synthetic_bs"
        "chain_diagnostics": chain_diag,
        "synthetic": spread.meta.get("synthetic", False),
        "order": order_result,
        "risk_decision": {"allowed": True},
        "account": {
            "equity": account.equity,
            "buying_power": account.buying_power,
        },
    })


@app.post("/api/notify/digest")
def trigger_digest():
    """Manually trigger the daily digest. Returns the digest payload whether
    or not the webhook URL is configured (useful for testing the format)."""
    from core.notifier import build_daily_digest, send_daily_digest
    from core.journal import get_journal
    journal = get_journal()
    digest = build_daily_digest(journal)
    sent = send_daily_digest(journal)
    return {"sent": sent, "digest": digest}


@app.get("/api/telegram/status")
def telegram_status():
    """Report whether the Telegram bot is configured and active.

    Returns whether ``TELEGRAM_BOT_TOKEN`` + ``TELEGRAM_CHAT_ID`` are set,
    the masked chat id (so the operator can verify they didn't typo it),
    and which slash commands are registered.
    """
    from core.telegram_bot import configured, list_commands
    from core.settings import SETTINGS
    poll_job = next(
        (j for j in scheduler.get_jobs() if j.id == "telegram_bot"), None,
    )
    chat_id = SETTINGS.telegram.chat_id
    masked_chat = (chat_id[:3] + "…" + chat_id[-3:]) if chat_id and len(chat_id) > 6 else (chat_id or "")
    return {
        "configured": configured(),
        "chat_id_masked": masked_chat,
        "poll_interval_seconds": SETTINGS.telegram.poll_interval_seconds,
        "polling_active": poll_job is not None,
        "commands": list_commands(),
    }


@app.post("/api/telegram/test")
def telegram_test_message(payload: Optional[dict] = None):
    """Send a test message to the configured chat.

    Body optional: ``{"text": "..."}`` to override the default. Used by
    the UI to confirm the token + chat id are working.
    """
    from core.telegram_bot import notify, configured
    if not configured():
        return {
            "sent": False,
            "error": "telegram_not_configured",
            "detail": "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars and restart.",
        }
    text = (payload or {}).get("text") or (
        "✅ *SPY Spread Bot test message*\n"
        "If you can read this, your Telegram bot is wired up correctly. "
        "Send /help for the command list."
    )
    sent = notify(text)
    return {"sent": sent}


@app.post("/api/telegram/poll_once")
def telegram_poll_once_endpoint():
    """Force one Telegram update poll. Useful for tests + manual debugging.

    Normally polling runs every few seconds via APScheduler, but this lets
    you drain pending updates synchronously without waiting for the next tick.
    """
    from core.telegram_bot import poll_once, configured
    if not configured():
        return {"error": "telegram_not_configured", "processed": 0}
    return {"processed": poll_once()}


@app.post("/api/monitor/stop")
def stop_monitor():
    """Remove monitor + fill watcher jobs and release the leader lock."""
    from core.leader import release_leadership

    removed = []
    for job in scheduler.get_jobs():
        if job.id in ("live_monitor", "fill_watcher", "daily_digest"):
            job.remove()
            removed.append(job.id)
    release_leadership()
    log_event(_startup_log, "monitor_stopped", removed=removed)
    return {"status": "stopped", "removed": removed}
