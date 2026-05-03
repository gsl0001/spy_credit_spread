"""Order-flow micro-structure engine for tick-level delta + absorption.

Consumes raw trade ticks (price, volume, timestamp) and produces 1-minute
bars enriched with:
  - Delta  (ask-aggressor volume - bid-aggressor volume)
  - Cumulative delta (running session total)
  - Absorption factor (|delta| / price_range)
  - VWAP

Aggressor identification uses the **Tick Test**: if price > prev_price the
trade is classified as an ask aggressor; if price < prev_price it's a bid
aggressor; if price == prev_price it inherits the previous classification.

The engine is broker-agnostic — it operates on plain (price, volume, ts)
tuples.  The MoomooTrader feeds it via its tick subscription handler.
"""

from __future__ import annotations

import logging
import time as _time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── Tick classification ──────────────────────────────────────────────────────

@dataclass(slots=True)
class ClassifiedTick:
    """A single trade tick with aggressor classification."""
    price: float
    volume: float
    ts: float  # unix timestamp
    aggressor: str  # 'ask' | 'bid' | 'neutral'


class TickClassifier:
    """Classify ticks as ask/bid aggressor using the Tick Test.

    Tick Test: price > last_price → ask aggressor (buyer lifting)
               price < last_price → bid aggressor (seller hitting)
               price == last_price → inherit previous classification
    """

    def __init__(self):
        self._last_price: float | None = None
        self._last_side: str = "neutral"

    def classify(self, price: float, volume: float, ts: float) -> ClassifiedTick:
        if self._last_price is None:
            self._last_price = price
            return ClassifiedTick(price, volume, ts, "neutral")

        if price > self._last_price:
            side = "ask"
        elif price < self._last_price:
            side = "bid"
        else:
            side = self._last_side  # inherit

        self._last_price = price
        self._last_side = side
        return ClassifiedTick(price, volume, ts, side)

    def reset(self):
        self._last_price = None
        self._last_side = "neutral"


# ── 1-Minute Bar with Order Flow ─────────────────────────────────────────────

@dataclass
class OrderFlowBar:
    """One completed 1-minute bar enriched with order flow data."""
    bar_ts: datetime  # bar open timestamp (truncated to minute)
    open: float = 0.0
    high: float = 0.0
    low: float = float("inf")
    close: float = 0.0
    volume: float = 0.0
    ask_volume: float = 0.0
    bid_volume: float = 0.0
    tick_count: int = 0

    @property
    def delta(self) -> float:
        """Net directional volume: positive = buy pressure."""
        return self.ask_volume - self.bid_volume

    @property
    def price_range(self) -> float:
        if self.high == float("inf") or self.low == float("inf"):
            return 0.0
        return self.high - self.low

    @property
    def absorption(self) -> float:
        """Absorption factor α = |delta| / price_range.

        High α means aggressive volume is entering but price isn't moving
        — institutional wall absorption.
        """
        rng = self.price_range
        if rng < 0.001:  # avoid division by zero
            return abs(self.delta)  # all delta, no movement = extreme absorption
        return abs(self.delta) / rng

    @property
    def range_pct(self) -> float:
        """Price range as percentage of the bar open."""
        if self.open < 0.01:
            return 0.0
        return (self.price_range / self.open) * 100.0

    @property
    def vwap(self) -> float:
        """Tick-volume-weighted average price (approximated from OHLC)."""
        if self.volume < 1:
            return self.close
        # True VWAP would need every tick. This is a reasonable approximation.
        return (self.open + self.high + self.low + self.close) / 4.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.bar_ts.isoformat() if self.bar_ts else None,
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "close": round(self.close, 2),
            "volume": round(self.volume, 0),
            "delta": round(self.delta, 0),
            "absorption": round(self.absorption, 2),
            "range_pct": round(self.range_pct, 4),
            "ask_vol": round(self.ask_volume, 0),
            "bid_vol": round(self.bid_volume, 0),
            "tick_count": self.tick_count,
        }


# ── Bar Builder ──────────────────────────────────────────────────────────────

class BarBuilder:
    """Aggregate classified ticks into 1-minute OrderFlowBars.

    Calls ``on_bar_complete(bar)`` when a minute boundary is crossed.
    """

    def __init__(self, on_bar_complete: Callable[[OrderFlowBar], None] | None = None):
        self._on_bar = on_bar_complete
        self._current_bar: OrderFlowBar | None = None
        self._current_minute: int | None = None

    def _minute_of(self, ts: float) -> int:
        """Return minute-truncated unix timestamp."""
        return int(ts) // 60

    def on_tick(self, tick: ClassifiedTick) -> OrderFlowBar | None:
        """Process one classified tick. Returns completed bar (if any)."""
        minute = self._minute_of(tick.ts)
        completed = None

        if self._current_minute is not None and minute != self._current_minute:
            # New minute → close current bar
            completed = self._current_bar
            if completed and self._on_bar:
                self._on_bar(completed)
            self._current_bar = None

        if self._current_bar is None:
            bar_dt = datetime.fromtimestamp(minute * 60, tz=timezone.utc)
            self._current_bar = OrderFlowBar(bar_ts=bar_dt)
            self._current_bar.open = tick.price
            self._current_bar.high = tick.price
            self._current_bar.low = tick.price
            self._current_minute = minute

        bar = self._current_bar
        bar.close = tick.price
        bar.high = max(bar.high, tick.price)
        bar.low = min(bar.low, tick.price)
        bar.volume += tick.volume
        bar.tick_count += 1

        if tick.aggressor == "ask":
            bar.ask_volume += tick.volume
        elif tick.aggressor == "bid":
            bar.bid_volume += tick.volume

        return completed

    def flush(self) -> OrderFlowBar | None:
        """Force-close the current bar (e.g. at market close)."""
        bar = self._current_bar
        self._current_bar = None
        self._current_minute = None
        if bar and self._on_bar:
            self._on_bar(bar)
        return bar

    def reset(self):
        self._current_bar = None
        self._current_minute = None


# ── Order Flow State ─────────────────────────────────────────────────────────

@dataclass
class SignalEvent:
    """Emitted when the order flow engine detects a tradeable signal."""
    signal_type: str  # 'absorption_bull' | 'absorption_bear' | 'delta_divergence'
    bar: OrderFlowBar
    confidence: float  # 0.0 - 1.0
    side: str  # 'call' | 'put'
    reason: str  # human-readable
    ts: float = field(default_factory=_time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "side": self.side,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "ts": self.ts,
            "bar": self.bar.as_dict() if self.bar else None,
        }


class OrderFlowEngine:
    """Maintains rolling state across 1-min bars and detects signals.

    Signals:
      1. **Absorption Bull**: Price at 5-min low + negative delta spike
         + price fails to drop further.
      2. **Absorption Bear**: Price at 5-min high + positive delta spike
         + price fails to rise further.
      3. **Delta Divergence**: Price makes lower low + 1-min delta is positive.

    Parameters:
      lookback: Number of bars for rolling calculations (default 5)
      vol_threshold: Volume must be > N × average to trigger (default 2.0)
      range_threshold_pct: Max bar range as % of price for absorption (default 0.05)
      delta_threshold: Min |delta| for significance (default 500)
    """

    def __init__(
        self,
        lookback: int = 5,
        vol_threshold: float = 2.0,
        range_threshold_pct: float = 0.05,
        delta_threshold: float = 500,
        on_signal: Callable[[SignalEvent], None] | None = None,
    ):
        self.lookback = lookback
        self.vol_threshold = vol_threshold
        self.range_threshold_pct = range_threshold_pct
        self.delta_threshold = delta_threshold
        self._on_signal = on_signal

        self._bars: deque[OrderFlowBar] = deque(maxlen=lookback + 1)
        self._cum_delta: float = 0.0
        self._signals: list[SignalEvent] = []

    @property
    def bars(self) -> list[OrderFlowBar]:
        return list(self._bars)

    @property
    def cumulative_delta(self) -> float:
        return self._cum_delta

    @property
    def signals(self) -> list[SignalEvent]:
        return list(self._signals)

    def on_bar(self, bar: OrderFlowBar) -> SignalEvent | None:
        """Process a completed 1-min bar. Returns signal if detected."""
        self._bars.append(bar)
        self._cum_delta += bar.delta

        if len(self._bars) < self.lookback:
            return None

        signal = self._check_signals(bar)
        if signal:
            self._signals.append(signal)
            if self._on_signal:
                self._on_signal(signal)
        return signal

    def _check_signals(self, bar: OrderFlowBar) -> SignalEvent | None:
        """Run all signal detectors on the latest bar."""
        bars = list(self._bars)

        # Rolling averages over lookback
        avg_vol = sum(b.volume for b in bars[:-1]) / max(len(bars) - 1, 1)
        recent_lows = [b.low for b in bars]
        recent_highs = [b.high for b in bars]
        min_low = min(recent_lows)
        max_high = max(recent_highs)

        # --- Signal 1: Absorption Bull ---
        # Price at lookback low + negative delta spike + price fails to drop
        if (
            bar.low <= min_low * 1.001  # at or near 5-min low
            and bar.delta < -self.delta_threshold  # negative delta (selling)
            and bar.range_pct < self.range_threshold_pct  # tight range (absorbed)
            and bar.volume > avg_vol * self.vol_threshold  # high volume
            and bar.close > bar.low  # price recovered from low
        ):
            confidence = min(1.0, bar.absorption / 5000)
            return SignalEvent(
                signal_type="absorption_bull",
                bar=bar,
                confidence=confidence,
                side="call",
                reason=(
                    f"Absorption at 5m low: Δ={bar.delta:+.0f}, "
                    f"α={bar.absorption:.0f}, range={bar.range_pct:.4f}%"
                ),
            )

        # --- Signal 2: Absorption Bear ---
        # Price at lookback high + positive delta spike + price fails to rise
        if (
            bar.high >= max_high * 0.999  # at or near 5-min high
            and bar.delta > self.delta_threshold  # positive delta (buying)
            and bar.range_pct < self.range_threshold_pct  # tight range
            and bar.volume > avg_vol * self.vol_threshold
            and bar.close < bar.high  # price rejected from high
        ):
            confidence = min(1.0, bar.absorption / 5000)
            return SignalEvent(
                signal_type="absorption_bear",
                bar=bar,
                confidence=confidence,
                side="put",
                reason=(
                    f"Absorption at 5m high: Δ={bar.delta:+.0f}, "
                    f"α={bar.absorption:.0f}, range={bar.range_pct:.4f}%"
                ),
            )

        # --- Signal 3: Delta Divergence ---
        # Price makes lower low but cumulative delta is rising
        if len(bars) >= 3:
            prev_bar = bars[-2]
            prev_prev = bars[-3]
            # Price: lower low
            price_lower_low = bar.low < prev_bar.low and prev_bar.low < prev_prev.low
            # Delta: higher (positive divergence)
            delta_rising = bar.delta > prev_bar.delta and bar.delta > 0

            if price_lower_low and delta_rising:
                confidence = min(1.0, abs(bar.delta - prev_bar.delta) / 2000)
                return SignalEvent(
                    signal_type="delta_divergence",
                    bar=bar,
                    confidence=confidence,
                    side="call",
                    reason=(
                        f"Delta divergence: price LL ({bar.low:.2f} < {prev_bar.low:.2f}) "
                        f"but Δ rising ({prev_bar.delta:+.0f} → {bar.delta:+.0f})"
                    ),
                )

        return None

    def reset(self):
        """Clear all state for a new session."""
        self._bars.clear()
        self._cum_delta = 0.0
        self._signals.clear()

    def state_dict(self) -> dict[str, Any]:
        """Snapshot for API/UI consumption."""
        return {
            "bars_count": len(self._bars),
            "cumulative_delta": round(self._cum_delta, 0),
            "signals_count": len(self._signals),
            "latest_bar": self._bars[-1].as_dict() if self._bars else None,
            "recent_signals": [s.as_dict() for s in self._signals[-5:]],
        }
