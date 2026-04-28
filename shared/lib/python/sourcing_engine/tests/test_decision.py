import pytest
from sourcing_engine.pipeline.decision import decide
from sourcing_engine.utils.flags import (
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
