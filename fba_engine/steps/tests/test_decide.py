"""Tests for fba_engine.steps.decide.

Stage 05 of the canonical engine: takes the calculate step's output
and applies the SHORTLIST/REVIEW/REJECT decision rules to each match
row. Pre-decided rows (REJECT from resolve, "No valid market price"
REJECT from calculate) flow through unchanged.

Note: this is the SUPPLIER_PRICELIST decide layer, distinct from
``fba_engine.steps.decision_engine`` which does the keepa_niche
BUY/NEGOTIATE/WATCH/KILL verdicts. Different verdict shape, different
input columns.
"""
from __future__ import annotations

import pandas as pd

from fba_engine.steps.decide import decide_rows, run_step


def _calculated_row(**overrides) -> dict:
    """A calculate-output row, ready for decide."""
    base = {
        "supplier": "test", "supplier_sku": "SKU-A",
        "ean": "5012345678900", "asin": "B0CLEAN001",
        "match_type": "UNIT", "buy_cost": 5.0,
        "market_price": 14.5, "price_basis": "FBA",
        "fees_current": 4.0, "fees_conservative": 4.5,
        "raw_conservative_price": 13.0,
        "floored_conservative_price": 13.0,
        "profit_current": 5.5, "profit_conservative": 4.0,
        "margin_current": 0.38, "margin_conservative": 0.31,
        "roi_current": 1.10, "roi_conservative": 0.80,
        "breakeven_price": 9.5, "capital_exposure": 5.0,
        "sales_estimate": 200, "gated": "N",
        "risk_flags": [],
    }
    base.update(overrides)
    return base


def _reject_row(**overrides) -> dict:
    base = {
        "supplier": "test", "supplier_sku": "SKU-X",
        "decision": "REJECT", "decision_reason": "Invalid or missing EAN",
        "risk_flags": [],
    }
    base.update(overrides)
    return base


class TestDecideRows:
    def test_match_row_gets_a_decision(self):
        df = pd.DataFrame([_calculated_row()])
        out = decide_rows(df)
        assert "decision" in out.columns
        assert out.iloc[0]["decision"] in {"SHORTLIST", "REVIEW", "REJECT"}
        assert out.iloc[0]["decision_reason"]

    def test_pre_rejected_row_passes_through(self):
        df = pd.DataFrame([_reject_row()])
        out = decide_rows(df)
        assert out.iloc[0]["decision"] == "REJECT"
        assert out.iloc[0]["decision_reason"] == "Invalid or missing EAN"

    def test_low_sales_estimate_gives_reject(self):
        df = pd.DataFrame([_calculated_row(sales_estimate=1)])
        out = decide_rows(df)
        assert out.iloc[0]["decision"] == "REJECT"

    def test_profitable_high_sales_gets_shortlist(self):
        # Healthy ROI + sales — should clear all gates.
        df = pd.DataFrame([_calculated_row(
            roi_current=0.35, roi_conservative=0.32,
            profit_current=5.0, profit_conservative=4.5,
            sales_estimate=200, gated="N",
        )])
        out = decide_rows(df)
        assert out.iloc[0]["decision"] == "SHORTLIST"

    def test_mixed_input_count_preserved(self):
        df = pd.DataFrame([
            _calculated_row(),
            _reject_row(),
            _calculated_row(supplier_sku="SKU-B", sales_estimate=1),
        ])
        out = decide_rows(df)
        assert len(out) == 3


class TestRunStep:
    def test_run_step_basic(self):
        df = pd.DataFrame([_calculated_row()])
        out = run_step(df, {})
        assert out.iloc[0]["decision"]

    def test_run_step_empty(self):
        out = run_step(pd.DataFrame(), {})
        assert out.empty
