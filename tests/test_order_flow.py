"""Tests for the order flow engine (core/order_flow.py).

Tests tick classification, bar building, delta/absorption calculation,
and all three signal types.
"""

import pytest
import time
from core.order_flow import (
    TickClassifier,
    ClassifiedTick,
    BarBuilder,
    OrderFlowBar,
    OrderFlowEngine,
    SignalEvent,
)


# ── TickClassifier ────────────────────────────────────────────────────────────

class TestTickClassifier:
    def test_first_tick_is_neutral(self):
        c = TickClassifier()
        t = c.classify(500.0, 100, time.time())
        assert t.aggressor == "neutral"

    def test_uptick_is_ask(self):
        c = TickClassifier()
        c.classify(500.0, 100, time.time())
        t = c.classify(500.05, 200, time.time())
        assert t.aggressor == "ask"

    def test_downtick_is_bid(self):
        c = TickClassifier()
        c.classify(500.0, 100, time.time())
        t = c.classify(499.95, 200, time.time())
        assert t.aggressor == "bid"

    def test_same_price_inherits(self):
        c = TickClassifier()
        c.classify(500.0, 100, time.time())
        c.classify(500.05, 100, time.time())  # ask
        t = c.classify(500.05, 150, time.time())  # same price
        assert t.aggressor == "ask"  # inherits

    def test_reset_clears_state(self):
        c = TickClassifier()
        c.classify(500.0, 100, time.time())
        c.classify(500.05, 100, time.time())
        c.reset()
        t = c.classify(501.0, 100, time.time())
        assert t.aggressor == "neutral"  # first tick after reset


# ── BarBuilder ────────────────────────────────────────────────────────────────

class TestBarBuilder:
    def _make_tick(self, price, vol, ts):
        return ClassifiedTick(price, vol, ts, "ask")

    def test_single_minute_no_completion(self):
        bars = []
        bb = BarBuilder(on_bar_complete=bars.append)
        base = 1000000  # some unix timestamp
        bb.on_tick(self._make_tick(500.0, 100, base))
        bb.on_tick(self._make_tick(500.05, 200, base + 10))
        assert len(bars) == 0  # still in same minute

    def test_minute_crossover_completes_bar(self):
        bars = []
        bb = BarBuilder(on_bar_complete=bars.append)
        base = 1000000 * 60  # aligned to minute
        bb.on_tick(self._make_tick(500.0, 100, base * 60))
        bb.on_tick(self._make_tick(500.05, 200, base * 60 + 30))
        # Cross into next minute
        bb.on_tick(self._make_tick(500.10, 50, (base + 1) * 60))
        assert len(bars) == 1
        assert bars[0].open == 500.0
        assert bars[0].close == 500.05
        assert bars[0].high == 500.05
        assert bars[0].low == 500.0
        assert bars[0].volume == 300

    def test_flush_returns_partial(self):
        bb = BarBuilder()
        base = 1000000
        bb.on_tick(self._make_tick(500.0, 100, base))
        bar = bb.flush()
        assert bar is not None
        assert bar.open == 500.0
        assert bar.volume == 100


# ── OrderFlowBar ──────────────────────────────────────────────────────────────

class TestOrderFlowBar:
    def test_delta(self):
        bar = OrderFlowBar(bar_ts=None, ask_volume=1000, bid_volume=400)
        assert bar.delta == 600

    def test_negative_delta(self):
        bar = OrderFlowBar(bar_ts=None, ask_volume=200, bid_volume=800)
        assert bar.delta == -600

    def test_absorption_tight_range(self):
        bar = OrderFlowBar(
            bar_ts=None, open=500.0, high=500.02, low=500.0, close=500.01,
            ask_volume=200, bid_volume=1200,
        )
        # |delta| = 1000, range = 0.02
        assert bar.absorption == pytest.approx(1000 / 0.02, rel=0.01)

    def test_absorption_zero_range(self):
        bar = OrderFlowBar(
            bar_ts=None, open=500.0, high=500.0, low=500.0, close=500.0,
            ask_volume=500, bid_volume=100,
        )
        # Zero range → absorption = |delta|
        assert bar.absorption == 400.0

    def test_range_pct(self):
        bar = OrderFlowBar(bar_ts=None, open=500.0, high=500.25, low=500.0)
        assert bar.range_pct == pytest.approx(0.05, rel=0.01)

    def test_as_dict(self):
        bar = OrderFlowBar(
            bar_ts=None, open=500.0, high=500.1, low=499.9, close=500.05,
            volume=1000, ask_volume=600, bid_volume=400, tick_count=50,
        )
        d = bar.as_dict()
        assert d["delta"] == 200
        assert d["tick_count"] == 50
        assert "absorption" in d


# ── OrderFlowEngine signals ──────────────────────────────────────────────────

class TestOrderFlowEngine:
    def _make_bar(self, open_, high, low, close, ask_vol, bid_vol, minute_offset=0):
        from datetime import datetime, timezone
        ts = datetime(2026, 5, 2, 14, minute_offset, 0, tzinfo=timezone.utc)
        return OrderFlowBar(
            bar_ts=ts, open=open_, high=high, low=low, close=close,
            volume=ask_vol + bid_vol, ask_volume=ask_vol, bid_volume=bid_vol,
            tick_count=100,
        )

    def test_needs_lookback_bars(self):
        engine = OrderFlowEngine(lookback=5)
        bar = self._make_bar(500, 500.5, 499.5, 500.2, 1000, 1000, 0)
        result = engine.on_bar(bar)
        assert result is None  # not enough history

    def test_absorption_bull_signal(self):
        """Price at 5-min low, heavy selling, tight range, price recovers."""
        signals = []
        engine = OrderFlowEngine(
            lookback=5, vol_threshold=1.5, range_threshold_pct=0.06,
            delta_threshold=300, on_signal=signals.append,
        )

        # Build history: price trending down
        for i in range(4):
            bar = self._make_bar(
                501 - i * 0.5, 501.5 - i * 0.5, 500.5 - i * 0.5, 501 - i * 0.5,
                500, 500, i,
            )
            engine.on_bar(bar)

        # Trigger bar: at the low, heavy selling (bid > ask), tight range, close > low
        trigger = self._make_bar(
            499.0, 499.02, 499.0, 499.01,  # tight range (0.02 = 0.004%)
            500, 1500,  # delta = -1000, total vol = 2000 (> 1.5*avg), heavy selling
            5,
        )
        result = engine.on_bar(trigger)

        assert result is not None
        assert result.signal_type == "absorption_bull"
        assert result.side == "call"

    def test_delta_divergence_signal(self):
        """Price makes lower low, delta is rising → bullish divergence."""
        signals = []
        engine = OrderFlowEngine(
            lookback=5, delta_threshold=100, on_signal=signals.append,
        )

        # Bar 0-4: setup with declining lows
        for i in range(3):
            bar = self._make_bar(
                500 - i, 501 - i, 499.5 - i, 500 - i,
                500, 500, i,
            )
            engine.on_bar(bar)

        # Bar 3: lower low, but delta turns positive
        bar3 = self._make_bar(
            497.5, 498, 497.0, 497.5,  # lower low than bar 2 (497.5)
            400, 600,  # delta = -200
            3,
        )
        engine.on_bar(bar3)

        # Bar 4: even lower low, but delta positive and rising
        bar4 = self._make_bar(
            497.0, 497.5, 496.5, 497.0,  # lower low
            700, 200,  # delta = +500 (positive and rising from -200)
            4,
        )
        result = engine.on_bar(bar4)

        assert result is not None
        assert result.signal_type == "delta_divergence"
        assert result.side == "call"

    def test_state_dict(self):
        engine = OrderFlowEngine(lookback=3)
        bar = self._make_bar(500, 500.5, 499.5, 500.2, 1000, 500, 0)
        engine.on_bar(bar)
        state = engine.state_dict()
        assert state["bars_count"] == 1
        assert state["cumulative_delta"] == 500
        assert state["latest_bar"] is not None

    def test_reset_clears_all(self):
        engine = OrderFlowEngine(lookback=3)
        bar = self._make_bar(500, 500.5, 499.5, 500.2, 1000, 500, 0)
        engine.on_bar(bar)
        engine.reset()
        assert engine.state_dict()["bars_count"] == 0
        assert engine.cumulative_delta == 0


# ── ATM Strike Selection ─────────────────────────────────────────────────────

class TestATMSelection:
    def test_call_selection(self):
        from strategies.order_flow_0dte import get_spy_0dte_ticker
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 2, tzinfo=timezone.utc)
        result = get_spy_0dte_ticker(510.25, "call", dt)
        assert result["strike"] == 510.0
        assert result["right"] == "C"
        assert result["code"] == "US.SPY260502C510000"
        assert result["expiry"] == "260502"

    def test_put_selection(self):
        from strategies.order_flow_0dte import get_spy_0dte_ticker
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 2, tzinfo=timezone.utc)
        result = get_spy_0dte_ticker(509.75, "put", dt)
        assert result["strike"] == 510.0  # rounds to nearest
        assert result["right"] == "P"
        assert "P510000" in result["code"]

    def test_half_dollar_rounds_correctly(self):
        from strategies.order_flow_0dte import get_spy_0dte_ticker
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 2, tzinfo=timezone.utc)
        result = get_spy_0dte_ticker(505.50, "call", dt)
        # Python rounds 0.5 to nearest even → 506
        assert result["strike"] in (505.0, 506.0)
