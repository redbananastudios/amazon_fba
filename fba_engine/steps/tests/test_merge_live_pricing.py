"""Tests for fba_engine.steps.merge_live_pricing.

Bridge step that maps SP-API `live_*` columns (populated by the
preflight step) into the canonical engine columns the second-pass
`calculate(recalculate=True)` reads. Without this step, niche
listings with no Keepa BB data stay at score 44 with target=£0.00
even when SP-API knows the current BB price.
"""
from __future__ import annotations

import pandas as pd

from fba_engine.steps.merge_live_pricing import (
    merge_live_pricing,
    run_step,
)


class TestMergeLivePricing:
    def test_live_buy_box_overwrites_canonical_when_present(self):
        # Keepa-derived buy_box_price was None (niche listing, csv[18]
        # empty). SP-API returned a live BB. Engine should now see the
        # live BB.
        df = pd.DataFrame([{
            "asin": "B0NICHE", "decision": "SHORTLIST",
            "buy_box_price": None,
            "live_buy_box": 15.99,
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["buy_box_price"] == 15.99

    def test_live_buy_box_overwrites_stale_keepa_value(self):
        # Stale Keepa says £14, live SP-API says £18. Live wins —
        # the operator cares about now, not the cache snapshot.
        df = pd.DataFrame([{
            "asin": "B0STALE", "decision": "SHORTLIST",
            "buy_box_price": 14.0,
            "live_buy_box": 18.0,
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["buy_box_price"] == 18.0

    def test_keeps_canonical_when_live_missing(self):
        # SP-API preflight failed for this ASIN — live_buy_box absent.
        # Don't clobber the Keepa value with None.
        df = pd.DataFrame([{
            "asin": "B0KEEPA", "decision": "SHORTLIST",
            "buy_box_price": 14.0,
            "live_buy_box": None,
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["buy_box_price"] == 14.0

    def test_fba_seller_count_overwrites_when_live_present(self):
        df = pd.DataFrame([{
            "asin": "B0SELL", "decision": "SHORTLIST",
            "fba_seller_count": None,
            "live_offer_count_fba": 4,
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["fba_seller_count"] == 4

    def test_amazon_status_set_to_on_listing_when_amzn_holds_bb(self):
        df = pd.DataFrame([{
            "asin": "B0AMZN", "decision": "SHORTLIST",
            "amazon_status": None,
            "live_buy_box_seller": "AMZN",
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["amazon_status"] == "ON_LISTING"

    def test_amazon_status_set_to_off_listing_when_fba_holds_bb(self):
        df = pd.DataFrame([{
            "asin": "B0FBA", "decision": "SHORTLIST",
            "amazon_status": None,
            "live_buy_box_seller": "FBA",
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["amazon_status"] == "OFF_LISTING"

    def test_amazon_status_does_not_clobber_confident_value(self):
        # Keepa-derived amazon_status = "ON_LISTING" (Keepa knows
        # Amazon has been on this listing). Live SP-API today says
        # FBA holds the BB, but Amazon could just be temporarily
        # OOS. Don't clobber the confident upstream value.
        df = pd.DataFrame([{
            "asin": "B0CONFIDENT", "decision": "SHORTLIST",
            "amazon_status": "ON_LISTING",
            "live_buy_box_seller": "FBA",
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["amazon_status"] == "ON_LISTING"

    def test_amazon_status_overrides_unknown(self):
        df = pd.DataFrame([{
            "asin": "B0UNK", "decision": "SHORTLIST",
            "amazon_status": "UNKNOWN",
            "live_buy_box_seller": "FBA",
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["amazon_status"] == "OFF_LISTING"

    def test_reject_rows_pass_through(self):
        # Even with live data present, REJECT rows are immutable.
        df = pd.DataFrame([{
            "asin": "B0KILLED", "decision": "REJECT",
            "buy_box_price": 5.0, "live_buy_box": 99.0,
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["buy_box_price"] == 5.0

    def test_empty_df_passes_through(self):
        out = merge_live_pricing(pd.DataFrame())
        assert out.empty

    def test_negative_or_zero_live_values_ignored(self):
        # Defensive: SP-API shouldn't return -1 or 0 for buy_box_price
        # but if it does (weird marketplace state), don't overwrite
        # a real value with garbage.
        df = pd.DataFrame([{
            "asin": "B0WEIRD", "decision": "SHORTLIST",
            "buy_box_price": 14.0,
            "live_buy_box": -1,
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["buy_box_price"] == 14.0

    def test_non_numeric_live_values_ignored(self):
        df = pd.DataFrame([{
            "asin": "B0BAD", "decision": "SHORTLIST",
            "buy_box_price": 14.0,
            "live_buy_box": "not-a-number",
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["buy_box_price"] == 14.0

    def test_initialises_canonical_columns_when_missing(self):
        # Some chains may produce frames that don't have the canonical
        # columns yet (oa_csv etc.). Step should not raise.
        df = pd.DataFrame([{
            "asin": "B0NEW", "decision": "SHORTLIST",
            "live_buy_box": 12.5,
            "live_offer_count_fba": 2,
            "live_buy_box_seller": "FBA",
        }])
        out = merge_live_pricing(df)
        assert out.iloc[0]["buy_box_price"] == 12.5
        assert out.iloc[0]["fba_seller_count"] == 2
        assert out.iloc[0]["amazon_status"] == "OFF_LISTING"


class TestRunStep:
    def test_run_step_dispatches(self):
        df = pd.DataFrame([{
            "asin": "B0X", "decision": "SHORTLIST",
            "buy_box_price": None, "live_buy_box": 10.0,
        }])
        out = run_step(df, {})
        assert out.iloc[0]["buy_box_price"] == 10.0
