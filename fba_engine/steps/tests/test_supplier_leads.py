"""Tests for fba_engine.steps.supplier_leads (Skill 99 v1).

Per `docs/PRD-sourcing-strategies.md` §8: bridges the gap between
"we should sell this ASIN" and "here's where to source it" by generating
Google search URLs per shortlisted ASIN.

This step appends 3 URL columns to the DataFrame AND writes a
`supplier_leads.md` file in the run output directory.

PRD §12 target: ~6 tests for the supplier_leads step.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fba_engine.steps.supplier_leads import (
    DEFAULT_SUPPLIER_LEADS_CONFIG_PATH,
    SUPPLIER_SEARCH_COLUMNS,
    SearchTemplate,
    SupplierLeadsConfig,
    build_supplier_leads_md,
    compute_supplier_leads,
    load_supplier_leads_config,
    run_step,
)


# Reusable test config — explicit so tests don't depend on the canonical
# YAML's exact contents.
def _test_config() -> SupplierLeadsConfig:
    return SupplierLeadsConfig(
        search_templates=[
            SearchTemplate(
                id="brand_distributor",
                label="Brand distributor UK",
                template="{brand} distributor UK",
                skip_if_brand_missing=True,
            ),
            SearchTemplate(
                id="product_wholesale",
                label="Product wholesale",
                template="{product_name} wholesale",
                skip_if_brand_missing=False,
            ),
            SearchTemplate(
                id="brand_trade",
                label="Brand trade account",
                template="{brand} trade account",
                skip_if_brand_missing=True,
            ),
        ],
        search_engine_url="https://www.google.com/search?q=",
    )


# ---------------------------------------------------------------------------
# compute_supplier_leads — appends URL columns to the DataFrame
# ---------------------------------------------------------------------------


class TestComputeSupplierLeads:
    def test_appends_three_supplier_search_columns(self):
        df = pd.DataFrame([{
            "ASIN": "B001", "Brand": "Acme", "Product Name": "Widget Pro",
        }])
        out = compute_supplier_leads(df, _test_config())
        for col in SUPPLIER_SEARCH_COLUMNS:
            assert col in out.columns

    def test_brand_distributor_url_uses_brand(self):
        df = pd.DataFrame([{
            "ASIN": "B001", "Brand": "Acme", "Product Name": "Widget",
        }])
        out = compute_supplier_leads(df, _test_config())
        url = out.iloc[0]["supplier_search_brand_distributor"]
        assert url.startswith("https://www.google.com/search?q=")
        # URL-encoded "Acme distributor UK"
        assert "Acme" in url
        assert "distributor" in url
        assert "UK" in url

    def test_product_wholesale_url_uses_product_name(self):
        df = pd.DataFrame([{
            "ASIN": "B001", "Brand": "Acme", "Product Name": "Widget Pro 2024",
        }])
        out = compute_supplier_leads(df, _test_config())
        url = out.iloc[0]["supplier_search_product_wholesale"]
        # Spaces should be URL-encoded.
        assert "Widget" in url
        assert " " not in url  # no raw spaces in a URL
        assert "wholesale" in url

    def test_skip_if_brand_missing_leaves_brand_columns_empty(self):
        df = pd.DataFrame([{
            "ASIN": "B001", "Brand": "", "Product Name": "Widget Pro",
        }])
        out = compute_supplier_leads(df, _test_config())
        # Brand-templated columns should be empty strings (not None,
        # since CSV writers handle empty-string cleanly).
        assert out.iloc[0]["supplier_search_brand_distributor"] == ""
        assert out.iloc[0]["supplier_search_brand_trade"] == ""
        # Product-only template still fires.
        assert "Widget" in out.iloc[0]["supplier_search_product_wholesale"]

    def test_special_characters_url_encoded(self):
        # Brand "M&M's" should encode to "M%26M%27s" or similar.
        df = pd.DataFrame([{
            "ASIN": "B001", "Brand": "M&M's", "Product Name": "Candy + 50% More",
        }])
        out = compute_supplier_leads(df, _test_config())
        url = out.iloc[0]["supplier_search_brand_distributor"]
        # Raw `&` would terminate the query string — must be encoded.
        assert "%26" in url
        # Raw `+` would mean "space" in form-encoding — must be encoded too
        # OR we accept `+` for spaces and encode literal `+` differently.
        # Either is fine; just verify the resulting URL works without
        # ambiguity by parsing it.
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        # The "q" parameter, decoded, should match the original text.
        assert "M&M's" in params["q"][0]

    def test_empty_df_returns_empty_with_columns_added(self):
        df = pd.DataFrame(columns=["ASIN", "Brand", "Product Name"])
        out = compute_supplier_leads(df, _test_config())
        for col in SUPPLIER_SEARCH_COLUMNS:
            assert col in out.columns
        assert len(out) == 0

    def test_does_not_mutate_input_df(self):
        df = pd.DataFrame([{
            "ASIN": "B001", "Brand": "Acme", "Product Name": "Widget",
        }])
        before = df.copy()
        _ = compute_supplier_leads(df, _test_config())
        pd.testing.assert_frame_equal(df, before)

    def test_alternate_brand_column_name_tolerated(self):
        # Some upstream frames use "brand" / "product_name" (snake_case).
        # The step reads case-insensitively and tolerates either spelling.
        df = pd.DataFrame([{
            "asin": "B001", "brand": "Acme", "product_name": "Widget",
        }])
        out = compute_supplier_leads(df, _test_config())
        url = out.iloc[0]["supplier_search_brand_distributor"]
        assert "Acme" in url


# ---------------------------------------------------------------------------
# build_supplier_leads_md — markdown side-output
# ---------------------------------------------------------------------------


class TestBuildSupplierLeadsMd:
    def test_includes_per_row_section(self):
        df = pd.DataFrame([{
            "ASIN": "B0WIDGET",
            "Brand": "Acme",
            "Product Name": "Widget Pro",
            "Category": "Tools",
            "supplier_search_brand_distributor": "https://example/dist",
            "supplier_search_product_wholesale": "https://example/whole",
            "supplier_search_brand_trade": "https://example/trade",
        }])
        md = build_supplier_leads_md(df, niche="kids-toys")
        assert "B0WIDGET" in md
        assert "Widget Pro" in md
        assert "Acme" in md
        # Each search URL appears as a markdown link.
        assert "https://example/dist" in md
        assert "https://example/whole" in md
        assert "https://example/trade" in md

    def test_includes_header_with_niche(self):
        df = pd.DataFrame(columns=[
            "ASIN", "Brand", "Product Name", "Category",
            *SUPPLIER_SEARCH_COLUMNS,
        ])
        md = build_supplier_leads_md(df, niche="afro-hair")
        assert md.startswith("# Supplier leads")
        assert "afro-hair" in md

    def test_skips_url_links_when_url_is_empty(self):
        df = pd.DataFrame([{
            "ASIN": "B0NOMARK",
            "Brand": "",
            "Product Name": "Generic",
            "Category": "Misc",
            "supplier_search_brand_distributor": "",  # empty (skipped via brand-missing)
            "supplier_search_product_wholesale": "https://example/whole",
            "supplier_search_brand_trade": "",
        }])
        md = build_supplier_leads_md(df, niche="x")
        # Wholesale URL appears.
        assert "https://example/whole" in md
        # Brand-missing URLs should NOT appear (no link with empty href).
        assert "()" not in md
        assert "[Brand distributor UK]()" not in md

    def test_appends_keepa_and_amazon_links(self):
        df = pd.DataFrame([{
            "ASIN": "B0KEEPA",
            "Brand": "Acme",
            "Product Name": "X",
            "Category": "Y",
            "supplier_search_brand_distributor": "https://search/dist",
            "supplier_search_product_wholesale": "https://search/whole",
            "supplier_search_brand_trade": "https://search/trade",
        }])
        md = build_supplier_leads_md(df, niche="x")
        assert "keepa.com" in md
        assert "amazon.co.uk/dp/B0KEEPA" in md


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class TestLoadSupplierLeadsConfig:
    def test_load_from_canonical_path(self):
        path = DEFAULT_SUPPLIER_LEADS_CONFIG_PATH
        if not path.exists():
            pytest.skip(f"canonical config not found: {path}")
        config = load_supplier_leads_config(path)
        # Pin canonical structure.
        assert len(config.search_templates) >= 3
        assert config.search_engine_url.startswith("https://")

    def test_round_trip_minimal_config(self, tmp_path: Path):
        body = """\
search_templates:
  - id: brand_distributor
    label: "Brand distributor UK"
    template: "{brand} distributor UK"
    skip_if_brand_missing: true
search_engine_url: "https://example.com/search?q="
"""
        path = tmp_path / "leads.yaml"
        path.write_text(body, encoding="utf-8")
        config = load_supplier_leads_config(path)
        assert len(config.search_templates) == 1
        assert config.search_templates[0].id == "brand_distributor"
        assert config.search_templates[0].skip_if_brand_missing is True


# ---------------------------------------------------------------------------
# run_step contract
# ---------------------------------------------------------------------------


class TestRunStep:
    def test_run_step_appends_columns_with_default_config(self):
        df = pd.DataFrame([{
            "ASIN": "B001", "Brand": "Acme", "Product Name": "Widget",
        }])
        out = run_step(df, {})
        for col in SUPPLIER_SEARCH_COLUMNS:
            assert col in out.columns

    def test_run_step_writes_md_when_output_path_set(self, tmp_path: Path):
        df = pd.DataFrame([{
            "ASIN": "B001", "Brand": "Acme", "Product Name": "Widget",
            "Category": "Tools",
        }])
        out_path = tmp_path / "supplier_leads.md"
        run_step(df, {"output_md_path": str(out_path), "niche": "kids-toys"})
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "B001" in content
        assert "kids-toys" in content

    def test_run_step_with_explicit_config_path(self, tmp_path: Path):
        # Caller can override the canonical config.
        body = """\
search_templates:
  - id: only_one
    label: "Single"
    template: "{brand} only"
    skip_if_brand_missing: false
search_engine_url: "https://example/?q="
"""
        cfg_path = tmp_path / "leads.yaml"
        cfg_path.write_text(body, encoding="utf-8")
        df = pd.DataFrame([{"ASIN": "B0", "Brand": "Acme", "Product Name": "X"}])
        out = run_step(df, {"config_path": str(cfg_path)})
        assert "supplier_search_only_one" in out.columns
