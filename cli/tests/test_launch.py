"""Tests for cli.launch — the `open` subcommand.

Per `docs/PRD-sourcing-strategies.md` §12: target ~4 tests for the CLI
launch helpers. We test URL construction (no browser opened) plus the
dispatch loop.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from cli.launch import (
    AMAZON_DOMAIN,
    KEEPA_MARKETPLACE_CODE,
    VALID_TARGETS,
    amazon_url,
    keepa_url,
    launch_urls,
    main,
    resolve_urls,
    storefront_url,
    supplier_search_urls,
)


# ---------------------------------------------------------------------------
# URL builders (pure)
# ---------------------------------------------------------------------------


class TestUrlBuilders:
    def test_keepa_url_uk_marketplace(self):
        url = keepa_url("B0CLEAN")
        assert f"keepa.com/#!product/{KEEPA_MARKETPLACE_CODE}-B0CLEAN" in url

    def test_amazon_url_uk_domain(self):
        assert amazon_url("B0CLEAN") == f"https://www.{AMAZON_DOMAIN}/dp/B0CLEAN"

    def test_storefront_url_includes_seller_id(self):
        url = storefront_url("A1B2C3D4E5")
        parsed = urlparse(url)
        assert parsed.netloc == f"www.{AMAZON_DOMAIN}"
        assert parsed.path == "/sp"
        assert parse_qs(parsed.query)["seller"] == ["A1B2C3D4E5"]


# ---------------------------------------------------------------------------
# supplier_search_urls
# ---------------------------------------------------------------------------


class TestSupplierSearchUrls:
    def test_brand_renders_three_urls(self):
        pairs = supplier_search_urls(brand="Acme", product_name="Widget Pro")
        # Default canonical config has 3 templates; with both brand and
        # product, all 3 should fire.
        assert len(pairs) == 3
        # Ensure every URL is a Google search.
        for label, url in pairs:
            assert url.startswith("https://www.google.com/search?q=")

    def test_brand_missing_skips_brand_only_templates(self):
        # Without a brand, only the product_wholesale template fires
        # (brand_distributor + brand_trade have skip_if_brand_missing=true).
        pairs = supplier_search_urls(brand="", product_name="Widget Pro")
        labels = [label for label, _ in pairs]
        assert "Product wholesale" in labels
        assert "Brand distributor UK" not in labels
        assert "Brand trade account" not in labels


# ---------------------------------------------------------------------------
# resolve_urls — dispatcher
# ---------------------------------------------------------------------------


class TestResolveUrls:
    def test_keepa_target(self):
        urls = resolve_urls(target="keepa", asin="B0CLEAN")
        assert len(urls) == 1
        assert "keepa.com" in urls[0][1]

    def test_amazon_target(self):
        urls = resolve_urls(target="amazon", asin="B0CLEAN")
        assert len(urls) == 1
        assert AMAZON_DOMAIN in urls[0][1]

    def test_storefront_target(self):
        urls = resolve_urls(target="storefront", seller="A1B2C3D4E5")
        assert len(urls) == 1
        assert "seller=A1B2C3D4E5" in urls[0][1]

    def test_supplier_target_with_brand(self):
        urls = resolve_urls(
            target="supplier", brand="Acme", product_name="Widget"
        )
        assert len(urls) == 3

    def test_supplier_target_with_only_product_name(self):
        urls = resolve_urls(
            target="supplier", brand="", product_name="Generic Mug"
        )
        # Only the product_wholesale template fires.
        assert len(urls) == 1
        assert "Product wholesale" in urls[0][0]

    def test_unknown_target_raises(self):
        with pytest.raises(ValueError, match="Unknown --target"):
            resolve_urls(target="invalid", asin="B0")

    def test_keepa_without_asin_raises(self):
        with pytest.raises(ValueError, match="requires --asin"):
            resolve_urls(target="keepa")

    def test_storefront_without_seller_raises(self):
        with pytest.raises(ValueError, match="requires --seller"):
            resolve_urls(target="storefront")

    def test_supplier_without_brand_or_product_raises(self):
        with pytest.raises(ValueError, match="requires --brand"):
            resolve_urls(target="supplier")


# ---------------------------------------------------------------------------
# launch_urls — actually opens browser, but mocked
# ---------------------------------------------------------------------------


class TestLaunchUrls:
    def test_calls_open_fn_for_each_url(self):
        opened: list[str] = []
        logged: list[str] = []
        urls = [("A", "https://example.com/a"), ("B", "https://example.com/b")]
        count = launch_urls(
            urls, open_fn=lambda u: (opened.append(u), True)[1], log_fn=logged.append
        )
        assert count == 2
        assert opened == ["https://example.com/a", "https://example.com/b"]
        # Each URL gets a log line.
        assert any("https://example.com/a" in line for line in logged)

    def test_empty_url_list_is_noop(self):
        opened: list[str] = []
        count = launch_urls([], open_fn=lambda u: (opened.append(u), True)[1])
        assert count == 0
        assert opened == []


# ---------------------------------------------------------------------------
# main — argparse + dispatch
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_invalid_target_returns_2_via_argparse(self, capsys):
        # Invalid choice triggers argparse SystemExit(2) before our code runs.
        with pytest.raises(SystemExit):
            main(["--target", "not-a-target", "--asin", "B0"])

    def test_main_missing_asin_returns_1_with_friendly_error(
        self, capsys, monkeypatch
    ):
        # webbrowser.open should never be called; if it is, the test fails.
        monkeypatch.setattr(
            "cli.launch.webbrowser.open",
            lambda url: pytest.fail(f"webbrowser.open called with {url}"),
        )
        rc = main(["--target", "keepa"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "requires --asin" in captured.err

    def test_main_keepa_invokes_open(self, monkeypatch):
        opened: list[str] = []
        monkeypatch.setattr("cli.launch.webbrowser.open", opened.append)
        rc = main(["--target", "keepa", "--asin", "B0CLEAN"])
        assert rc == 0
        assert len(opened) == 1
        assert "B0CLEAN" in opened[0]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_valid_targets_pinned(self):
        assert set(VALID_TARGETS) == {"keepa", "amazon", "supplier", "storefront"}
