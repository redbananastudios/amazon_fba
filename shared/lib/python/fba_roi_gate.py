"""
FBA Engine — ROI-based decision gate.

Replaces the previous MIN_MARGIN gate. ROI (profit / buy_cost) is the truer
measure of capital efficiency for a reseller — margin (profit / sell_price)
varies inversely with sell price for the same capital deployed.

This module is pure: no I/O, no global state. All inputs are explicit.

The legacy gate was:
    profit_conservative >= MIN_PROFIT and margin_conservative >= MIN_MARGIN

The new gate is:
    profit_conservative >= MIN_PROFIT_ABSOLUTE and roi_conservative >= TARGET_ROI

The MIN_PROFIT_ABSOLUTE floor handles low-ticket items where high ROI
multiplied by tiny capital still produces too-small absolute profit to be
worth handling. Both gates must pass.
"""
from __future__ import annotations

from dataclasses import dataclass


def calculate_roi(profit: float, buy_cost: float) -> float | None:
    """
    ROI = profit / buy_cost.

    Returns None if buy_cost is missing or zero — caller must handle this
    explicitly rather than getting a misleading zero or infinity.
    """
    if buy_cost is None or buy_cost <= 0:
        return None
    return profit / buy_cost


@dataclass(frozen=True)
class GateResult:
    """Outcome of evaluating ROI gate against a row's profit numbers."""
    passes: bool
    reason: str  # "passes", "profit_below_floor", "roi_below_target", "no_buy_cost"
    roi: float | None


def passes_decision_gates(
    profit_conservative: float,
    buy_cost: float | None,
    target_roi: float,
    min_profit_absolute: float,
) -> GateResult:
    """
    Apply the two-gate filter (absolute profit floor + ROI target).

    Both gates must pass. Returns the first failure reason if any fail.

    Args:
        profit_conservative: profit at raw_conservative_price, after fees
        buy_cost: landed cost per unit (the relevant cost for this match_type)
        target_roi: e.g. 0.30 for 30%
        min_profit_absolute: e.g. 2.50 for £2.50

    Returns:
        GateResult with pass/fail, reason, and computed ROI for output.
    """
    roi = calculate_roi(profit_conservative, buy_cost)

    if roi is None:
        return GateResult(passes=False, reason="no_buy_cost", roi=None)

    if profit_conservative < min_profit_absolute:
        return GateResult(passes=False, reason="profit_below_floor", roi=roi)

    if roi < target_roi:
        return GateResult(passes=False, reason="roi_below_target", roi=roi)

    return GateResult(passes=True, reason="passes", roi=roi)


__all__ = ["calculate_roi", "GateResult", "passes_decision_gates"]
