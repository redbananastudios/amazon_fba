"""Tests for fba_engine.steps.keepa_enrich_survivors.

The supplier_pricelist chain reads market data from a static Keepa
Browser CSV. After the bulk decide identifies a small set of
survivors (non-REJECT), this step calls live Keepa for those ASINs
and merges fresh market columns back into the full DataFrame.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from fba_engine.steps.keepa_enrich import KEEPA_ENRICH_COLUMNS
from fba_engine.steps.keepa_enrich_survivors import (
    refresh_survivors,
    run_step,
)
from keepa_client import KeepaProduct
from keepa_client.models import KeepaStats


def _stub_client(products: list[KeepaProduct]) -> MagicMock:
    client = MagicMock()
    client.get_products.return_value = products
    return client


def _product(asin: str, *, buy_box: int | None = 1525) -> KeepaProduct:
    current: list[int] = [-1] * 19
    current[18] = -1 if buy_box is None else buy_box
    return KeepaProduct(
        asin=asin,
        stats=KeepaStats(current=current, avg90=list(current)),
    )


class TestRefreshSurvivors:
    def test_refreshes_only_non_reject_rows(self):
        df = pd.DataFrame([
            {"asin": "B0SURVIVE1", "decision": "SHORTLIST", "buy_box_price": 9.99},
            {"asin": "B0KILLED01", "decision": "REJECT", "buy_box_price": 5.00},
            {"asin": "B0SURVIVE2", "decision": "REVIEW", "buy_box_price": 12.50},
        ])
        # Live Keepa returns fresh BB prices (different from stale).
        client = _stub_client([
            _product("B0SURVIVE1", buy_box=2000),  # £20.00 fresh
            _product("B0SURVIVE2", buy_box=1100),  # £11.00 fresh
        ])
        out = refresh_survivors(df, client=client, with_offers=False)
        # Survivors got fresh BB prices.
        assert out.iloc[0]["buy_box_price"] == 20.0
        assert out.iloc[2]["buy_box_price"] == 11.0
        # REJECT row untouched.
        assert out.iloc[1]["buy_box_price"] == 5.00
        # Keepa was called exactly once with both survivor ASINs.
        assert client.get_products.call_count == 1
        called_asins = client.get_products.call_args[0][0]
        assert set(called_asins) == {"B0SURVIVE1", "B0SURVIVE2"}

    def test_no_op_when_decision_column_missing(self):
        df = pd.DataFrame([{"asin": "B0NODECISION"}])
        client = _stub_client([])
        out = refresh_survivors(df, client=client)
        # Returns input unchanged; client never called.
        assert out.equals(df)
        client.get_products.assert_not_called()

    def test_no_op_when_no_survivors(self):
        df = pd.DataFrame([
            {"asin": "B0K1LL00001", "decision": "REJECT"},
            {"asin": "B0K1LL00002", "decision": "REJECT"},
        ])
        client = _stub_client([])
        out = refresh_survivors(df, client=client)
        assert len(out) == 2
        client.get_products.assert_not_called()

    def test_empty_df_passes_through(self):
        df = pd.DataFrame()
        client = _stub_client([])
        out = refresh_survivors(df, client=client)
        assert out.empty
        client.get_products.assert_not_called()

    def test_appends_missing_keepa_columns_to_full_df(self):
        # First-time enrichment: full df has no KEEPA_ENRICH_COLUMNS yet.
        df = pd.DataFrame([
            {"asin": "B0SURVIVE1", "decision": "SHORTLIST"},
            {"asin": "B0K1LLED01", "decision": "REJECT"},
        ])
        client = _stub_client([_product("B0SURVIVE1", buy_box=1500)])
        out = refresh_survivors(df, client=client, with_offers=False)
        # All canonical columns now present on the full df.
        for col in KEEPA_ENRICH_COLUMNS:
            assert col in out.columns
        # Survivor row has the fresh BB price.
        assert out.iloc[0]["buy_box_price"] == 15.0
        # REJECT row has None for the new column (not propagated).
        assert pd.isna(out.iloc[1]["buy_box_price"])

    def test_preserves_row_order(self):
        df = pd.DataFrame([
            {"asin": "B0FIRST0001", "decision": "REJECT", "x": 1},
            {"asin": "B0SECOND001", "decision": "SHORTLIST", "x": 2},
            {"asin": "B0THIRD0001", "decision": "REJECT", "x": 3},
        ])
        client = _stub_client([_product("B0SECOND001")])
        out = refresh_survivors(df, client=client, with_offers=False)
        assert list(out["x"]) == [1, 2, 3]


class TestRunStep:
    def test_run_step_dispatches_to_refresh_survivors(self):
        df = pd.DataFrame([
            {"asin": "B0SURVIVE1", "decision": "SHORTLIST"},
        ])
        client = _stub_client([_product("B0SURVIVE1", buy_box=2000)])
        out = run_step(df, {"client": client, "with_offers": False})
        assert out.iloc[0]["buy_box_price"] == 20.0

    def test_run_step_truthy_string_with_offers(self):
        # YAML interpolation produces strings — make sure "true" works.
        df = pd.DataFrame([{"asin": "B0X", "decision": "SHORTLIST"}])
        client = _stub_client([_product("B0X")])
        run_step(df, {"client": client, "with_offers": "true"})
        # Verified by call kwargs: with_offers=True flowed through.
        assert client.get_products.call_args.kwargs.get("with_offers") is True
