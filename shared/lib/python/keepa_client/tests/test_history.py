"""Tests for keepa_client.history.

Per HANDOFF_candidate_validation.md WS2.1 acceptance: target ≥90%
line coverage on the history module. Each function is tested with:
  - happy path
  - empty / None / malformed input
  - all-(-1)-sentinels
  - exactly-at-window-boundary (where relevant)
  - one positive synthetic shape (rising line, declining line, etc.)

These signals feed the candidate score in WS3 — if the math here
silently returns garbage, every downstream rubric inherits it.
"""
from __future__ import annotations

import pytest

from keepa_client.history import (
    amazon_bb_share_pct,
    bsr_slope,
    buy_box_min_in_window,
    buy_box_winner_flips,
    listing_age_days,
    offer_count_trend,
    out_of_stock_pct,
    parse_keepa_csv_series,
    price_volatility,
    review_count_change,
    sales_rank_consistency,
    yoy_bsr_ratio,
)
from keepa_client.models import _now_keepa_minutes


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────

DAY = 24 * 60        # minutes
WEEK = 7 * DAY
YEAR = 365 * DAY


def _interleave(pairs: list[tuple[int, int | None]]) -> list:
    """Build a Keepa-shaped [t, v, t, v, ...] array from typed pairs."""
    out: list = []
    for t, v in pairs:
        out.append(t)
        out.append(-1 if v is None else v)
    return out


# ────────────────────────────────────────────────────────────────────────
# parse_keepa_csv_series
# ────────────────────────────────────────────────────────────────────────


class TestParseKeepaCsvSeries:
    def test_round_trips_basic_pairs(self):
        now = _now_keepa_minutes()
        series = _interleave([(now - 60, 100), (now - 30, 200)])
        out = parse_keepa_csv_series(series)
        assert len(out) == 2
        assert out[0][1] == 100
        assert out[1][1] == 200

    def test_minus_one_sentinel_becomes_none(self):
        now = _now_keepa_minutes()
        series = [now - 60, -1, now - 30, 200]
        out = parse_keepa_csv_series(series)
        assert out[0][1] is None
        assert out[1][1] == 200

    def test_empty_returns_empty_list(self):
        assert parse_keepa_csv_series([]) == []
        assert parse_keepa_csv_series(None) == []
        assert parse_keepa_csv_series([100]) == []  # single timestamp

    def test_drops_dangling_trailing_timestamp(self):
        # Length 5 — trailing timestamp dropped.
        now = _now_keepa_minutes()
        series = [now - 60, 100, now - 30, 200, now - 15]
        out = parse_keepa_csv_series(series)
        assert len(out) == 2

    def test_skips_unparseable_timestamps(self):
        # Garbage at the timestamp slot — pair dropped.
        series = ["not-an-int", 100, _now_keepa_minutes() - 30, 200]
        out = parse_keepa_csv_series(series)
        assert len(out) == 1
        assert out[0][1] == 200


# ────────────────────────────────────────────────────────────────────────
# bsr_slope
# ────────────────────────────────────────────────────────────────────────


class TestBsrSlope:
    def test_improving_rank_returns_negative_slope(self):
        # Rank getting smaller (improving) over 30 days.
        now = _now_keepa_minutes()
        pts = [
            (now - 25 * DAY, 100_000),
            (now - 20 * DAY, 90_000),
            (now - 15 * DAY, 80_000),
            (now - 10 * DAY, 70_000),
            (now - 5 * DAY, 60_000),
        ]
        slope = bsr_slope(_interleave(pts), window_days=30)
        assert slope is not None
        assert slope < 0  # improving

    def test_declining_rank_returns_positive_slope(self):
        # Rank getting larger (declining) over 30 days.
        now = _now_keepa_minutes()
        pts = [
            (now - 25 * DAY, 50_000),
            (now - 20 * DAY, 60_000),
            (now - 15 * DAY, 70_000),
            (now - 10 * DAY, 80_000),
            (now - 5 * DAY, 90_000),
        ]
        slope = bsr_slope(_interleave(pts), window_days=30)
        assert slope is not None
        assert slope > 0  # declining

    def test_flat_rank_returns_zero_slope(self):
        now = _now_keepa_minutes()
        pts = [(now - i * DAY, 50_000) for i in range(25, 0, -5)]
        slope = bsr_slope(_interleave(pts), window_days=30)
        assert slope is not None
        assert abs(slope) < 1e-9

    def test_returns_none_for_insufficient_points(self):
        now = _now_keepa_minutes()
        pts = [(now - 10 * DAY, 50_000), (now - 5 * DAY, 60_000)]
        assert bsr_slope(_interleave(pts), window_days=30) is None

    def test_returns_none_for_all_sentinels(self):
        now = _now_keepa_minutes()
        series = []
        for i in range(10):
            series.extend([now - i * DAY, -1])
        assert bsr_slope(series, window_days=30) is None

    def test_returns_none_for_empty_input(self):
        assert bsr_slope([], window_days=30) is None
        assert bsr_slope(None, window_days=30) is None

    def test_window_excludes_old_points(self):
        # Rank improves dramatically inside the window; outside, flat.
        # Outside-window points must NOT pull the slope toward zero.
        # Critically, this includes a point JUST PAST the 30-day cutoff
        # (now - 31*DAY) — that's the boundary case `t_min < cutoff`
        # actually has to discriminate. Without that point, deeply-old
        # points trivially pass any cutoff check and the test would
        # silently approve a broken filter.
        now = _now_keepa_minutes()
        pts_outside_window = [
            (now - 31 * DAY, 50_000),       # just past the cutoff
            (now - 100 * DAY, 50_000),
            (now - 200 * DAY, 50_000),
        ]
        pts_inside = [
            (now - 25 * DAY, 100_000),
            (now - 20 * DAY, 80_000),
            (now - 15 * DAY, 60_000),
            (now - 10 * DAY, 40_000),
            (now - 5 * DAY, 20_000),
        ]
        slope_with_old = bsr_slope(
            _interleave(pts_outside_window + pts_inside), window_days=30,
        )
        slope_inside_only = bsr_slope(_interleave(pts_inside), window_days=30)
        # Both should agree — outside-window points are dropped, including
        # the boundary case at now - 31*DAY.
        assert slope_with_old is not None and slope_inside_only is not None
        assert abs(slope_with_old - slope_inside_only) < 1e-9

    def test_normalisation_makes_magnitudes_comparable(self):
        # Same relative shape on different rank magnitudes should
        # produce slopes of similar magnitude. Without normalisation
        # a 100k→50k change would dwarf a 1000→500 change numerically;
        # after dividing by mean rank both are -0.5/window equivalent.
        now = _now_keepa_minutes()
        big = [(now - i * DAY, 100_000 - (25 - i) * 2000)
               for i in range(25, 0, -5)]
        small = [(now - i * DAY, 1_000 - (25 - i) * 20)
                 for i in range(25, 0, -5)]
        s_big = bsr_slope(_interleave(big), window_days=30)
        s_small = bsr_slope(_interleave(small), window_days=30)
        assert s_big is not None and s_small is not None
        # Within a factor of 1.5 of each other — same shape, same scale.
        assert abs(s_big / s_small - 1) < 0.5


# ────────────────────────────────────────────────────────────────────────
# offer_count_trend
# ────────────────────────────────────────────────────────────────────────


class TestOfferCountTrend:
    def test_returns_full_dict_with_summary(self):
        now = _now_keepa_minutes()
        # 3 → 5 → 7 sellers over 90 days. Peak = end = 7.
        pts = [
            (now - 80 * DAY, 3),
            (now - 60 * DAY, 5),
            (now - 40 * DAY, 6),
            (now - 20 * DAY, 7),
        ]
        out = offer_count_trend(_interleave(pts), window_days=90)
        assert out is not None
        assert out["start"] == 3
        assert out["end"] == 7
        assert out["peak"] == 7
        assert out["joiners_90d"] == 4  # 7 - 3
        assert out["current"] == 7

    def test_joiners_zero_when_count_decreased(self):
        # Sellers leaving the listing — joiners is max(end-start, 0).
        now = _now_keepa_minutes()
        pts = [(now - 80 * DAY, 10), (now - 20 * DAY, 5)]
        out = offer_count_trend(_interleave(pts), window_days=90)
        assert out["joiners_90d"] == 0
        assert out["start"] == 10
        assert out["end"] == 5
        assert out["peak"] == 10

    def test_returns_none_for_no_observations_in_window(self):
        # All points outside window.
        now = _now_keepa_minutes()
        pts = [(now - 200 * DAY, 5), (now - 150 * DAY, 7)]
        assert offer_count_trend(_interleave(pts), window_days=90) is None

    def test_returns_none_for_empty(self):
        assert offer_count_trend([], window_days=90) is None
        assert offer_count_trend(None, window_days=90) is None

    def test_skips_minus_one_sentinel(self):
        now = _now_keepa_minutes()
        series = [now - 80 * DAY, -1, now - 60 * DAY, 5, now - 20 * DAY, 7]
        out = offer_count_trend(series, window_days=90)
        assert out is not None
        assert out["start"] == 5
        assert out["end"] == 7

    def test_peak_higher_than_end(self):
        # 3 → 8 → 6 — peak is 8, end is 6.
        now = _now_keepa_minutes()
        pts = [(now - 80 * DAY, 3), (now - 50 * DAY, 8), (now - 20 * DAY, 6)]
        out = offer_count_trend(_interleave(pts), window_days=90)
        assert out["peak"] == 8
        assert out["end"] == 6
        assert out["joiners_90d"] == 3  # 6 - 3, NOT 8 - 3


# ────────────────────────────────────────────────────────────────────────
# out_of_stock_pct
# ────────────────────────────────────────────────────────────────────────


class TestOutOfStockPct:
    def test_zero_pct_when_all_present(self):
        now = _now_keepa_minutes()
        pts = [(now - i * DAY, 1500) for i in range(80, 0, -10)]
        assert out_of_stock_pct(_interleave(pts), window_days=90) == 0.0

    def test_full_pct_when_all_sentinels(self):
        now = _now_keepa_minutes()
        series = []
        for i in range(80, 0, -10):
            series.extend([now - i * DAY, -1])
        # 8 sentinels, 0 real — should be 1.0.
        assert out_of_stock_pct(series, window_days=90) == 1.0

    def test_partial_pct(self):
        now = _now_keepa_minutes()
        # 5 present, 5 missing → 50% OOS.
        series = []
        for i in range(80, 30, -10):
            series.extend([now - i * DAY, 1500])
        for i in range(20, -1, -5):
            series.extend([now - i * DAY, -1])
        pct = out_of_stock_pct(series, window_days=90)
        assert pct is not None
        assert 0.49 < pct < 0.51

    def test_returns_none_for_too_few_points(self):
        # Below MIN_POINTS_OOS=5 → None.
        now = _now_keepa_minutes()
        pts = [(now - 10 * DAY, 1500), (now - 5 * DAY, -1)]
        assert out_of_stock_pct(_interleave(pts), window_days=90) is None

    def test_returns_none_for_empty(self):
        assert out_of_stock_pct([], window_days=90) is None
        assert out_of_stock_pct(None, window_days=90) is None


# ────────────────────────────────────────────────────────────────────────
# buy_box_winner_flips
# ────────────────────────────────────────────────────────────────────────


class TestBuyBoxWinnerFlips:
    def test_counts_distinct_sellers(self):
        now = _now_keepa_minutes()
        # 3 distinct sellers across 4 observations.
        history = [
            now - 80 * DAY, "A1",
            now - 60 * DAY, "A2",
            now - 40 * DAY, "A1",   # repeat
            now - 20 * DAY, "A3",
        ]
        assert buy_box_winner_flips(history, window_days=90) == 3

    def test_skips_sentinel_seller_ids(self):
        # -1 / "" / None all mean "no Buy Box winner".
        now = _now_keepa_minutes()
        history = [
            now - 80 * DAY, "A1",
            now - 60 * DAY, -1,
            now - 40 * DAY, "",
            now - 20 * DAY, None,
            now - 10 * DAY, "A2",
        ]
        assert buy_box_winner_flips(history, window_days=90) == 2

    def test_returns_none_for_empty_or_missing(self):
        assert buy_box_winner_flips(None, window_days=90) is None
        assert buy_box_winner_flips([], window_days=90) is None

    def test_returns_none_for_too_few_in_window(self):
        # Only 1 entry inside the 90-day window — not enough to call
        # it a meaningful flip count. Outside-window entries don't
        # count toward the in-window minimum either.
        now = _now_keepa_minutes()
        history = [
            now - 200 * DAY, "A0",   # outside window
            now - 80 * DAY, "A1",    # only one inside
        ]
        assert buy_box_winner_flips(history, window_days=90) is None

    def test_window_excludes_old_observations(self):
        now = _now_keepa_minutes()
        history = [
            now - 200 * DAY, "OLD1",
            now - 200 * DAY + WEEK, "OLD2",
            now - 80 * DAY, "A1",
            now - 40 * DAY, "A2",
        ]
        # Only A1, A2 inside the 90-day window.
        assert buy_box_winner_flips(history, window_days=90) == 2


# ────────────────────────────────────────────────────────────────────────
# price_volatility
# ────────────────────────────────────────────────────────────────────────


class TestPriceVolatility:
    def test_low_volatility_for_stable_price(self):
        # Buy Box hovering around 1500 cents.
        now = _now_keepa_minutes()
        pts = []
        for i, p in enumerate([1500, 1505, 1495, 1500, 1510, 1490, 1500, 1500]):
            pts.append((now - (80 - i * 10) * DAY, p))
        cv = price_volatility(_interleave(pts), window_days=90)
        assert cv is not None
        assert cv < 0.05

    def test_high_volatility_for_swinging_price(self):
        now = _now_keepa_minutes()
        pts = []
        for i, p in enumerate([1000, 2000, 1000, 2500, 1200, 1800, 900, 2200]):
            pts.append((now - (80 - i * 10) * DAY, p))
        cv = price_volatility(_interleave(pts), window_days=90)
        assert cv is not None
        assert cv > 0.20

    def test_returns_none_for_too_few_points(self):
        now = _now_keepa_minutes()
        pts = [(now - 10 * DAY, 1500), (now - 5 * DAY, 1600)]
        assert price_volatility(_interleave(pts), window_days=90) is None

    def test_skips_sentinels(self):
        now = _now_keepa_minutes()
        # 5 real + 3 sentinels — should still produce a CV based on
        # the 5 real values.
        series = []
        for i, p in enumerate([1500, 1500, 1500, 1500, 1500]):
            series.extend([now - (50 - i * 5) * DAY, p])
        for i in range(3):
            series.extend([now - (10 - i * 2) * DAY, -1])
        cv = price_volatility(series, window_days=90)
        assert cv is not None
        assert cv == 0.0  # all real values are 1500 → zero stdev

    def test_returns_none_for_empty(self):
        assert price_volatility([], window_days=90) is None
        assert price_volatility(None, window_days=90) is None


# ────────────────────────────────────────────────────────────────────────
# listing_age_days
# ────────────────────────────────────────────────────────────────────────


class TestListingAgeDays:
    def test_basic_age_in_days(self):
        # Tracked since 200 days ago — age should be 200.
        ts = _now_keepa_minutes() - 200 * DAY
        assert listing_age_days(ts) == 200

    def test_zero_for_brand_new(self):
        ts = _now_keepa_minutes()
        # Same minute → 0 days.
        assert listing_age_days(ts) == 0

    def test_none_for_missing_input(self):
        assert listing_age_days(None) is None

    def test_negative_delta_clamps_to_zero(self):
        # tracking_since in the future (clock skew) → age = 0.
        ts = _now_keepa_minutes() + 100 * DAY
        assert listing_age_days(ts) == 0

    def test_unparseable_input_returns_none(self):
        assert listing_age_days("not-a-number") is None  # type: ignore[arg-type]


# ────────────────────────────────────────────────────────────────────────
# yoy_bsr_ratio
# ────────────────────────────────────────────────────────────────────────


class TestBuyBoxMinInWindow:
    """Pin the 12-month BB-floor lookup. The chart-readable
    "have we ever seen this cheaper than today?" signal."""

    def test_returns_lowest_in_window(self):
        now = _now_keepa_minutes()
        bb = [
            now - 300 * DAY, 1500,
            now - 200 * DAY, 1100,    # the floor
            now - 100 * DAY, 1300,
            now - 30 * DAY, 1400,
        ]
        assert buy_box_min_in_window(bb, window_days=365) == 1100.0

    def test_excludes_outside_window(self):
        now = _now_keepa_minutes()
        bb = [
            now - 500 * DAY, 800,     # outside 365 — must NOT count
            now - 100 * DAY, 1200,    # in-window minimum
            now - 30 * DAY, 1500,
        ]
        assert buy_box_min_in_window(bb, window_days=365) == 1200.0

    def test_skips_minus_one_sentinels(self):
        now = _now_keepa_minutes()
        bb = [
            now - 100 * DAY, -1,
            now - 50 * DAY, 1500,
            now - 10 * DAY, -1,
        ]
        assert buy_box_min_in_window(bb, window_days=365) == 1500.0

    def test_single_observation_returns_that_value(self):
        now = _now_keepa_minutes()
        bb = [now - 60 * DAY, 1500]
        assert buy_box_min_in_window(bb, window_days=365) == 1500.0

    def test_empty_or_all_sentinels_returns_none(self):
        assert buy_box_min_in_window([], window_days=365) is None
        assert buy_box_min_in_window(None, window_days=365) is None
        now = _now_keepa_minutes()
        assert buy_box_min_in_window(
            [now - 60 * DAY, -1, now - 30 * DAY, -1],
            window_days=365,
        ) is None


class TestSalesRankConsistency:
    """Pin the sales-rank CV. Lower = steady; higher = spiky."""

    def test_steady_rank_low_cv(self):
        now = _now_keepa_minutes()
        # All ranks ~50,000; tiny variation.
        rank_csv = []
        for i, r in enumerate([50000, 50100, 49950, 50050, 50000, 49980]):
            rank_csv.extend([now - (60 - i * 10) * DAY, r])
        cv = sales_rank_consistency(rank_csv, window_days=90)
        assert cv is not None
        assert cv < 0.01

    def test_spiky_rank_high_cv(self):
        now = _now_keepa_minutes()
        # Rank bouncing 10K → 200K wildly.
        rank_csv = []
        for i, r in enumerate([10000, 200000, 15000, 180000, 8000, 250000]):
            rank_csv.extend([now - (60 - i * 10) * DAY, r])
        cv = sales_rank_consistency(rank_csv, window_days=90)
        assert cv is not None
        assert cv > 0.5

    def test_returns_none_for_too_few_points(self):
        now = _now_keepa_minutes()
        rank_csv = [now - 30 * DAY, 50000, now - 10 * DAY, 60000]
        assert sales_rank_consistency(rank_csv, window_days=90) is None

    def test_skips_sentinels(self):
        now = _now_keepa_minutes()
        # Mixture of real + sentinel; only real values counted.
        rank_csv = []
        for i in range(6):
            rank_csv.extend([now - (60 - i * 10) * DAY, 50000])
        rank_csv.extend([now - 5 * DAY, -1])
        cv = sales_rank_consistency(rank_csv, window_days=90)
        assert cv is not None
        # All real values are 50000 → CV = 0.
        assert cv == 0.0

    def test_returns_none_for_empty(self):
        assert sales_rank_consistency([], window_days=90) is None
        assert sales_rank_consistency(None, window_days=90) is None


class TestAmazonBbSharePct:
    """Pin the API-path derivation of Amazon's Buy-Box share %.

    The Browser CSV path carries this as a precomputed column; the
    API path didn't until PR A wired this helper. Without it,
    single_asin runs against niche listings landed at LOW
    data_confidence on the missing-amazon_bb_pct_90 critical field.
    """

    def test_returns_zero_when_amazon_never_holds_bb(self):
        # Amazon priced 23.86 throughout; BB priced 16.90 throughout
        # — Amazon never held the BB.
        now = _now_keepa_minutes()
        bb = []
        amz = []
        for day in range(80, 0, -10):
            bb.extend([now - day * DAY, 1690])
            amz.extend([now - day * DAY, 2386])
        assert amazon_bb_share_pct(bb, amz, window_days=90) == 0.0

    def test_returns_one_when_amazon_always_matches(self):
        # BB and Amazon the same price for every observation — Amazon
        # held the BB the entire time.
        now = _now_keepa_minutes()
        bb = []
        amz = []
        for day in range(80, 0, -10):
            bb.extend([now - day * DAY, 1500])
            amz.extend([now - day * DAY, 1500])
        assert amazon_bb_share_pct(bb, amz, window_days=90) == 1.0

    def test_partial_share(self):
        # 5 BB observations; Amazon matched 2 of them (40%).
        now = _now_keepa_minutes()
        bb = [
            now - 80 * DAY, 1500,
            now - 60 * DAY, 1500,    # match
            now - 40 * DAY, 1690,
            now - 20 * DAY, 1500,    # match
            now - 10 * DAY, 1690,
        ]
        # Amazon priced 1500 from day 60 onwards; 1690 before that.
        # Step-function lookup: BB at day 80 sees Amazon=1690 (no match).
        # BB at day 60 sees Amazon's last obs at-or-before — 1500 (match).
        # BB at day 40 sees Amazon=1500 (vs BB=1690, no match).
        # BB at day 20 sees Amazon=1500 (match).
        # BB at day 10 sees Amazon=1500 (vs BB=1690, no match).
        amz = [
            now - 100 * DAY, 1690,
            now - 60 * DAY, 1500,
        ]
        result = amazon_bb_share_pct(bb, amz, window_days=90)
        assert result is not None
        assert 0.39 < result < 0.41   # 2/5 = 0.4

    def test_tolerance_units_handles_rounding(self):
        # BB and Amazon differ by 1 cent — counts as a match (default
        # tolerance=1).
        now = _now_keepa_minutes()
        bb = [now - 60 * DAY, 1500, now - 30 * DAY, 1500]
        amz = [now - 60 * DAY, 1499, now - 30 * DAY, 1501]
        assert amazon_bb_share_pct(bb, amz, window_days=90) == 1.0

    def test_returns_none_when_either_input_empty(self):
        assert amazon_bb_share_pct([], [], window_days=90) is None
        assert amazon_bb_share_pct(None, None, window_days=90) is None
        assert amazon_bb_share_pct(
            [_now_keepa_minutes(), 1500], None, window_days=90,
        ) is None

    def test_returns_none_when_no_in_window_bb(self):
        # All BB observations outside the 90-day window.
        now = _now_keepa_minutes()
        bb = [now - 200 * DAY, 1500, now - 150 * DAY, 1500]
        amz = [now - 200 * DAY, 1500, now - 150 * DAY, 1500]
        assert amazon_bb_share_pct(bb, amz, window_days=90) is None

    def test_skips_amazon_sentinels(self):
        # Amazon's price arrays sometimes carry -1 sentinels (Amazon
        # OOS at that point). A BB observation with no live Amazon
        # price can't be Amazon-held — counted as no-match.
        now = _now_keepa_minutes()
        bb = [
            now - 80 * DAY, 1500,
            now - 40 * DAY, 1500,
        ]
        amz = [
            now - 80 * DAY, -1,        # Amazon OOS — BB at this time can't match
            now - 40 * DAY, 1500,      # match
        ]
        result = amazon_bb_share_pct(bb, amz, window_days=90)
        assert result is not None
        assert result == 0.5

    def test_b0b636zkzq_calibration(self):
        """Real-world: B0B636ZKZQ has Amazon dormant at £23.86 while
        the actual Buy Box bounces around £16.90. Expected
        amazon_bb_pct_90 ≈ 0.0 (Amazon never holds the BB).

        This pins the calibration scenario from the operator's
        first-run smoke test — the result must match a human
        looking at the chart and saying 'Amazon never wins this BB'."""
        now = _now_keepa_minutes()
        bb_observations: list[int] = []
        for day in range(85, 0, -2):
            bb_observations.extend([now - day * DAY, 1690])
        amazon_observations = [
            now - 85 * DAY, 2386,
            now - 30 * DAY, 2386,
            now - 5 * DAY, 2386,
        ]
        result = amazon_bb_share_pct(
            bb_observations, amazon_observations, window_days=90,
        )
        assert result == 0.0


class TestReviewCountChange:
    def test_positive_delta_for_growing_reviews(self):
        now = _now_keepa_minutes()
        # 50 → 60 → 73 over the 90-day window.
        series = [
            now - 80 * DAY, 50,
            now - 40 * DAY, 60,
            now - 10 * DAY, 73,
        ]
        assert review_count_change(series, window_days=90) == 23

    def test_zero_delta_for_flat_reviews(self):
        now = _now_keepa_minutes()
        series = [now - 80 * DAY, 100, now - 10 * DAY, 100]
        assert review_count_change(series, window_days=90) == 0

    def test_negative_delta_when_count_dropped(self):
        # Amazon occasionally prunes reviews; rare but possible.
        now = _now_keepa_minutes()
        series = [now - 80 * DAY, 100, now - 10 * DAY, 95]
        assert review_count_change(series, window_days=90) == -5

    def test_single_observation_returns_zero_not_none(self):
        # Only one observation in window — no change observable, but
        # the helper returns 0 (flat) rather than None so the
        # candidate-score's "flat → 2 points" path still scores it.
        now = _now_keepa_minutes()
        series = [now - 10 * DAY, 100]
        assert review_count_change(series, window_days=90) == 0

    def test_no_observations_returns_none(self):
        # All sentinels in window → genuinely insufficient → None.
        now = _now_keepa_minutes()
        series = [now - 10 * DAY, -1, now - 5 * DAY, -1]
        assert review_count_change(series, window_days=90) is None

    def test_empty_returns_none(self):
        assert review_count_change([], window_days=90) is None
        assert review_count_change(None, window_days=90) is None


class TestYoyBsrRatio:
    def test_returns_none_when_history_too_short(self):
        now = _now_keepa_minutes()
        # Only 100 days of history — needs ≥365.
        pts = [(now - i * DAY, 50_000) for i in range(100, 0, -10)]
        assert yoy_bsr_ratio(_interleave(pts)) is None

    def test_ratio_above_one_when_rank_improved_yoy(self):
        # Last year same week: rank 100k. This week: rank 50k → improved.
        # ratio = 100k / 50k = 2.0
        now = _now_keepa_minutes()
        pts = []
        # Earliest point: a year + a week ago, anchoring history length.
        pts.append((now - YEAR - WEEK, 100_000))
        # Last year's window — ±half-week around the YEAR mark.
        for offset in range(-3, 4):
            pts.append((now - YEAR + offset * DAY, 100_000))
        # This week — within last 7 days.
        for offset in range(0, 7):
            pts.append((now - offset * DAY, 50_000))
        ratio = yoy_bsr_ratio(_interleave(pts))
        assert ratio is not None
        assert 1.9 < ratio < 2.1

    def test_ratio_below_one_when_rank_declined_yoy(self):
        # Last year: rank 50k. This week: rank 100k → worse.
        now = _now_keepa_minutes()
        pts = []
        pts.append((now - YEAR - WEEK, 50_000))
        for offset in range(-3, 4):
            pts.append((now - YEAR + offset * DAY, 50_000))
        for offset in range(0, 7):
            pts.append((now - offset * DAY, 100_000))
        ratio = yoy_bsr_ratio(_interleave(pts))
        assert ratio is not None
        assert 0.4 < ratio < 0.6

    def test_returns_none_when_no_observations_in_either_window(self):
        # History spans a year but no points inside either ±half-week window.
        now = _now_keepa_minutes()
        pts = [
            (now - YEAR - 50 * DAY, 50_000),
            (now - 100 * DAY, 60_000),  # not in this-week window
        ]
        # Only 2 points, neither inside this-week or last-year-week.
        # Even though earliest is >365 days, no in-window data → None.
        assert yoy_bsr_ratio(_interleave(pts)) is None

    def test_returns_none_for_empty(self):
        assert yoy_bsr_ratio([]) is None
        assert yoy_bsr_ratio(None) is None
