"""Tests for renderer.py — HTML skeleton emission."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from sourcing_engine.buy_plan_html.renderer import render_html


def _row_payload(asin: str, verdict: str, **overrides) -> dict:
    base = {
        "asin": asin,
        "title": f"Title for {asin}",
        "brand": "TestBrand",
        "supplier": "test-supplier",
        "supplier_sku": "SKU-X",
        "amazon_url": f"https://www.amazon.co.uk/dp/{asin}",
        "image_url": f"https://images-na.ssl-images-amazon.com/images/P/{asin}.jpg",
        "verdict": verdict,
        "verdict_confidence": "HIGH",
        "opportunity_score": 80,
        "next_action": "test action",
        "economics": {
            "buy_cost_gbp": 4.00, "market_price_gbp": 16.85,
            "profit_per_unit_gbp": 8.35, "roi_conservative_pct": 1.114,
            "target_buy_cost_gbp": 9.50, "target_buy_cost_stretch_gbp": 8.52,
        },
        "buy_plan": {
            "order_qty_recommended": 13, "capital_required_gbp": 52.0,
            "projected_30d_units": 18, "projected_30d_revenue_gbp": 303.30,
            "projected_30d_profit_gbp": 150.30, "payback_days": 21.7,
            "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
            "buy_plan_status": "OK",
        },
        "metrics": [
            {"key": "fba_seller_count", "label": "FBA Sellers",
             "value_display": "4", "verdict": "green", "rationale": "≤ 5"},
            {"key": "sales_estimate", "label": "Volume",
             "value_display": "250", "verdict": "green", "rationale": "above target"},
        ],
        "engine_reasons": [], "engine_blockers": [], "risk_flags": [],
    }
    base.update(overrides)
    return base


def _payload(rows: list[dict], **kwargs) -> dict:
    base = {
        "schema_version": 1, "prompt_version": 1,
        "run_id": "20260503_120000", "strategy": "supplier_pricelist",
        "supplier": "test-supplier", "generated_at": "2026-05-03T12:00:00Z",
        "verdict_counts": {"BUY": 0, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        "rows": rows,
    }
    base.update(kwargs)
    return base


class TestRenderHtmlStructure:
    def test_empty_payload_produces_valid_html_with_no_actionable_notice(self):
        out = render_html(_payload(rows=[]))
        soup = BeautifulSoup(out, "html.parser")
        assert soup.find("html") is not None
        assert soup.find("h1") is not None
        assert soup.find("article", class_="card") is None
        assert "no actionable rows" in out.lower()

    def test_buy_row_produces_card_with_marker(self):
        p = _payload(
            rows=[_row_payload("B0BUY00001", "BUY")],
            verdict_counts={"BUY": 1, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        card = soup.find("article", id="asin-B0BUY00001")
        assert card is not None
        assert "verdict-buy" in card.get("class", [])
        a = card.find("a", class_="card-image")
        assert a is not None
        assert a.get("href") == "https://www.amazon.co.uk/dp/B0BUY00001"
        img = a.find("img")
        assert img is not None
        assert "B0BUY00001.jpg" in img.get("src", "")
        assert "<!-- prose:B0BUY00001 -->" in out

    def test_section_heading_with_count(self):
        p = _payload(
            rows=[_row_payload("B0001AAAAA", "BUY"), _row_payload("B0002BBBBB", "BUY")],
            verdict_counts={"BUY": 2, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        # h2 says "BUY (2)".
        assert "BUY (2)" in out

    def test_per_verdict_section_ordering(self):
        rows = [
            _row_payload("B0WATCH001", "WATCH"),
            _row_payload("B0SRC0001A", "SOURCE_ONLY"),
            _row_payload("B0NEG0001A", "NEGOTIATE"),
            _row_payload("B0BUY00001", "BUY"),
        ]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 1, "SOURCE_ONLY": 1, "NEGOTIATE": 1, "WATCH": 1, "KILL": 0},
        )
        out = render_html(p)
        i_buy = out.find('id="section-buy"')
        i_src = out.find('id="section-source-only"')
        i_neg = out.find('id="section-negotiate"')
        i_watch = out.find('id="section-watch"')
        assert -1 < i_buy < i_src < i_neg < i_watch

    def test_metrics_table_has_traffic_light_dots(self):
        p = _payload(
            rows=[_row_payload("B0BUY00001", "BUY")],
            verdict_counts={"BUY": 1, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        dots = soup.select(".card-scoring .dot")
        assert len(dots) == 2
        for span in dots:
            assert "dot-green" in span.get("class", [])

    def test_buy_card_economics_grid_has_order_qty(self):
        p = _payload(
            rows=[_row_payload("B0BUY00001", "BUY")],
            verdict_counts={"BUY": 1, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        assert "Order qty" in out
        assert "13" in out
        assert "£52.00" in out

    def test_source_only_economics_grid_has_no_supplier_label(self):
        rows = [_row_payload(
            "B0SRC0001A", "SOURCE_ONLY",
            economics={
                "buy_cost_gbp": None, "market_price_gbp": 16.85,
                "profit_per_unit_gbp": None, "roi_conservative_pct": None,
                "target_buy_cost_gbp": 4.85, "target_buy_cost_stretch_gbp": 4.10,
            },
            buy_plan={
                "order_qty_recommended": None, "capital_required_gbp": None,
                "projected_30d_units": 42, "projected_30d_revenue_gbp": 710.00,
                "projected_30d_profit_gbp": 136.00, "payback_days": None,
                "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
                "buy_plan_status": "NO_BUY_COST",
            },
        )]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 0, "SOURCE_ONLY": 1, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        assert "no supplier yet" in out
        assert "£4.85" in out

    def test_negotiate_economics_grid_has_gap(self):
        rows = [_row_payload(
            "B0NEG0001A", "NEGOTIATE",
            economics={
                "buy_cost_gbp": 5.00, "market_price_gbp": 16.85,
                "profit_per_unit_gbp": 1.50, "roi_conservative_pct": 0.20,
                "target_buy_cost_gbp": 4.38, "target_buy_cost_stretch_gbp": 3.50,
            },
            buy_plan={
                "order_qty_recommended": None, "capital_required_gbp": None,
                "projected_30d_units": 18, "projected_30d_revenue_gbp": 303.30,
                "projected_30d_profit_gbp": 42.30, "payback_days": None,
                "gap_to_buy_gbp": 0.62, "gap_to_buy_pct": 0.124,
                "buy_plan_status": "OK",
            },
        )]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 0, "SOURCE_ONLY": 0, "NEGOTIATE": 1, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        assert "£0.62" in out
        assert "12.4%" in out

    def test_within_verdict_buy_sorted_by_projected_30d_profit_desc(self):
        rows = [
            _row_payload("B0LOW00001", "BUY", buy_plan={
                **_row_payload("X", "BUY")["buy_plan"],
                "projected_30d_profit_gbp": 20.0,
            }),
            _row_payload("B0HIGH0001", "BUY", buy_plan={
                **_row_payload("X", "BUY")["buy_plan"],
                "projected_30d_profit_gbp": 200.0,
            }),
            _row_payload("B0MID00001", "BUY", buy_plan={
                **_row_payload("X", "BUY")["buy_plan"],
                "projected_30d_profit_gbp": 80.0,
            }),
        ]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 3, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        i_high = out.find('id="asin-B0HIGH0001"')
        i_mid = out.find('id="asin-B0MID00001"')
        i_low = out.find('id="asin-B0LOW00001"')
        assert i_high > 0
        assert i_high < i_mid < i_low

    def test_toc_omitted_for_small_runs(self):
        rows = [_row_payload("B0SMALL001", "BUY")]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 1, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        assert soup.find("nav", class_="toc") is None

    def test_toc_present_for_runs_above_threshold(self):
        rows = [_row_payload(f"B000000{i:03d}", "BUY") for i in range(4)]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 4, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        assert soup.find("nav", class_="toc") is not None

    def test_supplier_null_falls_back_in_title(self):
        p = _payload(rows=[], supplier=None, strategy="keepa_finder")
        out = render_html(p)
        assert "keepa_finder" in out
        # Title contains strategy not the literal "None" string.
        # (Allow "None" to not appear in user-visible places.)
        # Specifically the title element:
        soup = BeautifulSoup(out, "html.parser")
        title_text = soup.find("title").string
        assert "keepa_finder" in title_text
        assert "None" not in title_text

    def test_html_escaped_in_title(self):
        rows = [_row_payload("B0XSS00001A", "BUY", title="<script>alert(1)</script>")]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 1, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        # The literal <script> tag must NOT appear; should be escaped.
        assert "<script>" not in out
        assert "&lt;script&gt;" in out
