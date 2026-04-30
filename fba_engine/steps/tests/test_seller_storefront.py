"""Tests for fba_engine.steps.seller_storefront.

Discovery step for the wholesale sourcing strategy: walks an Amazon
seller's storefront via Keepa and emits a canonical DataFrame of ASINs
+ product metadata. Per `docs/PRD-sourcing-strategies.md` §6.

The actual KeepaClient is injected via the `client` kwarg so tests
don't need real Keepa credentials or network access. The standalone
CLI / run_step path resolves the client from
`shared/config/keepa_client.yaml` and the `KEEPA_API_KEY` env var.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from fba_engine.steps.seller_storefront import (
    SELLER_STOREFRONT_DISCOVERY_COLUMNS,
    discover_seller_storefront,
    run_step,
)
from keepa_client import KeepaProduct, KeepaSeller


def _make_client(seller: KeepaSeller, products: list[KeepaProduct]) -> MagicMock:
    """Stand-in KeepaClient that returns the canned values."""
    client = MagicMock()
    client.get_seller.return_value = seller
    client.get_products.return_value = products
    return client


_SELLER = KeepaSeller(
    sellerId="A1B2C3D4E5",
    sellerName="Acme Storefront",
    asinList=["B0AAA", "B0BBB"],
)

_PRODUCTS = [
    KeepaProduct(
        asin="B0AAA", title="Widget A", brand="Acme",
        categoryTree=[{"catId": 11, "name": "Tools"}],
    ),
    KeepaProduct(
        asin="B0BBB", title="Widget B", brand="Acme",
        categoryTree=[{"catId": 11, "name": "Tools"}],
    ),
]


class TestDiscoverSellerStorefront:
    def test_returns_canonical_schema(self):
        client = _make_client(_SELLER, _PRODUCTS)
        out = discover_seller_storefront("A1B2C3D4E5", client=client)
        assert list(out.columns) == list(SELLER_STOREFRONT_DISCOVERY_COLUMNS)

    def test_one_row_per_asin(self):
        client = _make_client(_SELLER, _PRODUCTS)
        out = discover_seller_storefront("A1B2C3D4E5", client=client)
        assert len(out) == 2
        assert set(out["asin"]) == {"B0AAA", "B0BBB"}

    def test_source_constant_is_seller_storefront(self):
        client = _make_client(_SELLER, _PRODUCTS)
        out = discover_seller_storefront("A1B2C3D4E5", client=client)
        # `source` lets downstream steps branch on origin (e.g. supplier
        # leads template choice). Pin the constant.
        assert (out["source"] == "seller_storefront").all()

    def test_seller_id_and_name_propagate_to_every_row(self):
        client = _make_client(_SELLER, _PRODUCTS)
        out = discover_seller_storefront("A1B2C3D4E5", client=client)
        assert (out["seller_id"] == "A1B2C3D4E5").all()
        assert (out["seller_name"] == "Acme Storefront").all()

    def test_calls_keepa_with_storefront_flag(self):
        client = _make_client(_SELLER, _PRODUCTS)
        discover_seller_storefront("A1B2C3D4E5", client=client)
        client.get_seller.assert_called_once_with("A1B2C3D4E5", storefront=True)
        # Single batch call for all ASINs in the storefront.
        client.get_products.assert_called_once_with(["B0AAA", "B0BBB"])

    def test_empty_storefront_returns_empty_df_with_canonical_columns(self):
        empty_seller = KeepaSeller(
            sellerId="A0", sellerName="No Stock", asinList=[],
        )
        client = _make_client(empty_seller, [])
        out = discover_seller_storefront("A0", client=client)
        assert out.empty
        assert list(out.columns) == list(SELLER_STOREFRONT_DISCOVERY_COLUMNS)
        # And we never made the batch call (no ASINs to fetch).
        client.get_products.assert_not_called()

    def test_filters_products_not_in_seller_asin_list(self):
        # Defensive: if the batch returns extras (Keepa quirk filtered by
        # get_products, but we test both layers), the discovery step
        # should still emit only ASINs that are part of the storefront.
        seller = KeepaSeller(
            sellerId="A1", sellerName="X", asinList=["B0AAA"],
        )
        # Stub returns BOTH the requested + a surprise — discovery
        # must filter the surprise so the user's "seller's portfolio"
        # report doesn't include random ASINs.
        products = [
            KeepaProduct(asin="B0AAA", title="A", brand="A"),
            KeepaProduct(asin="B0EXTRA", title="?", brand="?"),
        ]
        client = _make_client(seller, products)
        out = discover_seller_storefront("A1", client=client)
        assert set(out["asin"]) == {"B0AAA"}

    def test_partial_batch_loss_drops_missing_rows(self):
        # If the batch returns only some ASINs (Keepa null filtering or
        # stale-on-error fallback dropped the rest), the discovery step
        # surfaces what it has — the caller can detect partial loss by
        # comparing len(out) vs the seller.asin_list count.
        seller = KeepaSeller(
            sellerId="A1", sellerName="X", asinList=["B0AAA", "B0DEAD"],
        )
        products = [KeepaProduct(asin="B0AAA", title="A", brand="A")]
        client = _make_client(seller, products)
        out = discover_seller_storefront("A1", client=client)
        assert len(out) == 1
        assert out.iloc[0]["asin"] == "B0AAA"

    def test_amazon_url_uses_uk_marketplace(self):
        client = _make_client(_SELLER, _PRODUCTS)
        out = discover_seller_storefront("A1B2C3D4E5", client=client)
        assert all(
            url.startswith("https://www.amazon.co.uk/dp/")
            for url in out["amazon_url"]
        )

    def test_category_extracted_from_first_category_tree_entry(self):
        # `categoryTree` is an ordered list (root → leaf). Keepa puts
        # the most-specific category last, but a useful summary column
        # is the leaf — pin which one we surface.
        seller = KeepaSeller(
            sellerId="A1", sellerName="X", asinList=["B0NESTED"],
        )
        products = [KeepaProduct(
            asin="B0NESTED", title="Nested", brand="A",
            categoryTree=[
                {"catId": 1, "name": "Home"},
                {"catId": 2, "name": "Kitchen"},
                {"catId": 3, "name": "Mugs"},
            ],
        )]
        client = _make_client(seller, products)
        out = discover_seller_storefront("A1", client=client)
        assert out.iloc[0]["category"] == "Mugs"

    def test_missing_brand_is_empty_string_not_none(self):
        # Downstream steps (supplier_leads, output) coerce brand via
        # coerce_str — None would render as "None" in supplier search
        # URLs. Pin that the discovery step emits "" instead.
        seller = KeepaSeller(
            sellerId="A1", sellerName="X", asinList=["B0NOBRAND"],
        )
        products = [KeepaProduct(asin="B0NOBRAND", title="T", brand=None)]
        client = _make_client(seller, products)
        out = discover_seller_storefront("A1", client=client)
        assert out.iloc[0]["brand"] == ""


class TestRunStep:
    def test_run_step_invokes_discover_with_seller_id(self):
        client = _make_client(_SELLER, _PRODUCTS)
        out = run_step(
            pd.DataFrame(),
            {"seller_id": "A1B2C3D4E5", "client": client},
        )
        assert len(out) == 2
        assert set(out["asin"]) == {"B0AAA", "B0BBB"}

    def test_run_step_missing_seller_id_raises(self):
        with pytest.raises(ValueError, match="seller_id"):
            run_step(pd.DataFrame(), {})

    def test_run_step_ignores_input_df(self):
        # Discovery step pattern: input df is ignored — discovery
        # creates rows from the API call, just like oa_csv.
        client = _make_client(_SELLER, _PRODUCTS)
        out = run_step(
            pd.DataFrame({"junk": [1, 2, 3]}),
            {"seller_id": "A1B2C3D4E5", "client": client},
        )
        assert "junk" not in out.columns


class TestColumnsConstant:
    def test_columns_pinned(self):
        # Pinning the column tuple catches accidental drift in the
        # canonical discovery schema. Downstream steps and YAML
        # strategies index by these names.
        assert SELLER_STOREFRONT_DISCOVERY_COLUMNS == (
            "asin",
            "source",
            "seller_id",
            "seller_name",
            "product_name",
            "brand",
            "category",
            "amazon_url",
        )
