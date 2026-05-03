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
    assert out["verdict"] == "BUY"
    assert out["verdict_confidence"] == "HIGH"
    assert out["opportunity_score"] == 85


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
