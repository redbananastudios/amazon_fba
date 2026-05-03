"""Tests for keepa_client.

Covers: pydantic model round-trip, TokenBucket rate-limit behaviour,
DiskCache TTL + persistence, token-usage log append, KeepaConfig loading
from YAML, and KeepaClient end-to-end with HTTP mocked.

Per `docs/PRD-sourcing-strategies.md` §12: target ~25 tests for keepa_client.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from keepa_client import (
    DiskCache,
    KeepaApiError,
    KeepaClient,
    KeepaConfig,
    KeepaProduct,
    KeepaSeller,
    TokenBucket,
    load_keepa_config,
)
from keepa_client.log import append_token_log


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestKeepaProductModel:
    def test_round_trip_minimal_payload(self):
        # Keepa /product responses include 30+ fields; only model the subset
        # the engine actually consumes (asin, title, brand, csv arrays).
        payload = {
            "asin": "B01EXAMPLE",
            "title": "Example Product",
            "brand": "Acme",
            "categoryTree": [{"catId": 123, "name": "Toys"}],
            "csv": [None, [100, 999], None],  # placeholder arrays
        }
        product = KeepaProduct.model_validate(payload)
        assert product.asin == "B01EXAMPLE"
        assert product.title == "Example Product"
        assert product.brand == "Acme"

    def test_missing_optional_fields_default_none(self):
        # Keepa often returns title/brand as null for unmatched ASINs.
        payload = {"asin": "B0NOTFOUND"}
        product = KeepaProduct.model_validate(payload)
        assert product.asin == "B0NOTFOUND"
        assert product.title is None
        assert product.brand is None

    def test_missing_asin_raises(self):
        with pytest.raises(Exception):  # pydantic.ValidationError
            KeepaProduct.model_validate({"title": "no asin"})

    def test_null_category_tree_coerced_to_empty_list(self):
        """Real Keepa /product responses for some popular ASINs (e.g.
        B0CCM2W57G, B07JCZW3Z9) return ``categoryTree: null``. The model
        must coerce that to ``[]`` rather than raise — otherwise the
        single_asin strategy can't validate the response and the whole
        product is dropped from the batch. Regression-pin so a future
        Pydantic upgrade or refactor can't silently revert the fix."""
        payload = {"asin": "B0CCM2W57G", "categoryTree": None}
        product = KeepaProduct.model_validate(payload)
        assert product.category_tree == []
        # Belt-and-braces: also confirm a populated tree still works
        # (the validator only runs in `mode="before"` so non-None values
        # pass through to the inner list-of-dict validation).
        product2 = KeepaProduct.model_validate(
            {"asin": "B01EXAMPLE", "categoryTree": [{"catId": 1, "name": "X"}]}
        )
        assert product2.category_tree == [{"catId": 1, "name": "X"}]

    def test_null_variations_coerced_to_empty_list(self):
        """Real Keepa /product responses occasionally return ``variations: null``
        for products with no parent/child cluster (e.g. B01BZ20FE2, the
        Britains John Deere tractor that surfaced this in the abgee live run).
        Same coercion pattern as ``categoryTree`` — a null must yield ``[]``,
        never reject the model."""
        payload = {"asin": "B01BZ20FE2", "variations": None}
        product = KeepaProduct.model_validate(payload)
        assert product.variations == []
        # Populated variations still pass through.
        product2 = KeepaProduct.model_validate(
            {"asin": "B01EXAMPLE", "variations": [{"asin": "B0SIB1", "attributes": []}]}
        )
        assert len(product2.variations) == 1

    def test_market_snapshot_extracts_canonical_columns(self):
        # Stats current[] is keyed by Keepa's CSV index enum:
        #   0=AMAZON, 3=SALES (rank), 10=NEW_FBA, 11=COUNT_NEW, 18=BUY_BOX
        # Values are integer cents; -1 means "no current value".
        payload = {
            "asin": "B0FULL",
            "title": "Full Stats Product",
            "brand": "Acme",
            "stats": {
                "current": [
                    1499,  # 0 AMAZON: £14.99
                    1399,  # 1 NEW
                    -1,    # 2 USED
                    5234,  # 3 SALES rank
                    -1, -1, -1, -1, -1, -1,
                    1450,  # 10 NEW_FBA: £14.50
                    5,     # 11 COUNT_NEW (offers)
                    -1, -1, -1, -1, -1, -1,
                    1525,  # 18 BUY_BOX: £15.25
                ],
                "avg90": [
                    1500, 1400, -1, 5000,
                    -1, -1, -1, -1, -1, -1,
                    1475,
                    4,
                    -1, -1, -1, -1, -1, -1,
                    1510,  # avg90 BUY_BOX: £15.10
                ],
            },
            "monthlySold": 250,
        }
        product = KeepaProduct.model_validate(payload)
        snap = product.market_snapshot()
        assert snap["asin"] == "B0FULL"
        assert snap["amazon_price"] == 14.99
        assert snap["new_fba_price"] == 14.50
        assert snap["buy_box_price"] == 15.25
        assert snap["buy_box_avg90"] == 15.10
        assert snap["fba_seller_count"] == 5
        assert snap["sales_rank"] == 5234
        assert snap["sales_estimate"] == 250

    def test_market_snapshot_handles_missing_stats(self):
        # For ASINs Keepa hasn't tracked yet, stats is missing.
        # market_snapshot must return a dict with None values, not crash.
        payload = {"asin": "B0BARE", "title": "Bare", "brand": "X"}
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["asin"] == "B0BARE"
        assert snap["amazon_price"] is None
        assert snap["buy_box_price"] is None
        assert snap["fba_seller_count"] is None
        assert snap["sales_estimate"] is None

    def test_market_snapshot_treats_minus_one_as_none(self):
        # Keepa uses -1 to mean "no current value" in stats.current[].
        # The snapshot must convert these to None rather than emitting
        # negative-cent prices that downstream calculate would treat
        # as a real (negative) market price.
        payload = {
            "asin": "B0NEG",
            "stats": {"current": [-1] * 19},
            "monthlySold": -1,
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        for k in (
            "amazon_price", "new_fba_price", "buy_box_price",
            "fba_seller_count", "sales_rank", "sales_estimate",
        ):
            assert snap[k] is None, f"{k} should be None for -1 sentinel"

    def test_market_snapshot_handles_short_current_array(self):
        # Keepa sometimes returns a stats.current shorter than the full
        # 30-index range — older products or partial caches. Indexing
        # past the end must fail soft with None.
        payload = {
            "asin": "B0SHORT",
            "stats": {"current": [1500, 1400, -1, 5000]},  # only first 4
            "monthlySold": 100,
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["amazon_price"] == 15.00
        assert snap["sales_rank"] == 5000
        # Indices 10, 11, 18 don't exist in this array.
        assert snap["new_fba_price"] is None
        assert snap["fba_seller_count"] is None
        assert snap["buy_box_price"] is None
        assert snap["sales_estimate"] == 100


class TestKeepaOffersAndBsrDrops:
    """Coverage for the offer-list path + BSR-drop sales estimator added
    to make single_asin work on real ASINs (B0B636ZKZQ calibration —
    Casdon toaster toy where Keepa stats are sparse but offers list and
    rank history both have the actual market signal)."""

    def test_lowest_live_fba_price_picks_min(self):
        from keepa_client.models import KeepaOffer, lowest_live_fba_price, _now_keepa_minutes
        now = _now_keepa_minutes()
        offers = [
            KeepaOffer(sellerId="A1", isFBA=True, isAmazon=False, condition=1,
                       lastSeen=now - 60, offerCSV=[100, 1690, 0]),  # £16.90 fresh
            KeepaOffer(sellerId="A2", isFBA=True, isAmazon=False, condition=1,
                       lastSeen=now - 200, offerCSV=[100, 1698, 0]),  # £16.98 fresh
            KeepaOffer(sellerId="A3", isFBA=True, isAmazon=False, condition=1,
                       lastSeen=now - 5_000_000, offerCSV=[100, 999, 0]),  # £9.99 stale
        ]
        assert lowest_live_fba_price(offers) == 16.90

    def test_lowest_live_fba_price_excludes_amazon(self):
        from keepa_client.models import KeepaOffer, lowest_live_fba_price, _now_keepa_minutes
        now = _now_keepa_minutes()
        offers = [
            KeepaOffer(sellerId="AMZ", isFBA=True, isAmazon=True, condition=1,
                       lastSeen=now - 10, offerCSV=[100, 999, 0]),
            KeepaOffer(sellerId="A1", isFBA=True, isAmazon=False, condition=1,
                       lastSeen=now - 10, offerCSV=[100, 1690, 0]),
        ]
        # Amazon at £9.99 is excluded; £16.90 from 3rd-party FBA wins.
        assert lowest_live_fba_price(offers) == 16.90

    def test_lowest_live_fba_price_excludes_fbm_and_used(self):
        from keepa_client.models import KeepaOffer, lowest_live_fba_price, _now_keepa_minutes
        now = _now_keepa_minutes()
        offers = [
            KeepaOffer(sellerId="FBM", isFBA=False, isAmazon=False, condition=1,
                       lastSeen=now - 10, offerCSV=[100, 1100, 0]),
            KeepaOffer(sellerId="USED", isFBA=True, isAmazon=False, condition=2,
                       lastSeen=now - 10, offerCSV=[100, 800, 0]),
            KeepaOffer(sellerId="A1", isFBA=True, isAmazon=False, condition=1,
                       lastSeen=now - 10, offerCSV=[100, 1690, 0]),
        ]
        assert lowest_live_fba_price(offers) == 16.90

    def test_lowest_live_fba_price_returns_none_for_no_live_offers(self):
        from keepa_client.models import lowest_live_fba_price
        assert lowest_live_fba_price([]) is None

    def test_offer_csv_triple_stride_picks_price_field(self):
        """offerCSV is `[time, price, ship, time, price, ship, ...]` —
        the price field is at index `-2` of the last triple."""
        from keepa_client.models import KeepaOffer, _now_keepa_minutes
        now = _now_keepa_minutes()
        offer = KeepaOffer(
            isFBA=True, condition=1, lastSeen=now - 10,
            offerCSV=[100, 1500, 0, 200, 1690, 0],  # last price = 1690 = £16.90
        )
        assert offer.current_price() == 16.90

    def test_estimate_sales_from_rank_drops_counts_improvements(self):
        """Each rank-improvement event = ~1 sale. Counting them in the
        last 30 days approximates monthly sales."""
        from keepa_client.models import (
            estimate_sales_from_rank_drops, _now_keepa_minutes,
        )
        now = _now_keepa_minutes()
        # 3 rank drops in the last 30 days; one increase (return / unsold).
        rank_csv = [
            now - 25 * 24 * 60, 100_000,
            now - 20 * 24 * 60, 80_000,   # drop 1
            now - 15 * 24 * 60, 60_000,   # drop 2
            now - 10 * 24 * 60, 90_000,   # increase (skip)
            now - 5 * 24 * 60, 70_000,    # drop 3
        ]
        assert estimate_sales_from_rank_drops(rank_csv) == 3

    def test_estimate_sales_from_rank_drops_filters_to_window(self):
        """Drops outside the window don't count — but the last-rank
        bookkeeping still flows through them so the first drop INSIDE
        the window can be detected against the right baseline."""
        from keepa_client.models import (
            estimate_sales_from_rank_drops, _now_keepa_minutes,
        )
        now = _now_keepa_minutes()
        rank_csv = [
            now - 200 * 24 * 60, 100_000,  # outside window — sets baseline
            now - 5 * 24 * 60, 80_000,     # inside window — drop counted
        ]
        assert estimate_sales_from_rank_drops(rank_csv, window_days=30) == 1

    def test_estimate_sales_from_rank_drops_returns_none_for_empty(self):
        from keepa_client.models import estimate_sales_from_rank_drops
        assert estimate_sales_from_rank_drops(None) is None
        assert estimate_sales_from_rank_drops([]) is None

    def test_market_snapshot_prefers_live_fba_offer_over_stats(self):
        """Real-world calibration: B0B636ZKZQ has -1 in stats.current[10]
        (NEW_FBA) but a live FBA offer at £16.90. The snapshot should
        pick £16.90, not None."""
        from keepa_client.models import _now_keepa_minutes
        now = _now_keepa_minutes()
        payload = {
            "asin": "B0B636ZKZQ",
            "stats": {
                "current": [2386] + [-1] * 17 + [-1],  # AMAZON=23.86; rest -1
                "avg90":   [-1] * 19,
            },
            "offers": [
                {"sellerId": "A1", "isFBA": True, "isAmazon": False,
                 "condition": 1, "lastSeen": now - 10,
                 "offerCSV": [100, 1690, 0]},
            ],
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["new_fba_price"] == 16.90
        assert snap["amazon_price"] == 23.86

    def test_market_snapshot_falls_back_to_bsr_drops_when_monthly_sold_missing(self):
        from keepa_client.models import _now_keepa_minutes, _CSV_SALES_RANK
        now = _now_keepa_minutes()
        # csv list with rank history at index 3.
        csv = [None] * (_CSV_SALES_RANK + 1)
        csv[_CSV_SALES_RANK] = [
            now - 25 * 24 * 60, 100_000,
            now - 20 * 24 * 60, 80_000,    # drop 1
            now - 15 * 24 * 60, 60_000,    # drop 2
        ]
        payload = {"asin": "B0EXAMPLE1", "csv": csv}  # no monthlySold field
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["sales_estimate"] == 2

    def test_market_snapshot_uses_monthly_sold_when_present(self):
        """When Keepa provides monthlySold, prefer it over the
        rank-drop estimator (it's their own model, closer to ground
        truth than our drop-counting heuristic)."""
        from keepa_client.models import _now_keepa_minutes, _CSV_SALES_RANK
        now = _now_keepa_minutes()
        csv = [None] * (_CSV_SALES_RANK + 1)
        csv[_CSV_SALES_RANK] = [now - 5 * 24 * 60, 80_000]
        payload = {"asin": "B0EXAMPLE1", "monthlySold": 250, "csv": csv}
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["sales_estimate"] == 250


class TestFbaSellerCountFromOffers:
    """Pin the fba_seller_count fix: when the offers list is populated,
    count only live 3rd-party FBA offers (excluding Amazon, FBM, used).

    Previously `fba_seller_count` was sourced from `stats.current[11]`
    (COUNT_NEW), which is the total new-offer count summed across FBM
    + FBA — over-counting for any listing with FBM sellers and breaking
    every SINGLE_FBA_SELLER / dynamic-seller-ceiling decision rule by
    an unknown amount."""

    def test_count_live_fba_offers_basic(self):
        from keepa_client.models import (
            KeepaOffer, count_live_fba_offers, _now_keepa_minutes,
        )
        now = _now_keepa_minutes()
        offers = [
            KeepaOffer(sellerId="A1", isFBA=True, isAmazon=False, condition=1,
                       lastSeen=now - 10, offerCSV=[100, 1500, 0]),
            KeepaOffer(sellerId="A2", isFBA=True, isAmazon=False, condition=1,
                       lastSeen=now - 60, offerCSV=[100, 1600, 0]),
            KeepaOffer(sellerId="A3", isFBA=True, isAmazon=False, condition=1,
                       lastSeen=now - 200, offerCSV=[100, 1700, 0]),
        ]
        assert count_live_fba_offers(offers) == 3

    def test_count_live_fba_offers_excludes_amazon_fbm_used_stale(self):
        from keepa_client.models import (
            KeepaOffer, count_live_fba_offers, _now_keepa_minutes,
        )
        now = _now_keepa_minutes()
        offers = [
            # Live FBA NEW from 3rd-party — counts.
            KeepaOffer(sellerId="A1", isFBA=True, isAmazon=False, condition=1,
                       lastSeen=now - 10, offerCSV=[100, 1500, 0]),
            # Amazon's own FBA — excluded (Buy Box dynamics differ).
            KeepaOffer(sellerId="AMZ", isFBA=True, isAmazon=True, condition=1,
                       lastSeen=now - 10, offerCSV=[100, 1499, 0]),
            # Live FBM — excluded (different fulfilment channel).
            KeepaOffer(sellerId="A2", isFBA=False, isAmazon=False, condition=1,
                       lastSeen=now - 10, offerCSV=[100, 1100, 0]),
            # Used — excluded (we sell new only).
            KeepaOffer(sellerId="A3", isFBA=True, isAmazon=False, condition=2,
                       lastSeen=now - 10, offerCSV=[100, 800, 0]),
            # Stale lastSeen — excluded (old inventory record).
            KeepaOffer(sellerId="A4", isFBA=True, isAmazon=False, condition=1,
                       lastSeen=now - 5_000_000, offerCSV=[100, 999, 0]),
        ]
        # Only A1 qualifies — one live, NEW, 3rd-party FBA offer.
        assert count_live_fba_offers(offers) == 1

    def test_count_live_fba_offers_empty_returns_zero(self):
        from keepa_client.models import count_live_fba_offers
        assert count_live_fba_offers([]) == 0

    def test_market_snapshot_uses_offers_count_when_offers_populated(self):
        """3 FBA + 2 FBM live offers + Keepa says COUNT_NEW=10. The
        snapshot should report fba_seller_count=3 (offer-driven truth),
        not 10 (the COUNT_NEW combined count)."""
        from keepa_client.models import _now_keepa_minutes
        now = _now_keepa_minutes()
        # COUNT_NEW=10 in stats.current[11] — large enough that drift
        # is obvious if the fix regresses.
        current = [-1] * 19
        current[11] = 10
        payload = {
            "asin": "B0HASOFFERS",
            "stats": {"current": current},
            "offers": [
                # 3 live 3rd-party FBA offers
                {"sellerId": "A1", "isFBA": True, "isAmazon": False,
                 "condition": 1, "lastSeen": now - 10,
                 "offerCSV": [100, 1500, 0]},
                {"sellerId": "A2", "isFBA": True, "isAmazon": False,
                 "condition": 1, "lastSeen": now - 60,
                 "offerCSV": [100, 1550, 0]},
                {"sellerId": "A3", "isFBA": True, "isAmazon": False,
                 "condition": 1, "lastSeen": now - 200,
                 "offerCSV": [100, 1600, 0]},
                # 2 live FBM offers — must NOT count
                {"sellerId": "B1", "isFBA": False, "isAmazon": False,
                 "condition": 1, "lastSeen": now - 30,
                 "offerCSV": [100, 1100, 0]},
                {"sellerId": "B2", "isFBA": False, "isAmazon": False,
                 "condition": 1, "lastSeen": now - 30,
                 "offerCSV": [100, 1200, 0]},
            ],
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["fba_seller_count"] == 3
        # total_offer_count surfaces the legacy COUNT_NEW for callers
        # that legitimately want competition density across channels.
        assert snap["total_offer_count"] == 10

    def test_market_snapshot_falls_back_to_count_new_without_offers(self):
        """When the caller didn't request offers (with_offers=False —
        the default for bulk paths to save tokens), fba_seller_count
        falls back to stats.current[11]. Precision is degraded (FBM
        + FBA combined) but historical behaviour is preserved."""
        current = [-1] * 19
        current[11] = 7  # COUNT_NEW
        payload = {
            "asin": "B0NOOFFERS",
            "stats": {"current": current},
            # No "offers" key → empty offers list.
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["fba_seller_count"] == 7
        assert snap["total_offer_count"] == 7

    def test_market_snapshot_offers_with_zero_live_fba_returns_zero(self):
        """Offer list populated but no offer passes the live-FBA filter
        (e.g. Amazon-only listing, all stale). Don't fall back to
        COUNT_NEW — the offers list is authoritative when present."""
        from keepa_client.models import _now_keepa_minutes
        now = _now_keepa_minutes()
        current = [-1] * 19
        current[11] = 5  # COUNT_NEW says 5 — but offers say 0 live FBA
        payload = {
            "asin": "B0ALLAMZ",
            "stats": {"current": current},
            "offers": [
                # Amazon's own offer (excluded)
                {"sellerId": "AMZ", "isFBA": True, "isAmazon": True,
                 "condition": 1, "lastSeen": now - 10,
                 "offerCSV": [100, 2300, 0]},
                # Stale 3rd-party FBA (excluded)
                {"sellerId": "A1", "isFBA": True, "isAmazon": False,
                 "condition": 1, "lastSeen": now - 5_000_000,
                 "offerCSV": [100, 1500, 0]},
            ],
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["fba_seller_count"] == 0
        assert snap["total_offer_count"] == 5

    def test_market_snapshot_total_offer_count_independent_of_offers(self):
        """total_offer_count always pulls from stats.current[11], even
        when the offers list is populated. It is the FBM + FBA combined
        new-offer count and is independent of the FBA-only
        fba_seller_count."""
        from keepa_client.models import _now_keepa_minutes
        now = _now_keepa_minutes()
        current = [-1] * 19
        current[11] = 12
        payload = {
            "asin": "B0BOTH",
            "stats": {"current": current},
            "offers": [
                {"sellerId": "A1", "isFBA": True, "isAmazon": False,
                 "condition": 1, "lastSeen": now - 10,
                 "offerCSV": [100, 1500, 0]},
            ],
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["fba_seller_count"] == 1
        assert snap["total_offer_count"] == 12


class TestSchemaUnification:
    """Pin the new snapshot fields added by the schema-unification PR
    (WS1.2/1.3 of HANDOFF_candidate_validation.md). The whole point is
    that the API path (`market_snapshot`) and the CSV-export path
    (`market_data._KEEPA_COLUMN_MAP`) produce the same column set.
    These tests pin the API side; the CSV-export side is unit-tested
    in the legacy market_data tests."""

    def test_csv_last_value_walks_right_to_left(self):
        from keepa_client.models import _csv_last_value
        # Interleaved [t, v, t, v, ...] — last v wins.
        assert _csv_last_value([100, 5, 200, 10, 300, 15]) == 15

    def test_csv_last_value_skips_minus_one_sentinel(self):
        from keepa_client.models import _csv_last_value
        # Most recent observation is -1 (no data); fall back to the
        # one before it.
        assert _csv_last_value([100, 5, 200, -1]) == 5

    def test_csv_last_value_returns_none_for_empty(self):
        from keepa_client.models import _csv_last_value
        assert _csv_last_value([]) is None
        assert _csv_last_value(None) is None
        assert _csv_last_value([100]) is None  # one timestamp, no value

    def test_csv_last_value_returns_none_when_all_sentinels(self):
        from keepa_client.models import _csv_last_value
        assert _csv_last_value([100, -1, 200, -1]) is None

    def test_csv_last_value_handles_odd_length_array(self):
        """Latent bug guard: a length-5 array like `[t, v, t, v, t]`
        has a dangling trailing timestamp. Naively walking from len-1
        with stride -2 would visit the timestamp as if it were a value
        and return it. The helper must skip the trailing timestamp
        and return the most recent real value instead."""
        from keepa_client.models import _csv_last_value
        # [10=t, 1=v, 20=t, 2=v, 99=t (dangling)] — last value is 2.
        assert _csv_last_value([10, 1, 20, 2, 99]) == 2

    def test_csv_last_value_odd_length_returns_none_when_no_value(self):
        # Length 3 means only one value position — at index 1.
        from keepa_client.models import _csv_last_value
        assert _csv_last_value([10, -1, 99]) is None
        assert _csv_last_value([10, 5, 99]) == 5

    def test_market_snapshot_extracts_rating_and_review_count(self):
        """Keepa stores rating × 10 in csv[16]; review count is the
        last value of csv[17]. Snapshot divides rating by 10 so callers
        see the human-readable form (4.5)."""
        from keepa_client.models import _CSV_RATING, _CSV_COUNT_REVIEWS
        csv: list = [None] * 20
        csv[_CSV_RATING] = [100, 40, 200, 45]      # 4.0 then 4.5
        csv[_CSV_COUNT_REVIEWS] = [100, 50, 200, 73]  # last review count = 73
        payload = {"asin": "B0RATED", "csv": csv}
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["rating"] == 4.5
        assert snap["review_count"] == 73

    def test_market_snapshot_rating_none_when_csv_missing(self):
        # No csv entries → rating + review_count default to None.
        snap = KeepaProduct.model_validate({"asin": "B0BARE"}).market_snapshot()
        assert snap["rating"] is None
        assert snap["review_count"] is None

    def test_market_snapshot_buy_box_avg30(self):
        """Add stats.avg30 lane and surface the 30-day Buy Box average.
        Useful for momentum signals (current vs avg30 vs avg90)."""
        current = [-1] * 19
        avg30 = [-1] * 19
        avg90 = [-1] * 19
        current[18] = 1525   # £15.25
        avg30[18] = 1500     # £15.00
        avg90[18] = 1475     # £14.75
        payload = {
            "asin": "B0AVG",
            "stats": {"current": current, "avg30": avg30, "avg90": avg90},
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["buy_box_price"] == 15.25
        assert snap["buy_box_avg30"] == 15.00
        assert snap["buy_box_avg90"] == 14.75

    def test_market_snapshot_buy_box_avg30_none_when_lane_empty(self):
        # Pre-PR responses don't include avg30 — surface None, not 0.
        current = [-1] * 19
        avg90 = [-1] * 19
        current[18] = 1525
        avg90[18] = 1500
        payload = {
            "asin": "B0NOAVG30",
            "stats": {"current": current, "avg90": avg90},
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["buy_box_avg30"] is None
        assert snap["buy_box_avg90"] == 15.00

    def test_market_snapshot_sales_rank_avg90(self):
        current = [-1] * 19
        avg90 = [-1] * 19
        current[3] = 5234
        avg90[3] = 4800
        payload = {
            "asin": "B0RANK",
            "stats": {"current": current, "avg90": avg90},
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["sales_rank"] == 5234
        assert snap["sales_rank_avg90"] == 4800

    def test_market_snapshot_parent_asin(self):
        payload = {
            "asin": "B0CHILD",
            "parentAsin": "B0PARENT",
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["parent_asin"] == "B0PARENT"

    def test_market_snapshot_parent_asin_none_for_non_variation(self):
        snap = KeepaProduct.model_validate({"asin": "B0SOLO"}).market_snapshot()
        assert snap["parent_asin"] is None

    def test_market_snapshot_package_dimensions(self):
        """Weight in grams; volume derived from H × L × W (mm) ÷ 1000."""
        payload = {
            "asin": "B0BOX",
            "packageWeight": 250,         # 250 g
            "packageHeight": 100,         # 100 mm
            "packageLength": 200,         # 200 mm
            "packageWidth": 150,          # 150 mm
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["package_weight_g"] == 250
        # 100 * 200 * 150 = 3,000,000 mm³ = 3000 cm³
        assert snap["package_volume_cm3"] == 3000

    def test_market_snapshot_package_volume_none_when_dimensions_missing(self):
        payload = {"asin": "B0NODIM", "packageWeight": 100}
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["package_weight_g"] == 100
        # No height/length/width → volume can't be computed.
        assert snap["package_volume_cm3"] is None

    def test_market_snapshot_package_volume_none_when_any_zero(self):
        payload = {
            "asin": "B0FLAT",
            "packageHeight": 0, "packageLength": 100, "packageWidth": 100,
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["package_volume_cm3"] is None

    def test_market_snapshot_category_root(self):
        payload = {
            "asin": "B0CAT",
            "categoryTree": [
                {"catId": 1, "name": "Toys & Games"},
                {"catId": 2, "name": "Action Figures"},
            ],
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["category_root"] == "Toys & Games"

    def test_market_snapshot_category_root_none_when_tree_empty(self):
        payload = {"asin": "B0NOCAT", "categoryTree": None}
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["category_root"] is None

    def test_variation_count_one_for_standalone(self):
        # No variations field → standalone product.
        snap = KeepaProduct.model_validate({"asin": "B0SOLO"}).market_snapshot()
        assert snap["variation_count"] == 1

    def test_variation_count_reflects_keepa_variations_list(self):
        # Parent ASIN with 4 children — variation_count = 4.
        payload = {
            "asin": "B0PARENT",
            "variations": [
                {"asin": "B0CHILD1", "attributes": [{"name": "Color", "value": "Red"}]},
                {"asin": "B0CHILD2", "attributes": [{"name": "Color", "value": "Blue"}]},
                {"asin": "B0CHILD3", "attributes": [{"name": "Color", "value": "Green"}]},
                {"asin": "B0CHILD4", "attributes": [{"name": "Color", "value": "Black"}]},
            ],
        }
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["variation_count"] == 4

    def test_snapshot_keys_match_keepa_enrich_columns(self):
        """Schema parity: the set of keys emitted by `market_snapshot`
        must equal `set(KEEPA_ENRICH_COLUMNS) | {"asin"}`. Drift here
        means downstream calculate / decide silently misses data, or
        the enrich step asks for columns that don't exist."""
        from fba_engine.steps.keepa_enrich import KEEPA_ENRICH_COLUMNS
        payload = {"asin": "B0SCHEMA"}
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert set(snap.keys()) == set(KEEPA_ENRICH_COLUMNS) | {"asin"}


class TestHistoryFieldWiring:
    """Pin that history.* helpers wire correctly into market_snapshot
    (HANDOFF WS2.2). These are the inputs the candidate-score step
    in WS3 reads directly — silent drift in any of these breaks the
    entire downstream rubric."""

    def test_bare_payload_emits_none_for_all_history_fields(self):
        # No csv, no trackingSince, no stats → every history field None.
        snap = KeepaProduct.model_validate({"asin": "B0BARE"}).market_snapshot()
        for k in (
            "bsr_slope_30d", "bsr_slope_90d", "bsr_slope_365d",
            "fba_offer_count_90d_start", "fba_offer_count_90d_joiners",
            "buy_box_oos_pct_90", "price_volatility_90d",
            "listing_age_days", "yoy_bsr_ratio",
        ):
            assert snap[k] is None, f"{k} should be None for bare payload"

    def test_listing_age_populated_from_tracking_since(self):
        from keepa_client.models import _now_keepa_minutes
        # 200 days ago → listing_age_days ≈ 200.
        ts = _now_keepa_minutes() - 200 * 24 * 60
        payload = {"asin": "B0AGE", "trackingSince": ts}
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["listing_age_days"] == 200

    def test_bsr_slope_populated_from_csv_3(self):
        from keepa_client.models import _now_keepa_minutes, _CSV_SALES_RANK
        now = _now_keepa_minutes()
        rank_history = []
        # 5 declining-rank points (improving) inside the 30-day window.
        for i, day in enumerate([25, 20, 15, 10, 5]):
            rank_history.extend([now - day * 24 * 60, 100_000 - i * 10_000])
        csv = [None] * (_CSV_SALES_RANK + 1)
        csv[_CSV_SALES_RANK] = rank_history
        payload = {"asin": "B0SLOPE", "csv": csv}
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        # Improving rank → slope < 0.
        assert snap["bsr_slope_30d"] is not None
        assert snap["bsr_slope_30d"] < 0
        # 90d also covers — same shape, similar sign.
        assert snap["bsr_slope_90d"] is not None
        assert snap["bsr_slope_90d"] < 0
        # 365d — same shape, slope is comparable scale.
        assert snap["bsr_slope_365d"] is not None

    def test_offer_count_trend_populated_from_csv_11(self):
        from keepa_client.models import _now_keepa_minutes, _CSV_COUNT_NEW
        now = _now_keepa_minutes()
        # 3 → 5 → 7 sellers over 90 days.
        offer_history = []
        for day, count in [(80, 3), (60, 5), (40, 6), (20, 7)]:
            offer_history.extend([now - day * 24 * 60, count])
        csv = [None] * (_CSV_COUNT_NEW + 1)
        csv[_CSV_COUNT_NEW] = offer_history
        payload = {"asin": "B0JOINERS", "csv": csv}
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["fba_offer_count_90d_start"] == 3
        assert snap["fba_offer_count_90d_joiners"] == 4  # 7 - 3

    def test_buy_box_oos_pct_populated_from_csv_18(self):
        from keepa_client.models import _now_keepa_minutes, _CSV_BUY_BOX
        now = _now_keepa_minutes()
        # 5 in-window points: 3 present, 2 sentinel = 40% OOS.
        bb_history = [
            now - 80 * 24 * 60, 1500,
            now - 60 * 24 * 60, -1,
            now - 40 * 24 * 60, 1500,
            now - 20 * 24 * 60, -1,
            now - 10 * 24 * 60, 1500,
        ]
        csv = [None] * (_CSV_BUY_BOX + 1)
        csv[_CSV_BUY_BOX] = bb_history
        payload = {"asin": "B0OOS", "csv": csv}
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["buy_box_oos_pct_90"] is not None
        assert 0.39 < snap["buy_box_oos_pct_90"] < 0.41

    def test_price_volatility_populated_from_csv_18(self):
        from keepa_client.models import _now_keepa_minutes, _CSV_BUY_BOX
        now = _now_keepa_minutes()
        # Stable ≈1500 cents → low CV.
        bb_history = []
        for day, price in [(80, 1500), (60, 1505), (40, 1495),
                           (20, 1500), (10, 1500)]:
            bb_history.extend([now - day * 24 * 60, price])
        csv = [None] * (_CSV_BUY_BOX + 1)
        csv[_CSV_BUY_BOX] = bb_history
        payload = {"asin": "B0STABLE", "csv": csv}
        snap = KeepaProduct.model_validate(payload).market_snapshot()
        assert snap["price_volatility_90d"] is not None
        assert snap["price_volatility_90d"] < 0.05


class TestKeepaSellerModel:
    def test_seller_with_asin_list(self):
        payload = {
            "sellerId": "A1B2C3D4E5",
            "sellerName": "Acme Storefront",
            "asinList": ["B001", "B002", "B003"],
        }
        seller = KeepaSeller.model_validate(payload)
        assert seller.seller_id == "A1B2C3D4E5"
        assert seller.seller_name == "Acme Storefront"
        assert seller.asin_list == ["B001", "B002", "B003"]

    def test_empty_asin_list(self):
        payload = {"sellerId": "A0", "sellerName": "Empty", "asinList": []}
        seller = KeepaSeller.model_validate(payload)
        assert seller.asin_list == []

    def test_missing_asin_list_defaults_to_empty(self):
        # Some Keepa responses omit asinList for sellers with no inventory.
        seller = KeepaSeller.model_validate({"sellerId": "A0", "sellerName": "X"})
        assert seller.asin_list == []

    def test_round_trip_dump_uses_field_names_not_aliases(self):
        # Pydantic v2 default: model_dump() emits field names, not aliases.
        # Use by_alias=True if you need to send the dict back to Keepa.
        # Test pins the contract so a future "let's switch defaults" doesn't
        # silently break upstream compatibility.
        seller = KeepaSeller.model_validate({"sellerId": "A1", "asinList": ["B0"]})
        assert seller.model_dump()["seller_id"] == "A1"
        assert seller.model_dump(by_alias=True)["sellerId"] == "A1"


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


class TestTokenBucket:
    def test_acquire_within_burst_returns_immediately(self):
        # 100 burst allows 50 tokens to be drawn instantly.
        bucket = TokenBucket(tokens_per_minute=20, burst=100, sleep=lambda _: None)
        start = time.monotonic()
        bucket.acquire(50)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_acquire_above_burst_blocks_for_refill(self):
        # 20 tokens/min = 1 token per 3 seconds. After draining the 100-burst
        # and asking for 1 more, the bucket should sleep (mocked) for ~3s
        # worth of refill time.
        sleeps: list[float] = []
        bucket = TokenBucket(
            tokens_per_minute=20, burst=100, sleep=lambda s: sleeps.append(s)
        )
        bucket.acquire(100)
        bucket.acquire(1)
        # Should have slept at least once for refill.
        assert len(sleeps) >= 1
        assert sum(sleeps) > 0

    def test_acquire_zero_is_no_op(self):
        sleeps: list[float] = []
        bucket = TokenBucket(
            tokens_per_minute=20, burst=100, sleep=lambda s: sleeps.append(s)
        )
        bucket.acquire(0)
        assert sleeps == []

    def test_request_above_burst_capacity_raises(self):
        # Acquiring more than the bucket capacity is a programming error.
        bucket = TokenBucket(tokens_per_minute=20, burst=100, sleep=lambda _: None)
        with pytest.raises(ValueError, match="exceeds bucket capacity"):
            bucket.acquire(200)

    def test_refund_returns_tokens_to_bucket(self):
        # Reviewer M2: post-response reconciliation needs a refund path.
        # Acquire 50, then refund 30 — bucket should regain 30 tokens'
        # worth of capacity for the next acquire without sleeping.
        sleeps: list[float] = []
        bucket = TokenBucket(
            tokens_per_minute=10,  # slow refill so any sleep is observable
            burst=50,
            sleep=lambda s: sleeps.append(s),
        )
        bucket.acquire(50)
        bucket.refund(30)
        # Now acquire 30 — should NOT block, since refund put 30 back.
        bucket.acquire(30)
        assert sleeps == []

    def test_refund_caps_at_burst_capacity(self):
        bucket = TokenBucket(tokens_per_minute=20, burst=100, sleep=lambda _: None)
        bucket.acquire(10)
        bucket.refund(1000)  # way more than capacity
        # Acquiring full capacity should still work without sleeping.
        sleeps: list[float] = []
        bucket = TokenBucket(
            tokens_per_minute=20, burst=100, sleep=lambda s: sleeps.append(s)
        )
        bucket.refund(1000)
        bucket.acquire(100)
        assert sleeps == []

    def test_refund_zero_or_negative_is_noop(self):
        bucket = TokenBucket(tokens_per_minute=20, burst=100, sleep=lambda _: None)
        bucket.refund(0)
        bucket.refund(-50)  # silently ignored


# ---------------------------------------------------------------------------
# DiskCache
# ---------------------------------------------------------------------------


class TestDiskCache:
    def test_set_and_get_round_trips(self, tmp_path: Path):
        cache = DiskCache(root=tmp_path)
        cache.set("product", "B0SAMPLE", {"asin": "B0SAMPLE", "title": "T"}, ttl_seconds=3600)
        result = cache.get("product", "B0SAMPLE")
        assert result is not None
        assert result["asin"] == "B0SAMPLE"

    def test_get_returns_none_for_unknown_key(self, tmp_path: Path):
        cache = DiskCache(root=tmp_path)
        assert cache.get("product", "B0MISSING") is None

    def test_expired_entry_returns_none(self, tmp_path: Path):
        cache = DiskCache(root=tmp_path)
        cache.set("product", "B0EXPIRED", {"x": 1}, ttl_seconds=0)
        # TTL=0 means already expired — sleep a hair to ensure the wallclock
        # check fires.
        time.sleep(0.01)
        assert cache.get("product", "B0EXPIRED") is None

    def test_separate_namespaces_dont_collide(self, tmp_path: Path):
        cache = DiskCache(root=tmp_path)
        cache.set("product", "ID", {"kind": "product"}, ttl_seconds=3600)
        cache.set("seller", "ID", {"kind": "seller"}, ttl_seconds=3600)
        assert cache.get("product", "ID")["kind"] == "product"
        assert cache.get("seller", "ID")["kind"] == "seller"

    def test_cache_persists_across_instances(self, tmp_path: Path):
        # Same root dir, different cache objects — second instance reads
        # what the first wrote.
        DiskCache(root=tmp_path).set(
            "seller", "A1", {"id": "A1"}, ttl_seconds=3600
        )
        assert DiskCache(root=tmp_path).get("seller", "A1")["id"] == "A1"


# ---------------------------------------------------------------------------
# Token usage log
# ---------------------------------------------------------------------------


class TestTokenLog:
    def test_appends_entry_with_iso_timestamp(self, tmp_path: Path):
        log_path = tmp_path / "token_log.jsonl"
        append_token_log(
            log_path,
            endpoint="product",
            tokens=6,
            cached=False,
            extra={"asin": "B0XXXX"},
        )
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["endpoint"] == "product"
        assert entry["tokens"] == 6
        assert entry["cached"] is False
        assert entry["asin"] == "B0XXXX"
        # ISO 8601 with Z suffix.
        assert entry["ts"].endswith("Z")

    def test_multiple_appends_accumulate(self, tmp_path: Path):
        log_path = tmp_path / "log.jsonl"
        append_token_log(log_path, endpoint="seller", tokens=50, cached=False)
        append_token_log(log_path, endpoint="product", tokens=0, cached=True)
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2

    def test_creates_parent_directory(self, tmp_path: Path):
        nested = tmp_path / "deep" / "nested" / "token_log.jsonl"
        append_token_log(nested, endpoint="product", tokens=1, cached=False)
        assert nested.exists()


# ---------------------------------------------------------------------------
# KeepaConfig
# ---------------------------------------------------------------------------


class TestKeepaConfig:
    def test_load_from_yaml(self, tmp_path: Path):
        body = """\
api:
  base_url: https://api.keepa.com
  marketplace: 2
  request_timeout_seconds: 30
rate_limit:
  tokens_per_minute: 20
  burst: 100
  retry_on_429:
    max_retries: 3
    backoff_base_seconds: 5
    backoff_jitter_seconds: 2
cache:
  root: .cache/keepa
  ttl_seconds:
    product: 86400
    seller: 604800
    category: 2592000
batching:
  product_batch_size: 100
"""
        path = tmp_path / "keepa_client.yaml"
        path.write_text(body, encoding="utf-8")
        cfg = load_keepa_config(path)
        assert cfg.api.base_url == "https://api.keepa.com"
        assert cfg.api.marketplace == 2
        assert cfg.rate_limit.tokens_per_minute == 20
        assert cfg.rate_limit.burst == 100
        assert cfg.cache.ttl_seconds["product"] == 86400

    def test_load_from_canonical_path(self):
        # The canonical config at shared/config/keepa_client.yaml MUST
        # parse cleanly — pin it so a typo gets caught at PR time.
        repo_root = Path(__file__).resolve().parents[5]
        canonical = repo_root / "shared" / "config" / "keepa_client.yaml"
        if not canonical.exists():
            pytest.skip(f"canonical config not found: {canonical}")
        cfg = load_keepa_config(canonical)
        assert cfg.api.marketplace == 2  # UK

    def test_canonical_config_resolves_cache_root_to_repo(self):
        # Reviewer LOW (verified): the relative cache root in the canonical
        # YAML resolves to <repo>/.cache/keepa via parents[2] math in
        # config.py. Pin it with a real test so the math is locked, not
        # just hoped.
        repo_root = Path(__file__).resolve().parents[5]
        canonical = repo_root / "shared" / "config" / "keepa_client.yaml"
        if not canonical.exists():
            pytest.skip(f"canonical config not found: {canonical}")
        cfg = load_keepa_config(canonical)
        assert cfg.cache.root == repo_root / ".cache" / "keepa"

    def test_load_with_empty_yaml_uses_defaults(self, tmp_path: Path):
        # Reviewer LOW: an empty YAML should yield default-everything,
        # not crash. Defends against `pii:` typos that would otherwise
        # silently use the api block defaults.
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        cfg = load_keepa_config(path)
        assert cfg.api.marketplace == 2
        assert cfg.rate_limit.tokens_per_minute == 20


# ---------------------------------------------------------------------------
# KeepaClient (HTTP layer mocked)
# ---------------------------------------------------------------------------


def _config_for_test(tmp_path: Path) -> KeepaConfig:
    """Build a KeepaConfig that points all on-disk artefacts at tmp_path."""
    from keepa_client.config import (
        ApiConfig,
        BatchingConfig,
        CacheConfig,
        KeepaConfig,
        RateLimitConfig,
        RetryConfig,
    )

    return KeepaConfig(
        api=ApiConfig(
            base_url="https://api.keepa.test",
            marketplace=2,
            request_timeout_seconds=5,
        ),
        rate_limit=RateLimitConfig(
            tokens_per_minute=1000,  # high so tests don't sleep
            burst=10000,
            retry_on_429=RetryConfig(
                max_retries=1, backoff_base_seconds=0, backoff_jitter_seconds=0
            ),
        ),
        cache=CacheConfig(
            root=tmp_path / "keepa_cache",
            ttl_seconds={"product": 3600, "seller": 3600, "category": 3600},
        ),
        batching=BatchingConfig(product_batch_size=100),
    )


class TestKeepaClientGetSeller:
    @patch("keepa_client.client.requests.get")
    def test_calls_seller_endpoint_with_storefront_param(
        self, get_mock, tmp_path: Path
    ):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 50,
                "sellers": {
                    "A1B2C3D4E5": {
                        "sellerId": "A1B2C3D4E5",
                        "sellerName": "Acme",
                        "asinList": ["B001", "B002"],
                    }
                },
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        seller = client.get_seller("A1B2C3D4E5", storefront=True)
        assert seller.seller_id == "A1B2C3D4E5"
        assert seller.asin_list == ["B001", "B002"]

        # Verify URL parameters.
        call_args = get_mock.call_args
        params = call_args.kwargs["params"]
        assert params["seller"] == "A1B2C3D4E5"
        assert params["storefront"] == 1
        assert params["domain"] == 2

    @patch("keepa_client.client.requests.get")
    def test_caches_seller_response(self, get_mock, tmp_path: Path):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 50,
                "sellers": {
                    "A1": {
                        "sellerId": "A1", "sellerName": "X", "asinList": ["B0"]
                    }
                },
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))

        # First call hits the API.
        client.get_seller("A1", storefront=True)
        # Second call should hit cache.
        client.get_seller("A1", storefront=True)

        # API was called only once.
        assert get_mock.call_count == 1

    @patch("keepa_client.client.requests.get")
    def test_logs_token_usage(self, get_mock, tmp_path: Path):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 50,
                "sellers": {
                    "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []}
                },
            },
        )
        cfg = _config_for_test(tmp_path)
        client = KeepaClient(api_key="fake", config=cfg)
        client.get_seller("A1", storefront=True)

        log_path = cfg.cache.root / "token_log.jsonl"
        assert log_path.exists()
        entries = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines()]
        assert any(
            e["endpoint"] == "seller" and e["tokens"] == 50 and e["cached"] is False
            for e in entries
        )

    @patch("keepa_client.client.requests.get")
    def test_cached_call_logs_zero_tokens(self, get_mock, tmp_path: Path):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 50,
                "sellers": {
                    "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []}
                },
            },
        )
        cfg = _config_for_test(tmp_path)
        client = KeepaClient(api_key="fake", config=cfg)
        client.get_seller("A1", storefront=True)
        client.get_seller("A1", storefront=True)  # cached

        log_path = cfg.cache.root / "token_log.jsonl"
        entries = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines()]
        cached_entries = [e for e in entries if e["cached"] is True]
        assert len(cached_entries) == 1
        assert cached_entries[0]["tokens"] == 0

    @patch("keepa_client.client.requests.get")
    def test_500_raises_immediately_without_retry(self, get_mock, tmp_path: Path):
        # 500 is intentionally excluded from the retryable set — Keepa's
        # 500s are often deterministic (malformed param, unknown ASIN
        # format) and retrying just adds latency. 502/503/504 ARE retried;
        # see test_503_retries_then_succeeds below.
        get_mock.return_value = MagicMock(status_code=500, text="server error")
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        with pytest.raises(KeepaApiError, match="500"):
            client.get_seller("A1", storefront=True)
        # Single call, no retry on 500.
        assert get_mock.call_count == 1

    @patch("keepa_client.client.requests.get")
    def test_429_then_200_succeeds_after_retry(self, get_mock, tmp_path: Path):
        # Reviewer M3: core retry feature was previously untested.
        # 429 -> retry -> 200. With retry_on_429.max_retries=1 in
        # _config_for_test, we expect exactly 2 total HTTP calls.
        get_mock.side_effect = [
            MagicMock(status_code=429, text="rate limited"),
            MagicMock(
                status_code=200,
                json=lambda: {
                    "tokensConsumed": 50,
                    "sellers": {
                        "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []},
                    },
                },
            ),
        ]
        sleeps: list[float] = []
        client = KeepaClient(
            api_key="fake",
            config=_config_for_test(tmp_path),
            _sleep_for_tests=lambda s: sleeps.append(s),
        )
        seller = client.get_seller("A1", storefront=True)
        assert seller.seller_id == "A1"
        assert get_mock.call_count == 2

    @patch("keepa_client.client.requests.get")
    def test_persistent_429_raises_after_max_retries(
        self, get_mock, tmp_path: Path
    ):
        # 429 every attempt -> raises. With max_retries=1, expect 2 total
        # HTTP calls (initial + 1 retry).
        get_mock.return_value = MagicMock(status_code=429, text="rate limited")
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        with pytest.raises(KeepaApiError, match="429"):
            client.get_seller("A1", storefront=True)
        assert get_mock.call_count == 2

    @patch("keepa_client.client.requests.get")
    def test_503_retries_then_succeeds(self, get_mock, tmp_path: Path):
        # Reviewer M1: gateway-class 5xx (502/503/504) is now retryable.
        get_mock.side_effect = [
            MagicMock(status_code=503, text="unavailable"),
            MagicMock(
                status_code=200,
                json=lambda: {
                    "tokensConsumed": 50,
                    "sellers": {
                        "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []},
                    },
                },
            ),
        ]
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        seller = client.get_seller("A1", storefront=True)
        assert seller.seller_id == "A1"
        assert get_mock.call_count == 2

    @patch("keepa_client.client.requests.get")
    def test_token_estimate_reconciled_to_actual(self, get_mock, tmp_path: Path):
        # Reviewer M2: bucket should refund the diff between the pre-call
        # estimate (50 for /seller) and the actual tokensConsumed.
        # Two back-to-back /seller calls each consuming 10 tokens (vs
        # estimate 50): without reconciliation the bucket drifts by 80
        # over two calls; with it, drift is zero.
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 10,  # below estimate
                "sellers": {
                    "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []},
                    "A2": {"sellerId": "A2", "sellerName": "Y", "asinList": []},
                },
            },
        )
        from keepa_client.config import (
            ApiConfig, BatchingConfig, CacheConfig, KeepaConfig,
            RateLimitConfig, RetryConfig,
        )

        cfg = KeepaConfig(
            api=ApiConfig(
                base_url="https://api.keepa.test",
                marketplace=2,
                request_timeout_seconds=5,
            ),
            rate_limit=RateLimitConfig(
                tokens_per_minute=1,  # very slow refill — any sleep is observable
                burst=60,             # exactly enough for one 50-token estimate + one 10-token reconciled
                retry_on_429=RetryConfig(
                    max_retries=0, backoff_base_seconds=0, backoff_jitter_seconds=0
                ),
            ),
            cache=CacheConfig(
                root=tmp_path / "c",
                ttl_seconds={"product": 60, "seller": 60, "category": 60},
            ),
            batching=BatchingConfig(product_batch_size=100),
        )
        sleeps: list[float] = []
        client = KeepaClient(
            api_key="fake", config=cfg,
            _sleep_for_tests=lambda s: sleeps.append(s),
        )
        # First call: acquires 50 from a 60-token bucket (10 left), refunds
        # 40 → bucket back to 50.
        client.get_seller("A1", storefront=True)
        # Second call (different seller, fresh cache miss): acquires 50
        # from 50-available — no sleep needed thanks to reconciliation.
        client.get_seller("A2", storefront=True)
        assert sleeps == [], (
            "bucket should have reconciled after the first call so the "
            f"second acquire didn't block; sleeps={sleeps}"
        )


class TestKeepaClientGetProduct:
    @patch("keepa_client.client.requests.get")
    def test_get_product_returns_typed_model(self, get_mock, tmp_path: Path):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 6,
                "products": [{
                    "asin": "B0SAMPLE",
                    "title": "Sample",
                    "brand": "Acme",
                    "categoryTree": [{"catId": 1, "name": "Toys"}],
                    "csv": [],
                }],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        product = client.get_product("B0SAMPLE")
        assert isinstance(product, KeepaProduct)
        assert product.asin == "B0SAMPLE"
        assert product.title == "Sample"

    @patch("keepa_client.client.requests.get")
    def test_get_product_caches_per_asin(self, get_mock, tmp_path: Path):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 6,
                "products": [{"asin": "B0CACHE", "title": "T", "brand": "B"}],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        client.get_product("B0CACHE")
        client.get_product("B0CACHE")
        assert get_mock.call_count == 1

    @patch("keepa_client.client.requests.get")
    def test_get_product_requests_stats_90(self, get_mock, tmp_path: Path):
        # Pinning `stats=90` is critical: keepa_enrich.market_snapshot
        # reads stats.current[] / stats.avg90[]. Forgetting to request
        # stats here would silently emit None-filled enrichment columns
        # and break the calculate->decide chain.
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 6,
                "products": [{"asin": "B0SAMPLE", "title": "T"}],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        client.get_product("B0SAMPLE")
        params = get_mock.call_args.kwargs["params"]
        assert params.get("stats") == 90

    @patch("keepa_client.client.requests.get")
    def test_get_products_batch_requests_stats_90(
        self, get_mock, tmp_path: Path
    ):
        # Same contract for the batch path.
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 12,
                "products": [
                    {"asin": "B0A", "title": "A"},
                    {"asin": "B0B", "title": "B"},
                ],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        client.get_products(["B0A", "B0B"])
        params = get_mock.call_args.kwargs["params"]
        assert params.get("stats") == 90


class TestEstimateScaling:
    """Pin the per-ASIN + stats overhead in `_estimate_for`. Without
    scaling, the token bucket silently over-issues under heavy batch
    load and falls back to Keepa's HTTP 429 retries.
    """

    def _client(self, tmp_path: Path) -> KeepaClient:
        return KeepaClient(api_key="fake", config=_config_for_test(tmp_path))

    def test_seller_endpoint_unchanged(self, tmp_path: Path):
        client = self._client(tmp_path)
        assert client._estimate_for(
            "/seller", {"seller": "A1", "storefront": 1}
        ) == 50

    def test_single_product_no_stats_legacy_estimate(self, tmp_path: Path):
        # Pre-PR contract: 6 = 5 base + 1 product.
        client = self._client(tmp_path)
        est = client._estimate_for("/product", {"asin": "B0SOLO"})
        assert est == 6

    def test_single_product_with_stats_costs_one_more(self, tmp_path: Path):
        client = self._client(tmp_path)
        est = client._estimate_for(
            "/product", {"asin": "B0SOLO", "stats": 90}
        )
        # 5 base + 1 product * (1 + 1 stats) = 7
        assert est == 7

    def test_batch_scales_per_asin(self, tmp_path: Path):
        client = self._client(tmp_path)
        # 100 ASINs comma-separated; 5 base + 100 * 2 = 205 (with stats).
        asin_param = ",".join(f"B{i:04d}" for i in range(100))
        est = client._estimate_for(
            "/product", {"asin": asin_param, "stats": 90}
        )
        assert est == 5 + 100 * 2  # 205

    def test_batch_without_stats_still_scales(self, tmp_path: Path):
        client = self._client(tmp_path)
        asin_param = ",".join(["B0A", "B0B", "B0C"])
        est = client._estimate_for("/product", {"asin": asin_param})
        # 5 base + 3 products * 1 (no stats) = 8.
        assert est == 8


# ---------------------------------------------------------------------------
# End-to-end: token bucket actually engages
# ---------------------------------------------------------------------------


class TestTokenBucketEngagement:
    @patch("keepa_client.client.requests.get")
    def test_token_bucket_acquires_before_request(
        self, get_mock, tmp_path: Path
    ):
        from keepa_client.config import (
            ApiConfig, BatchingConfig, CacheConfig, KeepaConfig,
            RateLimitConfig, RetryConfig,
        )

        # Each .get() call returns the seller payload keyed to whichever
        # seller_id was requested. We can't read the seller_id from the
        # patched call here, so just return both keys in every response —
        # the client will pick the right one out by id.
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 50,
                "sellers": {
                    "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []},
                    "A2": {"sellerId": "A2", "sellerName": "Y", "asinList": []},
                },
            },
        )

        sleeps: list[float] = []
        cfg = KeepaConfig(
            api=ApiConfig(
                base_url="https://api.keepa.test",
                marketplace=2,
                request_timeout_seconds=5,
            ),
            rate_limit=RateLimitConfig(
                tokens_per_minute=600,  # 10/sec
                burst=50,                # tight enough that 2x50-token call triggers refill
                retry_on_429=RetryConfig(
                    max_retries=0, backoff_base_seconds=0, backoff_jitter_seconds=0
                ),
            ),
            cache=CacheConfig(
                root=tmp_path / "c",
                ttl_seconds={"product": 60, "seller": 60, "category": 60},
            ),
            batching=BatchingConfig(product_batch_size=100),
        )
        # Inject our sleep spy so we can verify the bucket waited.
        client = KeepaClient(
            api_key="fake", config=cfg, _sleep_for_tests=lambda s: sleeps.append(s)
        )

        client.get_seller("A1", storefront=True)
        client.get_seller("A2", storefront=True)
        # Second call drains burst → should sleep for refill.
        assert len(sleeps) >= 1


# ---------------------------------------------------------------------------
# DiskCache.get_stale — fallback contract for stale-on-error
# ---------------------------------------------------------------------------


class TestDiskCacheGetStale:
    def test_returns_value_even_when_expired(self, tmp_path: Path):
        cache = DiskCache(root=tmp_path)
        cache.set("product", "B0STALE", {"asin": "B0STALE"}, ttl_seconds=0)
        time.sleep(0.01)
        # Confirm the normal get returns None for the expired entry...
        assert cache.get("product", "B0STALE") is None
        # ...but get_stale returns the stored value regardless.
        stale = cache.get_stale("product", "B0STALE")
        assert stale is not None
        assert stale["asin"] == "B0STALE"

    def test_returns_value_for_fresh_entry_too(self, tmp_path: Path):
        # Don't penalise callers using get_stale on a still-fresh entry.
        # The flag means "expired is acceptable", not "expired is required".
        cache = DiskCache(root=tmp_path)
        cache.set("product", "B0FRESH", {"asin": "B0FRESH"}, ttl_seconds=3600)
        result = cache.get_stale("product", "B0FRESH")
        assert result["asin"] == "B0FRESH"

    def test_returns_none_for_unknown_key(self, tmp_path: Path):
        # Missing entry → still None; stale-on-error has nothing to fall
        # back to.
        cache = DiskCache(root=tmp_path)
        assert cache.get_stale("product", "B0MISSING") is None

    def test_returns_none_for_malformed_file(self, tmp_path: Path):
        # If the cache file is corrupted, get_stale should fail closed
        # the same way get does — never return garbage.
        cache = DiskCache(root=tmp_path)
        path = tmp_path / "product" / "B0BROKEN.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not valid json", encoding="utf-8")
        assert cache.get_stale("product", "B0BROKEN") is None


# ---------------------------------------------------------------------------
# Batch product lookup — get_products
# ---------------------------------------------------------------------------


class TestKeepaClientGetProducts:
    @patch("keepa_client.client.requests.get")
    def test_batch_calls_endpoint_with_comma_separated_asins(
        self, get_mock, tmp_path: Path
    ):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 12,
                "products": [
                    {"asin": "B0AAA", "title": "A", "brand": "X"},
                    {"asin": "B0BBB", "title": "B", "brand": "Y"},
                ],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        out = client.get_products(["B0AAA", "B0BBB"])
        assert len(out) == 2
        assert all(isinstance(p, KeepaProduct) for p in out)
        # One HTTP call for the whole batch.
        assert get_mock.call_count == 1
        # Comma-separated asin param is the Keepa contract.
        call_kwargs = get_mock.call_args
        assert call_kwargs.kwargs["params"]["asin"] == "B0AAA,B0BBB"

    @patch("keepa_client.client.requests.get")
    def test_batch_preserves_input_order(self, get_mock, tmp_path: Path):
        # Keepa is documented to return products in request order, but
        # callers (e.g. seller_storefront) iterate over the result list
        # zipped against ASIN context — pin the contract here so a
        # future Keepa quirk gets caught.
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 18,
                "products": [
                    {"asin": "B0CCC", "title": "C"},
                    {"asin": "B0AAA", "title": "A"},
                    {"asin": "B0BBB", "title": "B"},
                ],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        out = client.get_products(["B0CCC", "B0AAA", "B0BBB"])
        assert [p.asin for p in out] == ["B0CCC", "B0AAA", "B0BBB"]

    @patch("keepa_client.client.requests.get")
    def test_batch_serves_cached_asins_without_api_call(
        self, get_mock, tmp_path: Path
    ):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 6,
                "products": [{"asin": "B0NEW", "title": "N"}],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        # Prime cache for B0CACHED.
        client._cache.set(
            "product", "B0CACHED",
            {"asin": "B0CACHED", "title": "Cached"}, ttl_seconds=3600,
        )
        # Batch with one cached + one new → only one ASIN should hit API.
        out = client.get_products(["B0CACHED", "B0NEW"])
        assert get_mock.call_count == 1
        called_params = get_mock.call_args.kwargs["params"]
        assert called_params["asin"] == "B0NEW"
        # Output preserves order.
        assert [p.asin for p in out] == ["B0CACHED", "B0NEW"]

    @patch("keepa_client.client.requests.get")
    def test_batch_all_cached_skips_api(self, get_mock, tmp_path: Path):
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        client._cache.set(
            "product", "B0A",
            {"asin": "B0A", "title": "A"}, ttl_seconds=3600,
        )
        client._cache.set(
            "product", "B0B",
            {"asin": "B0B", "title": "B"}, ttl_seconds=3600,
        )
        out = client.get_products(["B0A", "B0B"])
        assert get_mock.call_count == 0
        assert {p.asin for p in out} == {"B0A", "B0B"}

    @patch("keepa_client.client.requests.get")
    def test_empty_input_returns_empty_list(self, get_mock, tmp_path: Path):
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        assert client.get_products([]) == []
        assert get_mock.call_count == 0

    @patch("keepa_client.client.requests.get")
    def test_batch_chunks_per_product_batch_size(
        self, get_mock, tmp_path: Path
    ):
        # If the input exceeds product_batch_size, the client must split
        # into multiple HTTP calls. Set batch_size=2 and pass 5 ASINs;
        # expect 3 HTTP calls (2+2+1).
        from keepa_client.config import (
            ApiConfig, BatchingConfig, CacheConfig, KeepaConfig,
            RateLimitConfig, RetryConfig,
        )
        cfg = KeepaConfig(
            api=ApiConfig(
                base_url="https://api.keepa.test",
                marketplace=2, request_timeout_seconds=5,
            ),
            rate_limit=RateLimitConfig(
                tokens_per_minute=10000, burst=10000,
                retry_on_429=RetryConfig(
                    max_retries=0, backoff_base_seconds=0,
                    backoff_jitter_seconds=0,
                ),
            ),
            cache=CacheConfig(
                root=tmp_path / "c",
                ttl_seconds={"product": 60, "seller": 60, "category": 60},
            ),
            batching=BatchingConfig(product_batch_size=2),
        )
        get_mock.side_effect = [
            MagicMock(status_code=200, json=lambda: {
                "tokensConsumed": 12,
                "products": [
                    {"asin": "B0A1", "title": "A1"},
                    {"asin": "B0A2", "title": "A2"},
                ],
            }),
            MagicMock(status_code=200, json=lambda: {
                "tokensConsumed": 12,
                "products": [
                    {"asin": "B0A3", "title": "A3"},
                    {"asin": "B0A4", "title": "A4"},
                ],
            }),
            MagicMock(status_code=200, json=lambda: {
                "tokensConsumed": 6,
                "products": [{"asin": "B0A5", "title": "A5"}],
            }),
        ]
        client = KeepaClient(api_key="fake", config=cfg)
        out = client.get_products(["B0A1", "B0A2", "B0A3", "B0A4", "B0A5"])
        assert get_mock.call_count == 3
        assert [p.asin for p in out] == [
            "B0A1", "B0A2", "B0A3", "B0A4", "B0A5",
        ]

    @patch("keepa_client.client.requests.get")
    def test_batch_skips_null_products(self, get_mock, tmp_path: Path):
        # Keepa returns a `products` array entry per requested ASIN; for
        # invalid/unknown ASINs the entry can be `null`. The batch API
        # must filter these so callers don't pass `None` to pydantic.
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 12,
                "products": [
                    {"asin": "B0GOOD", "title": "G"},
                    None,  # Keepa null for invalid ASIN
                    {"asin": "B0OK", "title": "O"},
                ],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        out = client.get_products(["B0GOOD", "B0BAD", "B0OK"])
        assert [p.asin for p in out] == ["B0GOOD", "B0OK"]

    @patch("keepa_client.client.requests.get")
    def test_batch_filters_extra_asins_keepa_returns(
        self, get_mock, tmp_path: Path
    ):
        # Defensive: if Keepa ever returned MORE products than asked
        # (extra ASINs we didn't request), the input-order rebuild
        # must still emit only what was asked. The extras get cached
        # (so a future single-ASIN call hits) but are excluded from
        # the output.
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 6,
                "products": [
                    {"asin": "B0ASKED", "title": "Asked"},
                    {"asin": "B0EXTRA", "title": "Surprise"},
                ],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        out = client.get_products(["B0ASKED"])
        assert [p.asin for p in out] == ["B0ASKED"]
        # Extra ASIN was still cached so a future call doesn't re-fetch.
        get_mock.reset_mock()
        cached = client.get_product("B0EXTRA")
        assert cached.asin == "B0EXTRA"
        assert get_mock.call_count == 0

    @patch("keepa_client.client.requests.get")
    def test_batch_dedupes_duplicate_input_asins(
        self, get_mock, tmp_path: Path
    ):
        # Pin that we don't waste tokens fetching the same ASIN twice
        # if the caller passes duplicates. The output preserves input
        # order including the duplicates (callers may iterate against
        # a parallel list zip) — so the duplicates appear once each.
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 12,
                "products": [
                    {"asin": "B0ONE", "title": "One"},
                    {"asin": "B0TWO", "title": "Two"},
                ],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        out = client.get_products(["B0ONE", "B0TWO", "B0ONE"])
        # Single API call with deduped asin param.
        assert get_mock.call_count == 1
        assert get_mock.call_args.kwargs["params"]["asin"] == "B0ONE,B0TWO"
        # Output preserves input order INCLUDING the duplicate.
        assert [p.asin for p in out] == ["B0ONE", "B0TWO", "B0ONE"]

    @patch("keepa_client.client.requests.get")
    def test_batch_caches_each_product_individually(
        self, get_mock, tmp_path: Path
    ):
        # After a batch fetch, individual get_product calls for the
        # returned ASINs should hit the cache (zero new HTTP calls).
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 12,
                "products": [
                    {"asin": "B0BAT1", "title": "1"},
                    {"asin": "B0BAT2", "title": "2"},
                ],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        client.get_products(["B0BAT1", "B0BAT2"])
        get_mock.reset_mock()
        client.get_product("B0BAT1")
        client.get_product("B0BAT2")
        assert get_mock.call_count == 0


# ---------------------------------------------------------------------------
# Stale-on-error fallback
# ---------------------------------------------------------------------------


class TestStaleOnError:
    @patch("keepa_client.client.requests.get")
    def test_get_product_falls_back_to_stale_on_5xx(
        self, get_mock, tmp_path: Path
    ):
        # Prime cache with stale (expired) entry; configure HTTP mock to
        # 503 forever; client should return the stale value rather than
        # raising. This is the entire point of stale-on-error: degrade
        # gracefully when Keepa is down.
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        client._cache.set(
            "product", "B0STALE",
            {"asin": "B0STALE", "title": "From cache"},
            ttl_seconds=0,  # already stale
        )
        time.sleep(0.01)
        get_mock.return_value = MagicMock(status_code=503, text="oops")
        product = client.get_product("B0STALE")
        assert product.asin == "B0STALE"
        assert product.title == "From cache"

    @patch("keepa_client.client.requests.get")
    def test_get_product_raises_when_no_stale_available(
        self, get_mock, tmp_path: Path
    ):
        # No cache entry → no fallback → propagate the original error.
        # This pins that the new behaviour doesn't silently swallow
        # failures — only the "we have something to serve" path is new.
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        get_mock.return_value = MagicMock(status_code=503, text="oops")
        with pytest.raises(KeepaApiError):
            client.get_product("B0NEVER")

    @patch("keepa_client.client.requests.get")
    def test_get_seller_falls_back_to_stale_on_5xx(
        self, get_mock, tmp_path: Path
    ):
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        client._cache.set(
            "seller", "A1__storefront",
            {"sellerId": "A1", "sellerName": "Stale", "asinList": ["B001"]},
            ttl_seconds=0,
        )
        time.sleep(0.01)
        get_mock.return_value = MagicMock(status_code=503, text="oops")
        seller = client.get_seller("A1", storefront=True)
        assert seller.seller_id == "A1"
        assert seller.asin_list == ["B001"]

    @patch("keepa_client.client.requests.get")
    def test_stale_fallback_logs_with_stale_flag(
        self, get_mock, tmp_path: Path
    ):
        # The token log entry for a stale-fallback hit should be
        # distinguishable from a fresh cache hit and from a successful
        # API call. Pin via `cached=True, stale=True` flag.
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        client._cache.set(
            "product", "B0STALE",
            {"asin": "B0STALE"}, ttl_seconds=0,
        )
        time.sleep(0.01)
        get_mock.return_value = MagicMock(status_code=503, text="oops")
        client.get_product("B0STALE")
        # Read back the token log JSONL.
        log_path = tmp_path / "keepa_cache" / "token_log.jsonl"
        lines = log_path.read_text().strip().splitlines()
        last = json.loads(lines[-1])
        assert last["cached"] is True
        assert last.get("stale") is True
        assert last["tokens"] == 0

    @patch("keepa_client.client.requests.get")
    def test_get_products_batch_falls_back_to_stale_on_5xx(
        self, get_mock, tmp_path: Path
    ):
        # When the batch request fails, the client should serve any
        # stale entries it has cached AND raise for the rest.
        # Decision: the batch returns ONLY the products it could get
        # (cached or stale). Missing-and-stale-unavailable ASINs are
        # filtered out — caller infers absence by comparing input
        # length to output. This matches the null-filtering contract
        # for valid 200-response batches.
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        client._cache.set(
            "product", "B0HAVE",
            {"asin": "B0HAVE", "title": "Have"},
            ttl_seconds=0,
        )
        time.sleep(0.01)
        get_mock.return_value = MagicMock(status_code=503, text="oops")
        out = client.get_products(["B0HAVE", "B0NEVER"])
        # B0HAVE comes from stale cache; B0NEVER has no fallback so it's
        # filtered out. Caller can detect this via len(out) < len(input).
        assert len(out) == 1
        assert out[0].asin == "B0HAVE"
