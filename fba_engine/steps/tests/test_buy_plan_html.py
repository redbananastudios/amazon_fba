"""Tests for fba_engine.steps.buy_plan_html — runner wrapper."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from bs4 import BeautifulSoup

from fba_engine.steps.buy_plan_html import (
    BUYER_REPORT_OUTPUTS,
    add_buy_plan_html,
    run_step,
)


def _row(verdict: str, **overrides) -> dict:
    base = {
        "asin": "B0TEST00001",
        "product_name": "Test product",
        "brand": "TestBrand",
        "supplier": "abgee",
        "supplier_sku": "SKU-X",
        "amazon_url": "https://www.amazon.co.uk/dp/B0TEST00001",
        "decision": "SHORTLIST",
        "opportunity_verdict": verdict,
        "opportunity_confidence": "HIGH",
        "opportunity_score": 80,
        "next_action": "test action",
        "buy_cost": 4.0, "market_price": 16.85,
        "raw_conservative_price": 16.85, "fees_conservative": 4.5,
        "profit_conservative": 8.35, "roi_conservative": 1.114,
        "fba_seller_count": 4, "amazon_on_listing": "N",
        "amazon_bb_pct_90": 0.10, "price_volatility_90d": 0.10,
        "sales_estimate": 250, "predicted_velocity_mid": 130,
        "bsr_drops_30d": 200,
        "order_qty_recommended": 13, "capital_required": 52.0,
        "projected_30d_units": 18, "projected_30d_revenue": 303.30,
        "projected_30d_profit": 150.30, "payback_days": 21.7,
        "target_buy_cost_buy": 9.5, "target_buy_cost_stretch": 8.52,
        "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
        "buy_plan_status": "OK", "risk_flags": [],
    }
    base.update(overrides)
    return base


def test_writes_json_and_html(tmp_path):
    df = pd.DataFrame([_row("BUY")])
    add_buy_plan_html(
        df, run_dir=tmp_path, timestamp="20260503_120000",
        strategy="supplier_pricelist", supplier="abgee",
    )
    json_path = tmp_path / "buyer_report_20260503_120000.json"
    html_path = tmp_path / "buyer_report_20260503_120000.html"
    assert json_path.exists()
    assert html_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert len(data["rows"]) == 1
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    assert soup.find("article", id="asin-B0TEST00001") is not None


def test_template_prose_filled_in_when_engine_alone(tmp_path):
    df = pd.DataFrame([_row("BUY")])
    add_buy_plan_html(
        df, run_dir=tmp_path, timestamp="20260503_120000",
        strategy="supplier_pricelist", supplier="abgee",
    )
    html = (tmp_path / "buyer_report_20260503_120000.html").read_text(encoding="utf-8")
    assert "<!-- prose:B0TEST00001 -->" not in html
    assert "prose-text" in html


def test_empty_df_writes_minimal_artefacts(tmp_path):
    add_buy_plan_html(
        pd.DataFrame(), run_dir=tmp_path, timestamp="20260503_120000",
        strategy="supplier_pricelist", supplier="abgee",
    )
    json_path = tmp_path / "buyer_report_20260503_120000.json"
    html_path = tmp_path / "buyer_report_20260503_120000.html"
    assert json_path.exists()
    assert html_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["rows"] == []


def test_disabled_via_config_writes_nothing(tmp_path, monkeypatch):
    from fba_config_loader import BuyPlanHtml
    monkeypatch.setattr(
        "fba_engine.steps.buy_plan_html.get_buy_plan_html",
        lambda: BuyPlanHtml(enabled=False),
    )
    add_buy_plan_html(
        pd.DataFrame([_row("BUY")]), run_dir=tmp_path,
        timestamp="20260503", strategy="supplier_pricelist", supplier="abgee",
    )
    assert not (tmp_path / "buyer_report_20260503.json").exists()
    assert not (tmp_path / "buyer_report_20260503.html").exists()


def test_kill_rows_excluded(tmp_path):
    df = pd.DataFrame([
        _row("BUY"),
        _row("KILL", asin="B0KILL00001"),
    ])
    add_buy_plan_html(
        df, run_dir=tmp_path, timestamp="20260503",
        strategy="supplier_pricelist", supplier="abgee",
    )
    data = json.loads((tmp_path / "buyer_report_20260503.json").read_text(encoding="utf-8"))
    asins = {r["asin"] for r in data["rows"]}
    assert asins == {"B0TEST00001"}
    # KILL is counted in verdict_counts but excluded from rows.
    assert data["verdict_counts"]["KILL"] == 1


def test_run_step_reads_runner_config(tmp_path):
    df = pd.DataFrame([_row("BUY")])
    out = run_step(df, {
        "run_dir": str(tmp_path), "timestamp": "20260503",
        "strategy": "supplier_pricelist", "supplier": "abgee",
    })
    assert out is df  # passes through unchanged
    assert (tmp_path / "buyer_report_20260503.json").exists()


def test_run_step_supports_output_dir_alias(tmp_path):
    df = pd.DataFrame([_row("BUY")])
    out = run_step(df, {
        "output_dir": str(tmp_path), "timestamp": "20260503",
        "strategy": "keepa_finder",
    })
    assert (tmp_path / "buyer_report_20260503.json").exists()


def test_run_step_skips_when_run_dir_missing(tmp_path, caplog):
    import logging
    df = pd.DataFrame([_row("BUY")])
    with caplog.at_level(logging.WARNING):
        out = run_step(df, {"timestamp": "20260503"})
    # No files written.
    assert not list(tmp_path.glob("*.json"))
    # df returned unchanged.
    assert out is df


def test_single_asin_filename_pattern(tmp_path):
    df = pd.DataFrame([_row("BUY")])
    add_buy_plan_html(
        df, run_dir=tmp_path, timestamp="20260503", asin="B0TEST00001",
        strategy="single_asin", supplier=None,
    )
    assert (tmp_path / "buyer_report_B0TEST00001_20260503.json").exists()
    assert (tmp_path / "buyer_report_B0TEST00001_20260503.html").exists()


def test_buyer_report_outputs_constant_exposes_filename_pattern():
    assert BUYER_REPORT_OUTPUTS == ("buyer_report_{ts}.json", "buyer_report_{ts}.html")


def test_does_not_mutate_input_df(tmp_path):
    df = pd.DataFrame([_row("BUY")])
    df_before = df.copy()
    add_buy_plan_html(
        df, run_dir=tmp_path, timestamp="20260503",
        strategy="supplier_pricelist", supplier="abgee",
    )
    pd.testing.assert_frame_equal(df, df_before)
