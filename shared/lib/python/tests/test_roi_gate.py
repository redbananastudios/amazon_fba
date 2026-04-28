"""Tests for fba_roi_gate.

Cover:
  - ROI calculation: positive, zero, negative profit, missing buy_cost
  - Two-gate behaviour: both must pass
  - Ordering of failures: profit floor checked before ROI to give clearer reasons
  - Boundary cases: exactly at threshold = passes
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB_DIR))

from fba_roi_gate import calculate_roi, passes_decision_gates


# --------------------------------------------------------------------------- #
# calculate_roi                                                               #
# --------------------------------------------------------------------------- #

def test_calculate_roi_positive():
    assert calculate_roi(profit=3.00, buy_cost=10.00) == 0.30


def test_calculate_roi_negative_profit():
    assert calculate_roi(profit=-2.00, buy_cost=10.00) == -0.20


def test_calculate_roi_zero_profit():
    assert calculate_roi(profit=0.00, buy_cost=10.00) == 0.0


def test_calculate_roi_zero_buy_cost_returns_none():
    """Don't return inf/nan — None forces caller to handle."""
    assert calculate_roi(profit=5.00, buy_cost=0) is None


def test_calculate_roi_negative_buy_cost_returns_none():
    assert calculate_roi(profit=5.00, buy_cost=-1) is None


def test_calculate_roi_none_buy_cost_returns_none():
    assert calculate_roi(profit=5.00, buy_cost=None) is None


# --------------------------------------------------------------------------- #
# passes_decision_gates                                                       #
# --------------------------------------------------------------------------- #

def test_passes_when_above_both_thresholds():
    # £4 profit on £10 cost = 40% ROI, above 30% target and £2.50 floor
    r = passes_decision_gates(
        profit_conservative=4.00,
        buy_cost=10.00,
        target_roi=0.30,
        min_profit_absolute=2.50,
    )
    assert r.passes is True
    assert r.reason == "passes"
    assert r.roi == pytest.approx(0.40)


def test_fails_when_profit_below_floor_even_with_high_roi():
    # 50% ROI but only £1.50 absolute profit — below £2.50 floor
    r = passes_decision_gates(
        profit_conservative=1.50,
        buy_cost=3.00,
        target_roi=0.30,
        min_profit_absolute=2.50,
    )
    assert r.passes is False
    assert r.reason == "profit_below_floor"
    assert r.roi == pytest.approx(0.50)


def test_fails_when_roi_below_target_even_with_high_absolute_profit():
    # £20 profit on £100 cost = 20% ROI, below 30% target
    r = passes_decision_gates(
        profit_conservative=20.00,
        buy_cost=100.00,
        target_roi=0.30,
        min_profit_absolute=2.50,
    )
    assert r.passes is False
    assert r.reason == "roi_below_target"
    assert r.roi == pytest.approx(0.20)


def test_profit_floor_takes_priority_over_roi_in_reason():
    """When both fail, profit_below_floor reported (more actionable)."""
    r = passes_decision_gates(
        profit_conservative=0.50,
        buy_cost=10.00,           # ROI = 5%, also fails
        target_roi=0.30,
        min_profit_absolute=2.50,
    )
    assert r.passes is False
    assert r.reason == "profit_below_floor"


def test_no_buy_cost_fails_with_specific_reason():
    r = passes_decision_gates(
        profit_conservative=5.00,
        buy_cost=None,
        target_roi=0.30,
        min_profit_absolute=2.50,
    )
    assert r.passes is False
    assert r.reason == "no_buy_cost"
    assert r.roi is None


def test_boundary_exactly_at_target_roi_passes():
    # Exactly 30% ROI should pass (>= not >)
    r = passes_decision_gates(
        profit_conservative=3.00,
        buy_cost=10.00,
        target_roi=0.30,
        min_profit_absolute=2.50,
    )
    assert r.passes is True


def test_boundary_exactly_at_min_profit_passes():
    # Exactly £2.50 profit should pass
    r = passes_decision_gates(
        profit_conservative=2.50,
        buy_cost=5.00,            # 50% ROI, well above target
        target_roi=0.30,
        min_profit_absolute=2.50,
    )
    assert r.passes is True


def test_negative_profit_fails():
    r = passes_decision_gates(
        profit_conservative=-1.00,
        buy_cost=10.00,
        target_roi=0.30,
        min_profit_absolute=2.50,
    )
    assert r.passes is False
    assert r.reason == "profit_below_floor"


def test_low_ticket_high_roi_correctly_rejected():
    """The exact case the absolute floor exists to catch.
    £4 cost item, 100% ROI = £4 profit. Sounds great, but:
      after FBA fees & handling time, not worth it. £2.50 floor catches it
      only if we set it higher; keep this as a documented behaviour test.
    """
    # With profit £1 and 50% ROI, fails the floor
    r = passes_decision_gates(
        profit_conservative=1.00,
        buy_cost=2.00,
        target_roi=0.30,
        min_profit_absolute=2.50,
    )
    assert r.passes is False
    assert r.reason == "profit_below_floor"


def test_high_ticket_low_roi_correctly_rejected():
    """The exact case the ROI gate exists to catch.
    £100 cost item, 10% ROI = £10 profit. Big absolute profit but poor capital efficiency.
    """
    r = passes_decision_gates(
        profit_conservative=10.00,
        buy_cost=100.00,
        target_roi=0.30,
        min_profit_absolute=2.50,
    )
    assert r.passes is False
    assert r.reason == "roi_below_target"
