"""Tests for keepa_browser_enrich step + browser_cache helpers.

The step is a silent no-op when the cache is missing (most rows on
most runs). Verifies merge semantics when the cache IS present —
specifically that:
  - Browser-derived buy_box_seller_stats replace API-shape dict
  - FBA flag from Browser flows through (not in API path)
  - top-seller fields populate
  - precomputed 365d signals override API-derived ones
  - missing cache leaves rows untouched
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from keepa_client.browser_cache import (
    BrowserActiveOffer,
    BrowserProductDetails,
    BrowserScrape,
    BrowserSellerStat,
    cache_path_for,
    now_iso,
    read,
    write,
)
from fba_engine.steps.keepa_browser_enrich import (
    BROWSER_ENRICH_COLUMNS,
    add_browser_enrich,
    run_step,
)


def _scrape(asin: str = "B0TEST0001", **overrides) -> BrowserScrape:
    pd_kwargs = overrides.pop("product_details", None) or {
        "buy_box_lowest_365d": 29.99,
        "buy_box_avg_365d": 47.91,
        "buy_box_oos_pct_90d": 0.03,
        "sales_rank_avg_365d": 4516,
        "sales_rank_drops_30d": 35,
    }
    sellers = overrides.pop("buy_box_seller_stats", None)
    if sellers is None:
        sellers = [
            BrowserSellerStat(
                seller_id="Godfreys", pct_won=59, avg_price=56.95,
                stock=43, is_fba=False,
            ),
            BrowserSellerStat(
                seller_id="MB Fulfilment", pct_won=11, avg_price=56.91,
                stock=1, is_fba=True,
            ),
            BrowserSellerStat(
                seller_id="ST7 Store", pct_won=5, avg_price=56.95, is_fba=True,
            ),
        ]
    offers = overrides.pop("active_offers", None) or [
        BrowserActiveOffer(seller_id="Godfreys", stock=43, sold_30d=64, is_fba=False),
        BrowserActiveOffer(seller_id="ST7 Store", stock=5, is_fba=True),
    ]
    return BrowserScrape(
        asin=asin,
        scraped_at=now_iso(),
        product_details=BrowserProductDetails(**pd_kwargs),
        buy_box_seller_stats=sellers,
        active_offers=offers,
        **overrides,
    )


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the cache root to a temp dir for this test."""
    monkeypatch.setattr(
        "keepa_client.browser_cache.cache_root",
        lambda repo_root=None: (tmp_path / "keepa_browser"),
    )
    (tmp_path / "keepa_browser").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ────────────────────────────────────────────────────────────────────────
# Cache I/O
# ────────────────────────────────────────────────────────────────────────


class TestCacheRoundtrip:
    def test_write_then_read_roundtrips(self, isolated_cache):
        scrape = _scrape("B0RT000001")
        path = write(scrape)
        assert path.exists()
        entry = read("B0RT000001")
        assert entry is not None
        assert entry.scrape.asin == "B0RT000001"
        assert len(entry.scrape.buy_box_seller_stats) == 3
        assert entry.scrape.buy_box_seller_stats[0].seller_id == "Godfreys"

    def test_missing_cache_returns_none(self, isolated_cache):
        assert read("B0NOTEXIST") is None

    def test_malformed_cache_returns_none(self, isolated_cache, tmp_path):
        path = tmp_path / "keepa_browser" / "B0BROKEN12.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert read("B0BROKEN12") is None

    def test_path_traversal_asin_rejected(self, isolated_cache):
        from keepa_client.browser_cache import cache_path_for
        with pytest.raises(ValueError):
            cache_path_for("../../../etc/passwd")
        with pytest.raises(ValueError):
            cache_path_for("B001Y54F88/../escape")
        with pytest.raises(ValueError):
            cache_path_for("short")

    def test_stale_entry_marked_stale(self, isolated_cache, tmp_path):
        scrape = _scrape("B0STALE001")
        # Force scraped_at far in the past.
        scrape = scrape.model_copy(update={"scraped_at": "2024-01-01T00:00:00Z"})
        write(scrape)
        entry = read("B0STALE001", ttl_seconds=3600)
        assert entry is not None
        assert entry.is_stale is True

    def test_stale_entry_dropped_when_allow_stale_false(self, isolated_cache):
        scrape = _scrape("B0STALE002")
        scrape = scrape.model_copy(update={"scraped_at": "2024-01-01T00:00:00Z"})
        write(scrape)
        assert read("B0STALE002", ttl_seconds=3600, allow_stale=False) is None


# ────────────────────────────────────────────────────────────────────────
# Enrich step — merge semantics
# ────────────────────────────────────────────────────────────────────────


class TestEnrichWithCacheHit:
    def test_browser_data_overrides_buy_box_min_365d(self, isolated_cache):
        write(_scrape("B0HIT00001"))
        df = pd.DataFrame([{
            "asin": "B0HIT00001",
            # API-derived value would be slightly different — Browser
            # precomputed value should win.
            "buy_box_min_365d": 31.50,
        }])
        out = add_browser_enrich(df)
        assert out.iloc[0]["buy_box_min_365d"] == 29.99
        assert bool(out.iloc[0]["browser_scrape_present"]) is True

    def test_browser_data_replaces_buy_box_seller_stats_with_named_sellers(
        self, isolated_cache,
    ):
        write(_scrape("B0HIT00002"))
        df = pd.DataFrame([{"asin": "B0HIT00002"}])
        out = add_browser_enrich(df)
        stats = out.iloc[0]["buy_box_seller_stats"]
        # Real seller names (vs anonymous merchant IDs from API).
        assert "Godfreys" in stats
        assert stats["Godfreys"]["isFBA"] is False
        assert stats["Godfreys"]["percentageWon"] == 59
        assert stats["MB Fulfilment"]["isFBA"] is True

    def test_top_seller_fields_populate(self, isolated_cache):
        write(_scrape("B0HIT00003"))
        df = pd.DataFrame([{"asin": "B0HIT00003"}])
        out = add_browser_enrich(df)
        assert out.iloc[0]["browser_top_seller"] == "Godfreys"
        assert abs(out.iloc[0]["browser_top_seller_pct"] - 0.59) < 0.001
        assert bool(out.iloc[0]["browser_top_seller_is_fba"]) is False

    def test_active_seller_count_populates(self, isolated_cache):
        write(_scrape("B0HIT00004"))
        df = pd.DataFrame([{"asin": "B0HIT00004"}])
        out = add_browser_enrich(df)
        assert out.iloc[0]["browser_active_seller_count"] == 2
        assert out.iloc[0]["browser_active_fba_seller_count"] == 1

    def test_precomputed_signals_override_api_derived(self, isolated_cache):
        scrape = _scrape("B0HIT00005")
        scrape.product_details.buy_box_oos_pct_90d = 0.05
        scrape.product_details.sales_rank_drops_30d = 25
        write(scrape)
        df = pd.DataFrame([{
            "asin": "B0HIT00005",
            # API-derived values would be different.
            "buy_box_oos_pct_90": 0.20,   # API said 20%
            "bsr_drops_30d": 10,           # API said 10
        }])
        out = add_browser_enrich(df)
        # Browser's precomputed values should override.
        assert out.iloc[0]["buy_box_oos_pct_90"] == 0.05
        assert out.iloc[0]["bsr_drops_30d"] == 25


# ────────────────────────────────────────────────────────────────────────
# Enrich step — silent no-op when cache missing
# ────────────────────────────────────────────────────────────────────────


class TestEnrichCacheMiss:
    def test_missing_cache_leaves_row_untouched(self, isolated_cache):
        df = pd.DataFrame([{
            "asin": "B0MISS0001",
            "buy_box_min_365d": 31.50,
            "amazon_bb_pct_90": 0.02,
        }])
        out = add_browser_enrich(df)
        # API-derived fields untouched.
        assert out.iloc[0]["buy_box_min_365d"] == 31.50
        assert out.iloc[0]["amazon_bb_pct_90"] == 0.02
        # Browser-cache marker = False.
        assert bool(out.iloc[0]["browser_scrape_present"]) is False

    def test_mixed_hit_and_miss(self, isolated_cache):
        write(_scrape("B0HIT99999"))
        df = pd.DataFrame([
            {"asin": "B0HIT99999"},
            {"asin": "B0MISS9999"},
        ])
        out = add_browser_enrich(df)
        assert bool(out.iloc[0]["browser_scrape_present"]) is True
        assert bool(out.iloc[1]["browser_scrape_present"]) is False

    def test_empty_df_produces_columns_anyway(self, isolated_cache):
        out = add_browser_enrich(pd.DataFrame())
        assert out.empty
        for col in BROWSER_ENRICH_COLUMNS:
            assert col in out.columns


class TestRunStep:
    def test_run_step_basic(self, isolated_cache):
        write(_scrape("B0RUN00001"))
        df = pd.DataFrame([{"asin": "B0RUN00001"}])
        out = run_step(df, {})
        assert bool(out.iloc[0]["browser_scrape_present"]) is True


# ────────────────────────────────────────────────────────────────────────
# Validator integration — share-aware velocity uses Browser data
# ────────────────────────────────────────────────────────────────────────


class TestValidatorIntegration:
    def test_velocity_predictor_filters_to_fba_via_browser_isfba(self, isolated_cache):
        """The Browser cache tags isFBA per seller. predict_seller_velocity
        already filters buy_box_seller_stats by isFBA — so a Browser cache
        hit should produce a different (more accurate) velocity than the
        equal-split fallback, AND should exclude FBM sellers from the
        median."""
        from sourcing_engine.opportunity import predict_seller_velocity

        write(_scrape("B0VEL00001"))
        df = pd.DataFrame([{
            "asin": "B0VEL00001",
            "sales_estimate": 100,
            "fba_seller_count": 9,   # Browser shows 9 sellers in BB
            "amazon_bb_pct_90": 0.0,
        }])
        out = add_browser_enrich(df)
        row = out.iloc[0].to_dict()
        v = predict_seller_velocity(row)
        # share_source label confirms it used the per-seller data.
        assert v is not None
        assert "median-of" in v["share_source"]
        # The 7 FBA sellers in our default fixture: 11/5/<1>... median is
        # ~5% — so velocity should be small but not zero.
        assert v["mid"] > 0


class TestSubOnePercentSellers:
    """Regression for the dual-format heuristic bug: live cache files
    carry tail sellers with ``pct_won = 0.5`` meaning literally 0.5%.
    Old code treated <=1.0 as already-fraction → 50%. Convention is
    now locked: ``pct_won`` is always raw percent (0–100)."""

    def test_fractional_pct_stays_fractional(self, isolated_cache):
        sellers = [
            BrowserSellerStat(seller_id="Big",   pct_won=98.0, is_fba=True),
            BrowserSellerStat(seller_id="Tail1", pct_won=0.5,  is_fba=True),
            BrowserSellerStat(seller_id="Tail2", pct_won=0.5,  is_fba=True),
        ]
        write(_scrape("B0SUBPCT01", buy_box_seller_stats=sellers))
        df = pd.DataFrame([{"asin": "B0SUBPCT01"}])
        out = add_browser_enrich(df)
        # Top seller dominates BB → 0.98 fraction (NOT 98 / NOT 0.98 misread).
        assert abs(out.iloc[0]["browser_top_seller_pct"] - 0.98) < 0.001
        # API-shape dict carries raw-percent for validator's /100 step.
        stats = out.iloc[0]["buy_box_seller_stats"]
        assert stats["Tail1"]["percentageWon"] == 0.5  # NOT 50
        assert stats["Big"]["percentageWon"] == 98.0

    def test_velocity_uses_correct_median_for_subpct_tail(self, isolated_cache):
        """Ten FBA sellers (one at 90.5%, nine at 0.5%). Median = 0.5%.
        Sales baseline 1000/mo → entrant share ≈ 5/mo. Pre-fix the
        0.5 values were misread as 50% → median 50% → ~500/mo
        (10x off). The post-fix range 1–50 covers the correct answer
        and excludes the buggy one.

        The fixture overrides product_details to drop the default
        sales_rank_drops_30d (which the validator would otherwise
        merge in as bsr_drops_30d=35 and let bsr_proxy=52 dominate
        sales_estimate=1000 via the conservative-min rule)."""
        from sourcing_engine.opportunity import predict_seller_velocity

        sellers = [BrowserSellerStat(seller_id="Big", pct_won=90.5, is_fba=True)]
        for i in range(9):
            sellers.append(
                BrowserSellerStat(seller_id=f"Tail{i}", pct_won=0.5, is_fba=True),
            )
        # Empty product_details ⇒ no bsr_drops merged; sales_estimate wins.
        write(_scrape(
            "B0SUBPCT02",
            buy_box_seller_stats=sellers,
            product_details={"buy_box_lowest_365d": 9.99},
        ))
        df = pd.DataFrame([{
            "asin": "B0SUBPCT02",
            "sales_estimate": 1000,
            "fba_seller_count": 10,
            "amazon_bb_pct_90": 0.0,
        }])
        out = add_browser_enrich(df)
        v = predict_seller_velocity(out.iloc[0].to_dict())
        assert v is not None
        assert "median-of-10-sellers" in v["share_source"]
        # Median of 0.5% on 1000/mo sales → ~5/mo. Pre-fix bug would
        # have produced ~500/mo (median treated as 50%).
        assert 1 <= v["mid"] <= 50
