"""Tests for renderer.py — HTML structure emission (v2 design)."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from sourcing_engine.buy_plan_html.renderer import render_html


def _row_payload(asin: str, analyst_verdict: str, **overrides) -> dict:
    """Build a row payload with the analyst block populated.

    Mirrors the shape `payload.build_row_payload` produces + the
    `analyst.fallback_analyse` output. Tests inject specific
    values into the analyst block by overriding `analyst`.
    """
    base = {
        "asin": asin,
        "title": f"Title for {asin}",
        "brand": "TestBrand",
        "supplier": "test-supplier",
        "supplier_sku": "SKU-X",
        "amazon_url": f"https://www.amazon.co.uk/dp/{asin}",
        "image_url": f"https://images-na.ssl-images-amazon.com/images/P/{asin}.jpg",
        "engine_verdict": "BUY",
        "engine_verdict_confidence": "HIGH",
        "engine_opportunity_score": 80,
        "next_action": "test engine next-action",
        "analyst": {
            "verdict": analyst_verdict,
            "verdict_confidence": "HIGH",
            "score": 80,
            "dimensions": [
                {"name": "Profit", "score": 22, "max": 25, "rationale": "healthy ROI"},
                {"name": "Competition", "score": 20, "max": 25, "rationale": "few sellers"},
                {"name": "Stability", "score": 18, "max": 25, "rationale": "stable"},
                {"name": "Operational", "score": 20, "max": 25, "rationale": "ungated"},
            ],
            "trend_arrows": {"sales": "↗", "sellers": "→", "price": "→"},
            "trend_story": "Demand rising, supply steady — entrance window.",
            "narrative": "Strong economics; place a test order.",
            "action_prompt": "Place a test order at the size suggested.",
        },
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
        "trends": {
            "bsr_slope_30d": -0.005, "bsr_slope_90d": -0.005,
            "bsr_slope_365d": 0.0, "joiners_90d": 0,
            "fba_count_90d_start": 4, "bb_drop_pct_90": 5.0,
            "buy_box_avg_30d": 16.85, "buy_box_avg_90d": 16.85,
            "buy_box_min_365d": 14.0, "buy_box_oos_pct_90": 0.05,
            "listing_age_days": 800,
        },
        "metrics": [
            {"key": "fba_seller_count", "label": "FBA Sellers",
             "value_display": "4", "verdict": "green", "rationale": "≤ 5"},
            {"key": "sales_estimate", "label": "Listing Sales/mo",
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

    def test_buy_row_produces_card(self):
        p = _payload(rows=[_row_payload("B0BUY00001", "BUY")])
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        card = soup.find("article", id="asin-B0BUY00001")
        assert card is not None
        assert "verdict-buy" in card.get("class", [])
        # Image rail wraps in anchor pointing to amazon_url.
        a = card.find("a", class_="card-image")
        assert a is not None
        assert a.get("href") == "https://www.amazon.co.uk/dp/B0BUY00001"

    def test_section_heading_with_count(self):
        p = _payload(rows=[
            _row_payload("B0001AAAAA", "BUY"),
            _row_payload("B0002BBBBB", "BUY"),
        ])
        out = render_html(p)
        assert "BUY (2)" in out

    def test_per_verdict_section_ordering(self):
        rows = [
            _row_payload("B0SKIP00001", "SKIP"),
            _row_payload("B0WAIT00001", "WAIT"),
            _row_payload("B0SRC0001AA", "SOURCE"),
            _row_payload("B0NEG0001AA", "NEGOTIATE"),
            _row_payload("B0BUY00001", "BUY"),
        ]
        out = render_html(_payload(rows=rows))
        # BUY → NEGOTIATE → SOURCE → WAIT → SKIP
        i_buy = out.find('id="section-buy"')
        i_neg = out.find('id="section-negotiate"')
        i_src = out.find('id="section-source"')
        i_wait = out.find('id="section-wait"')
        i_skip = out.find('id="section-skip"')
        assert -1 < i_buy < i_neg < i_src < i_wait < i_skip

    @pytest.mark.parametrize("verdict", ["BUY", "NEGOTIATE", "SOURCE", "WAIT", "SKIP"])
    def test_card_has_verdict_badge(self, verdict):
        asin = f"B0{verdict[:6]:06s}1"[:10]
        p = _payload(rows=[_row_payload(asin, verdict)])
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        card = soup.find("article", id=f"asin-{asin}")
        assert card is not None, f"card not rendered for {verdict}"
        badge = card.find("div", class_="verdict-badge")
        assert badge is not None
        assert verdict in badge.get_text()

    @pytest.mark.parametrize("verdict", ["BUY", "NEGOTIATE", "SOURCE", "WAIT", "SKIP"])
    def test_card_has_dimension_breakdown(self, verdict):
        asin = f"B0{verdict[:6]:06s}1"[:10]
        p = _payload(rows=[_row_payload(asin, verdict)])
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        dim_rows = soup.select(".dimensions tbody tr")
        assert len(dim_rows) == 4, f"{verdict} card missing dimension breakdown"
        profit_row = next(r for r in dim_rows if "Profit" in r.get_text())
        assert "22" in profit_row.get_text()

    @pytest.mark.parametrize("verdict", ["BUY", "NEGOTIATE", "SOURCE", "WAIT", "SKIP"])
    def test_card_has_buyers_read_section(self, verdict):
        asin = f"B0{verdict[:6]:06s}1"[:10]
        p = _payload(rows=[_row_payload(asin, verdict)])
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        section = soup.find("section", class_="card-buyers-read")
        assert section is not None, f"{verdict} card missing buyer's read"
        narrative = section.find("p", class_="buyers-narrative")
        assert narrative is not None
        # Fixture narrative is the same string across verdicts.
        assert "Strong economics" in narrative.get_text()

    @pytest.mark.parametrize("verdict", ["BUY", "NEGOTIATE", "SOURCE", "WAIT", "SKIP"])
    def test_card_has_direction_section_with_arrows(self, verdict):
        asin = f"B0{verdict[:6]:06s}1"[:10]
        p = _payload(rows=[_row_payload(asin, verdict)])
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        section = soup.find("section", class_="card-direction")
        assert section is not None, f"{verdict} card missing direction section"
        arrows = section.select(".dir-arrow")
        # 3 arrows: sales / sellers / price
        assert len(arrows) == 3

    def test_economics_grid_uses_aim_for_dont_exceed_labels(self):
        p = _payload(rows=[_row_payload("B0BUY00001", "BUY")])
        out = render_html(p)
        # Fixed labels per Q2 brainstorm
        assert "Aim for" in out
        assert "Don&#x27;t exceed" in out or "Don't exceed" in out
        assert "stretch" not in out.lower()  # old confusing term gone

    def test_economics_shows_units_and_prices_inc_vat(self):
        p = _payload(rows=[_row_payload("B0BUY00001", "BUY")])
        out = render_html(p)
        # Order qty rendered with capital
        assert "13 units" in out
        # All £ shown with (inc) per VAT clarification
        assert "(inc)" in out

    def test_supporting_metrics_table_renders(self):
        p = _payload(rows=[_row_payload("B0BUY00001", "BUY")])
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        section = soup.find("section", class_="card-supporting")
        assert section is not None
        dots = section.select(".dot")
        assert len(dots) == 2  # 2 metrics in fixture

    def test_engine_cross_check_collapsible(self):
        p = _payload(rows=[_row_payload("B0BUY00001", "BUY")])
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        details = soup.find("details", class_="engine-note")
        assert details is not None
        assert "Engine cross-check" in details.find("summary").get_text()

    def test_within_verdict_buy_sorted_by_projected_30d_profit_desc(self):
        # BUY tier sorts by projected_30d_profit desc.
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
        out = render_html(_payload(rows=rows))
        i_high = out.find('id="asin-B0HIGH0001"')
        i_mid = out.find('id="asin-B0MID00001"')
        i_low = out.find('id="asin-B0LOW00001"')
        assert i_high > 0
        assert i_high < i_mid < i_low

    def test_toc_omitted_for_small_runs(self):
        p = _payload(rows=[_row_payload("B0SMALL001", "BUY")])
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        assert soup.find("nav", class_="toc") is None

    def test_toc_present_for_runs_above_threshold(self):
        rows = [_row_payload(f"B000000{i:03d}", "BUY") for i in range(4)]
        out = render_html(_payload(rows=rows))
        soup = BeautifulSoup(out, "html.parser")
        assert soup.find("nav", class_="toc") is not None

    def test_supplier_null_falls_back_in_title(self):
        out = render_html(_payload(rows=[], supplier=None, strategy="keepa_finder"))
        soup = BeautifulSoup(out, "html.parser")
        title_text = soup.find("title").string
        assert "keepa_finder" in title_text
        assert "None" not in title_text

    def test_html_escaped_in_title(self):
        rows = [_row_payload("B0XSS00001A", "BUY", title="<script>alert(1)</script>")]
        out = render_html(_payload(rows=rows))
        # The literal <script> tag must NOT appear; should be escaped.
        assert "<script>alert" not in out
        assert "&lt;script&gt;" in out
