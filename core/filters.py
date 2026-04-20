"""Shared strategy-filter module.

Currently the backtest engine and the live scanner each implement their
own filter logic. The live path skips SMA-200, Volume, VIX and regime
filters, so a signal that fires live would never have fired in backtest.
That divergence silently breaks the expected edge.

`apply_filters` is the single function both paths MUST call. Given a
single row (a pandas Series or dict-like) and a request object with the
canonical filter flags, it returns `(allowed: bool, rejected_by: str)`.
"""

from __future__ import annotations

from typing import Any, Mapping


def _get(row: Any, key: str, default: Any = None) -> Any:
    """Fetch a field from either a pandas Series, dict, or dataclass-row."""
    try:
        if hasattr(row, "get"):
            val = row.get(key, default)
        else:
            val = getattr(row, key, default)
    except (KeyError, AttributeError):
        return default
    # pandas uses NaN for missing — normalise to default.
    try:
        import math
        if val is None:
            return default
        if isinstance(val, float) and math.isnan(val):
            return default
    except Exception:
        pass
    return val


def resolve_bias(req) -> str:
    """Return canonical 'bull' or 'bear' bias from legacy-aware req fields."""
    direction = getattr(req, "direction", "") or ""
    strategy_type = getattr(req, "strategy_type", "") or ""
    if direction == "bear" or strategy_type == "bear_put":
        return "bear"
    return "bull"


def apply_filters(row: Any, req) -> tuple[bool, str]:
    """
    Evaluate the universal entry filters against a single bar row.

    Returns
    -------
    allowed : bool
        True when every enabled filter passes.
    reason : str
        Empty when allowed; otherwise the first filter that rejected.

    Filters — each controlled by a request flag — in this order:
        rsi, ema, sma200, volume, vix, regime
    """
    bias = resolve_bias(req)
    is_bear = bias == "bear"

    # ── RSI ───────────────────────────────────────────────────────────────
    if getattr(req, "use_rsi_filter", False):
        rsi = _get(row, "RSI")
        if rsi is None:
            return False, "rsi_unavailable"
        rsi = float(rsi)
        threshold = float(getattr(req, "rsi_threshold", 30))
        if is_bear:
            if rsi <= (100 - threshold):
                return False, "rsi_filter"
        else:
            if rsi >= threshold:
                return False, "rsi_filter"

    # ── EMA ───────────────────────────────────────────────────────────────
    if getattr(req, "use_ema_filter", False):
        ema_length = int(getattr(req, "ema_length", 10))
        ema_col = f"EMA_{ema_length}"
        ema = _get(row, ema_col)
        close = _get(row, "Close")
        if ema is None or close is None:
            return False, "ema_unavailable"
        if is_bear:
            if float(close) <= float(ema):
                return False, "ema_filter"
        else:
            if float(close) >= float(ema):
                return False, "ema_filter"

    # ── SMA-200 ───────────────────────────────────────────────────────────
    if getattr(req, "use_sma200_filter", False):
        sma200 = _get(row, "SMA_200")
        close = _get(row, "Close")
        if sma200 is None or close is None:
            return False, "sma200_unavailable"
        if is_bear:
            if float(close) >= float(sma200):
                return False, "sma200_filter"
        else:
            if float(close) <= float(sma200):
                return False, "sma200_filter"

    # ── Volume ────────────────────────────────────────────────────────────
    if getattr(req, "use_volume_filter", False):
        vol = _get(row, "Volume")
        vol_ma = _get(row, "Volume_MA")
        if vol is None or vol_ma is None:
            return False, "volume_unavailable"
        if float(vol) <= float(vol_ma):
            return False, "volume_filter"

    # ── VIX range ────────────────────────────────────────────────────────
    if getattr(req, "use_vix_filter", False):
        vix = _get(row, "VIX")
        if vix is None:
            # Unlike other filters, VIX missing means neutral; don't reject.
            return True, ""
        v = float(vix)
        if v < float(getattr(req, "vix_min", 15.0)):
            return False, "vix_below_min"
        if v > float(getattr(req, "vix_max", 35.0)):
            return False, "vix_above_max"

    # ── Regime ────────────────────────────────────────────────────────────
    if getattr(req, "use_regime_filter", False):
        allowed_regime = getattr(req, "regime_allowed", "all")
        if allowed_regime and allowed_regime != "all":
            current = _get(row, "regime", "sideways")
            if current != allowed_regime:
                return False, "regime_filter"

    return True, ""


def attach_regime(df):
    """Add a `regime` column to a DataFrame when SMA_200/SMA_50 are present."""
    if "SMA_200" not in df.columns or "SMA_50" not in df.columns:
        df["regime"] = "sideways"
        return df
    df = df.copy()
    df["regime"] = "sideways"
    bull = (df["Close"] > df["SMA_200"]) & (df["SMA_50"] > df["SMA_200"])
    bear = (df["Close"] < df["SMA_200"]) & (df["SMA_50"] < df["SMA_200"])
    df.loc[bull, "regime"] = "bull"
    df.loc[bear, "regime"] = "bear"
    return df


__all__ = ["apply_filters", "attach_regime", "resolve_bias"]
