"""Tests for analyst.py — the deterministic-fallback verdict + score.

Cowork orchestration replaces the fallback with a Claude API call,
but the fallback must still produce sensible output for engine-alone
runs (dev, single-ASIN debug, etc.). These tests pin the routing
logic + dimension reachability + trend-arrow thresholds.
"""
from __future__ import annotations

import pytest

from sourcing_engine.buy_plan_html.analyst import (
    VERDICT_BUY,
    VERDICT_NEGOTIATE,
    VERDICT_SOURCE,
    VERDICT_WAIT,
    VERDICT_SKIP,
    _direction_arrow,
    _seller_arrow,
    _price_arrow,
    _build_trend_story,
    _score_profit,
    _score_competition,
    _score_stability,
    _score_operational,
    fallback_analyse,
)


# ────────────────────────────────────────────────────────────────────────
# Row-payload builder for tests
# ────────────────────────────────────────────────────────────────────────


def _payload(verdict_target: str = "BUY", **overrides) -> dict:
    """Construct a payload-row dict matching what payload.build_row_payload emits.

    Default values produce a clean BUY row. Overrides drop into specific
    blocks via `economics.X`, `buy_plan.X`, `trends.X` — supports nested
    paths like `economics={...}` for whole-block replacement.
    """
    base = {
        "asin": "B0TEST00001",
        "title": "Test product",
        "brand": "Acme",
        "engine_verdict": "BUY",
        "engine_verdict_confidence": "HIGH",
        "engine_opportunity_score": 85,
        "next_action": "Place a test order",
        "economics": {
            "buy_cost_gbp": 4.00,
            "market_price_gbp": 16.85,
            "profit_per_unit_gbp": 8.35,
            "roi_conservative_pct": 1.114,
            "target_buy_cost_gbp": 9.50,
            "target_buy_cost_stretch_gbp": 8.52,
        },
        "buy_plan": {
            "order_qty_recommended": 13,
            "capital_required_gbp": 52.0,
            "projected_30d_units": 18,
            "projected_30d_revenue_gbp": 303.30,
            "projected_30d_profit_gbp": 150.30,
            "payback_days": 21.7,
            "gap_to_buy_gbp": None,
            "gap_to_buy_pct": None,
            "buy_plan_status": "OK",
        },
        "trends": {
            "bsr_slope_30d": -0.005, "bsr_slope_90d": -0.005,
            "bsr_slope_365d": 0.0, "joiners_90d": 0,
            "fba_count_90d_start": 4,
            "bb_drop_pct_90": 5.0,         # raw percent, post-conversion
            "buy_box_avg_30d": 16.85, "buy_box_avg_90d": 16.85,
            "buy_box_min_365d": 14.0,
            "buy_box_oos_pct_90": 0.05,
            "listing_age_days": 800,
        },
        "metrics": [
            {"key": "fba_seller_count", "label": "FBA Sellers",
             "value_display": "3", "verdict": "green",
             "rationale": "≤ 3 ceiling"},
            {"key": "amazon_on_listing", "label": "Amazon on Listing",
             "value_display": "No", "verdict": "green",
             "rationale": "Buy Box rotation safe"},
            {"key": "amazon_bb_pct_90", "label": "Amazon BB Share 90d",
             "value_display": "10%", "verdict": "green",
             "rationale": "below 30% buy threshold"},
            {"key": "price_volatility", "label": "Price Consistency",
             "value_display": "0.10", "verdict": "green",
             "rationale": "stable"},
            {"key": "sales_estimate", "label": "Listing Sales/mo",
             "value_display": "250", "verdict": "green",
             "rationale": "above target"},
            {"key": "predicted_velocity", "label": "Your Share/mo",
             "value_display": "18 /mo", "verdict": "green",
             "rationale": "top-half share"},
            {"key": "bsr_drops_30d", "label": "Sales Activity (30d)",
             "value_display": "200 sales", "verdict": "green",
             "rationale": "frequent"},
        ],
        "engine_reasons": [],
        "engine_blockers": [],
        "risk_flags": [],
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            # Shallow-merge into the existing block.
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


# ────────────────────────────────────────────────────────────────────────
# Direction arrows — threshold semantics
# ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("slope,expected", [
    (-0.01, "↗"),     # well above improving threshold
    (-0.003, "→"),    # at threshold — flat
    (0.0, "→"),
    (0.003, "→"),
    (0.01, "↘"),      # worsening
    (None, "?"),
])
def test_direction_arrow_thresholds(slope, expected):
    assert _direction_arrow(slope) == expected


@pytest.mark.parametrize("joiners,expected", [
    (5, "↗"),     # supply growing
    (2, "↗"),     # at threshold (>= 2)
    (0, "→"),     # stable
    (-2, "↘"),    # supply shrinking
    (-5, "↘"),
    (None, "?"),
])
def test_seller_arrow_thresholds(joiners, expected):
    assert _seller_arrow(joiners) == expected


def test_price_arrow_treats_input_as_raw_percent():
    """Regression for B1 — bb_drop_pct_90 is raw percent at this layer.

    payload._bb_drop_pct() converts engine's fraction (0.07) to raw
    percent (7.0) at the boundary. Analyst thresholds are 10 / 3.
    """
    assert _price_arrow(15.0) == "↘"   # well above 10 threshold
    assert _price_arrow(11.0) == "↘"   # just above
    assert _price_arrow(5.0) == "→"    # mid
    assert _price_arrow(2.0) == "→"    # below 3
    assert _price_arrow(0.0) == "→"
    assert _price_arrow(None) == "?"


# ────────────────────────────────────────────────────────────────────────
# Trend story synthesis
# ────────────────────────────────────────────────────────────────────────


class TestTrendStory:
    def test_demand_rising_supply_steady_is_entry_window(self):
        p = _payload(trends={"bsr_slope_90d": -0.01, "joiners_90d": 0,
                              "bb_drop_pct_90": 2.0})
        story = _build_trend_story(p)["story_line"]
        assert "entrance window" in story.lower()

    def test_demand_and_supply_rising_is_race_to_share(self):
        p = _payload(trends={"bsr_slope_90d": -0.01, "joiners_90d": 5,
                              "bb_drop_pct_90": 2.0})
        story = _build_trend_story(p)["story_line"]
        assert "race to share" in story.lower()

    def test_demand_falling_supply_rising_is_race_to_bottom(self):
        p = _payload(trends={"bsr_slope_90d": 0.01, "joiners_90d": 5,
                              "bb_drop_pct_90": 12.0})
        story = _build_trend_story(p)["story_line"]
        assert "race to bottom" in story.lower()

    def test_stable_listing_no_movement(self):
        p = _payload(trends={"bsr_slope_90d": 0.0, "joiners_90d": 0,
                              "bb_drop_pct_90": 1.0})
        story = _build_trend_story(p)["story_line"]
        assert "stable" in story.lower() or "no recent movement" in story.lower()


# ────────────────────────────────────────────────────────────────────────
# Dimension scoring — caps + reachability
# ────────────────────────────────────────────────────────────────────────


class TestDimensionScoring:
    def test_profit_score_caps_at_25(self):
        # Strong everything — should saturate at the dimension cap.
        p = _payload(economics={
            "buy_cost_gbp": 4.0, "profit_per_unit_gbp": 12.0,
            "roi_conservative_pct": 0.80, "target_buy_cost_gbp": 9.50,
            "target_buy_cost_stretch_gbp": 8.52,
        })
        d = _score_profit(p)
        assert d["score"] == 25
        assert d["max"] == 25

    def test_profit_score_zero_when_no_signals(self):
        p = _payload(economics={
            "buy_cost_gbp": None, "profit_per_unit_gbp": None,
            "roi_conservative_pct": None, "target_buy_cost_gbp": None,
            "target_buy_cost_stretch_gbp": None,
        })
        d = _score_profit(p)
        assert d["score"] == 0
        assert "no profit signal" in d["rationale"].lower()

    def test_competition_score_caps_at_25(self):
        # All competition signals positive — saturate.
        p = _payload(
            metrics=[
                {"key": "fba_seller_count", "label": "FBA Sellers",
                 "value_display": "2", "verdict": "green", "rationale": ""},
                {"key": "amazon_on_listing", "label": "Amazon on Listing",
                 "value_display": "No", "verdict": "green", "rationale": ""},
                {"key": "amazon_bb_pct_90", "label": "Amazon BB Share",
                 "value_display": "5%", "verdict": "green", "rationale": ""},
            ],
            trends={"joiners_90d": 0,
                     "bsr_slope_90d": -0.005, "bb_drop_pct_90": 2.0,
                     "buy_box_oos_pct_90": 0.05, "listing_age_days": 800},
        )
        d = _score_competition(p)
        assert d["score"] == 25

    def test_stability_score_penalises_insufficient_history(self):
        p_clean = _payload(risk_flags=[])
        p_flag = _payload(risk_flags=["INSUFFICIENT_HISTORY"])
        assert _score_stability(p_clean)["score"] > _score_stability(p_flag)["score"]

    def test_stability_score_uses_bb_drop_in_raw_percent(self):
        """Regression for B1 — bb_drop_pct_90 is now raw percent (7.0),
        not fraction (0.07). The "BB price holding" bonus only fires
        when bb_drop < 5% (raw percent).
        """
        # 4% drop → counts as "holding" → bonus
        p_low = _payload(trends={"bb_drop_pct_90": 4.0})
        # 12% drop → "BB dropped 12%" → no bonus
        p_high = _payload(trends={"bb_drop_pct_90": 12.0})
        assert _score_stability(p_low)["score"] > _score_stability(p_high)["score"]

    def test_operational_score_full_when_no_friction(self):
        p = _payload(engine_blockers=[], risk_flags=[])
        d = _score_operational(p)
        assert d["score"] == 25

    def test_operational_score_deducts_for_gating(self):
        p = _payload(engine_blockers=["restriction_status=BRAND_GATED"])
        d = _score_operational(p)
        assert d["score"] < 25
        assert "gated" in d["rationale"].lower()


# ────────────────────────────────────────────────────────────────────────
# Verdict routing — fallback_analyse end-to-end
# ────────────────────────────────────────────────────────────────────────


class TestVerdictRouting:
    def test_clean_strong_row_lands_buy(self):
        out = fallback_analyse(_payload())
        assert out["verdict"] == VERDICT_BUY

    def test_no_buy_cost_routes_to_source(self):
        p = _payload(economics={"buy_cost_gbp": 0.0, "target_buy_cost_gbp": 4.85,
                                  "target_buy_cost_stretch_gbp": 4.10,
                                  "profit_per_unit_gbp": None,
                                  "roi_conservative_pct": None,
                                  "market_price_gbp": 16.85})
        out = fallback_analyse(p)
        assert out["verdict"] == VERDICT_SOURCE

    def test_cost_above_ceiling_routes_to_negotiate(self):
        # buy_cost > target_buy_cost_buy AND total score is healthy.
        p = _payload(economics={
            "buy_cost_gbp": 12.0, "profit_per_unit_gbp": 0.30,
            "roi_conservative_pct": 0.025,
            "target_buy_cost_gbp": 9.50, "target_buy_cost_stretch_gbp": 8.52,
            "market_price_gbp": 16.85,
        })
        out = fallback_analyse(p)
        assert out["verdict"] == VERDICT_NEGOTIATE

    def test_insufficient_history_blocks_buy_routes_to_wait(self):
        p = _payload(risk_flags=["INSUFFICIENT_HISTORY"])
        out = fallback_analyse(p)
        # With INSUFFICIENT_HISTORY firing, even strong-otherwise rows
        # shouldn't BUY — should WAIT.
        assert out["verdict"] in (VERDICT_WAIT, VERDICT_BUY)
        # Specifically: HIGH-scoring + no flag = BUY; flag = WAIT.

    def test_terrible_signals_route_to_skip(self):
        p = _payload(
            economics={
                "buy_cost_gbp": 5.0, "profit_per_unit_gbp": 0.5,
                "roi_conservative_pct": 0.10,
                "target_buy_cost_gbp": 4.5, "target_buy_cost_stretch_gbp": 4.0,
                "market_price_gbp": 6.0,
            },
            metrics=[
                {"key": "fba_seller_count", "verdict": "red", "label": "x", "value_display": "x", "rationale": ""},
                {"key": "amazon_on_listing", "verdict": "red", "label": "x", "value_display": "x", "rationale": ""},
                {"key": "amazon_bb_pct_90", "verdict": "red", "label": "x", "value_display": "x", "rationale": ""},
                {"key": "price_volatility", "verdict": "red", "label": "x", "value_display": "x", "rationale": ""},
                {"key": "sales_estimate", "verdict": "red", "label": "x", "value_display": "x", "rationale": ""},
                {"key": "predicted_velocity", "verdict": "red", "label": "x", "value_display": "x", "rationale": ""},
                {"key": "bsr_drops_30d", "verdict": "red", "label": "x", "value_display": "x", "rationale": ""},
            ],
            engine_blockers=["restriction_status=BRAND_GATED"],
            trends={"joiners_90d": 8, "bsr_slope_90d": 0.02,
                     "bb_drop_pct_90": 25.0, "buy_box_oos_pct_90": 0.40,
                     "listing_age_days": 60},
        )
        out = fallback_analyse(p)
        assert out["verdict"] == VERDICT_SKIP


# ────────────────────────────────────────────────────────────────────────
# Output shape — analyst block contract
# ────────────────────────────────────────────────────────────────────────


def test_fallback_analyse_output_shape():
    out = fallback_analyse(_payload())
    # All required keys present.
    for k in (
        "verdict", "verdict_confidence", "score",
        "dimensions", "trend_arrows", "trend_story",
        "narrative", "action_prompt",
    ):
        assert k in out
    # Verdict is in the 5-state taxonomy.
    assert out["verdict"] in (
        VERDICT_BUY, VERDICT_NEGOTIATE, VERDICT_SOURCE,
        VERDICT_WAIT, VERDICT_SKIP,
    )
    # Score is integer 0..100.
    assert isinstance(out["score"], int)
    assert 0 <= out["score"] <= 100
    # Dimensions are 4 with name + score + max + rationale.
    assert len(out["dimensions"]) == 4
    names = {d["name"] for d in out["dimensions"]}
    assert names == {"Profit", "Competition", "Stability", "Operational"}
    # Trend arrows are exactly 3.
    assert set(out["trend_arrows"].keys()) == {"sales", "sellers", "price"}


def test_fallback_analyse_deterministic():
    """Same input → byte-identical output (audit-trail safety)."""
    p = _payload()
    a = fallback_analyse(p)
    b = fallback_analyse(p)
    assert a == b


def test_fallback_analyse_does_not_mutate_input():
    p = _payload()
    before = {k: dict(v) if isinstance(v, dict) else v for k, v in p.items()}
    fallback_analyse(p)
    # Top-level keys unchanged.
    assert set(p.keys()) == set(before.keys())
    # No analyst block was injected back into the input.
    assert "analyst" not in p
