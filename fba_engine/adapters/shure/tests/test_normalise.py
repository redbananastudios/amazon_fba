import pytest
import pandas as pd
from normalise import resolve_buy_cost, normalise


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


def test_normalise_abgee_row():
    raw = pd.DataFrame([{
        "part_code": "285 E7876",
        "description": "Avengers Titan Hero Black Panther",
        "pack_size": "EA",
        "trade_price": "6.24",
        "retail_price": "9.99",
        "carton_size": "4",
        "barcode": "5010996214669",
        "source_file": "Hasbro.pdf",
        "supplier": "Hasbro",
    }])
    result = normalise(raw)
    row = result.iloc[0]
    assert row["ean"] == "5010996214669"
    assert row["supplier_price_ex_vat"] == pytest.approx(6.24)
    assert row["supplier_price_inc_vat"] == pytest.approx(6.24 * 1.20)
    assert row["rrp_inc_vat"] == pytest.approx(9.99)
    assert row["supplier_price_basis"] == "UNIT"
    assert row["case_qty"] == 1
