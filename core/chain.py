"""Live options-chain resolver.

The backtest path prices spreads with Black-Scholes against a 21-day
realized-volatility proxy. That is fine for backtest but unacceptable
for live entry: real bid/ask differs from BS fair value by several
percent, and naive calendar-math expiries rarely coincide with a listed
SPY expiration.

This module provides one primary function:

    resolve_bull_call_spread(trader, symbol, target_dte, target_cost)

which asks IBKR for the real chain, picks the nearest listed expiry,
pulls live quotes for candidate strikes, and returns a spec the live
order path can submit as-is.

All network interaction is inside ``resolve_*``. Unit tests can drive
the strike-picking logic through :func:`pick_bull_call_strikes`, which
is pure and does not touch IBKR.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional


@dataclass(frozen=True)
class ChainLeg:
    type: str          # "call" | "put"
    strike: float
    side: str          # "long" | "short"
    expiry: str        # YYYYMMDD
    bid: float = 0.0
    ask: float = 0.0
    iv: float = 0.0

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return 0.0


@dataclass(frozen=True)
class SpreadSpec:
    symbol: str
    direction: str
    topology: str
    expiry: str               # YYYYMMDD
    legs: tuple[ChainLeg, ...]
    net_debit: float          # per-contract $ at spread's combined mid (positive = debit)
    margin_req: float         # per-contract margin (debit spread = debit; credit = width*100)
    underlying_price: float
    implied_vol: float
    quote_time: str
    meta: dict = field(default_factory=dict)

    def as_ib_legs(self) -> list[dict]:
        """Format legs for ibkr_trading.IBKRTrader.place_combo_order."""
        return [
            {"type": leg.type, "strike": leg.strike,
             "expiry": leg.expiry, "side": leg.side}
            for leg in self.legs
        ]


# ── Pure logic (testable without IBKR) ─────────────────────────────────────

def pick_nearest_expiry(expirations: list[str], target_dte: int,
                        today: Optional[date] = None) -> Optional[str]:
    """Given a list of YYYYMMDD expirations, pick the one closest to target_dte.

    Prefers the closest expiry NOT earlier than today. Ties resolve to the
    later expiry so we don't accidentally pick same-day.
    """
    if not expirations:
        return None
    ref = today or date.today()
    candidates = []
    for s in expirations:
        try:
            d = datetime.strptime(s, "%Y%m%d").date()
        except (TypeError, ValueError):
            continue
        dte = (d - ref).days
        if dte < 0:
            continue
        candidates.append((s, dte))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (abs(t[1] - target_dte), -t[1]))
    return candidates[0][0]


def pick_bull_call_strikes(
    strike_grid: list[float],
    underlying: float,
    call_prices: dict[float, tuple[float, float]],  # strike -> (bid, ask)
    target_debit: float,
    max_width: int = 40,
    otm_offset: float = 0.0,
) -> Optional[dict]:
    """
    Pick (K_long, K_short) for a bull call where
        K_long  ≈ ATM + otm_offset (nearest whole-dollar strike to underlying + offset)
        K_short > K_long, chosen so that (mid_long - mid_short) ~= target_debit/100

    Parameters
    ----------
    strike_grid : sorted list of available strikes
    underlying : live SPY spot
    call_prices : dict mapping strike → (bid, ask)
    target_debit : user-requested debit in dollars (e.g. 250.0 = $2.50/contract)
    max_width : search range in strike units
    otm_offset : points above underlying to target for K_long (e.g. 1.50 per SSRN 6355218)

    Returns
    -------
    dict with keys: K_long, K_short, debit_per_contract, long_bid, long_ask,
                    short_bid, short_ask, long_mid, short_mid
    or None when no viable spread was found.
    """
    if not strike_grid:
        return None

    # Target strike = underlying + offset; nearest whole-dollar strike wins.
    target_k_long = underlying + otm_offset
    k_long = min(strike_grid, key=lambda k: abs(k - target_k_long))
    long_quote = call_prices.get(k_long)
    if not long_quote or long_quote[0] <= 0 or long_quote[1] <= 0:
        return None
    long_bid, long_ask = long_quote
    long_mid = (long_bid + long_ask) / 2.0
    target_per_contract = target_debit / 100.0 if target_debit > 0 else 2.50

    # Candidate K_short strikes: strictly above K_long, within max_width
    above = [k for k in strike_grid if k > k_long and (k - k_long) <= max_width]
    if not above:
        return None

    best = None
    for k_short in above:
        q = call_prices.get(k_short)
        if not q or q[0] <= 0 or q[1] <= 0:
            continue
        s_bid, s_ask = q
        s_mid = (s_bid + s_ask) / 2.0
        debit = long_mid - s_mid
        if debit <= 0:
            break  # debits only get cheaper as K_short rises; once negative, stop.
        diff = abs(debit - target_per_contract)
        cand = {
            "K_long": k_long,
            "K_short": k_short,
            "debit_per_contract": debit,
            "long_bid": long_bid,
            "long_ask": long_ask,
            "short_bid": s_bid,
            "short_ask": s_ask,
            "long_mid": long_mid,
            "short_mid": s_mid,
        }
        if best is None or diff < best["_diff"]:
            cand["_diff"] = diff
            best = cand

    if best is None:
        return None
    best.pop("_diff", None)
    return best


def validate_spread_quality(
    spread: dict,
    *,
    max_bid_ask_pct: float = 0.10,    # 10% — generous default; production use 0.05
    min_mid: float = 0.05,             # reject spreads worth < 5¢ per contract
    quality_lookup: Optional[dict] = None,  # {strike: {"volume": int, "open_interest": int}}
    min_volume: Optional[int] = None,
    min_open_interest: Optional[int] = None,
) -> tuple[bool, str]:
    """Return (is_acceptable, reason) for a picked spread.

    Checks performed:
      1. Both legs have positive bid AND ask (sanity)
      2. Per-leg bid/ask spread <= max_bid_ask_pct of mid
      3. Spread mid >= min_mid (non-trivial value)
      4. Per-leg volume >= min_volume (if quality_lookup provided)
      5. Per-leg open_interest >= min_open_interest (if quality_lookup provided)

    Returns (True, "ok") on pass, (False, "<reason>") on first failure.
    Designed to short-circuit before order submission so degenerate
    quotes (zero bids, blown-out spreads, no volume) never reach the
    broker.
    """
    long_bid = float(spread.get("long_bid", 0) or 0)
    long_ask = float(spread.get("long_ask", 0) or 0)
    short_bid = float(spread.get("short_bid", 0) or 0)
    short_ask = float(spread.get("short_ask", 0) or 0)
    if long_bid <= 0 or long_ask <= 0:
        return False, "long_leg_no_quote"
    if short_bid <= 0 or short_ask <= 0:
        return False, "short_leg_no_quote"
    if long_ask < long_bid:
        return False, "long_leg_crossed_quote"
    if short_ask < short_bid:
        return False, "short_leg_crossed_quote"

    long_mid = (long_bid + long_ask) / 2.0
    short_mid = (short_bid + short_ask) / 2.0
    if long_mid > 0 and (long_ask - long_bid) / long_mid > max_bid_ask_pct:
        return False, f"long_leg_spread_wide_{(long_ask - long_bid) / long_mid * 100:.1f}pct"
    if short_mid > 0 and (short_ask - short_bid) / short_mid > max_bid_ask_pct:
        return False, f"short_leg_spread_wide_{(short_ask - short_bid) / short_mid * 100:.1f}pct"

    debit = float(spread.get("debit_per_contract", 0) or 0)
    if debit < min_mid:
        return False, f"spread_too_thin_{debit:.3f}"

    if quality_lookup:
        for strike, label in (
            (spread.get("K_long"), "long"),
            (spread.get("K_short"), "short"),
        ):
            if strike is None:
                continue
            q = quality_lookup.get(float(strike)) or quality_lookup.get(strike) or {}
            if min_volume is not None:
                vol = int(q.get("volume", 0) or 0)
                if vol < min_volume:
                    return False, f"{label}_leg_volume_{vol}_below_{min_volume}"
            if min_open_interest is not None:
                oi = int(q.get("open_interest", 0) or 0)
                if oi < min_open_interest:
                    return False, f"{label}_leg_oi_{oi}_below_{min_open_interest}"

    return True, "ok"


# ── Live IBKR-backed resolver ──────────────────────────────────────────────

async def resolve_bull_call_spread(
    trader,
    symbol: str,
    target_dte: int,
    target_cost: float,
    max_width: int = 40,
    quote_wait: float = 3.0,
    otm_offset: float = 0.0,
) -> Optional[SpreadSpec]:
    """Build a bull-call spread from live IBKR chain data.

    Falls back to None (with an error message attached via ``meta``) when
    any required step fails. Callers should treat None as "do not submit
    a live order" rather than retrying forever.
    """
    try:
        from brokers.ibkr_trading import Option, Stock
    except ImportError:
        return None

    await trader.ensure_connected()
    ib = trader.ib

    # 1. Live underlying quote
    stock = Stock(symbol, "SMART", "USD")
    await ib.qualifyContractsAsync(stock)
    if not stock.conId:
        return None
    tick = ib.reqMktData(stock, "", False, False)
    start = asyncio.get_running_loop().time()
    while (asyncio.get_running_loop().time() - start) < quote_wait:
        if tick.last and tick.last > 0:
            break
        if tick.bid and tick.ask and tick.bid > 0 and tick.ask > 0:
            break
        await asyncio.sleep(0.1)
    underlying = tick.last if (tick.last and tick.last > 0) else (
        (tick.bid + tick.ask) / 2 if (tick.bid and tick.ask) else 0.0
    )
    ib.cancelMktData(stock)
    if underlying <= 0:
        return None

    # 2. Chain parameters (expirations + strikes)
    params = await ib.reqSecDefOptParamsAsync(symbol, "", "STK", stock.conId)
    if not params:
        return None
    smart = next((p for p in params if p.exchange in ("SMART", "CBOE")), params[0])
    expirations = sorted(smart.expirations)
    strike_grid = sorted(float(s) for s in smart.strikes if s)
    expiry = pick_nearest_expiry(expirations, target_dte)
    if not expiry or not strike_grid:
        return None

    # 3. Build a small window of candidate strikes around ATM
    near = [k for k in strike_grid
            if (k - underlying) >= -2 and (k - underlying) <= max_width + 2]
    if not near:
        return None

    # 4. Fetch live call quotes for candidate strikes
    call_prices: dict[float, tuple[float, float]] = {}
    contracts = []
    for k in near:
        opt = Option(symbol, expiry, k, "C", "SMART")
        contracts.append(opt)

    # Qualify in one shot; ib_insync handles batching internally
    await ib.qualifyContractsAsync(*contracts)

    tickers = [ib.reqMktData(c, "", False, False) for c in contracts]
    start = asyncio.get_running_loop().time()
    while (asyncio.get_running_loop().time() - start) < quote_wait:
        # done when all have bid+ask
        pending = [t for t in tickers
                   if (t.bid is None or t.ask is None
                       or t.bid != t.bid or t.ask != t.ask)]  # NaN check
        if not pending:
            break
        await asyncio.sleep(0.15)

    iv_estimate = 0.0
    for t, c in zip(tickers, contracts):
        b, a = t.bid or 0.0, t.ask or 0.0
        # NaN guard
        if b != b:
            b = 0.0
        if a != a:
            a = 0.0
        if b > 0 and a > 0:
            call_prices[float(c.strike)] = (b, a)
        # Grab IV from the ATM-ish option for reporting
        if abs(float(c.strike) - underlying) < 1.5:
            mv = getattr(t, "modelGreeks", None)
            if mv and getattr(mv, "impliedVol", None):
                iv_estimate = float(mv.impliedVol)
    for t, c in zip(tickers, contracts):
        try:
            ib.cancelMktData(c)
        except Exception:
            pass

    pick = pick_bull_call_strikes(
        list(call_prices.keys()),
        underlying,
        call_prices,
        target_cost,
        max_width=max_width,
        otm_offset=otm_offset,
    )
    if pick is None:
        return None

    legs = (
        ChainLeg(type="call", strike=pick["K_long"], side="long",
                 expiry=expiry, bid=pick["long_bid"], ask=pick["long_ask"],
                 iv=iv_estimate),
        ChainLeg(type="call", strike=pick["K_short"], side="short",
                 expiry=expiry, bid=pick["short_bid"], ask=pick["short_ask"]),
    )
    debit = pick["debit_per_contract"] * 100.0
    margin = debit  # debit spreads: margin == debit

    return SpreadSpec(
        symbol=symbol,
        direction="bull_call",
        topology="vertical_spread",
        expiry=expiry,
        legs=legs,
        net_debit=debit,
        margin_req=margin,
        underlying_price=underlying,
        implied_vol=iv_estimate,
        quote_time=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        meta={
            "K_long": pick["K_long"],
            "K_short": pick["K_short"],
            "width": pick["K_short"] - pick["K_long"],
        },
    )


async def resolve_bull_call_spread_with_diagnostics(
    trader,
    symbol: str,
    target_dte: int,
    target_cost: float,
    max_width: int = 40,
    quote_wait: float = 3.0,
    otm_offset: float = 0.0,
) -> tuple[Optional[SpreadSpec], dict]:
    """Same as ``resolve_bull_call_spread`` but also returns a diagnostics dict
    that pinpoints which step failed.  Useful for dry-run / troubleshooting.

    Returns
    -------
    (spread_or_None, diagnostics)
    """
    try:
        from brokers.ibkr_trading import Option, Stock
    except ImportError:
        return None, {"error": "ib_insync not available"}

    diag: dict = {
        "underlying": None,
        "expirations_count": 0,
        "strikes_count": 0,
        "chosen_expiry": None,
        "candidate_strikes": 0,
        "quoted_strikes": 0,
        "pick": None,
        "error": None,
    }

    try:
        await trader.ensure_connected()
        ib = trader.ib

        stock = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(stock)
        if not stock.conId:
            diag["error"] = "stock_qualify_failed"
            return None, diag

        tick = ib.reqMktData(stock, "", False, False)
        start = asyncio.get_running_loop().time()
        while (asyncio.get_running_loop().time() - start) < quote_wait:
            if tick.last and tick.last > 0:
                break
            if tick.bid and tick.ask and tick.bid > 0 and tick.ask > 0:
                break
            await asyncio.sleep(0.1)
        underlying = tick.last if (tick.last and tick.last > 0) else (
            (tick.bid + tick.ask) / 2 if (tick.bid and tick.ask) else 0.0
        )
        ib.cancelMktData(stock)
        diag["underlying"] = underlying
        if underlying <= 0:
            diag["error"] = "no_underlying_quote"
            return None, diag

        params = await ib.reqSecDefOptParamsAsync(symbol, "", "STK", stock.conId)
        if not params:
            diag["error"] = "no_chain_params"
            return None, diag
        smart = next((p for p in params if p.exchange in ("SMART", "CBOE")), params[0])
        expirations = sorted(smart.expirations)
        strike_grid = sorted(float(s) for s in smart.strikes if s)
        diag["expirations_count"] = len(expirations)
        diag["strikes_count"] = len(strike_grid)

        expiry = pick_nearest_expiry(expirations, target_dte)
        diag["chosen_expiry"] = expiry
        if not expiry or not strike_grid:
            diag["error"] = "no_expiry_or_strikes"
            return None, diag

        near = [k for k in strike_grid
                if (k - underlying) >= -2 and (k - underlying) <= max_width + 2]
        diag["candidate_strikes"] = len(near)
        if not near:
            diag["error"] = "no_candidate_strikes"
            return None, diag

        call_prices: dict[float, tuple[float, float]] = {}
        contracts = [Option(symbol, expiry, k, "C", "SMART") for k in near]
        await ib.qualifyContractsAsync(*contracts)
        tickers = [ib.reqMktData(c, "", False, False) for c in contracts]
        start = asyncio.get_running_loop().time()
        while (asyncio.get_running_loop().time() - start) < quote_wait:
            pending = [t for t in tickers
                       if t.bid is None or t.ask is None
                       or t.bid != t.bid or t.ask != t.ask]
            if not pending:
                break
            await asyncio.sleep(0.15)

        iv_estimate = 0.0
        for t, c in zip(tickers, contracts):
            b, a = (t.bid or 0.0), (t.ask or 0.0)
            if b != b: b = 0.0
            if a != a: a = 0.0
            if b > 0 and a > 0:
                call_prices[float(c.strike)] = (b, a)
            if abs(float(c.strike) - underlying) < 1.5:
                mv = getattr(t, "modelGreeks", None)
                if mv and getattr(mv, "impliedVol", None):
                    iv_estimate = float(mv.impliedVol)
        for t, c in zip(tickers, contracts):
            try:
                ib.cancelMktData(c)
            except Exception:
                pass

        diag["quoted_strikes"] = len(call_prices)
        if not call_prices:
            diag["error"] = (
                "no_live_option_quotes — market likely closed or no data subscription. "
                "In TWS: Global Configuration → API → Settings → enable 'Send instrument-specific attributes'."
            )
            return None, diag

        pick = pick_bull_call_strikes(
            list(call_prices.keys()), underlying, call_prices, target_cost,
            max_width=max_width, otm_offset=otm_offset,
        )
        diag["pick"] = pick
        if pick is None:
            diag["error"] = "no_viable_spread"
            return None, diag

        legs = (
            ChainLeg(type="call", strike=pick["K_long"], side="long",
                     expiry=expiry, bid=pick["long_bid"], ask=pick["long_ask"], iv=iv_estimate),
            ChainLeg(type="call", strike=pick["K_short"], side="short",
                     expiry=expiry, bid=pick["short_bid"], ask=pick["short_ask"]),
        )
        debit = pick["debit_per_contract"] * 100.0

        return SpreadSpec(
            symbol=symbol, direction="bull_call", topology="vertical_spread",
            expiry=expiry, legs=legs, net_debit=debit, margin_req=debit,
            underlying_price=underlying, implied_vol=iv_estimate,
            quote_time=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            meta={"K_long": pick["K_long"], "K_short": pick["K_short"],
                  "width": pick["K_short"] - pick["K_long"]},
        ), diag

    except Exception as exc:
        diag["error"] = f"exception: {exc}"
        return None, diag


def build_synthetic_spread(
    symbol: str,
    target_dte: int,
    target_cost: float,
    strike_width: int = 5,
    underlying: float = 0.0,
) -> Optional[SpreadSpec]:
    """Build a BS-priced synthetic SpreadSpec without IBKR quotes.

    Used when the market is closed or no data subscription is available.
    Prices the spread with OptionTopologyBuilder (same as backtest engine)
    using the current SPY price and 21-day HV from yfinance.

    Returns None on any error.
    """
    try:
        import yfinance as yf
        import numpy as np
        from datetime import timezone
        from strategies.builder import OptionTopologyBuilder

        import math as _math
        RISK_FREE_RATE = 0.053  # hard-coded fallback; same as main.py

        # Sanitise incoming underlying: NaN comparisons always return False, so
        # `nan <= 0` is False and yfinance would be skipped.  Force NaN/Inf to 0.
        if not isinstance(underlying, (int, float)) or _math.isnan(underlying) or _math.isinf(underlying):
            underlying = 0.0

        # Live SPY price.  yfinance ≥0.2 returns MultiIndex columns
        # (e.g. ('Close', 'SPY')), so we use .values.flatten() for both layouts.
        if underlying <= 0:
            tick = yf.download(symbol, period="2d", interval="1d", progress=False)
            if tick.empty:
                return None
            underlying = float(tick["Close"].values.flatten()[-1])

        # 21-day HV from recent daily closes
        hist = yf.download(symbol, period="30d", interval="1d", progress=False)
        if len(hist) < 5:
            sigma = 0.18  # reasonable SPY HV fallback
        else:
            closes = hist["Close"].values.flatten().astype(float)
            rets = np.diff(np.log(closes))
            sigma = float(np.std(rets) * np.sqrt(252))
            sigma = max(sigma, 0.10)

        T = target_dte / 365.25
        pos = OptionTopologyBuilder.construct_legs(
            topology="vertical_spread",
            direction="bull_call",
            S=underlying,
            T=T,
            r=RISK_FREE_RATE,
            sigma=sigma,
            target_cost=target_cost,
            strike_width=strike_width,
            realism_factor=1.0,
        )
        if not pos or abs(pos.get("net_cost", 0)) < 0.01:
            return None

        net_debit = pos["net_cost"]
        legs_raw = pos.get("legs", [])

        # Convert builder legs to ChainLeg objects (no live bid/ask available)
        k_long = underlying   # builder ATM
        k_short = underlying + strike_width
        # Try to extract strikes from legs if builder populates them
        for leg in legs_raw:
            if leg.get("side") == "long":
                k_long = float(leg.get("strike", k_long))
            elif leg.get("side") == "short":
                k_short = float(leg.get("strike", k_short))

        # Nearest listed Friday expiry from today
        from_today = date.today()
        dte_date = from_today + timedelta(days=target_dte)
        # Snap to next Friday (weekday 4)
        days_to_friday = (4 - dte_date.weekday()) % 7
        expiry_date = dte_date + timedelta(days=days_to_friday)
        expiry = expiry_date.strftime("%Y%m%d")

        # Synthetic mid prices from BS
        mid_long = net_debit / 100.0 + 0.0   # approx
        mid_short = 0.0

        chain_legs = (
            ChainLeg(type="call", strike=k_long, side="long", expiry=expiry,
                     bid=round(mid_long * 0.95, 2), ask=round(mid_long * 1.05, 2), iv=sigma),
            ChainLeg(type="call", strike=k_short, side="short", expiry=expiry,
                     bid=round(mid_short * 0.95, 2) if mid_short > 0 else 0.05,
                     ask=round(mid_short * 1.05, 2) if mid_short > 0 else 0.15),
        )

        return SpreadSpec(
            symbol=symbol,
            direction="bull_call",
            topology="vertical_spread",
            expiry=expiry,
            legs=chain_legs,
            net_debit=net_debit,
            margin_req=net_debit,
            underlying_price=underlying,
            implied_vol=sigma,
            quote_time=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            meta={
                "K_long": k_long,
                "K_short": k_short,
                "width": k_short - k_long,
                "synthetic": True,
                "hv_sigma": round(sigma, 4),
            },
        )
    except Exception:
        return None


__all__ = [
    "ChainLeg",
    "SpreadSpec",
    "pick_nearest_expiry",
    "pick_bull_call_strikes",
    "resolve_bull_call_spread",
    "resolve_bull_call_spread_with_diagnostics",
    "build_synthetic_spread",
]
