import pytest
from sourcing_engine.pipeline.case_detection import derive_costs, detect_price_basis
from sourcing_engine.utils.flags import PRICE_BASIS_AMBIGUOUS, CASE_QTY_UNKNOWN, CASE_MATCH_SKIPPED


def test_explicit_case_price_column_detected():
    result = derive_costs(supplier_price_ex_vat=24.00, supplier_price_basis="CASE", case_qty=6, rrp_inc_vat=9.99)
    assert result["case_cost_ex_vat"] == pytest.approx(24.00)
    assert result["unit_cost_ex_vat"] == pytest.approx(4.00)
    assert result["case_cost_inc_vat"] == pytest.approx(24.00 * 1.20)
    assert result["unit_cost_inc_vat"] == pytest.approx(4.00 * 1.20)


def test_explicit_unit_price_column_detected():
    result = derive_costs(supplier_price_ex_vat=6.24, supplier_price_basis="UNIT", case_qty=1, rrp_inc_vat=9.99)
    assert result["unit_cost_ex_vat"] == pytest.approx(6.24)
    assert result["unit_cost_inc_vat"] == pytest.approx(6.24 * 1.20)
    assert result["case_cost_ex_vat"] is None
    assert result["case_cost_inc_vat"] is None


def test_implied_price_below_threshold_flagged_as_case():
    basis = detect_price_basis(supplier_price_ex_vat=2.00, case_qty=12, rrp_inc_vat=None, column_hint=None)
    assert basis == "CASE"


def test_ambiguous_routes_to_review():
    result = derive_costs(supplier_price_ex_vat=5.00, supplier_price_basis="AMBIGUOUS", case_qty=6, rrp_inc_vat=None)
    assert result["unit_cost_ex_vat"] is None
    assert result["case_cost_ex_vat"] is None
    assert PRICE_BASIS_AMBIGUOUS in result["flags"]


def test_case_qty_null_treated_as_unit():
    result = derive_costs(supplier_price_ex_vat=5.00, supplier_price_basis="UNIT", case_qty=None, rrp_inc_vat=None)
    assert result["unit_cost_ex_vat"] == pytest.approx(5.00)
    assert result["case_cost_ex_vat"] is None
    assert CASE_QTY_UNKNOWN in result["flags"]


def test_case_qty_zero_treated_as_one():
    result = derive_costs(supplier_price_ex_vat=5.00, supplier_price_basis="UNIT", case_qty=0, rrp_inc_vat=None)
    assert result["unit_cost_ex_vat"] == pytest.approx(5.00)
    assert result["case_cost_ex_vat"] is None


def test_case_qty_1_no_duplicate_row():
    result = derive_costs(supplier_price_ex_vat=5.00, supplier_price_basis="UNIT", case_qty=1, rrp_inc_vat=None)
    assert result["unit_cost_ex_vat"] == pytest.approx(5.00)
    assert result["case_cost_ex_vat"] is None
    assert result["case_cost_inc_vat"] is None
