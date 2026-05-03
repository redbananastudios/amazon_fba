"""Tests for sourcing_engine.buy_plan_html.payload — pure JSON builder."""
from __future__ import annotations

import pandas as pd
import pytest

from sourcing_engine.buy_plan_html.payload import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    build_payload,
    build_row_payload,
)


def _buy_row(**overrides) -> dict:
    base = {
        "asin": "B0B636ZKZQ",
        "product_name": "Casdon Toaster Toy",
        "brand": "Casdon",
        "supplier": "abgee",
        "supplier_sku": "12345",
        "amazon_url": "https://www.amazon.co.uk/dp/B0B636ZKZQ",
        "decision": "SHORTLIST",
        "opportunity_verdict": "BUY",
        "opportunity_confidence": "HIGH",
        "opportunity_score": 85,
        "next_action": "Check live price, confirm stock, place test order",
        "buy_cost": 4.00,
        "market_price": 16.85,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        "profit_conservative": 8.35,
        "roi_conservative": 1.114,
        "fba_seller_count": 4,
        "amazon_on_listing": "N",
        "amazon_bb_pct_90": 0.10,
        "price_volatility_90d": 0.10,
        "sales_estimate": 250,
        "predicted_velocity_mid": 18,
        "bsr_drops_30d": 45,
        "buy_box_oos_pct_90": 0.05,
        "order_qty_recommended": 13,
        "capital_required": 52.00,
        "projected_30d_units": 18,
        "projected_30d_revenue": 303.30,
        "projected_30d_profit": 150.30,
        "payback_days": 21.7,
        "target_buy_cost_buy": 9.50,
        "target_buy_cost_stretch": 8.52,
        "gap_to_buy_gbp": None,
        "gap_to_buy_pct": None,
        "buy_plan_status": "OK",
        "risk_flags": [],
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────
# Top-level payload shape
# ─────────────────────────────────────────────────────────────────────


def test_build_payload_top_level_fields():
    df = pd.DataFrame([_buy_row()])
    out = build_payload(df, run_id="20260503_120000", strategy="supplier_pricelist", supplier="abgee")
    assert out["schema_version"] == SCHEMA_VERSION
    assert out["prompt_version"] == PROMPT_VERSION
    assert out["run_id"] == "20260503_120000"
    assert out["strategy"] == "supplier_pricelist"
    assert out["supplier"] == "abgee"
    assert "generated_at" in out
    assert out["verdict_counts"] == {
        "BUY": 1, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0
    }
    assert len(out["rows"]) == 1


def test_build_payload_supplier_null_when_strategy_lacks_one():
    df = pd.DataFrame([_buy_row()])
    out = build_payload(df, run_id="20260503", strategy="keepa_finder", supplier=None)
    assert out["supplier"] is None


def test_build_payload_filters_kill_rows():
    df = pd.DataFrame([
        _buy_row(),
        _buy_row(asin="B0KILL00001", opportunity_verdict="KILL"),
    ])
    out = build_payload(df, run_id="20260503", strategy="supplier_pricelist", supplier="abgee")
    assert out["verdict_counts"]["BUY"] == 1
    assert out["verdict_counts"]["KILL"] == 1
    # Only BUY survives in rows[].
    assert len(out["rows"]) == 1
    assert out["rows"][0]["asin"] == "B0B636ZKZQ"


def test_build_payload_empty_df():
    out = build_payload(pd.DataFrame(), run_id="x", strategy="x", supplier=None)
    assert out["rows"] == []
    assert out["verdict_counts"]["BUY"] == 0


# ─────────────────────────────────────────────────────────────────────
# Per-row identity block
# ─────────────────────────────────────────────────────────────────────


def test_build_row_payload_buy_identity_block():
    row = _buy_row()
    out = build_row_payload(row)
    assert out["asin"] == "B0B636ZKZQ"
    assert out["title"] == "Casdon Toaster Toy"
    assert out["brand"] == "Casdon"
    assert out["amazon_url"] == "https://www.amazon.co.uk/dp/B0B636ZKZQ"
    assert out["image_url"] == "https://images-na.ssl-images-amazon.com/images/P/B0B636ZKZQ.jpg"
    # Engine's deterministic verdict — kept as a cross-check signal
    # in v2; the operator-facing verdict comes from the analyst block.
    assert out["engine_verdict"] == "BUY"
    assert out["engine_verdict_confidence"] == "HIGH"
    assert out["engine_opportunity_score"] == 85
    # Analyst block is initialised to nulls — populated downstream.
    assert out["analyst"]["verdict"] is None


def test_build_row_payload_buy_economics_block():
    out = build_row_payload(_buy_row())
    eco = out["economics"]
    assert eco["buy_cost_gbp"] == 4.00
    assert eco["market_price_gbp"] == 16.85
    assert eco["profit_per_unit_gbp"] == 8.35
    assert eco["roi_conservative_pct"] == pytest.approx(1.114)
    assert eco["target_buy_cost_gbp"] == 9.50
    assert eco["target_buy_cost_stretch_gbp"] == 8.52


def test_build_row_payload_buy_plan_block():
    out = build_row_payload(_buy_row())
    bp = out["buy_plan"]
    assert bp["order_qty_recommended"] == 13
    assert bp["capital_required_gbp"] == 52.00
    assert bp["projected_30d_units"] == 18
    assert bp["projected_30d_revenue_gbp"] == 303.30
    assert bp["projected_30d_profit_gbp"] == 150.30
    assert bp["payback_days"] == 21.7
    assert bp["gap_to_buy_gbp"] is None
    assert bp["gap_to_buy_pct"] is None
    assert bp["buy_plan_status"] == "OK"


def test_build_row_payload_carries_engine_lists():
    row = _buy_row(risk_flags=["INSUFFICIENT_HISTORY"])
    out = build_row_payload(row)
    assert out["risk_flags"] == ["INSUFFICIENT_HISTORY"]
    assert "engine_reasons" in out
    assert "engine_blockers" in out


def test_build_row_payload_image_url_for_blank_asin():
    row = _buy_row(asin="")
    out = build_row_payload(row)
    assert out["image_url"] is None


def test_build_row_payload_handles_nan_inputs():
    row = _buy_row(buy_cost=float("nan"), order_qty_recommended=float("nan"))
    out = build_row_payload(row)
    assert out["economics"]["buy_cost_gbp"] is None
    assert out["buy_plan"]["order_qty_recommended"] is None


def test_build_row_payload_does_not_mutate_input():
    row = _buy_row()
    before = dict(row)
    build_row_payload(row)
    assert row == before


# ─────────────────────────────────────────────────────────────────────
# Traffic-light metric judgments (PRD §4.3)
# ─────────────────────────────────────────────────────────────────────


class TestMetricsTrafficLight:
    def _row(self, **overrides) -> dict:
        return _buy_row(**overrides)

    # ──────────────── fba_seller_count ────────────────
    def test_fba_green_at_healthy_low_volume(self):
        row = self._row(fba_seller_count=2, sales_estimate=50)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "fba_seller_count")
        assert m["verdict"] == "green"

    def test_fba_green_at_healthy_high_volume(self):
        row = self._row(fba_seller_count=4, sales_estimate=250)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "fba_seller_count")
        assert m["verdict"] == "green"

    def test_fba_amber_when_50pct_over_ceiling(self):
        row = self._row(fba_seller_count=15, sales_estimate=250)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "fba_seller_count")
        assert m["verdict"] == "amber"

    def test_fba_red_when_far_over_ceiling(self):
        row = self._row(fba_seller_count=25, sales_estimate=250)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "fba_seller_count")
        assert m["verdict"] == "red"

    # ──────────────── amazon_on_listing ────────────────
    @pytest.mark.parametrize("value,expected", [
        ("N", "green"), ("", "green"), (None, "green"),
        ("UNKNOWN", "amber"),
        ("Y", "red"),
    ])
    def test_amazon_on_listing_verdict(self, value, expected):
        row = self._row(amazon_on_listing=value)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "amazon_on_listing")
        assert m["verdict"] == expected

    # ──────────────── amazon_bb_pct_90 ────────────────
    @pytest.mark.parametrize("value,expected", [
        (0.10, "green"),     # < 0.30
        (0.29, "green"),
        (0.30, "amber"),     # 0.30 ≤ x < 0.70
        (0.50, "amber"),
        (0.70, "red"),       # ≥ 0.70
        (0.95, "red"),
    ])
    def test_amazon_bb_share_verdict(self, value, expected):
        row = self._row(amazon_bb_pct_90=value)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "amazon_bb_pct_90")
        assert m["verdict"] == expected

    # ──────────────── price_volatility ────────────────
    @pytest.mark.parametrize("value,expected", [
        (0.05, "green"),
        (0.20, "amber"),
        (0.30, "amber"),
        (0.40, "red"),
        (0.50, "red"),
    ])
    def test_price_volatility_verdict(self, value, expected):
        row = self._row(price_volatility_90d=value)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "price_volatility")
        assert m["verdict"] == expected

    # ──────────────── sales_estimate ────────────────
    @pytest.mark.parametrize("value,expected", [
        (250, "green"),
        (100, "green"),
        (50, "amber"),
        (20, "amber"),
        (15, "red"),
    ])
    def test_sales_estimate_verdict(self, value, expected):
        row = self._row(sales_estimate=value)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "sales_estimate")
        assert m["verdict"] == expected

    # ──────────────── predicted_velocity ────────────────
    # Note: predicted_velocity reconciles `min(sales_estimate, bsr_drops × 1.5)`
    # to mirror the engine's velocity calc. These tests set
    # `bsr_drops_30d=None` so the reconciliation collapses to
    # sales_estimate alone, isolating the share-of-rotation boundary.
    def test_predicted_velocity_green_above_half_share(self):
        # non_amazon_share = 250 × 0.9 = 225. Half = 112.5.
        row = self._row(
            predicted_velocity_mid=120, sales_estimate=250,
            amazon_bb_pct_90=0.10, bsr_drops_30d=None,
        )
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "predicted_velocity")
        assert m["verdict"] == "green"

    def test_predicted_velocity_amber_quarter_to_half(self):
        # 0.25 × 225 = 56.25 ≤ mid < 112.5 → amber.
        row = self._row(
            predicted_velocity_mid=70, sales_estimate=250,
            amazon_bb_pct_90=0.10, bsr_drops_30d=None,
        )
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "predicted_velocity")
        assert m["verdict"] == "amber"

    def test_predicted_velocity_red_below_quarter_share(self):
        row = self._row(
            predicted_velocity_mid=20, sales_estimate=250,
            amazon_bb_pct_90=0.10, bsr_drops_30d=None,
        )
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "predicted_velocity")
        assert m["verdict"] == "red"

    def test_predicted_velocity_uses_min_of_sales_and_bsr_proxy(self):
        # Engine convention: when bsr_drops × 1.5 < sales_estimate,
        # use the conservative (lower) number for share calc.
        # bsr=20 → bsr_proxy=30; sales=250 → use 30 not 250.
        # non_amazon = 30 × 0.9 = 27. mid=15 → 15/27 = 56% → green.
        # If we'd used sales=250 instead: 15/225 = 7% → red.
        row = self._row(
            predicted_velocity_mid=15, sales_estimate=250,
            amazon_bb_pct_90=0.10, bsr_drops_30d=20,
        )
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "predicted_velocity")
        assert m["verdict"] == "green"

    def test_predicted_velocity_grey_when_amazon_bb_missing(self):
        row = self._row(
            predicted_velocity_mid=18, sales_estimate=250,
        )
        row.pop("amazon_bb_pct_90")
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "predicted_velocity")
        assert m["verdict"] == "grey"

    # ──────────────── bsr_drops_30d ────────────────
    def test_bsr_drops_green_above_green_floor(self):
        # green_floor = max(20, 250 × 0.5) = 125. drops=200 → green.
        row = self._row(bsr_drops_30d=200, sales_estimate=250)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "bsr_drops_30d")
        assert m["verdict"] == "green"

    def test_bsr_drops_amber_in_middle(self):
        # amber_floor = max(10, 62.5) = 62.5. drops=75 → amber (between 62.5 and 125).
        row = self._row(bsr_drops_30d=75, sales_estimate=250)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "bsr_drops_30d")
        assert m["verdict"] == "amber"

    def test_bsr_drops_red_below_amber_floor(self):
        row = self._row(bsr_drops_30d=5, sales_estimate=250)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == "bsr_drops_30d")
        assert m["verdict"] == "red"

    # ──────────────── grey when source absent ────────────────
    @pytest.mark.parametrize("missing_field,target_key", [
        ("fba_seller_count", "fba_seller_count"),
        ("amazon_bb_pct_90", "amazon_bb_pct_90"),
        ("price_volatility_90d", "price_volatility"),
        ("sales_estimate", "sales_estimate"),
        ("bsr_drops_30d", "bsr_drops_30d"),
    ])
    def test_grey_when_source_absent(self, missing_field, target_key):
        row = self._row()
        row.pop(missing_field)
        m = next(x for x in build_row_payload(row)["metrics"] if x["key"] == target_key)
        assert m["verdict"] == "grey"
        assert m["value_display"] == "—"
        assert "signal missing" in m["rationale"].lower()


def test_metrics_ordered_per_prd_4_3():
    """The 7 metrics MUST appear in the exact order spec'd in PRD §4.3."""
    out = build_row_payload(_buy_row())
    keys = [m["key"] for m in out["metrics"]]
    assert keys == [
        "fba_seller_count",
        "amazon_on_listing",
        "amazon_bb_pct_90",
        "price_volatility",
        "sales_estimate",
        "predicted_velocity",
        "bsr_drops_30d",
    ]
