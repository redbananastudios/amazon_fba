"""Tests for fba_engine.steps.resolve.

The resolve step is stage 02 of the canonical engine. It takes a
normalised supplier DataFrame (output of supplier_pricelist_discover)
plus a Keepa market data CSV path and:

  - Validates each row's EAN
  - Matches against market_data (multi-match: produces 0/1/2 rows per input)
  - Emits a flat DataFrame: one row per match + one REJECT row per invalid
    EAN / no-match (so no row is silently dropped — every input is
    accounted for in the output, matching the legacy `run_pipeline` shape)

These tests pin the boundary contract; deeper behaviour is in
``test_integration_pipeline.py``.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fba_engine.steps.resolve import resolve_matches, run_step


_KEEPA_CSV = (
    'ASIN,Title,Brand,Buy Box: Current,Amazon: Current,'
    'New Offer Count: Current,Sales Rank: Current,Bought in past month,'
    'Buy Box: 90 days avg.,"New, 3rd Party FBA: Current",'
    'FBA Pick&Pack Fee,Referral Fee %,Product Codes: EAN,'
    'Reviews: Rating,Reviews: Rating Count\n'
    'B0CLEAN001,Profitable Widget,Acme,£15.00,£14.00,5,5000,150,'
    '£14.50,£14.80,£3.00,15%,5012345678900,4.5,200\n'
)


def _write_keepa(tmp_path: Path) -> Path:
    p = tmp_path / "keepa.csv"
    p.write_text(_KEEPA_CSV, encoding="utf-8")
    return p


def _input_row(**overrides) -> dict:
    """A normalised-with-costs row, ready for resolve."""
    base = {
        "supplier": "test-supplier",
        "source_file": "x.csv",
        "supplier_sku": "SKU-A",
        "ean": "5012345678900",
        "case_ean": None,
        "product_name": "Local widget name",
        "supplier_price_basis": "UNIT",
        "case_qty": 1,
        "supplier_price_ex_vat": 5.0,
        "unit_cost_ex_vat": 5.0,
        "unit_cost_inc_vat": 6.0,
        "case_cost_ex_vat": 5.0,
        "case_cost_inc_vat": 6.0,
        "rrp_inc_vat": 19.99,
        "moq": 1,
        "brand": "Acme",
        "risk_flags": [],
    }
    base.update(overrides)
    return base


class TestResolveMatches:
    def test_match_emits_one_row(self, tmp_path):
        df = pd.DataFrame([_input_row()])
        out = resolve_matches(df, market_data_path=str(_write_keepa(tmp_path)))
        assert isinstance(out, pd.DataFrame)
        assert len(out) == 1
        assert out.iloc[0]["asin"] == "B0CLEAN001"
        # decision should NOT be set yet — that's a later stage.
        assert "decision" not in out.columns or pd.isna(out.iloc[0].get("decision"))

    def test_invalid_ean_emits_reject_row(self, tmp_path):
        df = pd.DataFrame([_input_row(ean="not-an-ean", supplier_sku="SKU-X")])
        out = resolve_matches(df, market_data_path=str(_write_keepa(tmp_path)))
        assert len(out) == 1
        assert out.iloc[0]["decision"] == "REJECT"
        assert out.iloc[0]["decision_reason"] == "Invalid or missing EAN"

    def test_no_match_emits_reject_row(self, tmp_path):
        # A valid-checksum EAN that doesn't appear in the Keepa CSV.
        df = pd.DataFrame([_input_row(ean="5099999999995", supplier_sku="SKU-Y")])
        out = resolve_matches(df, market_data_path=str(_write_keepa(tmp_path)))
        assert len(out) == 1
        assert out.iloc[0]["decision"] == "REJECT"
        assert out.iloc[0]["decision_reason"] == "No Amazon match found"

    def test_multi_input_keeps_one_row_per_input_when_unit_match(self, tmp_path):
        # 2 inputs, one matches, one is rejected for invalid EAN.
        df = pd.DataFrame([
            _input_row(supplier_sku="SKU-A", ean="5012345678900"),
            _input_row(supplier_sku="SKU-B", ean="bad"),
        ])
        out = resolve_matches(df, market_data_path=str(_write_keepa(tmp_path)))
        assert len(out) == 2
        assert set(out["supplier_sku"]) == {"SKU-A", "SKU-B"}

    def test_match_row_carries_buy_cost(self, tmp_path):
        df = pd.DataFrame([_input_row()])
        out = resolve_matches(df, market_data_path=str(_write_keepa(tmp_path)))
        assert out.iloc[0]["buy_cost"] == pytest.approx(6.0)

    def test_market_data_path_none_means_empty_market(self, tmp_path):
        # When no market data is provided, every input should be a no-match
        # REJECT (matches the load_market_data(None) -> {} contract).
        df = pd.DataFrame([_input_row()])
        out = resolve_matches(df, market_data_path=None)
        assert len(out) == 1
        assert out.iloc[0]["decision"] == "REJECT"
        assert out.iloc[0]["decision_reason"] == "No Amazon match found"

    def test_preloaded_market_data_kwarg_takes_precedence_over_path(self):
        # Pin the contract main.py relies on: when both `market_data`
        # and `market_data_path` are passed, the pre-loaded dict wins
        # and the path is NOT read. Pointing path at /does/not/exist
        # would normally be loaded, fail, and return {}; here it must
        # not even be touched.
        market = {
            "5012345678900": {
                "asin": "B0FROMDICT", "title": "From Dict",
                "buy_box_price": 15.0, "amazon_price": 14.0,
                "fba_seller_count": 3, "monthly_sales_estimate": 100,
                "size_tier": "STANDARD", "gated": "N",
            }
        }
        df = pd.DataFrame([_input_row()])
        out = resolve_matches(
            df,
            market_data_path="/this/does/not/exist.csv",
            market_data=market,
        )
        assert len(out) == 1
        assert out.iloc[0]["asin"] == "B0FROMDICT"


class TestRunStep:
    def test_run_step_uses_market_data_path(self, tmp_path):
        df = pd.DataFrame([_input_row()])
        out = run_step(df, {"market_data_path": str(_write_keepa(tmp_path))})
        assert len(out) == 1
        assert out.iloc[0]["asin"] == "B0CLEAN001"

    def test_run_step_passes_through_when_market_data_path_omitted(self):
        # Omitted market_data_path is allowed — produces empty market.
        df = pd.DataFrame([_input_row()])
        out = run_step(df, {})
        assert len(out) == 1
        assert out.iloc[0]["decision"] == "REJECT"

    def test_run_step_empty_input_returns_empty(self):
        df = pd.DataFrame()
        out = run_step(df, {})
        assert out.empty
