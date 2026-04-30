"""Tests for fba_engine.steps.scoring.

Phase 3 of the legacy keepa_niche pipeline: scores each ASIN across
4 dimensions (Demand, Stability, Competition, Margin), computes a
weighted composite, derives lane scores (Cash Flow, Profit, Balanced),
classifies the row's Opportunity Lane, and assigns a Verdict.

Tests cover:
  - Each dimension's primary signal mapping (BSR -> demand, etc.)
  - Representative modifiers (positive + negative)
  - Composite weighting
  - Lane classification + Commercial Priority
  - Hard reject paths (price range, oversaturation, hazmat)
  - Verdict assignment for each verdict shape
"""
from __future__ import annotations

import pandas as pd
import pytest

from fba_engine.steps.scoring import (
    SCORING_COLUMNS,
    compute_scoring,
    run_step,
    score_competition,
    score_demand,
    score_margin,
    score_stability,
)


def _row(**overrides) -> dict:
    """A clean Phase-2-shape row that should land as YES with no modifiers
    firing. Tests override fields to exercise specific branches.
    """
    base = {
        "ASIN": "B0CLEAN",
        "Product Name": "Clean Widget",
        "Brand": "Acme",
        "BSR Current": "5000",
        "BSR Drops 90d": "20",
        "Bought per Month": "300",
        "Star Rating": "4.5",
        "Review Count": "200",
        "Buy Box 90d Avg": "GBP25.00",
        "Current Price": "GBP25.00",
        "Buy Box Amazon %": "5",
        "Price Drop % 90d": "0",
        "FBA Seller Count": "5",
        "FBA Seller 90d Avg": "5",
        "Brand 1P": "N",
        "Est ROI %": "38",
        "Est Profit": "GBP5.00",
        "Weight Flag": "OK",
        "Hazmat": "N",
        "Gated": "N",
        "Listing Quality": "Good",
        "Buy Box Is FBA": "Y",
        "PRICE CHECK": "N",
    }
    base.update(overrides)
    return base


# ────────────────────────────────────────────────────────────────────────
# Demand score
# ────────────────────────────────────────────────────────────────────────


class TestDemandScore:
    @pytest.mark.parametrize("bsr,expected_base", [
        ("5000", 10),
        ("15000", 9),
        ("25000", 8),
        ("35000", 7),
        ("45000", 6),
        ("55000", 5),
        ("70000", 3),
        ("100000", 1),
    ])
    def test_bsr_tier_mapping(self, bsr, expected_base):
        # Pure BSR mapping with all modifiers neutral. Caller-provided
        # row is normalised so review/rating/drops modifiers don't fire.
        score = score_demand(_row(
            **{"BSR Current": bsr, "BSR Drops 90d": "10",
               "Bought per Month": "150", "Star Rating": "4.0",
               "Review Count": "50"}
        ))
        assert score == expected_base

    def test_modifier_high_drops_adds_one(self):
        # 15+ drops in 90d -> +1. Use mid-BSR (25k = 8) so the +1
        # isn't masked by the cap.
        mid_bsr = {"BSR Current": "25000", "Bought per Month": "150"}
        base_score = score_demand(_row(**mid_bsr, **{"BSR Drops 90d": "10"}))
        boosted = score_demand(_row(**mid_bsr, **{"BSR Drops 90d": "20"}))
        assert boosted == base_score + 1

    def test_modifier_proven_product_adds_one(self):
        # 500+ reviews AND rating > 4.0 -> +1. Use mid-BSR so the cap
        # doesn't mask the modifier.
        mid_bsr = {"BSR Current": "25000", "Bought per Month": "150",
                   "BSR Drops 90d": "10"}
        base_score = score_demand(_row(
            **mid_bsr, **{"Review Count": "100", "Star Rating": "4.5"},
        ))
        boosted = score_demand(_row(
            **mid_bsr, **{"Review Count": "600", "Star Rating": "4.5"},
        ))
        assert boosted == base_score + 1

    def test_modifier_low_rating_subtracts_one(self):
        # Rating < 3.5 -> -1. Use mid-BSR so the cap doesn't mask.
        mid_bsr = {"BSR Current": "25000", "Bought per Month": "150",
                   "BSR Drops 90d": "10", "Review Count": "100"}
        base = score_demand(_row(**mid_bsr, **{"Star Rating": "4.5"}))
        penalised = score_demand(_row(**mid_bsr, **{"Star Rating": "3.0"}))
        assert penalised == base - 1

    def test_demand_caps_at_10(self):
        # Best-case modifiers stacked must not push above 10.
        score = score_demand(_row(
            **{"BSR Current": "5000", "BSR Drops 90d": "30",
               "Bought per Month": "500", "Star Rating": "4.8",
               "Review Count": "1000"}
        ))
        assert score == 10


# ────────────────────────────────────────────────────────────────────────
# Stability score
# ────────────────────────────────────────────────────────────────────────


class TestStabilityScore:
    @pytest.mark.parametrize("drop_pct,expected", [
        ("3", 10),    # +3% (rising) -> 10
        ("0", 10),    # flat -> 10
        ("-3", 8),    # -3% -> 8
        ("-7", 6),    # -7% -> 6
        # Below -10% the recovery/erosion modifier fires. With the
        # default fixture (current == avg90), the "still falling"
        # branch applies -1.
        ("-12", 3),
        ("-17", 1),
        ("-25", 0),   # PRICE EROSION; clamped to 0
    ])
    def test_drop_pct_tier(self, drop_pct, expected):
        score = score_stability(_row(**{"Price Drop % 90d": drop_pct}))
        assert score == expected

    def test_recovery_modifier_adds_two_when_current_above_avg90(self):
        # Steep historical drop but current price has recovered above
        # the 90-day average -> +2 (BUY THE DIP candidate).
        score = score_stability(_row(**{
            "Price Drop % 90d": "-15",
            "Current Price": "GBP30.00",
            "Buy Box 90d Avg": "GBP25.00",
        }))
        # Base tier for -15: 4. Modifier: +2. Result: 6.
        assert score == 6

    def test_price_check_flag_subtracts_one(self):
        base = score_stability(_row(**{"Price Drop % 90d": "0"}))
        flagged = score_stability(_row(
            **{"Price Drop % 90d": "0", "PRICE CHECK": "Y"}
        ))
        assert flagged == base - 1


# ────────────────────────────────────────────────────────────────────────
# Competition score
# ────────────────────────────────────────────────────────────────────────


class TestCompetitionScore:
    @pytest.mark.parametrize("sellers,expected_base", [
        ("2", 10), ("3", 9), ("5", 7), ("7", 5),
        ("10", 3), ("18", 1),
    ])
    def test_seller_count_tier(self, sellers, expected_base):
        # Use 1500/mo velocity so the dynamic ceiling is 20 — every
        # tier in the parametrize list survives that ceiling.
        score = score_competition(_row(**{
            "FBA Seller Count": sellers,
            "FBA Seller 90d Avg": sellers,
            "Bought per Month": "1500",
            "Buy Box Amazon %": "10",
        }))
        assert score == expected_base

    def test_dynamic_ceiling_kills_low_velocity_high_sellers(self):
        # Under 300/month, ceiling is 8 sellers. 10 sellers -> 0.
        score = score_competition(_row(**{
            "FBA Seller Count": "10", "FBA Seller 90d Avg": "10",
            "Bought per Month": "100", "Buy Box Amazon %": "10",
        }))
        assert score == 0

    def test_amazon_dominant_buy_box_penalises(self):
        # Amazon BB > 70% -> -3
        base = score_competition(_row(**{"Buy Box Amazon %": "10"}))
        penalised = score_competition(_row(**{"Buy Box Amazon %": "75"}))
        assert penalised == base - 3

    def test_brand_1p_penalises(self):
        # Brand 1P=Y -> -2
        base = score_competition(_row(**{"Brand 1P": "N"}))
        penalised = score_competition(_row(**{"Brand 1P": "Y"}))
        assert penalised == base - 2


# ────────────────────────────────────────────────────────────────────────
# Margin score
# ────────────────────────────────────────────────────────────────────────


class TestMarginScore:
    @pytest.mark.parametrize("roi,expected_base", [
        ("45", 10), ("38", 9), ("32", 7),
        ("27", 5), ("22", 3), ("15", 1),
    ])
    def test_roi_tier(self, roi, expected_base):
        score = score_margin(_row(**{
            "Est ROI %": roi, "Est Profit": "GBP5.00", "Weight Flag": "OK",
        }))
        assert score == expected_base

    def test_high_profit_bonus(self):
        base = score_margin(_row(**{"Est Profit": "GBP5.00"}))
        bonus = score_margin(_row(**{"Est Profit": "GBP10.00"}))
        assert bonus == base + 1

    def test_low_profit_penalty(self):
        base = score_margin(_row(**{"Est Profit": "GBP5.00"}))
        penalised = score_margin(_row(**{"Est Profit": "GBP2.00"}))
        assert penalised == base - 1

    def test_heavy_oversize_penalty(self):
        base = score_margin(_row(**{"Weight Flag": "OK"}))
        heavy = score_margin(_row(**{"Weight Flag": "HEAVY"}))
        assert heavy == base - 1


# ────────────────────────────────────────────────────────────────────────
# compute_scoring (full pipeline)
# ────────────────────────────────────────────────────────────────────────


class TestComputeScoring:
    def test_appends_scoring_columns(self):
        df = pd.DataFrame([_row()])
        out = compute_scoring(df)
        for col in SCORING_COLUMNS:
            assert col in out.columns, f"missing {col}"

    def test_clean_row_lands_yes(self):
        # Clean fixture (BSR 5k, 5 sellers, ROI 38%, all flags clean)
        # should land YES.
        df = pd.DataFrame([_row()])
        out = compute_scoring(df)
        assert out.iloc[0]["Verdict"] == "YES"

    def test_hazmat_short_circuits_to_hazmat(self):
        df = pd.DataFrame([_row(**{"Hazmat": "Y"})])
        out = compute_scoring(df)
        assert out.iloc[0]["Verdict"] == "HAZMAT"

    def test_oversaturated_rejects(self):
        df = pd.DataFrame([_row(**{"FBA Seller Count": "25"})])
        out = compute_scoring(df)
        assert out.iloc[0]["Verdict"] == "NO"
        assert "Oversaturated" in out.iloc[0]["Verdict Reason"]

    def test_price_outside_range_rejects(self):
        df = pd.DataFrame([_row(**{"Current Price": "GBP10.00"})])
        out = compute_scoring(df)
        assert out.iloc[0]["Verdict"] == "NO"
        assert "Price Range" in out.iloc[0]["Verdict Reason"]

    def test_price_erosion_verdict(self):
        # Stability score = 0 -> PRICE EROSION
        df = pd.DataFrame([_row(**{"Price Drop % 90d": "-30"})])
        out = compute_scoring(df)
        assert out.iloc[0]["Verdict"] == "PRICE EROSION"

    def test_gated_keeps_in_file_with_gated_verdict(self):
        df = pd.DataFrame([_row(**{"Gated": "Y"})])
        out = compute_scoring(df)
        # Gated wins over composite-based verdicts (per skill ordering).
        assert out.iloc[0]["Verdict"] == "GATED"

    def test_brand_approach_verdict_when_2_3_sellers_and_weak_listing(self):
        df = pd.DataFrame([_row(**{
            "FBA Seller Count": "2",
            "FBA Seller 90d Avg": "2",
            "Listing Quality": "WEAK",
            # Use marginal scores so neither YES nor MAYBE fires first.
            "BSR Current": "60000",
            "Est ROI %": "22",
        })])
        out = compute_scoring(df)
        assert out.iloc[0]["Verdict"] == "BRAND APPROACH"

    def test_maybe_roi_when_composite_5_to_7_and_roi_below_20(self):
        # MAYBE-ROI fires when composite is in the 5-7 band AND ROI is
        # below 20. Strong-composite low-ROI rows still win MAYBE
        # (matches legacy phase3_scoring ordering).
        df = pd.DataFrame([_row(**{
            "BSR Current": "55000",   # demand tier 5
            "Bought per Month": "100",
            "BSR Drops 90d": "5",
            "Est ROI %": "15",        # margin tier 1
            "Star Rating": "4.0",
            "Review Count": "100",
        })])
        out = compute_scoring(df)
        # Composite expected: 5*0.3 + 10*0.3 + 7*0.2 + 1*0.2 = 5.9.
        # ROI < 20 -> MAYBE-ROI.
        assert out.iloc[0]["Verdict"] == "MAYBE-ROI"

    def test_strong_composite_with_low_roi_still_lands_maybe(self):
        # A row with strong demand+stability+competition but low ROI
        # still gets MAYBE — not MAYBE-ROI. Matches legacy ordering.
        df = pd.DataFrame([_row(**{
            "Est ROI %": "15", "BSR Current": "8000",
        })])
        out = compute_scoring(df)
        # Composite is high enough (>= 7) that MAYBE wins.
        assert out.iloc[0]["Verdict"] == "MAYBE"

    def test_brand_1p_dominant_rejects(self):
        # Brand 1P=Y AND Amazon BB > 60% -> NO (Brand 1P dominant).
        df = pd.DataFrame([_row(**{
            "Brand 1P": "Y", "Buy Box Amazon %": "70",
        })])
        out = compute_scoring(df)
        assert out.iloc[0]["Verdict"] == "NO"
        assert "Brand 1P" in out.iloc[0]["Verdict Reason"]

    def test_verdict_reason_populated_for_every_row(self):
        df = pd.DataFrame([_row(), _row(**{"Hazmat": "Y"})])
        out = compute_scoring(df)
        for reason in out["Verdict Reason"]:
            assert reason  # non-empty

    def test_composite_score_weighted_correctly(self):
        # Pin the 30/30/20/20 weighting: with d=10 s=10 c=10 m=10 the
        # composite should be 10.0; with d=10 s=10 c=0 m=0 it should
        # be (10*0.3 + 10*0.3 + 0 + 0) = 6.0.
        # Use a clean YES row first to read the components, then verify.
        df = pd.DataFrame([_row()])
        out = compute_scoring(df)
        composite = out.iloc[0]["Composite Score"]
        d = out.iloc[0]["Demand Score"]
        s = out.iloc[0]["Stability Score"]
        c = out.iloc[0]["Competition Score"]
        m = out.iloc[0]["Margin Score"]
        expected = round(d * 0.30 + s * 0.30 + c * 0.20 + m * 0.20, 1)
        assert composite == expected


# ────────────────────────────────────────────────────────────────────────
# Lane classification
# ────────────────────────────────────────────────────────────────────────


class TestLaneClassification:
    def test_balanced_lane_assigned_for_clean_row(self):
        df = pd.DataFrame([_row()])  # 300/mo, 38% ROI, £5 profit, 5 sellers
        out = compute_scoring(df)
        assert out.iloc[0]["Opportunity Lane"] == "BALANCED"
        assert out.iloc[0]["Commercial Priority"] == 1

    def test_profit_lane_when_high_profit_low_volume(self):
        # >£8 profit, lower velocity -> PROFIT lane (priority 2).
        df = pd.DataFrame([_row(**{
            "Est Profit": "GBP10.00",
            "Bought per Month": "100",
            "Est ROI %": "30",
        })])
        out = compute_scoring(df)
        assert out.iloc[0]["Opportunity Lane"] == "PROFIT"
        assert out.iloc[0]["Commercial Priority"] == 2

    def test_cash_flow_lane_when_high_volume_thin_margin(self):
        # 250+/mo, ROI >= 10%, profit >= £1.50, NOT meeting profit criteria.
        df = pd.DataFrame([_row(**{
            "Bought per Month": "300",
            "Est ROI %": "12",
            "Est Profit": "GBP2.00",
        })])
        out = compute_scoring(df)
        assert out.iloc[0]["Opportunity Lane"] == "CASH FLOW"
        assert out.iloc[0]["Commercial Priority"] == 3

    def test_unclassified_when_no_lane_matches(self):
        # Low volume, low margin -> unclassified (priority 9).
        df = pd.DataFrame([_row(**{
            "Bought per Month": "30",
            "Est ROI %": "8",
            "Est Profit": "GBP1.20",
        })])
        out = compute_scoring(df)
        assert out.iloc[0]["Commercial Priority"] == 9

    def test_no_lane_for_disqualified_rows(self):
        # NO / PRICE EROSION / HAZMAT verdicts get no lane.
        df = pd.DataFrame([_row(**{"Hazmat": "Y"})])
        out = compute_scoring(df)
        assert out.iloc[0]["Commercial Priority"] == 9

    def test_monthly_gross_profit_calculated(self):
        df = pd.DataFrame([_row(**{
            "Bought per Month": "200", "Est Profit": "GBP4.50",
        })])
        out = compute_scoring(df)
        assert out.iloc[0]["Monthly Gross Profit"] == pytest.approx(900.0)


# ────────────────────────────────────────────────────────────────────────
# Price compression
# ────────────────────────────────────────────────────────────────────────


class TestPriceCompression:
    def test_compressed_when_current_below_80_pct_of_avg(self):
        df = pd.DataFrame([_row(**{
            "Current Price": "GBP20.00", "Buy Box 90d Avg": "GBP30.00",
        })])
        out = compute_scoring(df)
        assert out.iloc[0]["Price Compression"] == "COMPRESSED"

    def test_squeezed_when_85_pct(self):
        df = pd.DataFrame([_row(**{
            "Current Price": "GBP25.50", "Buy Box 90d Avg": "GBP30.00",
        })])
        out = compute_scoring(df)
        assert out.iloc[0]["Price Compression"] == "SQUEEZED"

    def test_ok_when_at_avg(self):
        df = pd.DataFrame([_row()])  # current == avg
        out = compute_scoring(df)
        assert out.iloc[0]["Price Compression"] == "OK"


# ────────────────────────────────────────────────────────────────────────
# run_step
# ────────────────────────────────────────────────────────────────────────


class TestRunStep:
    def test_run_step_appends_columns(self):
        df = pd.DataFrame([_row()])
        out = run_step(df, {})
        assert "Verdict" in out.columns
        assert "Composite Score" in out.columns

    def test_run_step_empty_returns_empty_with_columns(self):
        df = pd.DataFrame()
        out = run_step(df, {})
        assert out.empty
        for col in SCORING_COLUMNS:
            assert col in out.columns


# ────────────────────────────────────────────────────────────────────────
# Schema constant
# ────────────────────────────────────────────────────────────────────────


class TestColumnsConstant:
    def test_scoring_columns_are_subset_of_final_headers(self):
        # Pin that everything scoring writes lives in build_output's
        # FINAL_HEADERS — otherwise the next stage drops the columns.
        from fba_engine.steps.build_output import FINAL_HEADERS
        for col in SCORING_COLUMNS:
            assert col in FINAL_HEADERS, (
                f"scoring writes '{col}' but build_output's "
                f"FINAL_HEADERS doesn't include it — column would be "
                f"dropped at the next stage."
            )
