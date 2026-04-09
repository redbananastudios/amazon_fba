import pytest
import pandas as pd
from sourcing_engine.pipeline.normalise import resolve_buy_cost, normalise


def test_resolve_ex_vat_only():
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=10.00, cost_inc_vat=None)
    assert buy_cost == pytest.approx(12.00)
    assert flag is None


def test_resolve_inc_vat_only():
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=None, cost_inc_vat=12.00)
    assert buy_cost == pytest.approx(12.00)
    assert flag is None


def test_resolve_both_consistent():
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=10.00, cost_inc_vat=12.00)
    assert buy_cost == pytest.approx(12.00)
    assert flag is None


def test_resolve_both_within_tolerance():
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=10.00, cost_inc_vat=12.01)
    assert buy_cost == pytest.approx(12.01)
    assert flag is None


def test_resolve_both_conflict():
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=10.00, cost_inc_vat=13.00)
    assert buy_cost == pytest.approx(13.00)
    assert flag == "VAT_FIELD_MISMATCH"


def test_resolve_neither():
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=None, cost_inc_vat=None)
    assert buy_cost is None
    assert flag == "VAT_UNCLEAR"


def test_normalise_connect_beauty_row():
    """Connect Beauty row: unit price is ex-VAT, case size is plain integer."""
    raw = pd.DataFrame([{
        "part_code": "BOUFOUFAB110",
        "description": "Bourjois Always Fabulous 24H Foundation - 110 Light Vanilla",
        "pack_size": "12",
        "trade_price": " \u00a33.00 ",
        "case_price": " \u00a336.00 ",
        "barcode": "3614228413411",
        "brand": "Bourjois",
        "category": "Foundation",
        "comments": "432",
        "source_file": "price-list.csv",
        "supplier": "Price List",
    }])
    result = normalise(raw)
    assert len(result) == 1
    row = result.iloc[0]
    assert row["ean"] == "3614228413411"
    assert row["supplier_price_ex_vat"] == pytest.approx(3.00)
    assert row["supplier_price_inc_vat"] == pytest.approx(3.00 * 1.20)
    assert row["supplier_price_basis"] == "UNIT"
    assert row["case_qty"] == 12
    assert row["case_price_ex_vat"] == pytest.approx(36.00)
    assert row["brand"] == "Bourjois"
    assert row["category"] == "Foundation"


def test_normalise_connect_beauty_preorder_stock():
    """Pre-Order in Units Available maps to stock_status."""
    raw = pd.DataFrame([{
        "part_code": "O-BARVARNPSFOR",
        "description": "Barry M Nail Paint Silk Nail Polish - Forest",
        "pack_size": "110",
        "trade_price": " \u00a30.47 ",
        "case_price": " \u00a351.70 ",
        "barcode": "5019301038082",
        "brand": "Barry M",
        "category": "Nail Polish",
        "comments": "Pre-Order",
        "source_file": "price-list.csv",
        "supplier": "Price List",
    }])
    result = normalise(raw)
    assert len(result) == 1
    row = result.iloc[0]
    assert row["stock_status"] == "Pre-Order"
    assert row["case_qty"] == 110
    assert row["supplier_price_basis"] == "UNIT"
    assert row["supplier_price_ex_vat"] == pytest.approx(0.47)


def test_normalise_connect_beauty_unit_basis_for_case_size_gt_1():
    """When case_size > 1, price_basis should be UNIT (per-unit pricing)."""
    raw = pd.DataFrame([{
        "part_code": "TEST123",
        "description": "Test Product",
        "pack_size": "6",
        "trade_price": "5.00",
        "case_price": "30.00",
        "barcode": "5019301038082",
        "brand": "TestBrand",
        "source_file": "price-list.csv",
        "supplier": "Price List",
    }])
    result = normalise(raw)
    row = result.iloc[0]
    assert row["case_qty"] == 6
    assert row["supplier_price_basis"] == "UNIT"
    assert row["case_price_ex_vat"] == pytest.approx(30.00)
