"""Buy plan — verdict-driven order-list rollup (08_buy_plan).

Pure transformation: reads existing columns produced by upstream
steps and combines them into eleven new columns that turn a
validated row into a line-of-a-purchase-order.

Inputs (already on the row by the time this runs):
    opportunity_verdict           BUY / SOURCE_ONLY / NEGOTIATE / WATCH / KILL
    opportunity_confidence        HIGH / MEDIUM / LOW
    opportunity_blockers          list[str]   (informational, not consumed)
    risk_flags                    list[str]   (history-derived flags drive dampener)
    predicted_velocity_low/mid/high
    predicted_velocity_share_source
    raw_conservative_price        GBP
    fees_conservative             GBP
    profit_conservative           per-unit GBP, may be None
    buy_cost                      GBP, may be None or 0
    moq                           int, may be None

Outputs (eleven new fields, returned as a flat dict):
    order_qty_recommended         int | None
    capital_required              GBP | None
    projected_30d_units           int | None
    projected_30d_revenue         GBP | None
    projected_30d_profit          GBP | None
    payback_days                  float | None
    target_buy_cost_buy           GBP | None
    target_buy_cost_stretch       GBP | None
    gap_to_buy_gbp                GBP | None
    gap_to_buy_pct                float | None
    buy_plan_status               OK | INSUFFICIENT_VELOCITY | INSUFFICIENT_DATA
                                  | NO_BUY_COST | BLOCKED_BY_VERDICT
                                  | UNECONOMIC_AT_ANY_PRICE

The step is verdict-driven (see PRD §5.4). It does NOT make decisions —
the verdict is already decided upstream; this step computes the buy-list
view of that verdict.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from fba_config_loader import (
    BuyPlan,
    OpportunityValidation,
    get_buy_plan,
    get_opportunity_validation,
)
from sourcing_engine.opportunity import (
    VERDICT_BUY,
    VERDICT_KILL,
    VERDICT_NEGOTIATE,
    VERDICT_SOURCE_ONLY,
    VERDICT_WATCH,
)


# ────────────────────────────────────────────────────────────────────────
# Output column contract — reused by step wrapper + writers.
# ────────────────────────────────────────────────────────────────────────


BUY_PLAN_COLUMNS: tuple[str, ...] = (
    "order_qty_recommended",
    "capital_required",
    "projected_30d_units",
    "projected_30d_revenue",
    "projected_30d_profit",
    "payback_days",
    "target_buy_cost_buy",
    "target_buy_cost_stretch",
    "gap_to_buy_gbp",
    "gap_to_buy_pct",
    "buy_plan_status",
)


# Status values, surfaced via buy_plan_status.
STATUS_OK = "OK"
STATUS_INSUFFICIENT_VELOCITY = "INSUFFICIENT_VELOCITY"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
STATUS_NO_BUY_COST = "NO_BUY_COST"
STATUS_BLOCKED_BY_VERDICT = "BLOCKED_BY_VERDICT"
STATUS_UNECONOMIC_AT_ANY_PRICE = "UNECONOMIC_AT_ANY_PRICE"


# ────────────────────────────────────────────────────────────────────────
# Coercion helpers — NaN / string-typed-numeric safe.
# ────────────────────────────────────────────────────────────────────────


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and value != value:   # NaN
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _num(value: Any) -> Optional[float]:
    """Coerce a value to float. None / NaN / non-numeric → None."""
    if not _is_present(value):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:
        return None
    return n


def _flags_set(row: dict) -> set[str]:
    raw = row.get("risk_flags")
    if isinstance(raw, list):
        return {str(f) for f in raw if f}
    if isinstance(raw, str):
        return {f.strip() for f in raw.replace(",", ";").split(";") if f.strip()}
    return set()


def _empty_result(status: str) -> dict:
    """Empty-output skeleton — every numeric blank, status set."""
    return {
        "order_qty_recommended": None,
        "capital_required": None,
        "projected_30d_units": None,
        "projected_30d_revenue": None,
        "projected_30d_profit": None,
        "payback_days": None,
        "target_buy_cost_buy": None,
        "target_buy_cost_stretch": None,
        "gap_to_buy_gbp": None,
        "gap_to_buy_pct": None,
        "buy_plan_status": status,
    }


# ────────────────────────────────────────────────────────────────────────
# Pure helpers (PRD §5).
# ────────────────────────────────────────────────────────────────────────


def _compute_risk_factor(
    opportunity_confidence: Any,
    flags: set[str],
    config: BuyPlan,
) -> float:
    """Multiply dampeners; floor at config.risk_floor.

    Each dampener is in (0, 1] so the product can only shrink mid.
    Five history-derived flags can compound; the floor (default 0.5)
    prevents runaway over-dampening on a row that legitimately fires
    several flags at once.
    """
    factor = 1.0
    conf = str(opportunity_confidence or "").upper().strip()
    if conf == "LOW":
        factor *= config.risk_low_confidence
    elif conf == "MEDIUM":
        factor *= config.risk_medium_confidence
    if "INSUFFICIENT_HISTORY" in flags:
        factor *= config.risk_insufficient_history
    if "LISTING_TOO_NEW" in flags:
        factor *= config.risk_listing_too_new
    if "COMPETITION_GROWING" in flags:
        factor *= config.risk_competition_growing
    if "BSR_DECLINING" in flags:
        factor *= config.risk_bsr_declining
    if "PRICE_UNSTABLE" in flags:
        factor *= config.risk_price_unstable
    return max(factor, config.risk_floor)


def _compute_target_buy_costs(
    raw_cp: Optional[float],
    fees_cons: Optional[float],
    config: BuyPlan,
    ov: OpportunityValidation,
) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Compute (target_buy, target_stretch, status_override).

    ``status_override`` is ``UNECONOMIC_AT_ANY_PRICE`` when even a free
    supplier wouldn't satisfy the absolute profit gate, else None.
    Returns ``(None, None, None)`` when inputs aren't sufficient (caller
    decides whether that's INSUFFICIENT_DATA).
    """
    if raw_cp is None or fees_cons is None or raw_cp <= 0:
        return None, None, None

    gross_after_fees = raw_cp - fees_cons

    if gross_after_fees <= ov.min_profit_absolute_buy:
        # Even a free supplier would leave profit below the absolute
        # floor — this listing is structurally unprofitable.
        return None, None, STATUS_UNECONOMIC_AT_ANY_PRICE

    # Buy ceiling: satisfy BOTH gates (ROI ≥ min_roi_buy AND profit ≥
    # min_profit_absolute_buy). Take the lower (more conservative) cap.
    roi_ceiling = gross_after_fees / (1.0 + ov.min_roi_buy)
    abs_ceiling = gross_after_fees - ov.min_profit_absolute_buy
    target_buy = round(min(roi_ceiling, abs_ceiling), 2)

    # Stretch — the price the operator should aim for in negotiation,
    # not the ceiling. Multiplier on both gates so the relationship
    # buy_target ≥ stretch_target holds across a wide range of prices.
    stretch_roi = ov.min_roi_buy * config.stretch_roi_multiplier
    roi_stretch = gross_after_fees / (1.0 + stretch_roi)
    abs_stretch = (
        gross_after_fees
        - (ov.min_profit_absolute_buy * config.stretch_roi_multiplier)
    )
    target_stretch = round(min(roi_stretch, abs_stretch), 2)

    # On thin-margin listings (gross < min_profit_absolute_buy ×
    # stretch_multiplier — default £3.75), abs_stretch goes negative.
    # A negative ceiling makes no economic sense — surface as None
    # so the operator sees "no positive stretch exists" not "£-1.24".
    # Defensive: stretch also must never exceed buy ceiling (algebra
    # guarantees this, but pin it).
    if target_stretch is not None and target_stretch <= 0:
        target_stretch = None
    elif target_stretch > target_buy:
        target_stretch = target_buy

    return target_buy, target_stretch, None


def _compute_sizing(
    projected_units: int,
    buy_cost: Optional[float],
    moq: Optional[float],
    config: BuyPlan,
    order_mode: str,
) -> Optional[dict]:
    """Order-quantity / capital / payback for a BUY row.

    Returns a dict ``{order_qty, capital_required, payback_days}`` or
    None when sizing can't be computed (no buy_cost / no velocity).
    The caller decides whether None → INSUFFICIENT_DATA or
    INSUFFICIENT_VELOCITY based on which input was missing.
    """
    if buy_cost is None or buy_cost <= 0:
        return None
    if projected_units <= 0:
        return None

    days_of_cover = (
        config.first_order_days if order_mode == "first" else config.reorder_days
    )

    order_qty_raw = math.ceil(projected_units * days_of_cover / 30)
    order_qty = order_qty_raw

    # First-order cap — unit-based (was £-based pre-PR-78 review).
    # Caps the test order regardless of buy_cost so the operator's
    # safety net is "how many units am I committing to" rather than
    # "how much cash" (which moved with cost-of-goods drift). Reorders
    # aren't capped — at that point the operator has already validated
    # sell-through.
    if order_mode == "first":
        order_qty = min(order_qty, config.max_first_order_units)

    # min_test_qty floor wins even when the cap would bring us below
    # it. Loader invariant pins max_first_order_units >= min_test_qty
    # so this only fires when raw velocity-driven qty was even smaller.
    order_qty = max(order_qty, config.min_test_qty)

    # MOQ — supplier-imposed lower bound. PRD §7.5 — MOQ wins even
    # when it busts the capital cap; HIGH_MOQ flag (separate, upstream)
    # already surfaces this to the operator. Use ceil to round UP on
    # fractional MOQs (a supplier saying "20.5 minimum" means 21, not
    # 20 — we need to satisfy their floor, not undercut it).
    if moq is not None and moq > 0:
        order_qty = max(order_qty, math.ceil(moq))

    capital_required = round(order_qty * buy_cost, 2)
    payback_days = round(order_qty / projected_units * 30, 1)

    return {
        "order_qty": int(order_qty),
        "capital_required": capital_required,
        "payback_days": payback_days,
    }


def _compute_projections(
    projected_units: int,
    raw_cp: Optional[float],
    profit_cons: Optional[float],
    target_buy: Optional[float],
    fees_cons: Optional[float],
    verdict: str,
) -> dict:
    """30d revenue / profit projections.

    For BUY / NEGOTIATE / WATCH: profit projected at ``profit_conservative``
    (real expected profit at current cost, when known).

    For SOURCE_ONLY: profit projected at ``target_buy_cost_buy``
    (best-case profit if the operator hits the supplier-negotiation
    ceiling). PRD §5.5.

    Revenue is independent of buy_cost — useful for SOURCE_ONLY ranking.
    """
    revenue = None
    profit = None

    if projected_units > 0 and raw_cp is not None and raw_cp > 0:
        revenue = round(projected_units * raw_cp, 2)

    if projected_units > 0:
        if verdict == VERDICT_SOURCE_ONLY:
            # Best-case profit at the negotiation ceiling.
            if target_buy is not None and raw_cp is not None and fees_cons is not None:
                per_unit = raw_cp - fees_cons - target_buy
                if per_unit > 0:
                    profit = round(projected_units * per_unit, 2)
        else:
            # BUY / NEGOTIATE / WATCH — real per-unit profit when known.
            if profit_cons is not None:
                profit = round(projected_units * profit_cons, 2)

    return {
        "projected_30d_revenue": revenue,
        "projected_30d_profit": profit,
    }


def _compute_gap(
    buy_cost: Optional[float],
    target_buy: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """Gap between current buy_cost and the BUY ceiling.

    Positive = supplier is over the ceiling, needs to come down.
    Returns (None, None) when either input is missing.
    """
    if buy_cost is None or target_buy is None or buy_cost <= 0:
        return None, None
    gap_gbp = round(buy_cost - target_buy, 2)
    gap_pct = round(gap_gbp / buy_cost, 4)
    return gap_gbp, gap_pct


# ────────────────────────────────────────────────────────────────────────
# Public entry point.
# ────────────────────────────────────────────────────────────────────────


def compute_buy_plan(
    row: dict,
    config: BuyPlan | None = None,
    opportunity_validation: OpportunityValidation | None = None,
    order_mode: str = "first",
) -> dict:
    """Compute the eleven buy-plan fields for one row.

    Pure function. Reads from `row` via `.get()` only — never mutates.
    Returns a flat dict with the 11 new columns; caller merges into the row.
    Missing fields never raise — they degrade to a status value.

    Args:
        row: validated row dict (post-validate_opportunity).
        config: BuyPlan thresholds (defaults to ``get_buy_plan()``).
        opportunity_validation: OV thresholds (defaults to
            ``get_opportunity_validation()``).
        order_mode: ``"first"`` (default) or ``"reorder"``. First-order
            uses tighter days-of-cover + capital cap; reorder uses
            longer cover and no cap.
    """
    cfg = config or get_buy_plan()
    ov = opportunity_validation or get_opportunity_validation()

    verdict = str(row.get("opportunity_verdict") or "").upper().strip()

    # Verdict precedence (matches PRD §5.4):
    #   KILL                       → all blank
    #   WATCH                      → projections + targets, no sizing
    #   SOURCE_ONLY                → projections + targets, no sizing
    #   NEGOTIATE                  → projections + targets + gap, no sizing
    #   BUY                        → everything

    if verdict == VERDICT_KILL:
        return _empty_result(STATUS_BLOCKED_BY_VERDICT)

    raw_cp = _num(row.get("raw_conservative_price"))
    fees_cons = _num(row.get("fees_conservative"))
    profit_cons = _num(row.get("profit_conservative"))
    buy_cost = _num(row.get("buy_cost"))
    moq = _num(row.get("moq"))
    mid_velocity = _num(row.get("predicted_velocity_mid"))

    flags = _flags_set(row)
    risk_factor = _compute_risk_factor(
        row.get("opportunity_confidence"), flags, cfg,
    )

    # Projected_30d_units always populated when mid is — independent
    # of verdict (so WATCH / SOURCE_ONLY rows carry it).
    projected_units: Optional[int] = None
    if mid_velocity is not None and mid_velocity > 0:
        projected_units = max(0, int(round(mid_velocity * risk_factor)))

    # Target buy costs — populated whenever inputs allow.
    target_buy, target_stretch, target_status_override = (
        _compute_target_buy_costs(raw_cp, fees_cons, cfg, ov)
    )

    # Projections (revenue + profit) are computed off projected_units.
    projections = (
        _compute_projections(
            projected_units, raw_cp, profit_cons,
            target_buy, fees_cons, verdict,
        )
        if projected_units is not None and projected_units > 0
        else {"projected_30d_revenue": None, "projected_30d_profit": None}
    )

    # ── Verdict-driven population matrix (PRD §5.4) ───────────────────
    if verdict == VERDICT_WATCH:
        # Projections + targets stay populated so WATCH stays
        # re-evaluable. Sizing + gap blank. UNECONOMIC override
        # surfaces structural unprofitability that the operator
        # would otherwise miss (the BLOCKED_BY_VERDICT label looks
        # the same as a viable WATCH).
        return {
            "order_qty_recommended": None,
            "capital_required": None,
            "projected_30d_units": projected_units,
            "projected_30d_revenue": projections["projected_30d_revenue"],
            "projected_30d_profit": projections["projected_30d_profit"],
            "payback_days": None,
            "target_buy_cost_buy": target_buy,
            "target_buy_cost_stretch": target_stretch,
            "gap_to_buy_gbp": None,
            "gap_to_buy_pct": None,
            "buy_plan_status": (
                target_status_override
                if target_status_override == STATUS_UNECONOMIC_AT_ANY_PRICE
                else STATUS_BLOCKED_BY_VERDICT
            ),
        }

    if verdict == VERDICT_SOURCE_ONLY:
        # Targets always populated (no buy_cost needed). Sizing blank
        # because we don't know the cost yet. Status documents why.
        status = (
            target_status_override
            if target_status_override is not None
            else STATUS_NO_BUY_COST
        )
        return {
            "order_qty_recommended": None,
            "capital_required": None,
            "projected_30d_units": projected_units,
            "projected_30d_revenue": projections["projected_30d_revenue"],
            "projected_30d_profit": projections["projected_30d_profit"],
            "payback_days": None,
            "target_buy_cost_buy": target_buy,
            "target_buy_cost_stretch": target_stretch,
            "gap_to_buy_gbp": None,
            "gap_to_buy_pct": None,
            "buy_plan_status": status,
        }

    if verdict == VERDICT_NEGOTIATE:
        gap_gbp, gap_pct = _compute_gap(buy_cost, target_buy)
        # Per PRD §7.4 — UNECONOMIC override applies even on NEGOTIATE
        # (the listing is structurally unprofitable, the operator
        # shouldn't waste effort negotiating).
        if target_status_override == STATUS_UNECONOMIC_AT_ANY_PRICE:
            status = STATUS_UNECONOMIC_AT_ANY_PRICE
        else:
            status = STATUS_OK
        return {
            "order_qty_recommended": None,
            "capital_required": None,
            "projected_30d_units": projected_units,
            "projected_30d_revenue": projections["projected_30d_revenue"],
            "projected_30d_profit": projections["projected_30d_profit"],
            "payback_days": None,
            "target_buy_cost_buy": target_buy,
            "target_buy_cost_stretch": target_stretch,
            "gap_to_buy_gbp": gap_gbp,
            "gap_to_buy_pct": gap_pct,
            "buy_plan_status": status,
        }

    if verdict == VERDICT_BUY:
        # BUY needs valid buy_cost AND velocity to size. Either missing
        # → degrade to a status value, leave sizing blank.
        if buy_cost is None or buy_cost <= 0:
            # Defensive — BUY shouldn't reach here without buy_cost,
            # but a malformed upstream row could.
            return {
                "order_qty_recommended": None,
                "capital_required": None,
                "projected_30d_units": projected_units,
                "projected_30d_revenue": projections["projected_30d_revenue"],
                "projected_30d_profit": projections["projected_30d_profit"],
                "payback_days": None,
                "target_buy_cost_buy": target_buy,
                "target_buy_cost_stretch": target_stretch,
                "gap_to_buy_gbp": None,
                "gap_to_buy_pct": None,
                "buy_plan_status": STATUS_INSUFFICIENT_DATA,
            }

        if projected_units is None or projected_units <= 0:
            return {
                "order_qty_recommended": None,
                "capital_required": None,
                "projected_30d_units": projected_units,
                "projected_30d_revenue": projections["projected_30d_revenue"],
                "projected_30d_profit": projections["projected_30d_profit"],
                "payback_days": None,
                "target_buy_cost_buy": target_buy,
                "target_buy_cost_stretch": target_stretch,
                "gap_to_buy_gbp": None,
                "gap_to_buy_pct": None,
                "buy_plan_status": STATUS_INSUFFICIENT_VELOCITY,
            }

        sizing = _compute_sizing(
            projected_units, buy_cost, moq, cfg, order_mode,
        )
        if sizing is None:
            # Should not reach — guards above cover it. Belt-and-braces.
            return {
                "order_qty_recommended": None,
                "capital_required": None,
                "projected_30d_units": projected_units,
                "projected_30d_revenue": projections["projected_30d_revenue"],
                "projected_30d_profit": projections["projected_30d_profit"],
                "payback_days": None,
                "target_buy_cost_buy": target_buy,
                "target_buy_cost_stretch": target_stretch,
                "gap_to_buy_gbp": None,
                "gap_to_buy_pct": None,
                "buy_plan_status": STATUS_INSUFFICIENT_DATA,
            }

        # UNECONOMIC override applies even on a successful sizing —
        # if validate_opportunity emitted BUY despite gross_after_fees
        # being below the absolute floor, the operator should still
        # see the structural-unprofitability flag. Defensive: shouldn't
        # reach here in practice, but the "fail soft, never crash"
        # mandate covers malformed upstream rows.
        status = (
            STATUS_UNECONOMIC_AT_ANY_PRICE
            if target_status_override == STATUS_UNECONOMIC_AT_ANY_PRICE
            else STATUS_OK
        )
        return {
            "order_qty_recommended": sizing["order_qty"],
            "capital_required": sizing["capital_required"],
            "projected_30d_units": projected_units,
            "projected_30d_revenue": projections["projected_30d_revenue"],
            "projected_30d_profit": projections["projected_30d_profit"],
            "payback_days": sizing["payback_days"],
            "target_buy_cost_buy": target_buy,
            "target_buy_cost_stretch": target_stretch,
            "gap_to_buy_gbp": None,
            "gap_to_buy_pct": None,
            "buy_plan_status": status,
        }

    # Unknown verdict (defensive — validate_opportunity guarantees a
    # known one). Treat as BLOCKED_BY_VERDICT, blank everything.
    return _empty_result(STATUS_BLOCKED_BY_VERDICT)
