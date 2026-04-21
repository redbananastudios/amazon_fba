import pytest
from sourcing_engine.pipeline.profit import calculate_profit
from sourcing_engine.pipeline.fees import calculate_fees_fba, calculate_fees_fbm
from sourcing_engine.config import MIN_PROFIT


def test_profit_uses_raw_conservative_not_floored():
    market_price = 20.00
    raw_conservative = 12.00
    floored_conservative = 15.00
    buy_cost = 8.00
    fees_current = calculate_fees_fba(market_price, "small_parcel")
    fees_conservative = calculate_fees_fba(raw_conservative, "small_parcel")
    result = calculate_profit(market_price, raw_conservative, fees_current, fees_conservative, buy_cost)
    expected = raw_conservative - fees_conservative["total"] - buy_cost
    assert result["profit_conservative"] == pytest.approx(expected)
    wrong = floored_conservative - fees_conservative["total"] - buy_cost
    if abs(expected - wrong) > 0.01:
        assert result["profit_conservative"] != pytest.approx(wrong)


def test_price_floor_hit_flag_set_correctly():
    from sourcing_engine.pipeline.conservative_price import calculate_conservative_price
    history = [(i, 5.00, 2) for i in range(90)]
    buy_cost = 8.00
    fees_conservative = calculate_fees_fba(5.00, "small_parcel")
    raw, floored, flag = calculate_conservative_price(history, 10.00, buy_cost, fees_conservative["total"])
    assert flag == "PRICE_FLOOR_HIT"
    assert raw == pytest.approx(5.00)
    assert floored > raw


def test_fbm_fee_path_no_fba_fee():
    fees = calculate_fees_fbm(20.00)
    assert fees["fba_fee"] == 0.0
    assert fees["storage_fee"] == 0.0
    assert fees["shipping"] > 0
    assert fees["packaging"] > 0


def test_fba_fee_path_no_shipping_cost():
    fees = calculate_fees_fba(20.00, "small_parcel")
    assert "shipping" not in fees or fees.get("shipping", 0) == 0
    assert "packaging" not in fees or fees.get("packaging", 0) == 0
    assert fees["fba_fee"] > 0


def test_case_match_uses_case_cost():
    case_cost_inc_vat = 24.00
    market_price = 30.00
    fees = calculate_fees_fba(market_price, "small_parcel")
    fees_cons = calculate_fees_fba(25.00, "small_parcel")
    result = calculate_profit(market_price, 25.00, fees, fees_cons, case_cost_inc_vat)
    assert result["profit_current"] == pytest.approx(market_price - fees["total"] - case_cost_inc_vat)


def test_unit_match_uses_unit_cost():
    unit_cost_inc_vat = 7.49
    market_price = 20.00
    fees = calculate_fees_fba(market_price, "small_parcel")
    fees_cons = calculate_fees_fba(15.00, "small_parcel")
    result = calculate_profit(market_price, 15.00, fees, fees_cons, unit_cost_inc_vat)
    assert result["profit_current"] == pytest.approx(market_price - fees["total"] - unit_cost_inc_vat)
