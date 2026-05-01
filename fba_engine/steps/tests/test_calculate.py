"""Tests for fba_engine.steps.calculate.

Stage 04 of the canonical engine: takes resolved match rows and applies
the math layer (price_basis selection, fees, conservative price, profit,
capital exposure, risk-flag accumulation).

Match rows acquire numeric fields here. REJECT rows from resolve flow
through untouched.
"""
from __future__ import annotations

import pandas as pd

from fba_engine.steps.calculate import calculate_economics, run_step


def _match_row(**overrides) -> dict:
    """A resolve-output match row (one or more FBA sellers, valid prices)."""
    base = {
        "supplier": "test", "supplier_sku": "SKU-A",
        "ean": "5012345678900", "asin": "B0CLEAN001",
        "match_type": "UNIT", "case_qty": 1, "moq": 1,
        "buy_cost": 5.0, "rrp_inc_vat": 19.99,
        "supplier_price_basis": "UNIT",
        "buy_box_price": 15.0, "amazon_price": 14.0,
        "new_fba_price": 14.5, "amazon_status": "OFF_LISTING",
        "fba_seller_count": 5, "sales_estimate": 150,
        "size_tier": "STANDARD", "fba_pick_pack_fee": 3.0,
        "referral_fee_pct": 15.0, "gated": "UNKNOWN",
        "price_history": None, "history_days": None,
        "risk_flags": [],
    }
    base.update(overrides)
    return base


def _reject_row(**overrides) -> dict:
    base = {
        "supplier": "test", "supplier_sku": "SKU-X",
        "ean": "bad", "match_type": "UNIT",
        "decision": "REJECT", "decision_reason": "Invalid or missing EAN",
        "risk_flags": [],
    }
    base.update(overrides)
    return base


class TestCalculateEconomics:
    def test_match_row_acquires_market_price(self):
        df = pd.DataFrame([_match_row()])
        out = calculate_economics(df)
        assert "market_price" in out.columns
        # market_price = min(buy_box, fba_price) when fba_seller_count > 0
        assert out.iloc[0]["market_price"] == 14.5

    def test_match_row_acquires_profit_fields(self):
        df = pd.DataFrame([_match_row()])
        out = calculate_economics(df)
        for col in (
            "price_basis", "fees_current", "fees_conservative",
            "raw_conservative_price", "floored_conservative_price",
            "capital_exposure",
        ):
            assert col in out.columns

    def test_zero_fba_seller_count_gives_fbm_basis(self):
        df = pd.DataFrame([_match_row(fba_seller_count=0)])
        out = calculate_economics(df)
        assert out.iloc[0]["price_basis"] == "FBM"

    def test_no_market_price_emits_reject_for_that_row(self):
        df = pd.DataFrame([_match_row(
            buy_box_price=None, new_fba_price=None, fba_seller_count=1,
        )])
        out = calculate_economics(df)
        assert out.iloc[0]["decision"] == "REJECT"
        assert out.iloc[0]["decision_reason"] == "No valid market price"

    def test_reject_rows_pass_through_unchanged(self):
        df = pd.DataFrame([_reject_row()])
        out = calculate_economics(df)
        assert len(out) == 1
        assert out.iloc[0]["decision"] == "REJECT"
        # Did NOT acquire calc-only fields.
        assert "fees_current" not in out.columns or pd.isna(out.iloc[0].get("fees_current"))

    def test_capital_exposure_uses_moq(self):
        df = pd.DataFrame([_match_row(moq=10, buy_cost=5.0)])
        out = calculate_economics(df)
        assert out.iloc[0]["capital_exposure"] == 50.0

    def test_mixed_input_produces_expected_count(self):
        df = pd.DataFrame([_match_row(), _reject_row(), _match_row(supplier_sku="SKU-B")])
        out = calculate_economics(df)
        assert len(out) == 3


class TestRunStep:
    def test_run_step_basic(self):
        df = pd.DataFrame([_match_row()])
        out = run_step(df, {})
        assert "market_price" in out.columns

    def test_run_step_empty(self):
        df = pd.DataFrame()
        out = run_step(df, {})
        assert out.empty

    def test_run_step_default_omits_stability_score(self):
        """Backwards compat — strategies that don't request the score
        keep the same output schema as before."""
        df = pd.DataFrame([_match_row()])
        out = run_step(df, {})
        assert "stability_score" not in out.columns

    def test_run_step_compute_stability_score_appends_column(self):
        df = pd.DataFrame([_match_row(
            delta_buy_box_30d_pct=5.0, delta_buy_box_90d_pct=5.0,
        )])
        out = run_step(df, {"compute_stability_score": True})
        assert "stability_score" in out.columns
        # 1 - (5 + 5) / 200 = 0.95
        assert abs(out.iloc[0]["stability_score"] - 0.95) < 1e-9


class TestStabilityScore:
    """Tests for the add_stability_score helper directly."""

    def test_zero_deltas_score_one(self):
        from fba_engine.steps.calculate import add_stability_score
        df = pd.DataFrame([{
            "delta_buy_box_30d_pct": 0.0,
            "delta_buy_box_90d_pct": 0.0,
        }])
        out = add_stability_score(df)
        assert out.iloc[0]["stability_score"] == 1.0

    def test_max_volatility_clamped_to_zero(self):
        from fba_engine.steps.calculate import add_stability_score
        df = pd.DataFrame([{
            "delta_buy_box_30d_pct": -150.0,
            "delta_buy_box_90d_pct": 200.0,
        }])
        out = add_stability_score(df)
        # Raw formula gives a negative; clamped to 0.0.
        assert out.iloc[0]["stability_score"] == 0.0

    def test_negative_deltas_use_absolute_value(self):
        """Buy Box dropping 10% is just as volatile as rising 10%."""
        from fba_engine.steps.calculate import add_stability_score
        df_drop = pd.DataFrame([{
            "delta_buy_box_30d_pct": -10.0, "delta_buy_box_90d_pct": -10.0,
        }])
        df_rise = pd.DataFrame([{
            "delta_buy_box_30d_pct":  10.0, "delta_buy_box_90d_pct":  10.0,
        }])
        out_drop = add_stability_score(df_drop)
        out_rise = add_stability_score(df_rise)
        assert out_drop.iloc[0]["stability_score"] == out_rise.iloc[0]["stability_score"]

    def test_missing_columns_default_to_max_stability(self):
        """Defensive: rows without delta columns get max stability,
        not a KeyError."""
        from fba_engine.steps.calculate import add_stability_score
        df = pd.DataFrame([{"asin": "B0X"}])
        out = add_stability_score(df)
        assert out.iloc[0]["stability_score"] == 1.0

    def test_empty_df_returns_empty_with_column_present(self):
        from fba_engine.steps.calculate import add_stability_score
        df = pd.DataFrame()
        out = add_stability_score(df)
        assert out.empty
        assert "stability_score" in out.columns

    def test_preserves_other_columns(self):
        """Adding stability_score doesn't drop or rename any existing column."""
        from fba_engine.steps.calculate import add_stability_score
        df = pd.DataFrame([{
            "asin": "B0X",
            "decision": "SHORTLIST",
            "delta_buy_box_30d_pct": 2.0,
            "delta_buy_box_90d_pct": 3.0,
        }])
        out = add_stability_score(df)
        assert set(out.columns) == {
            "asin", "decision", "delta_buy_box_30d_pct",
            "delta_buy_box_90d_pct", "stability_score",
        }
        assert out.iloc[0]["asin"] == "B0X"
        assert out.iloc[0]["decision"] == "SHORTLIST"
