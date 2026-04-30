"""Tests for fba_engine.steps.keepa_enrich.

Enrichment step that takes a DataFrame with an ASIN column, fetches
market data per ASIN via the keepa_client batch, and joins the
canonical engine's market columns onto each row.

This is the missing connector that lets ASIN-only sources (oa_csv,
seller_storefront, future Keepa Finder) chain into the
calculate -> decide steps.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from fba_engine.steps.keepa_enrich import (
    KEEPA_ENRICH_COLUMNS,
    enrich_with_keepa,
    run_step,
)
from keepa_client import KeepaProduct
from keepa_client.models import KeepaStats


def _stub_client(products: list[KeepaProduct]) -> MagicMock:
    client = MagicMock()
    client.get_products.return_value = products
    return client


def _product(
    asin: str,
    *,
    amazon: int | None = 1499,
    new_fba: int | None = 1450,
    buy_box: int | None = 1525,
    sales_rank: int | None = 5234,
    fba_offers: int | None = 5,
    monthly_sold: int | None = 250,
    title: str | None = None,
    brand: str | None = None,
) -> KeepaProduct:
    """Build a stats-bearing KeepaProduct with the indices the engine needs."""
    current: list[int] = [-1] * 19
    current[0] = -1 if amazon is None else amazon
    current[3] = -1 if sales_rank is None else sales_rank
    current[10] = -1 if new_fba is None else new_fba
    current[11] = -1 if fba_offers is None else fba_offers
    current[18] = -1 if buy_box is None else buy_box
    avg90 = list(current)
    return KeepaProduct(
        asin=asin,
        title=title,
        brand=brand,
        stats=KeepaStats(current=current, avg90=avg90),
        monthlySold=-1 if monthly_sold is None else monthly_sold,
    )


class TestEnrichWithKeepa:
    def test_appends_market_columns_to_each_row(self):
        df = pd.DataFrame([
            {"asin": "B0AAA"},
            {"asin": "B0BBB"},
        ])
        client = _stub_client([_product("B0AAA"), _product("B0BBB")])
        out = enrich_with_keepa(df, client=client)
        # All canonical market columns should be present.
        for col in KEEPA_ENRICH_COLUMNS:
            assert col in out.columns
        # Two rows in, two rows out (no duplication).
        assert len(out) == 2

    def test_preserves_input_columns(self):
        df = pd.DataFrame([
            {"asin": "B0AAA", "retail_cost_inc_vat": 5.0, "source": "oa_csv"},
        ])
        client = _stub_client([_product("B0AAA")])
        out = enrich_with_keepa(df, client=client)
        assert out.iloc[0]["retail_cost_inc_vat"] == 5.0
        assert out.iloc[0]["source"] == "oa_csv"
        # Plus the new market columns.
        assert out.iloc[0]["buy_box_price"] == 15.25

    def test_calls_get_products_with_unique_asins(self):
        # Don't burn tokens on duplicates — the batch method dedupes
        # internally, but we should still pass unique ASINs.
        df = pd.DataFrame([
            {"asin": "B0AAA"},
            {"asin": "B0BBB"},
            {"asin": "B0AAA"},  # duplicate
        ])
        client = _stub_client([_product("B0AAA"), _product("B0BBB")])
        enrich_with_keepa(df, client=client)
        called_asins = client.get_products.call_args.args[0]
        assert sorted(set(called_asins)) == ["B0AAA", "B0BBB"]

    def test_join_preserves_row_order(self):
        # Output rows are in the SAME order as input rows. Critical for
        # callers that paired the enriched df with another by index.
        df = pd.DataFrame([
            {"asin": "B0CCC"}, {"asin": "B0AAA"}, {"asin": "B0BBB"},
        ])
        client = _stub_client([
            _product("B0AAA", amazon=100), _product("B0BBB", amazon=200),
            _product("B0CCC", amazon=300),
        ])
        out = enrich_with_keepa(df, client=client)
        assert list(out["asin"]) == ["B0CCC", "B0AAA", "B0BBB"]
        assert list(out["amazon_price"]) == [3.00, 1.00, 2.00]

    def test_missing_keepa_record_yields_none_market_cols(self):
        # If Keepa doesn't have a record for an ASIN (filtered null,
        # stale-on-error miss), the input row stays in the output but
        # market columns are None. Caller can filter these downstream.
        df = pd.DataFrame([
            {"asin": "B0HAVE"}, {"asin": "B0DEAD"},
        ])
        client = _stub_client([_product("B0HAVE")])
        out = enrich_with_keepa(df, client=client)
        assert len(out) == 2
        have = out[out["asin"] == "B0HAVE"].iloc[0]
        dead = out[out["asin"] == "B0DEAD"].iloc[0]
        assert have["buy_box_price"] == 15.25
        assert pd.isna(dead["buy_box_price"])

    def test_empty_input_returns_empty_with_canonical_columns(self):
        df = pd.DataFrame(columns=["asin"])
        client = _stub_client([])
        out = enrich_with_keepa(df, client=client)
        assert out.empty
        for col in KEEPA_ENRICH_COLUMNS:
            assert col in out.columns

    def test_missing_asin_column_raises(self):
        df = pd.DataFrame([{"title": "no asin column"}])
        client = _stub_client([])
        with pytest.raises(ValueError, match="asin"):
            enrich_with_keepa(df, client=client)

    def test_custom_asin_column_name(self):
        # Some upstream feeds may use 'ASIN' / 'product_id' / etc.
        # Caller passes asin_col to override the default.
        df = pd.DataFrame([{"product_id": "B0AAA"}])
        client = _stub_client([_product("B0AAA")])
        out = enrich_with_keepa(df, asin_col="product_id", client=client)
        assert out.iloc[0]["amazon_price"] == 14.99

    def test_does_not_overwrite_existing_market_columns(self):
        # If the input df already has an `amazon_price` column (e.g.
        # from a previous enrichment pass), enrich must NOT silently
        # blow it away. Raise to force the caller to be explicit.
        df = pd.DataFrame([
            {"asin": "B0AAA", "amazon_price": 99.99},
        ])
        client = _stub_client([_product("B0AAA")])
        with pytest.raises(ValueError, match="already present|amazon_price"):
            enrich_with_keepa(df, client=client)

    def test_overwrite_flag_allows_re_enrichment(self):
        # Re-enrichment is sometimes legitimate (TTL expired in the
        # outer pipeline; user wants fresh data). overwrite=True
        # accepts the trade-off and replaces existing columns.
        df = pd.DataFrame([
            {"asin": "B0AAA", "amazon_price": 99.99},
        ])
        client = _stub_client([_product("B0AAA")])
        out = enrich_with_keepa(df, client=client, overwrite=True)
        assert out.iloc[0]["amazon_price"] == 14.99


class TestRunStep:
    def test_run_step_uses_injected_client(self):
        df = pd.DataFrame([{"asin": "B0AAA"}])
        client = _stub_client([_product("B0AAA")])
        out = run_step(df, {"client": client})
        assert out.iloc[0]["buy_box_price"] == 15.25

    def test_run_step_respects_asin_col(self):
        df = pd.DataFrame([{"ASIN": "B0AAA"}])
        client = _stub_client([_product("B0AAA")])
        out = run_step(df, {"client": client, "asin_col": "ASIN"})
        assert out.iloc[0]["amazon_price"] == 14.99

    def test_run_step_empty_df_passes_through(self):
        df = pd.DataFrame(columns=["asin"])
        client = _stub_client([])
        out = run_step(df, {"client": client})
        assert out.empty


class TestColumnsConstant:
    def test_pinned(self):
        # Catches accidental drift in the canonical enrichment schema.
        # Downstream calculate / decide read these names. Market data
        # only — discovery owns descriptive fields (product_name, brand).
        assert KEEPA_ENRICH_COLUMNS == (
            "amazon_price",
            "new_fba_price",
            "buy_box_price",
            "buy_box_avg90",
            "fba_seller_count",
            "sales_rank",
            "monthly_sales_estimate",
        )


class TestDiscoveryToEnrichChain:
    def test_seller_storefront_output_is_compatible(self):
        # The seller_storefront discovery emits product_name + brand.
        # Those columns MUST NOT clash with KEEPA_ENRICH_COLUMNS — the
        # whole point is that discovery → keepa_enrich chains cleanly
        # without an overwrite=True flag. Pin that contract here so a
        # future addition to KEEPA_ENRICH_COLUMNS can't silently break
        # the chain.
        from fba_engine.steps.seller_storefront import (
            SELLER_STOREFRONT_DISCOVERY_COLUMNS,
        )
        clash = set(SELLER_STOREFRONT_DISCOVERY_COLUMNS) & set(KEEPA_ENRICH_COLUMNS)
        assert not clash, (
            f"discovery + enrichment column collision: {clash}. "
            f"Either rename in discovery, or drop from KEEPA_ENRICH_COLUMNS."
        )

    def test_oa_csv_output_is_compatible(self):
        from fba_engine.steps.oa_csv import OA_DISCOVERY_COLUMNS
        clash = set(OA_DISCOVERY_COLUMNS) & set(KEEPA_ENRICH_COLUMNS)
        assert not clash, (
            f"oa_csv + enrichment column collision: {clash}."
        )

    def test_discovery_output_chains_through_enrich_without_overwrite(self):
        # End-to-end pin: feed a seller_storefront-shaped df into
        # enrich and confirm no ValueError + no data loss.
        import pandas as pd
        df = pd.DataFrame([{
            "asin": "B0AAA",
            "source": "seller_storefront",
            "seller_id": "A1",
            "seller_name": "X",
            "product_name": "Discovery title",
            "brand": "Discovery brand",
            "category": "Tools",
            "amazon_url": "https://www.amazon.co.uk/dp/B0AAA",
        }])
        client = _stub_client([_product("B0AAA")])
        out = enrich_with_keepa(df, client=client)
        # Discovery columns survive untouched.
        assert out.iloc[0]["product_name"] == "Discovery title"
        assert out.iloc[0]["brand"] == "Discovery brand"
        # Market columns appended.
        assert out.iloc[0]["buy_box_price"] == 15.25
        assert out.iloc[0]["fba_seller_count"] == 5


class TestRowOrderWithNonDefaultIndex:
    def test_filtered_input_preserves_row_pairing(self):
        # If the caller filtered+sliced the df without resetting the
        # index, row pairing must still be ASIN-correct (not positional).
        import pandas as pd
        df = pd.DataFrame([
            {"asin": "B0AAA"},
            {"asin": "B0BBB"},
            {"asin": "B0CCC"},
        ])
        # Reverse-order slice; index becomes [2, 1, 0].
        df_filtered = df.iloc[[2, 0, 1]].copy()
        client = _stub_client([
            _product("B0AAA", amazon=100),
            _product("B0BBB", amazon=200),
            _product("B0CCC", amazon=300),
        ])
        out = enrich_with_keepa(df_filtered, client=client)
        # Output preserves the filtered order AND the asin→price pairing.
        assert list(out["asin"]) == ["B0CCC", "B0AAA", "B0BBB"]
        assert list(out["amazon_price"]) == [3.00, 1.00, 2.00]
