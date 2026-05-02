"""Time-series helpers for Keepa csv arrays.

Most of the value in `KeepaProduct.csv` is unused by the engine today.
This module distils that history into the signals the candidate-score
step (WS3) needs: BSR slope, offer-count trend, out-of-stock %, Buy
Box winner churn, price volatility, listing age, year-over-year rank
ratio.

Conventions every function follows:

- Inputs are Keepa csv-style interleaved arrays `[t, v, t, v, ...]`
  where `t` is minutes-since-Keepa-epoch and `v` is the integer
  observation (price in cents, rank, count, seller-id-as-int, etc.).
  -1 means "no observation".
- All windowed reads use `now` from `_now_keepa_minutes()` and
  filter to observations inside the last `window_days * 24 * 60`
  minutes.
- Insufficient-data returns `None`, never `0` or a degenerate value.
  Downstream scoring treats `None` as "signal missing" and adjusts
  data-confidence accordingly. Returning a hard `0` would silently
  feed garbage into the score.
- Indices reference Keepa's documented CSV enum at
  https://keepa.com/#!discuss/t/keepa-time-series-data/116. Constants
  used by the engine live in `keepa_client.models`.

Buy Box winner seller IDs:
  Keepa's csv array doesn't carry a stable buy-box-winner seller
  index across all marketplaces. The seller-id history lives on the
  product response under `buyBoxSellerIdHistory` (a separate
  interleaved `[t, sellerId, ...]` array). `buy_box_winner_flips`
  expects that array, not a csv index — pass it through directly
  from the Keepa product response. If the field is None / empty,
  the helper returns None and the caller treats the signal as
  missing.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

from .models import _keepa_minutes_to_datetime, _now_keepa_minutes


# ────────────────────────────────────────────────────────────────────────
# Parsing helpers.
# ────────────────────────────────────────────────────────────────────────


def parse_keepa_csv_series(
    series: Any,
) -> list[tuple[datetime, Optional[int]]]:
    """Convert Keepa's `[t, v, t, v, ...]` array to typed `(datetime, value)` pairs.

    -1 sentinels become `None`. Timestamps are converted from
    Keepa-epoch minutes via `_keepa_minutes_to_datetime`. Trailing
    dangling timestamps (odd-length input) are dropped silently.

    Returns an empty list when input is None / not a list / shorter
    than two elements.
    """
    if not isinstance(series, list) or len(series) < 2:
        return []
    pairs: list[tuple[datetime, Optional[int]]] = []
    # Even-length stride only — drop a dangling trailing timestamp
    # so we never index a value that doesn't exist.
    n = len(series) - (len(series) % 2)
    for i in range(0, n, 2):
        t_raw = series[i]
        v_raw = series[i + 1]
        if t_raw is None:
            continue
        try:
            t_min = int(t_raw)
        except (TypeError, ValueError):
            continue
        try:
            v: Optional[int] = int(v_raw) if v_raw is not None else None
        except (TypeError, ValueError):
            v = None
        if v is not None and v < 0:
            v = None
        pairs.append((_keepa_minutes_to_datetime(t_min), v))
    return pairs


def _window_pairs_with_sentinels(
    series: Any, *, window_days: int, now_minutes: Optional[int] = None,
) -> list[tuple[int, Optional[int]]]:
    """Filter raw csv series to (minutes, value) pairs inside the window.

    Returns minutes (int) rather than datetimes — least-squares math
    in `bsr_slope` is cleaner on integers than on `datetime` deltas.

    **Pairs with `value=None` (sentinels) are KEPT** — the function
    name is explicit because most callers (`bsr_slope`,
    `price_volatility`, `offer_count_trend`) need to filter those
    out, while `out_of_stock_pct` needs to *count* them as the OOS
    proxy. Mixing the two contracts in a "filtered" helper would
    silently wreck OOS%; mixing them in an "unfiltered" helper would
    silently wreck the slope/volatility maths. Explicit name forces
    the caller to acknowledge which side of the contract they're on.
    """
    if not isinstance(series, list) or len(series) < 2:
        return []
    if now_minutes is None:
        now_minutes = _now_keepa_minutes()
    cutoff = now_minutes - window_days * 24 * 60
    out: list[tuple[int, Optional[int]]] = []
    n = len(series) - (len(series) % 2)
    for i in range(0, n, 2):
        t_raw = series[i]
        v_raw = series[i + 1]
        try:
            t_min = int(t_raw)
        except (TypeError, ValueError):
            continue
        if t_min < cutoff:
            continue
        try:
            v: Optional[int] = int(v_raw) if v_raw is not None else None
        except (TypeError, ValueError):
            v = None
        if v is not None and v < 0:
            v = None
        out.append((t_min, v))
    return out


# ────────────────────────────────────────────────────────────────────────
# Trend signals.
# ────────────────────────────────────────────────────────────────────────


# Minimum points for least-squares slope and OOS%. Below this the
# signal is too noisy to trust; helpers return None and the candidate
# score treats it as missing rather than zero.
_MIN_POINTS_SLOPE = 5
_MIN_POINTS_OOS = 5


def bsr_slope(rank_csv: Any, *, window_days: int) -> Optional[float]:
    """Least-squares slope of (rank vs time) over the window, normalised.

    Negative slope = rank improving (going down = ranking up the
    chart). Positive slope = rank declining. The slope is normalised
    by the mean rank in the window so the value is comparable across
    listings with very different rank magnitudes (a 100-rank movement
    on a niche product is meaningful; on a top-100 product it's noise).

    Returns None when:
      - input is empty / not a list
      - fewer than `_MIN_POINTS_SLOPE` real (non-(-1)) points in window
      - all points share the same timestamp (would divide by zero)
      - mean rank is zero (would divide by zero in normalisation —
        shouldn't happen for real ranks, defensive)
    """
    pairs = _window_pairs_with_sentinels(rank_csv, window_days=window_days)
    valid = [(t, v) for t, v in pairs if v is not None and v > 0]
    if len(valid) < _MIN_POINTS_SLOPE:
        return None
    n = len(valid)
    sum_t = sum(t for t, _ in valid)
    sum_v = sum(v for _, v in valid)
    mean_t = sum_t / n
    mean_v = sum_v / n
    if mean_v == 0:
        return None
    num = sum((t - mean_t) * (v - mean_v) for t, v in valid)
    den = sum((t - mean_t) ** 2 for t, _ in valid)
    if den == 0:
        return None
    raw_slope = num / den
    # Normalise: divide by mean to make magnitudes comparable across
    # listings. Multiply back by minutes-per-day so the slope is
    # "fraction-of-mean per day", which scores cleanly into config.
    return (raw_slope / mean_v) * (24 * 60)


def offer_count_trend(
    count_csv: Any, *, window_days: int = 90,
) -> Optional[dict[str, Optional[int]]]:
    """Summarise the new-offer-count series in the window.

    Returns a dict:
      - start         first observed count in window
      - end           last observed count in window (also = current)
      - peak          max observed count in window
      - joiners_90d   max(end - start, 0)  — net new sellers entering
      - current       last observed count (alias for `end`, kept
                      separate so callers don't have to know the
                      semantic equivalence)

    `joiners_90d` is the most useful single early-warning for price
    erosion: even three new sellers landing on a previously sleepy
    listing reliably foreshadows downward Buy Box pressure.

    Returns None when the window contains zero real observations —
    callers treat that as "signal missing".
    """
    pairs = _window_pairs_with_sentinels(count_csv, window_days=window_days)
    valid = [(t, v) for t, v in pairs if v is not None]
    if not valid:
        return None
    valid_sorted = sorted(valid, key=lambda p: p[0])
    start = valid_sorted[0][1]
    end = valid_sorted[-1][1]
    peak = max(v for _, v in valid_sorted)
    joiners = max(end - start, 0) if (start is not None and end is not None) else None
    return {
        "start": start,
        "end": end,
        "peak": peak,
        "joiners_90d": joiners,
        "current": end,
    }


def out_of_stock_pct(
    buy_box_csv: Any, *, window_days: int = 90,
) -> Optional[float]:
    """Fraction of observations in the window where Buy Box was missing.

    Keepa records the absence of a Buy Box as a -1 sentinel — the
    price slot is "no Buy Box right now". Counting those as a share
    of total observations approximates the time-weighted out-of-stock
    rate for the window.

    Returns a float in `[0.0, 1.0]`, or None when fewer than
    `_MIN_POINTS_OOS` observations exist in window.
    """
    pairs = _window_pairs_with_sentinels(buy_box_csv, window_days=window_days)
    if len(pairs) < _MIN_POINTS_OOS:
        return None
    sentinels = sum(1 for _, v in pairs if v is None)
    return sentinels / len(pairs)


def buy_box_winner_flips(
    buy_box_seller_history: Any, *, window_days: int = 90,
) -> Optional[int]:
    """Count distinct sellers that won the Buy Box in the window.

    Keepa exposes the Buy Box winner timeline as a separate
    `buyBoxSellerIdHistory` field on the product response — an
    interleaved `[t, sellerId, t, sellerId, ...]` array where
    `sellerId` is a string (Amazon merchant ID), `-1` means
    "no Buy Box at this point", `""` is sometimes used for FBM-only
    moments by some marketplaces.

    Reliability varies by marketplace: amazon.co.uk populates it for
    the headline ASINs but Keepa's coverage thins out on niche
    catalogues. When the field is None / empty / has fewer than
    2 entries inside the window, this helper returns None and the
    caller treats the signal as missing.
    """
    if not isinstance(buy_box_seller_history, list) or len(buy_box_seller_history) < 2:
        return None
    now_minutes = _now_keepa_minutes()
    cutoff = now_minutes - window_days * 24 * 60
    sellers: set[str] = set()
    n = len(buy_box_seller_history) - (len(buy_box_seller_history) % 2)
    in_window = 0
    for i in range(0, n, 2):
        t_raw = buy_box_seller_history[i]
        seller = buy_box_seller_history[i + 1]
        try:
            t_min = int(t_raw)
        except (TypeError, ValueError):
            continue
        if t_min < cutoff:
            continue
        in_window += 1
        # -1 / "" / None all mean "no Buy Box winner at this point".
        if seller is None or seller == "" or seller == -1 or seller == "-1":
            continue
        sellers.add(str(seller))
    if in_window < 2:
        return None
    return len(sellers)


def price_volatility(
    buy_box_csv: Any, *, window_days: int = 90,
) -> Optional[float]:
    """Coefficient of variation (stdev / mean) of Buy Box prices in window.

    The classic instability signal. CV is dimensionless so 0.20 means
    "stdev is 20% of the mean" regardless of whether the listing
    sells for £5 or £500 — directly comparable across the catalogue.

    Returns None when fewer than `_MIN_POINTS_OOS` real (non-(-1))
    observations exist or the mean is zero.
    """
    pairs = _window_pairs_with_sentinels(buy_box_csv, window_days=window_days)
    values = [v for _, v in pairs if v is not None and v > 0]
    if len(values) < _MIN_POINTS_OOS:
        return None
    mean = sum(values) / len(values)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in values) / len(values)
    stdev = math.sqrt(var)
    return stdev / mean


# ────────────────────────────────────────────────────────────────────────
# Listing-age signals.
# ────────────────────────────────────────────────────────────────────────


def listing_age_days(
    tracking_since_minutes: Optional[int],
) -> Optional[int]:
    """Days since Keepa first started tracking this ASIN.

    `tracking_since_minutes` comes from the `trackingSince` field on
    the Keepa product response (Keepa-epoch minutes). New listings
    are riskier — operator may be the second seller for a brand-new
    ASIN that's still finding its market price.

    Returns None when the input is None / not parseable. Negative
    deltas (clock skew) coerce to 0.
    """
    if tracking_since_minutes is None:
        return None
    try:
        ts = int(tracking_since_minutes)
    except (TypeError, ValueError):
        return None
    delta_minutes = _now_keepa_minutes() - ts
    if delta_minutes < 0:
        return 0
    return delta_minutes // (24 * 60)


def yoy_bsr_ratio(rank_csv: Any) -> Optional[float]:
    """Mean rank in the same week last year divided by mean rank this week.

    Values >1 mean rank improved (this year's number is smaller — a
    smaller rank is better on Amazon). Values <1 mean rank declined.

    Returns None when the rank history doesn't span at least 365
    days, or when either window has zero real observations, or when
    this-week mean is zero (defensive — unreal for genuine ranks).
    """
    if not isinstance(rank_csv, list) or len(rank_csv) < 2:
        return None
    now_minutes = _now_keepa_minutes()
    week_minutes = 7 * 24 * 60
    year_minutes = 365 * 24 * 60

    # Earliest timestamp must be at least a year ago.
    earliest = None
    n = len(rank_csv) - (len(rank_csv) % 2)
    for i in range(0, n, 2):
        try:
            t = int(rank_csv[i])
        except (TypeError, ValueError):
            continue
        if earliest is None or t < earliest:
            earliest = t
    if earliest is None or now_minutes - earliest < year_minutes:
        return None

    this_week_lo = now_minutes - week_minutes
    last_year_hi = now_minutes - year_minutes + week_minutes // 2
    last_year_lo = now_minutes - year_minutes - week_minutes // 2

    this_week_vals: list[int] = []
    last_year_vals: list[int] = []
    for i in range(0, n, 2):
        try:
            t = int(rank_csv[i])
            v = int(rank_csv[i + 1]) if rank_csv[i + 1] is not None else -1
        except (TypeError, ValueError):
            continue
        if v < 0:
            continue
        if t >= this_week_lo:
            this_week_vals.append(v)
        elif last_year_lo <= t <= last_year_hi:
            last_year_vals.append(v)

    if not this_week_vals or not last_year_vals:
        return None
    this_mean = sum(this_week_vals) / len(this_week_vals)
    last_mean = sum(last_year_vals) / len(last_year_vals)
    if this_mean == 0:
        return None
    return last_mean / this_mean
