"""Snapshot test for HTML structural stability (v2 layout).

The snapshot is stored at `snapshots/buyer_report_4_verdicts.html`.
It captures structural HTML for a fixed 4-row fixture (one per
verdict) AFTER the analyst fallback has populated each row's
analyst block — i.e. the same path the engine uses at run time.
Drift in `_render_card_header` / `_render_dimension_bars` /
`_render_buyers_read` / `_render_direction` / `_render_economics`
fails this test; intentional changes update the snapshot via
`pytest --snapshot-update`.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from sourcing_engine.buy_plan_html.analyst import fallback_analyse
from sourcing_engine.buy_plan_html.payload import build_payload
from sourcing_engine.buy_plan_html.renderer import render_html


SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "buyer_report_4_verdicts.html"
RUN_ID = "20260503_120000"


def _fixture_df() -> pd.DataFrame:
    rows = [
        {
            "asin": "B0BUY00001A", "product_name": "BUY product",
            "brand": "Acme", "supplier": "test", "supplier_sku": "BUY",
            "amazon_url": "https://www.amazon.co.uk/dp/B0BUY00001A",
            "opportunity_verdict": "BUY", "opportunity_confidence": "HIGH",
            "opportunity_score": 85, "next_action": "place test order",
            "buy_cost": 4.00, "market_price": 16.85,
            "raw_conservative_price": 16.85, "fees_conservative": 4.50,
            "profit_conservative": 8.35, "roi_conservative": 1.114,
            "fba_seller_count": 4, "amazon_on_listing": "N",
            "amazon_bb_pct_90": 0.10, "price_volatility_90d": 0.10,
            "sales_estimate": 250, "predicted_velocity_mid": 130,
            "bsr_drops_30d": 200,
            "order_qty_recommended": 13, "capital_required": 52.0,
            "projected_30d_units": 18, "projected_30d_revenue": 303.30,
            "projected_30d_profit": 150.30, "payback_days": 21.7,
            "target_buy_cost_buy": 9.50, "target_buy_cost_stretch": 8.52,
            "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
            "buy_plan_status": "OK", "risk_flags": [],
        },
        {
            "asin": "B0SRC00001A", "product_name": "SOURCE product",
            "brand": "Acme", "supplier": "test", "supplier_sku": "SRC",
            "amazon_url": "https://www.amazon.co.uk/dp/B0SRC00001A",
            "opportunity_verdict": "SOURCE_ONLY", "opportunity_confidence": "HIGH",
            "opportunity_score": 80, "next_action": "find supplier",
            "buy_cost": 0.0, "market_price": 16.85,
            "raw_conservative_price": 16.85, "fees_conservative": 4.50,
            "profit_conservative": None, "roi_conservative": None,
            "fba_seller_count": 4, "amazon_on_listing": "N",
            "amazon_bb_pct_90": 0.10, "price_volatility_90d": 0.10,
            "sales_estimate": 320, "predicted_velocity_mid": 165,
            "bsr_drops_30d": 200,
            "order_qty_recommended": None, "capital_required": None,
            "projected_30d_units": 42, "projected_30d_revenue": 710.00,
            "projected_30d_profit": 136.00, "payback_days": None,
            "target_buy_cost_buy": 4.85, "target_buy_cost_stretch": 4.10,
            "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
            "buy_plan_status": "NO_BUY_COST", "risk_flags": [],
        },
        {
            "asin": "B0NEG00001A", "product_name": "NEGOTIATE product",
            "brand": "Acme", "supplier": "test", "supplier_sku": "NEG",
            "amazon_url": "https://www.amazon.co.uk/dp/B0NEG00001A",
            "opportunity_verdict": "NEGOTIATE", "opportunity_confidence": "MEDIUM",
            "opportunity_score": 70, "next_action": "negotiate down",
            "buy_cost": 5.00, "market_price": 16.85,
            "raw_conservative_price": 16.85, "fees_conservative": 4.50,
            "profit_conservative": 1.50, "roi_conservative": 0.20,
            "fba_seller_count": 5, "amazon_on_listing": "N",
            "amazon_bb_pct_90": 0.20, "price_volatility_90d": 0.15,
            "sales_estimate": 180, "predicted_velocity_mid": 70,
            "bsr_drops_30d": 90,
            "order_qty_recommended": None, "capital_required": None,
            "projected_30d_units": 18, "projected_30d_revenue": 303.30,
            "projected_30d_profit": 42.30, "payback_days": None,
            "target_buy_cost_buy": 4.38, "target_buy_cost_stretch": 3.50,
            "gap_to_buy_gbp": 0.62, "gap_to_buy_pct": 0.124,
            "buy_plan_status": "OK", "risk_flags": [],
        },
        {
            "asin": "B0WAT00001A", "product_name": "WATCH product",
            "brand": "Acme", "supplier": "test", "supplier_sku": "WAT",
            "amazon_url": "https://www.amazon.co.uk/dp/B0WAT00001A",
            "opportunity_verdict": "WATCH", "opportunity_confidence": "LOW",
            "opportunity_score": 60, "next_action": "monitor",
            "buy_cost": 4.00, "market_price": 16.85,
            "raw_conservative_price": 16.85, "fees_conservative": 4.50,
            "profit_conservative": 8.35, "roi_conservative": 1.114,
            "fba_seller_count": 4, "amazon_on_listing": "N",
            "amazon_bb_pct_90": 0.10, "price_volatility_90d": 0.10,
            "sales_estimate": 70, "predicted_velocity_mid": 18,
            "bsr_drops_30d": 25,
            "order_qty_recommended": None, "capital_required": None,
            "projected_30d_units": 18, "projected_30d_revenue": 303.30,
            "projected_30d_profit": 150.30, "payback_days": None,
            "target_buy_cost_buy": 6.85, "target_buy_cost_stretch": 5.20,
            "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
            "buy_plan_status": "BLOCKED_BY_VERDICT",
            "risk_flags": ["INSUFFICIENT_HISTORY"],
        },
    ]
    return pd.DataFrame(rows)


def _normalised_html() -> str:
    df = _fixture_df()
    payload = build_payload(df, run_id=RUN_ID, strategy="supplier_pricelist", supplier="test")
    payload["generated_at"] = "2026-05-03T12:00:00Z"  # freeze for determinism
    # Mirror the engine's run-time flow: populate each row's analyst
    # block via the deterministic fallback before rendering. This is
    # what `fba_engine/steps/buy_plan_html.py:add_buy_plan_html` does
    # in production. Without this, the snapshot would capture an
    # empty-analyst layout — undetected drift in the v2 sections.
    for row in payload["rows"]:
        row["analyst"] = fallback_analyse(row)
    return render_html(payload)


def test_html_snapshot_matches(request):
    actual = _normalised_html()
    if request.config.getoption("--snapshot-update", default=False):
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(actual, encoding="utf-8")
        pytest.skip("snapshot updated")
    if not SNAPSHOT_PATH.exists():
        pytest.fail(
            f"Snapshot missing at {SNAPSHOT_PATH}; "
            "run pytest with --snapshot-update to create it."
        )
    expected = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "HTML structure drifted; review changes and re-run with "
        "--snapshot-update if the change is intentional."
    )
