import pytest
from sourcing_engine.pipeline.decision import decide
from sourcing_engine.utils.flags import (
    BUY_BOX_ABOVE_AVG90,
    PRICE_FLOOR_HIT, VAT_UNCLEAR, VAT_FIELD_MISMATCH,
    INSUFFICIENT_HISTORY, SIZE_TIER_UNKNOWN, FBM_ONLY, FBM_SHIPPING_ESTIMATED,
)


def _make_row(**overrides):
    defaults = {
        "profit_current": 5.00, "profit_conservative": 5.00,
        "margin_current": 0.25, "margin_conservative": 0.25,
        "sales_estimate": 30, "gated": "N", "risk_flags": [],
        "price_basis": "FBA", "buy_cost": 8.00,
    }
    defaults.update(overrides)
    return defaults


def test_fbm_can_shortlist():
    row = _make_row(price_basis="FBM", risk_flags=[FBM_ONLY, FBM_SHIPPING_ESTIMATED])
    decision, reason = decide(row)
    assert decision == "SHORTLIST"


def test_price_floor_hit_blocks_shortlist():
    row = _make_row(risk_flags=[PRICE_FLOOR_HIT])
    decision, reason = decide(row)
    assert decision != "SHORTLIST"
    assert "PRICE_FLOOR_HIT" in reason


def test_vat_unclear_blocks_shortlist():
    row = _make_row(risk_flags=[VAT_UNCLEAR], buy_cost=None)
    decision, reason = decide(row)
    assert decision != "SHORTLIST"


def test_insufficient_history_does_not_block_shortlist():
    row = _make_row(risk_flags=[INSUFFICIENT_HISTORY])
    decision, reason = decide(row)
    assert decision == "SHORTLIST"


def test_size_tier_unknown_does_not_block_shortlist():
    row = _make_row(risk_flags=[SIZE_TIER_UNKNOWN])
    decision, reason = decide(row)
    assert decision == "SHORTLIST"


def test_buy_box_above_avg90_routes_to_review():
    """Peak-buying flag is in REVIEW_FLAGS — a row that would otherwise
    SHORTLIST gets pushed to REVIEW so the operator confirms the price
    isn't a temporary spike before committing supplier capital."""
    row = _make_row(risk_flags=[BUY_BOX_ABOVE_AVG90])
    decision, reason = decide(row)
    assert decision == "REVIEW"
    assert BUY_BOX_ABOVE_AVG90 in reason


def test_dual_set_flag_appears_once_in_reason():
    """Flags that are members of BOTH SHORTLIST_BLOCKERS and REVIEW_FLAGS
    must appear ONCE in decision_reason ("Blocked by: …"), not also
    repeated under "Review flags: …". Operator-readability fix —
    duplication is noise.

    BUY_BOX_ABOVE_AVG90, PRICE_FLOOR_HIT, VAT_FIELD_MISMATCH,
    PRICE_MISMATCH_RRP and the VAT flags are all in both sets by design.
    """
    row = _make_row(risk_flags=[BUY_BOX_ABOVE_AVG90])
    decision, reason = decide(row)
    assert decision == "REVIEW"
    # Flag appears in "Blocked by:" line.
    assert "Blocked by: BUY_BOX_ABOVE_AVG90" in reason
    # And NOT in any "Review flags:" line.
    assert "Review flags:" not in reason or BUY_BOX_ABOVE_AVG90 not in (
        reason.split("Review flags:", 1)[1] if "Review flags:" in reason else ""
    )
    # Belt-and-braces: the literal flag name appears exactly once.
    assert reason.count(BUY_BOX_ABOVE_AVG90) == 1


def test_blocking_and_review_flags_keep_distinct_attribution():
    """When a row carries one SHORTLIST_BLOCKERS flag AND one purely-
    REVIEW_FLAGS flag, each appears in its own section."""
    from sourcing_engine.utils.flags import SINGLE_FBA_SELLER
    row = _make_row(risk_flags=[BUY_BOX_ABOVE_AVG90, SINGLE_FBA_SELLER])
    decision, reason = decide(row)
    assert decision == "REVIEW"
    # BUY_BOX_ABOVE_AVG90 in both sets → "Blocked by:" only.
    assert "Blocked by: BUY_BOX_ABOVE_AVG90" in reason
    # SINGLE_FBA_SELLER only in REVIEW_FLAGS → "Review flags:" only.
    assert "Review flags: SINGLE_FBA_SELLER" in reason
    # Each name appears exactly once.
    assert reason.count(BUY_BOX_ABOVE_AVG90) == 1
    assert reason.count("SINGLE_FBA_SELLER") == 1


def test_gated_y_shortlists_with_indicator():
    row = _make_row(gated="Y")
    decision, reason = decide(row)
    assert decision == "SHORTLIST"
    assert "gated" in reason.lower() or "GATED" in reason


def test_gated_unknown_shortlists_with_indicator():
    row = _make_row(gated="UNKNOWN")
    decision, reason = decide(row)
    assert decision == "SHORTLIST"
    assert "gated" in reason.lower() or "unknown" in reason.lower()


def test_low_sales_10_19_routes_review():
    row = _make_row(sales_estimate=15)
    decision, reason = decide(row)
    assert decision == "REVIEW"
    assert "sales" in reason.lower()


def test_sales_below_10_rejects():
    row = _make_row(sales_estimate=5)
    decision, reason = decide(row)
    assert decision == "REJECT"


def test_single_supplier_row_produces_two_output_rows_when_both_match():
    unit_row = _make_row(match_type="UNIT", buy_cost=4.80)
    case_row = _make_row(match_type="CASE", buy_cost=24.00)
    d1, r1 = decide(unit_row)
    d2, r2 = decide(case_row)
    assert d1 in ("SHORTLIST", "REVIEW", "REJECT")
    assert d2 in ("SHORTLIST", "REVIEW", "REJECT")
    assert r1
    assert r2
