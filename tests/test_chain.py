"""Pure-logic chain tests.

Exercise the strike/expiry picker without touching IBKR.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.chain import pick_bull_call_strikes, pick_nearest_expiry


@pytest.mark.unit
def test_pick_nearest_expiry_exact_match():
    today = date(2026, 4, 14)
    exps = ["20260417", "20260421", "20260428", "20260515"]
    # target_dte=14 → 20260428 is closest (14 days)
    assert pick_nearest_expiry(exps, 14, today=today) == "20260428"


@pytest.mark.unit
def test_pick_nearest_expiry_skips_past_expiries():
    today = date(2026, 4, 14)
    exps = ["20260101", "20260417", "20260515"]
    # 20260101 is in the past → ignored
    assert pick_nearest_expiry(exps, 30, today=today) == "20260515"


@pytest.mark.unit
def test_pick_nearest_expiry_empty():
    assert pick_nearest_expiry([], 14) is None


@pytest.mark.unit
def test_pick_bull_call_basic():
    # SPY around 500. K_long picks 500 (ATM). K_short targets debit 2.50.
    strikes = [498, 499, 500, 501, 502, 505, 510]
    prices = {
        498: (7.0, 7.2),
        499: (6.3, 6.5),
        500: (5.5, 5.7),
        501: (4.8, 5.0),
        502: (4.2, 4.4),
        505: (2.6, 2.8),   # (5.6 - 2.7) = 2.9 debit
        510: (0.9, 1.1),   # (5.6 - 1.0) = 4.6 debit
    }
    pick = pick_bull_call_strikes(strikes, 500.0, prices,
                                  target_debit=250.0, max_width=20)
    assert pick is not None
    assert pick["K_long"] == 500
    # Target 2.50 — the choices are K_short=501 (0.8), 502 (1.4), 505 (2.9), 510 (4.6)
    # abs(2.9 - 2.5) = 0.4; abs(1.4 - 2.5) = 1.1 → 505 wins
    assert pick["K_short"] == 505


@pytest.mark.unit
def test_pick_bull_call_no_above():
    strikes = [490, 495, 500]
    prices = {490: (11, 11.2), 495: (6, 6.2), 500: (2, 2.2)}
    pick = pick_bull_call_strikes(strikes, 500.0, prices,
                                  target_debit=250.0, max_width=20)
    assert pick is None


@pytest.mark.unit
def test_pick_bull_call_returns_none_when_atm_has_no_quote():
    strikes = [498, 500, 505]
    prices = {498: (7, 7.2), 505: (2, 2.2)}  # no quote for 500
    pick = pick_bull_call_strikes(strikes, 500.0, prices,
                                  target_debit=250.0, max_width=20)
    assert pick is None
