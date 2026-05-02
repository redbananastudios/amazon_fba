"""Tests for fba_engine.steps.validate_opportunity.

HANDOFF: Final Opportunity Validation. Verifies the deterministic
verdict logic (BUY / SOURCE_ONLY / NEGOTIATE / WATCH / KILL) +
opportunity_score + next_action assignment.

Pure-function semantics: each test exercises ``validate_opportunity``
on a hand-built row dict; no I/O. The DataFrame wrapper
``add_opportunity_verdict`` is also exercised end-to-end.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fba_engine.steps.validate_opportunity import (
    OPPORTUNITY_COLUMNS,
    add_opportunity_verdict,
    run_step,
)
from sourcing_engine.opportunity import (
    NEXT_ACTIONS,
    VERDICT_BUY,
    VERDICT_KILL,
    VERDICT_NEGOTIATE,
    VERDICT_SOURCE_ONLY,
    VERDICT_WATCH,
    VERDICTS,
    predict_seller_velocity,
    validate_opportunity,
)


# ────────────────────────────────────────────────────────────────────────
# Row builders
# ────────────────────────────────────────────────────────────────────────


def _shortlist_row(**overrides) -> dict:
    """A clean SHORTLIST row that should land BUY against the canonical config."""
    base = {
        "decision": "SHORTLIST",
        "candidate_score": 85,
        "candidate_band": "STRONG",
        "data_confidence": "HIGH",
        "sales_estimate": 250,
        "roi_conservative": 0.50,
        "profit_conservative": 8.0,
        "profit_current": 9.0,
        "raw_conservative_price": 15.0,
        "market_price": 16.0,
        "fees_conservative": 4.5,
        "fees_current": 4.5,
        "buy_cost": 4.0,
        "fba_seller_count": 4,
        "total_offer_count": 5,
        "amazon_on_listing": "N",
        "amazon_bb_pct_90": 0.10,
        "buy_box_oos_pct_90": 0.05,
        "price_volatility_90d": 0.10,
        "bsr_slope_90d": -0.05,
        "fba_offer_count_90d_joiners": 1,
        "restriction_status": "UNRESTRICTED",
        "fba_eligible": True,
        "gated": "N",
        "risk_flags": [],
    }
    base.update(overrides)
    return base


def _keepa_niche_row(**overrides) -> dict:
    """A keepa_niche row with no buy_cost (wholesale-discovery convention)."""
    base = _shortlist_row(
        buy_cost=0.0,
        decision="REVIEW",            # wholesale flow lands REVIEW with max_buy_price
        candidate_score=80,
        candidate_band="STRONG",
        data_confidence="MEDIUM",
        source_type="keepa_finder",
    )
    base.update(overrides)
    return base


# ────────────────────────────────────────────────────────────────────────
# Verdict-by-verdict
# ────────────────────────────────────────────────────────────────────────


class TestKill:
    def test_reject_becomes_kill(self):
        row = _shortlist_row(decision="REJECT")
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_KILL
        assert any("decision=REJECT" in r for r in out["opportunity_blockers"])

    def test_negative_profit_becomes_kill(self):
        row = _shortlist_row(profit_conservative=-1.5)
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_KILL
        assert any("profit_conservative" in r for r in out["opportunity_blockers"])

    def test_low_roi_becomes_kill(self):
        # roi 0.10 is below kill_min_roi 0.15.
        row = _shortlist_row(roi_conservative=0.10)
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_KILL

    def test_low_sales_becomes_kill(self):
        row = _shortlist_row(sales_estimate=10)
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_KILL

    def test_restricted_becomes_kill(self):
        row = _shortlist_row(restriction_status="RESTRICTED")
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_KILL
        assert any("RESTRICTED" in r for r in out["opportunity_blockers"])

    def test_fba_ineligible_becomes_kill(self):
        row = _shortlist_row(fba_eligible=False)
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_KILL
        assert any("fba_eligible" in r for r in out["opportunity_blockers"])

    def test_amazon_dominance_above_kill_threshold(self):
        # 0.92 ≥ kill_amazon_bb_share = 0.90.
        row = _shortlist_row(amazon_bb_pct_90=0.92)
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_KILL

    def test_severe_volatility_becomes_kill(self):
        # 0.45 ≥ kill_price_volatility = 0.40.
        row = _shortlist_row(price_volatility_90d=0.45)
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_KILL

    def test_severe_bsr_decline_becomes_kill(self):
        row = _shortlist_row(bsr_slope_90d=0.15)   # > 0.10 kill threshold
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_KILL

    def test_price_floor_hit_flag_becomes_kill(self):
        row = _shortlist_row(risk_flags=["PRICE_FLOOR_HIT"])
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_KILL


class TestBuy:
    def test_high_confidence_shortlist_becomes_buy(self):
        out = validate_opportunity(_shortlist_row())
        assert out["opportunity_verdict"] == VERDICT_BUY
        assert out["opportunity_blockers"] == []
        assert out["next_action"] == NEXT_ACTIONS[VERDICT_BUY]

    def test_amazon_dominance_blocks_buy(self):
        # 0.40 > max_amazon_bb_share_buy 0.30 — too much Amazon presence.
        # Not severe (< 0.90 kill); routes to WATCH.
        row = _shortlist_row(amazon_bb_pct_90=0.40)
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] != VERDICT_BUY
        assert any("amazon_bb_share" in b for b in out["opportunity_blockers"])

    def test_price_instability_blocks_buy(self):
        # 0.25 > max_price_volatility_buy 0.20 — too volatile.
        row = _shortlist_row(price_volatility_90d=0.25)
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] != VERDICT_BUY
        assert any("price_volatility" in b for b in out["opportunity_blockers"])

    def test_medium_confidence_blocks_buy(self):
        # min_data_confidence_buy is HIGH — MEDIUM routes to WATCH.
        row = _shortlist_row(data_confidence="MEDIUM")
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] != VERDICT_BUY

    def test_gated_blocks_buy_when_disallowed(self):
        # allow_gated_buy=false (default) — gated=Y blocks BUY.
        row = _shortlist_row(gated="Y")
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] != VERDICT_BUY
        assert any("gated" in b.lower() for b in out["opportunity_blockers"])

    def test_low_sales_blocks_buy(self):
        # 80 < target_monthly_sales 100 (and ≥ kill_min_sales 20, so not KILL).
        row = _shortlist_row(sales_estimate=80)
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] != VERDICT_BUY


class TestSourceOnly:
    def test_strong_keepa_without_buy_cost_becomes_source_only(self):
        row = _keepa_niche_row()
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_SOURCE_ONLY
        assert out["next_action"] == NEXT_ACTIONS[VERDICT_SOURCE_ONLY]

    def test_weak_demand_keepa_falls_through_to_watch(self):
        # Same shape as keepa-niche but sales below source_only_min_sales.
        row = _keepa_niche_row(sales_estimate=80)
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] != VERDICT_SOURCE_ONLY

    def test_amazon_owns_keepa_blocks_source_only(self):
        # Even with no buy_cost and strong sales, Amazon owning the BB
        # makes sourcing pointless.
        row = _keepa_niche_row(amazon_bb_pct_90=0.80)
        out = validate_opportunity(row)
        # Above source_only_max_amazon_bb_share=0.70; below kill 0.90.
        assert out["opportunity_verdict"] != VERDICT_SOURCE_ONLY

    def test_low_data_confidence_blocks_source_only(self):
        # Low-confidence keepa-niche shouldn't push the operator to find
        # a supplier on a stale signal.
        row = _keepa_niche_row(data_confidence="LOW")
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] != VERDICT_SOURCE_ONLY


class TestNegotiate:
    def test_current_profit_but_weak_conservative_becomes_negotiate(self):
        # Strong sales + currently profitable + conservative below
        # min_profit_absolute_buy → NEGOTIATE.
        row = _shortlist_row(
            decision="REVIEW",
            profit_current=4.0,
            profit_conservative=1.5,         # below £2.50 BUY floor
            roi_conservative=0.20,           # above kill 0.15, below buy 0.30
            candidate_score=70,
        )
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_NEGOTIATE

    def test_negotiate_emits_max_buy_cost_in_reasons(self):
        row = _shortlist_row(
            decision="REVIEW",
            profit_current=4.0,
            profit_conservative=1.5,
            roi_conservative=0.20,
            candidate_score=70,
        )
        out = validate_opportunity(row)
        assert any("max_buy_cost" in r for r in out["opportunity_reasons"])


class TestWatch:
    def test_default_path_is_watch(self):
        # Profitable + reasonable but not BUY-grade (e.g. medium
        # confidence + below-target volatility but joiners high).
        row = _shortlist_row(
            data_confidence="MEDIUM",
            fba_offer_count_90d_joiners=10,    # > max 5 joiners
        )
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_WATCH


# ────────────────────────────────────────────────────────────────────────
# Score + confidence
# ────────────────────────────────────────────────────────────────────────


class TestOpportunityScore:
    def test_perfect_row_scores_high(self):
        out = validate_opportunity(_shortlist_row())
        assert out["opportunity_score"] >= 80

    def test_kill_row_still_gets_score(self):
        # KILL rows are scored too — useful context for the operator.
        row = _shortlist_row(decision="REJECT")
        out = validate_opportunity(row)
        assert isinstance(out["opportunity_score"], int)
        assert 0 <= out["opportunity_score"] <= 100


class TestConfidence:
    def test_full_data_yields_high_confidence(self):
        out = validate_opportunity(_shortlist_row())
        assert out["opportunity_confidence"] == "HIGH"

    def test_missing_critical_fields_lower_confidence(self):
        row = _shortlist_row()
        # Strip 4 critical fields → LOW (≥3 missing).
        for f in ("amazon_bb_pct_90", "buy_box_oos_pct_90",
                  "price_volatility_90d", "fba_seller_count"):
            row.pop(f, None)
        out = validate_opportunity(row)
        assert out["opportunity_confidence"] == "LOW"

    def test_one_missing_critical_field_yields_medium(self):
        row = _shortlist_row()
        row.pop("price_volatility_90d", None)
        out = validate_opportunity(row)
        assert out["opportunity_confidence"] == "MEDIUM"


# ────────────────────────────────────────────────────────────────────────
# Robustness
# ────────────────────────────────────────────────────────────────────────


class TestRobustness:
    def test_missing_optional_fields_do_not_crash(self):
        # Bare-minimum row: only decision + buy_cost. Everything else
        # missing. Must not raise.
        row = {"decision": "REVIEW", "buy_cost": 5.0}
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] in VERDICTS
        assert out["next_action"] == NEXT_ACTIONS[out["opportunity_verdict"]]

    def test_completely_empty_row_does_not_crash(self):
        out = validate_opportunity({})
        assert out["opportunity_verdict"] in VERDICTS
        # Empty input → no profit signal → KILL via missing-data
        # cascade is acceptable; or WATCH. Just don't raise.
        assert out["opportunity_score"] >= 0

    def test_nan_fields_handled_gracefully(self):
        # pandas fills missing keys with NaN — the validator must not
        # treat NaN as a real signal.
        row = _shortlist_row(
            sales_estimate=float("nan"),
            roi_conservative=float("nan"),
            profit_conservative=float("nan"),
        )
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] in VERDICTS

    def test_string_typed_numerics_handled(self):
        # CSV-loaded rows may carry numerics as strings ("100", "0.50").
        row = _shortlist_row(
            sales_estimate="250",
            roi_conservative="0.50",
            profit_conservative="8.00",
        )
        out = validate_opportunity(row)
        assert out["opportunity_verdict"] == VERDICT_BUY


# ────────────────────────────────────────────────────────────────────────
# next_action coverage
# ────────────────────────────────────────────────────────────────────────


class TestNextAction:
    def test_next_action_populated_for_every_verdict(self):
        # Build one row per verdict and verify the next_action map
        # is consulted for each.
        cases = [
            (VERDICT_KILL, _shortlist_row(decision="REJECT")),
            (VERDICT_BUY, _shortlist_row()),
            (VERDICT_SOURCE_ONLY, _keepa_niche_row()),
            (
                VERDICT_NEGOTIATE,
                _shortlist_row(
                    decision="REVIEW",
                    profit_current=4.0,
                    profit_conservative=1.5,
                    roi_conservative=0.20,
                    candidate_score=70,
                ),
            ),
            (
                VERDICT_WATCH,
                _shortlist_row(
                    data_confidence="MEDIUM",
                    fba_offer_count_90d_joiners=10,
                ),
            ),
        ]
        for expected_verdict, row in cases:
            out = validate_opportunity(row)
            assert out["opportunity_verdict"] == expected_verdict
            assert out["next_action"] == NEXT_ACTIONS[expected_verdict]
            assert out["next_action"]   # non-empty


# ────────────────────────────────────────────────────────────────────────
# DataFrame wrapper
# ────────────────────────────────────────────────────────────────────────


class TestDataFrameApi:
    def test_add_opportunity_verdict_appends_columns(self):
        df = pd.DataFrame([_shortlist_row(), _shortlist_row(decision="REJECT")])
        out = add_opportunity_verdict(df)
        for col in OPPORTUNITY_COLUMNS:
            assert col in out.columns
        # First row → BUY; second row (REJECT) → KILL.
        assert out.iloc[0]["opportunity_verdict"] == VERDICT_BUY
        assert out.iloc[1]["opportunity_verdict"] == VERDICT_KILL

    def test_empty_df_yields_empty_with_columns(self):
        out = add_opportunity_verdict(pd.DataFrame())
        assert out.empty
        for col in OPPORTUNITY_COLUMNS:
            assert col in out.columns

    def test_run_step_basic(self):
        df = pd.DataFrame([_shortlist_row()])
        out = run_step(df, {})
        assert out.iloc[0]["opportunity_verdict"] == VERDICT_BUY

    def test_does_not_mutate_input_row_dict(self):
        row = _shortlist_row()
        before = set(row.keys())
        validate_opportunity(row)
        assert set(row.keys()) == before, (
            "validate_opportunity must not mutate the input row"
        )


# ────────────────────────────────────────────────────────────────────────
# Decision invariant — the WHOLE POINT of this step
# ────────────────────────────────────────────────────────────────────────


class TestPredictSellerVelocity:
    """PR F — units/mo prediction for a new entrant taking equal BB share."""

    def test_basic_equal_split(self):
        # 100 sales/mo, 0% Amazon BB share, 4 sellers → 25/mo equal share.
        row = {
            "sales_estimate": 100,
            "fba_seller_count": 4,
            "amazon_bb_pct_90": 0.0,
        }
        v = predict_seller_velocity(row)
        assert v is not None
        assert v["mid"] == 25
        assert v["low"] == 8     # 25 × 0.30 = 7.5 → 8
        assert v["high"] == 38   # 25 × 1.50 = 37.5 → 38

    def test_amazon_bb_share_reduces_non_amazon_volume(self):
        # 100 sales/mo, 50% Amazon BB share → 50 left for 3rd-party.
        # 4 sellers → 12.5 each.
        row = {
            "sales_estimate": 100,
            "fba_seller_count": 4,
            "amazon_bb_pct_90": 0.5,
        }
        v = predict_seller_velocity(row)
        assert v["mid"] == 12

    def test_bsr_drops_caps_overestimated_sales(self):
        # Real-world B0B636ZKZQ: Keepa says monthlySold=70 but BSR drops
        # show only ~25 (so bsr_drops × 1.5 = 37.5 < 70 × 0.5).
        # Predictor should use the lower number (37.5), not 70.
        row = {
            "sales_estimate": 70,
            "bsr_drops_30d": 25,
            "fba_seller_count": 3,
            "amazon_bb_pct_90": 0.02,
        }
        v = predict_seller_velocity(row)
        # 37.5 × 0.98 / 3 = 12.25 → 12
        assert v is not None
        assert v["mid"] == 12

    def test_bsr_drops_only_when_disagreement_is_large(self):
        # When Keepa's monthlySold and bsr_drops × 1.5 are close, prefer
        # Keepa's number (their model is closer to ground truth).
        # 100/mo monthlySold, 80 BSR drops × 1.5 = 120 — they're close,
        # use 100.
        row = {
            "sales_estimate": 100,
            "bsr_drops_30d": 80,
            "fba_seller_count": 4,
            "amazon_bb_pct_90": 0.0,
        }
        v = predict_seller_velocity(row)
        assert v["mid"] == 25     # 100 / 4 = 25

    def test_uses_bsr_drops_when_sales_estimate_missing(self):
        row = {
            "bsr_drops_30d": 30,    # × 1.5 = 45
            "fba_seller_count": 3,
            "amazon_bb_pct_90": 0.0,
        }
        v = predict_seller_velocity(row)
        # 45 × 1.0 / 3 = 15
        assert v is not None
        assert v["mid"] == 15

    def test_joiners_dilute_share(self):
        row = {
            "sales_estimate": 100,
            "fba_seller_count": 4,
            "amazon_bb_pct_90": 0.0,
            "fba_offer_count_90d_joiners": 5,
        }
        v = predict_seller_velocity(row)
        # 25 × 0.7 (joiners penalty) = 17.5 → 18
        assert v["mid"] == 18

    def test_oos_lifts_estimate_for_latent_demand(self):
        row = {
            "sales_estimate": 100,
            "fba_seller_count": 4,
            "amazon_bb_pct_90": 0.0,
            "buy_box_oos_pct_90": 0.30,
        }
        v = predict_seller_velocity(row)
        # 25 × 1.15 = 28.75 → 29
        assert v["mid"] == 29

    def test_high_capped_at_total_non_amazon(self):
        # High shouldn't exceed total non-Amazon sales — a single seller
        # can't sell more than 100% of the BB rotation.
        row = {
            "sales_estimate": 30,
            "fba_seller_count": 2,
            "amazon_bb_pct_90": 0.0,
        }
        v = predict_seller_velocity(row)
        # mid = 15; high = 22.5 → capped at 30 (total non-Amazon).
        assert v["high"] <= 30

    def test_returns_none_when_sales_signal_absent(self):
        row = {"fba_seller_count": 4, "amazon_bb_pct_90": 0.0}
        assert predict_seller_velocity(row) is None

    def test_returns_none_when_no_fba_sellers(self):
        row = {
            "sales_estimate": 100,
            "fba_seller_count": 0,
            "amazon_bb_pct_90": 0.0,
        }
        assert predict_seller_velocity(row) is None

    def test_returns_none_for_empty_row(self):
        assert predict_seller_velocity({}) is None

    def test_validate_opportunity_includes_velocity_fields(self):
        # End-to-end: velocity fields appear in every verdict's output.
        out = validate_opportunity(_shortlist_row())
        assert "predicted_velocity_low" in out
        assert "predicted_velocity_mid" in out
        assert "predicted_velocity_high" in out
        assert isinstance(out["predicted_velocity_mid"], int)


class TestDecisionInvariant:
    def test_decision_column_unchanged(self):
        """HANDOFF acceptance: existing SHORTLIST/REVIEW/REJECT logic
        must not change. The validator's only output is the 6 new
        columns; `decision` flows through unmodified."""
        rows = [
            _shortlist_row(),
            _shortlist_row(decision="REVIEW"),
            _shortlist_row(decision="REJECT"),
        ]
        df = pd.DataFrame(rows)
        before = list(df["decision"])
        out = add_opportunity_verdict(df)
        after = list(out["decision"])
        assert before == after
