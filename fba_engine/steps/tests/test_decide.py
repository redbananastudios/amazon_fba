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


class TestDecideOverrides:
    """Per-call threshold overrides (no_rank_hidden_gem strategy etc.).

    Default thresholds (from shared/config/decision_thresholds.yaml):
        min_sales_review:    10
        min_sales_shortlist: 20
        min_profit:          2.50
        target_roi:          0.30
    """

    def test_no_overrides_preserves_default_behaviour(self):
        # Sales = 8 — below min_sales_review (10) → REJECT under defaults.
        df = pd.DataFrame([_calculated_row(sales_estimate=8)])
        out = run_step(df, {})
        assert out.iloc[0]["decision"] == "REJECT"

    def test_override_min_sales_review_lets_low_sales_clear(self):
        """Lowering min_sales_review from 10 → 2 lets a sales=5 row past
        the REJECT gate. Sales=5 still trips min_sales_shortlist (20),
        so the row goes to REVIEW — not SHORTLIST. Proves the override
        is applied AND the unmoved threshold still gates."""
        df = pd.DataFrame([_calculated_row(sales_estimate=5)])
        out = run_step(df, {"overrides": {"min_sales_review": 2}})
        assert out.iloc[0]["decision"] == "REVIEW"
        assert "below shortlist threshold 20" in out.iloc[0]["decision_reason"].lower() \
            or "sales 5/month" in out.iloc[0]["decision_reason"].lower()

    def test_override_min_sales_shortlist_lets_otherwise_qualifying_row_shortlist(self):
        """no_rank_hidden_gem use case — drop min_sales_shortlist to 5
        so a sales=8 row with healthy ROI gets SHORTLIST instead of REVIEW."""
        df = pd.DataFrame([_calculated_row(
            sales_estimate=8,
            roi_conservative=0.40, profit_conservative=4.0,
            gated="N",
        )])
        out = run_step(df, {"overrides": {
            "min_sales_review": 2,
            "min_sales_shortlist": 5,
        }})
        assert out.iloc[0]["decision"] == "SHORTLIST"

    def test_override_target_roi_changes_gate(self):
        """Tightening target_roi from 30% → 60% kicks an otherwise-passing
        row out of SHORTLIST. The ROI gate computes from profit/buy_cost
        directly (not the row's roi_conservative field), so we set
        buy_cost=10 + profit_conservative=3.5 → real ROI 35%."""
        df = pd.DataFrame([_calculated_row(
            buy_cost=10.0,
            profit_conservative=3.5,    # 3.5 / 10 = 35% — clears 30%, fails 60%
            roi_conservative=0.35,       # mirrored for completeness; not load-bearing
            sales_estimate=200, gated="N",
        )])
        baseline = run_step(df.copy(), {})
        assert baseline.iloc[0]["decision"] == "SHORTLIST"
        tightened = run_step(df.copy(), {"overrides": {"target_roi": 0.60}})
        assert tightened.iloc[0]["decision"] == "REVIEW"
        assert "below target 60%" in tightened.iloc[0]["decision_reason"]

    def test_override_min_profit_changes_reject_threshold(self):
        df = pd.DataFrame([_calculated_row(
            profit_current=1.0, profit_conservative=1.0,
            sales_estimate=200,
        )])
        # Default min_profit = 2.50 → 1.0 < 2.50 → REJECT.
        baseline = run_step(df.copy(), {})
        assert baseline.iloc[0]["decision"] == "REJECT"
        # Override to 0.50 — row is now profitable enough to pass the floor.
        # (It still won't SHORTLIST due to ROI, but it shouldn't REJECT.)
        loosened = run_step(df.copy(), {"overrides": {"min_profit": 0.50}})
        assert loosened.iloc[0]["decision"] != "REJECT" or \
            "Unprofitable" not in loosened.iloc[0]["decision_reason"]

    def test_unknown_override_key_raises_value_error(self):
        df = pd.DataFrame([_calculated_row()])
        import pytest
        with pytest.raises(ValueError, match="unknown key"):
            run_step(df, {"overrides": {"min_sales_typo": 5}})

    def test_invariant_violation_raises(self):
        """Override that pushes review above shortlist must fail loud."""
        df = pd.DataFrame([_calculated_row()])
        import pytest
        with pytest.raises(ValueError, match="cannot exceed"):
            run_step(df, {"overrides": {
                "min_sales_review": 50, "min_sales_shortlist": 10,
            }})

    def test_min_profit_absolute_alias_works(self):
        """min_profit_absolute is an alias for min_profit (matches the
        decision_thresholds.yaml key name + the loader's typed accessor)."""
        df = pd.DataFrame([_calculated_row(
            profit_current=1.0, profit_conservative=1.0,
            sales_estimate=200,
        )])
        out = run_step(df, {"overrides": {"min_profit_absolute": 0.50}})
        assert out.iloc[0]["decision"] != "REJECT" or \
            "Unprofitable" not in out.iloc[0]["decision_reason"]

    def test_empty_overrides_dict_is_no_op(self):
        df = pd.DataFrame([_calculated_row(sales_estimate=8)])
        out = run_step(df, {"overrides": {}})
        # sales=8 below default min_sales_review=10 → REJECT, same as no overrides.
        assert out.iloc[0]["decision"] == "REJECT"
