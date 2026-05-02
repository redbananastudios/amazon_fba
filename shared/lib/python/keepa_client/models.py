"""Pydantic models for Keepa API responses.

We model only the subset of fields the engine actually consumes. Keepa's
`/product` and `/seller` responses include 30+ fields; modelling all of
them would couple this library to upstream changes for no payoff. If a
caller needs a field not modelled here, add it as an `Optional` field on
the relevant class — pydantic ignores extras by default so backwards
compat is automatic.

Field aliases let the JSON-as-shipped (`sellerId`, `asinList`,
`categoryTree`, `tokensConsumed`) map to Pythonic names.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Keepa epoch — minutes since 2011-01-01 UTC. Used to translate Keepa's
# integer-minute timestamps (in `lastSeen`, `csv` arrays, `offerCSV`)
# back into Python datetimes for "is this offer recent?" filtering.
_KEEPA_EPOCH = datetime(2011, 1, 1, tzinfo=timezone.utc)


def _keepa_minutes_to_datetime(minutes: int) -> datetime:
    return _KEEPA_EPOCH + timedelta(minutes=int(minutes))


def _now_keepa_minutes() -> int:
    """Current time as Keepa-epoch minutes (matching `lastSeen` units)."""
    return int((datetime.now(timezone.utc) - _KEEPA_EPOCH).total_seconds() // 60)


# ────────────────────────────────────────────────────────────────────────
# Keepa CSV-index enum positions used by the canonical engine.
#
# Keepa's `stats.current[]` and `stats.avgN[]` are indexed by a documented
# enum (https://keepa.com/#!discuss/t/keepa-time-series-data/116). We pin
# only the indices the engine consumes — adding a new column means adding
# a new constant here AND a new key in `market_snapshot()`.
#
# Prices are integer cents (so 1499 == £14.99 in UK marketplace). -1 is
# the "no current value" sentinel; ``market_snapshot`` converts both to
# None for downstream consumers.
# ────────────────────────────────────────────────────────────────────────

_CSV_AMAZON: int = 0           # Amazon's own offer
_CSV_SALES_RANK: int = 3       # Sales rank
_CSV_NEW_FBA: int = 10         # Lowest 3rd-party FBA
_CSV_COUNT_NEW: int = 11       # New offer count (FBM + FBA combined)
_CSV_RATING: int = 16          # Reviews: rating (stored as int, rating × 10)
_CSV_COUNT_REVIEWS: int = 17   # Reviews: total review count
_CSV_BUY_BOX: int = 18         # Buy Box Shipping (winner price + shipping)


class KeepaOffer(BaseModel):
    """One offer row from Keepa `/product?offers=N`.

    Keepa returns the full historical offer list — many entries are
    long-dormant (`lastSeen` years ago). We surface the raw fields and
    let helpers below filter to genuinely-live offers.

    The `offerCSV` time series is `[time, price, shippingCost, time,
    price, shippingCost, ...]` — last triple gives the most recent
    observed price. Prices are integer pence.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    seller_id: Optional[str] = Field(default=None, alias="sellerId")
    is_fba: bool = Field(default=False, alias="isFBA")
    is_amazon: bool = Field(default=False, alias="isAmazon")
    is_prime: bool = Field(default=False, alias="isPrime")
    # Keepa condition codes — 1 = NEW. We only want NEW for market-price
    # selection; "Used", "Open Box" etc. don't compete with our FBA listing.
    condition: Optional[int] = None
    last_seen: Optional[int] = Field(default=None, alias="lastSeen")
    # Triple-stride array `[t, price, ship, t, price, ship, ...]` — last
    # `price` field (every 3rd starting from index 1) is the most recent
    # observed price in pence. Empty list when Keepa has no offer data.
    offer_csv: list[int] = Field(default_factory=list, alias="offerCSV")

    def is_live(self, *, max_age_minutes: int = 24 * 60 * 7) -> bool:
        """Is this offer fresh enough to count as a live market signal?

        Default window: 7 days. Keepa's offer list includes years of
        dormant entries (sellers who once stocked an ASIN and moved on).
        Filtering to `lastSeen` within the last week keeps us anchored
        to "what could win the Buy Box right now" without throwing away
        legitimate offers from sellers Keepa polled a couple of days
        ago.
        """
        if self.last_seen is None:
            return False
        return self.last_seen >= _now_keepa_minutes() - max_age_minutes

    def current_price(self) -> Optional[float]:
        """Most recent price seen on this offer, in pounds.

        offerCSV stride is 3 (time, price, ship). The price field at
        index `-2` is the last observed sticker price; we ignore the
        shipping field for now since UK FBA listings typically include
        free Prime shipping.
        """
        if not self.offer_csv or len(self.offer_csv) < 2:
            return None
        # Last triple: indices [-3, -2, -1] = [time, price, ship].
        # Some entries lack a shipping field (legacy data); fall back to
        # `[-1]` if the array length isn't a multiple of 3.
        if len(self.offer_csv) % 3 == 0:
            cents = self.offer_csv[-2]
        else:
            cents = self.offer_csv[-1]
        if cents is None or cents < 0:
            return None
        return cents / 100.0


def lowest_live_fba_price(offers: list[KeepaOffer]) -> Optional[float]:
    """Return the lowest price among live, NEW-condition, FBA offers.

    "Live" = lastSeen in the last 7 days (see `KeepaOffer.is_live`).
    Excludes Amazon's own offer — when Amazon competes, the operator
    typically can't win the Buy Box anyway, and we'd rather see what
    the lowest 3rd-party FBA seller is asking. Amazon's price is
    surfaced separately via `_stat_money(stats, _CSV_AMAZON)`.
    """
    candidates: list[float] = []
    for offer in offers:
        if offer.condition != 1:
            continue
        if not offer.is_fba or offer.is_amazon:
            continue
        if not offer.is_live():
            continue
        price = offer.current_price()
        if price is not None and price > 0:
            candidates.append(price)
    return min(candidates) if candidates else None


def count_live_fba_offers(offers: list[KeepaOffer]) -> int:
    """Count live, NEW-condition, 3rd-party FBA offers.

    Same filter as `lowest_live_fba_price` (condition=NEW, is_fba, not
    Amazon, lastSeen within 7 days), but returns the count instead of
    the min price. This is the FBA-only seller count — distinct from
    Keepa's `stats.current[11]` (COUNT_NEW) which sums FBA + FBM.

    Used by `market_snapshot()` to populate the (correctly-named)
    `fba_seller_count` field. Decision rules that key on
    `SINGLE_FBA_SELLER` need real FBA-only count to avoid firing on
    listings with one FBA + several FBM sellers.
    """
    return sum(
        1
        for offer in offers
        if offer.condition == 1
        and offer.is_fba
        and not offer.is_amazon
        and offer.is_live()
    )


def estimate_sales_from_rank_drops(
    rank_csv: list[int] | None, *, window_days: int = 30,
) -> Optional[int]:
    """Estimate sales/month from BSR-drop count in the recent window.

    Heuristic used by SellerAmp / Helium / AMZScout: a sales-rank drop
    (rank value decreases = listing moves UP the chart) is a strong
    proxy for a sale event. Counting drops in the last 30 days
    approximates monthly sales — the standard fallback when Keepa's
    `monthlySold` field isn't populated.

    Args:
        rank_csv: Keepa's csv[3] series — interleaved `[time, rank, time,
            rank, ...]` with rank values as integers (-1 sentinel for
            "no data").
        window_days: lookback window in days; default 30.

    Returns:
        Drop count (rough monthly sales estimate), or None when the
        series is empty / has no data inside the window.
    """
    if not rank_csv or len(rank_csv) < 4:
        return None
    cutoff = _now_keepa_minutes() - window_days * 24 * 60
    drops = 0
    last_rank: Optional[int] = None
    for i in range(0, len(rank_csv) - 1, 2):
        ts, rank = rank_csv[i], rank_csv[i + 1]
        if rank is None or rank < 0:
            continue
        if ts < cutoff:
            last_rank = rank
            continue
        # Inside the window — count rank-improvement events.
        if last_rank is not None and rank < last_rank:
            drops += 1
        last_rank = rank
    return drops if drops > 0 else None


class KeepaStats(BaseModel):
    """Subset of `stats` block from `/product?stats=N`.

    Each list is indexed by the Keepa CSV enum. `current` holds the
    most-recent observation; `avg30` and `avg90` hold rolling averages
    (populated when the request includes ``stats=30`` / ``stats=90``).
    The `avg30` lane is useful as a 30d-vs-90d compare for momentum
    signals — e.g. "Buy Box trending up vs 90d baseline".
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    current: list[int] = Field(default_factory=list)
    avg30: list[int] = Field(default_factory=list)
    avg90: list[int] = Field(default_factory=list)


class KeepaProduct(BaseModel):
    """Subset of Keepa /product response fields actually used by the engine."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    asin: str
    title: Optional[str] = None
    brand: Optional[str] = None
    # Keepa returns ``categoryTree=None`` for some popular ASINs (e.g.
    # Amazon-branded electronics) — coerce to empty list so the model
    # validates instead of rejecting the whole product. Operators still
    # see the (empty) list rather than a missing key.
    category_tree: list[dict[str, Any]] = Field(
        default_factory=list, alias="categoryTree", validate_default=False,
    )

    @field_validator("category_tree", mode="before")
    @classmethod
    def _coerce_none_category_tree(cls, v: Any) -> Any:
        return [] if v is None else v
    # Keepa's `csv` is a list of 30+ parallel time-series. We surface it as
    # opaque list-of-lists; callers that need a specific series index by
    # Keepa's documented enum positions (kept out of this model to avoid
    # coupling to indices that may shift).
    csv: list[Any] = Field(default_factory=list)

    # Stats requires `stats=N` query param on the Keepa request. Missing
    # for ASINs Keepa hasn't profiled yet — `market_snapshot` handles None.
    stats: Optional[KeepaStats] = None
    monthly_sold: Optional[int] = Field(default=None, alias="monthlySold")
    # Live offer list — populated when the Keepa request includes
    # `offers=N`. Empty when not requested. Each entry is a KeepaOffer
    # carrying the seller's most-recent price, FBA flag, condition, and
    # `lastSeen` for liveness filtering. Critical for the lowest-FBA
    # market-price path: Keepa's `stats.current[10]` (NEW_FBA) returns
    # -1 sentinels for many ASINs even when active FBA offers exist —
    # the offers list is the authoritative source.
    offers: list[KeepaOffer] = Field(default_factory=list)
    # Variation grouping. ASINs sharing a parent are size / colour
    # variants of the same product — knowing this lets the operator
    # spot whether they're competing across a variation cluster.
    parent_asin: Optional[str] = Field(default=None, alias="parentAsin")
    # Physical dimensions Keepa surfaces for FBA fee + shipping math.
    # Weight is grams; height/length/width are millimetres. All
    # optional — Keepa returns None for ASINs without dimensions on
    # file. `market_snapshot` derives `package_volume_cm3` (mm³ → cm³).
    package_weight: Optional[int] = Field(default=None, alias="packageWeight")
    package_height: Optional[int] = Field(default=None, alias="packageHeight")
    package_length: Optional[int] = Field(default=None, alias="packageLength")
    package_width: Optional[int] = Field(default=None, alias="packageWidth")
    # Keepa-epoch minutes when Keepa first started tracking this ASIN.
    # Used by `history.listing_age_days` to derive the LISTING_TOO_NEW
    # signal — new listings carry meaningful risk because they're still
    # finding their market price. None when Keepa hasn't recorded a
    # tracking-start timestamp.
    tracking_since: Optional[int] = Field(default=None, alias="trackingSince")

    def market_snapshot(self) -> dict[str, Any]:
        """Extract the canonical engine's market-data columns from `stats`.

        Returns a dict shaped to match `sourcing_engine.pipeline.match._build_match`
        + the columns `calculate.calculate_economics` consumes directly.
        Values are in pounds (cents/100), with -1 sentinels and
        missing-stats both coerced to None so downstream code can rely
        on the ``v is None or v <= 0`` pattern.

        Naming aligns with what `calculate` reads, NOT the legacy
        Keepa-CSV-export column names. The legacy `load_market_data`
        path emits ``monthly_sales_estimate``; `_build_match` then
        renames it to ``sales_estimate`` for `calculate`. Here we skip
        the intermediate name and emit ``sales_estimate`` directly so
        ``keepa_enrich → calculate`` chains without a rename hop.

        Returns market-data columns only — descriptive fields like title
        and brand belong to the discovery step (oa_csv, seller_storefront)
        and shouldn't be silently overwritten by enrichment. Callers
        that need title/brand read them directly off this object.
        """
        # New-FBA price selection: prefer the lowest LIVE FBA offer from
        # the offers list (real-market signal — what's actually available
        # to buyers right now). Fall back to Keepa's `stats.current[10]`
        # only when offers data wasn't requested or all live offers are
        # filtered out. Real-world: B0B636ZKZQ has -1 in stats.current[10]
        # but the offers list shows £16.90 from a fresh FBA seller — the
        # stats path alone would miss the actual market price.
        new_fba_from_offers = lowest_live_fba_price(self.offers or [])
        new_fba_from_stats = _stat_money(self.stats, _CSV_NEW_FBA)
        new_fba_price = (
            new_fba_from_offers if new_fba_from_offers is not None
            else new_fba_from_stats
        )

        # Sales estimate: prefer Keepa's `monthly_sold` (their own
        # estimator); fall back to counting BSR-drops in csv[3] when
        # monthly_sold is missing — the standard heuristic SellerAmp /
        # Helium use. Without this, ASINs Keepa hasn't profiled show
        # `sales_estimate=None` even though the rank-drop history
        # tells a clear story.
        sales_estimate = self.monthly_sold
        if sales_estimate is None:
            csv = self.csv or []
            rank_csv = csv[_CSV_SALES_RANK] if len(csv) > _CSV_SALES_RANK else None
            sales_estimate = estimate_sales_from_rank_drops(rank_csv)

        # FBA seller count: prefer real FBA-only count from the offers
        # list. Keepa's stats.current[11] (COUNT_NEW) is the total new
        # offer count (FBM + FBA combined) — using it as
        # `fba_seller_count` over-counts for listings with FBM sellers
        # and made every SINGLE_FBA_SELLER / dynamic-seller-ceiling
        # decision rule wrong by an unknown amount. Fall back to
        # COUNT_NEW only when offers data wasn't requested
        # (`with_offers=False`); precision is degraded in that case but
        # the historical behaviour is preserved for bulk paths that
        # don't pay the +4-tokens-per-ASIN cost of fetching offers.
        offers_list = self.offers or []
        if offers_list:
            fba_seller_count: Optional[int] = count_live_fba_offers(offers_list)
        else:
            fba_seller_count = _stat_int(self.stats, _CSV_COUNT_NEW)

        # Rating and review_count come from the csv time-series (not
        # stats.current). Keepa stores rating as integer × 10
        # (e.g. 45 → 4.5).
        csv = self.csv or []
        rating_raw = (
            _csv_last_value(csv[_CSV_RATING])
            if len(csv) > _CSV_RATING else None
        )
        rating = rating_raw / 10.0 if rating_raw is not None else None
        review_count = (
            _csv_last_value(csv[_CSV_COUNT_REVIEWS])
            if len(csv) > _CSV_COUNT_REVIEWS else None
        )

        # Package volume in cm³, derived from H × L × W (mm). Keepa
        # returns None for ASINs without dimensions on file. mm³ → cm³
        # divides by 1000.
        package_volume_cm3: Optional[int] = None
        if (
            self.package_height is not None
            and self.package_length is not None
            and self.package_width is not None
            and self.package_height > 0
            and self.package_length > 0
            and self.package_width > 0
        ):
            package_volume_cm3 = (
                self.package_height * self.package_length * self.package_width
            ) // 1000

        # categoryTree is root → leaf. The first element is the
        # marketplace root (e.g. "Toys & Games" on amazon.co.uk). When
        # categoryTree is empty (popular ASINs sometimes return null),
        # category_root falls through to None.
        category_root: Optional[str] = None
        if self.category_tree:
            name = self.category_tree[0].get("name")
            if isinstance(name, str) and name:
                category_root = name

        # History-derived signals. These read directly off the csv
        # arrays via the helpers in `keepa_client.history`. All
        # optional, all None when input data is insufficient — the
        # candidate-score step in WS3 reads None as "signal missing"
        # and adjusts data confidence accordingly.
        from .history import (
            amazon_bb_share_pct,
            bsr_slope,
            listing_age_days,
            offer_count_trend,
            out_of_stock_pct,
            price_volatility,
            review_count_change,
            yoy_bsr_ratio,
        )

        rank_csv = csv[_CSV_SALES_RANK] if len(csv) > _CSV_SALES_RANK else None
        bb_csv = csv[_CSV_BUY_BOX] if len(csv) > _CSV_BUY_BOX else None
        # COUNT_NEW (idx 11) is the closest FBA-offer-count proxy
        # available via the csv path. Per PR #52 we know this is
        # FBM + FBA combined — but for *trend* detection (joiners
        # over time) the combined count is the right signal: a new
        # FBM seller landing on the listing is also competition the
        # operator should know about.
        count_csv = csv[_CSV_COUNT_NEW] if len(csv) > _CSV_COUNT_NEW else None

        offer_trend = offer_count_trend(count_csv, window_days=90)
        offer_count_start = (
            offer_trend.get("start") if offer_trend is not None else None
        )
        offer_count_joiners = (
            offer_trend.get("joiners_90d") if offer_trend is not None else None
        )

        return {
            "asin": self.asin,
            "amazon_price": _stat_money(self.stats, _CSV_AMAZON),
            "new_fba_price": new_fba_price,
            "buy_box_price": _stat_money(self.stats, _CSV_BUY_BOX),
            "buy_box_avg30": _stat_money(self.stats, _CSV_BUY_BOX, lane="avg30"),
            "buy_box_avg90": _stat_money(self.stats, _CSV_BUY_BOX, lane="avg90"),
            "fba_seller_count": fba_seller_count,
            # Total new-offer count (FBM + FBA combined) from
            # stats.current[11]. Some calculators legitimately want the
            # total — e.g. peak-buying detection looking at competition
            # density independent of fulfilment channel.
            "total_offer_count": _stat_int(self.stats, _CSV_COUNT_NEW),
            "sales_rank": _stat_int(self.stats, _CSV_SALES_RANK),
            "sales_rank_avg90": _stat_int(self.stats, _CSV_SALES_RANK, lane="avg90"),
            "sales_estimate": _coerce_positive_int(sales_estimate),
            "rating": rating,
            "review_count": review_count,
            "parent_asin": self.parent_asin,
            # Grams. Keepa's package_weight is None for ASINs without
            # dimensions on file.
            "package_weight_g": self.package_weight,
            "package_volume_cm3": package_volume_cm3,
            "category_root": category_root,
            # History-derived signals (added in PR 4).
            "bsr_slope_30d": bsr_slope(rank_csv, window_days=30),
            "bsr_slope_90d": bsr_slope(rank_csv, window_days=90),
            "bsr_slope_365d": bsr_slope(rank_csv, window_days=365),
            "fba_offer_count_90d_start": offer_count_start,
            "fba_offer_count_90d_joiners": offer_count_joiners,
            "buy_box_oos_pct_90": out_of_stock_pct(bb_csv, window_days=90),
            "price_volatility_90d": price_volatility(bb_csv, window_days=90),
            "listing_age_days": listing_age_days(self.tracking_since),
            "yoy_bsr_ratio": yoy_bsr_ratio(rank_csv),
            # Review velocity (PR 5) — net change in review_count over
            # the 90d window. Drives the candidate_score Demand
            # dimension's review-velocity sub-score.
            "review_velocity_90d": review_count_change(
                csv[_CSV_COUNT_REVIEWS] if len(csv) > _CSV_COUNT_REVIEWS else None,
                window_days=90,
            ),
            # Amazon Buy-Box share % over 90d. Derived from csv[18]
            # (BB price) vs csv[0] (Amazon price) — closes the gap
            # against the Browser CSV path which carries this as the
            # precomputed "Buy Box: % Amazon 90 days" column. Without
            # this, single_asin runs against niche listings landed at
            # LOW data_confidence even when every other signal was
            # healthy.
            "amazon_bb_pct_90": amazon_bb_share_pct(
                bb_csv,
                csv[_CSV_AMAZON] if len(csv) > _CSV_AMAZON else None,
                window_days=90,
            ),
        }


def _stat_money(
    stats: Optional[KeepaStats],
    idx: int,
    *,
    avg: bool = False,
    lane: str = "current",
) -> Optional[float]:
    """Pull a money cell out of one of stats' lanes.

    By default reads ``stats.current[idx]``. Pass ``avg=True`` for the
    legacy 90-day shortcut (kept for back-compat) or ``lane="avg30"`` /
    ``lane="avg90"`` to be explicit. Returns pounds (cents/100). Missing
    stats / out-of-range index / -1 sentinel → None.
    """
    if stats is None:
        return None
    if avg:
        arr = stats.avg90
    elif lane == "avg30":
        arr = stats.avg30
    elif lane == "avg90":
        arr = stats.avg90
    else:
        arr = stats.current
    if idx >= len(arr):
        return None
    cents = arr[idx]
    if cents is None or cents < 0:
        return None
    return cents / 100.0


def _stat_int(
    stats: Optional[KeepaStats], idx: int, *, lane: str = "current",
) -> Optional[int]:
    """Pull an integer cell (rank, count) out of stats' chosen lane."""
    if stats is None:
        return None
    if lane == "avg30":
        arr = stats.avg30
    elif lane == "avg90":
        arr = stats.avg90
    else:
        arr = stats.current
    if idx >= len(arr):
        return None
    val = arr[idx]
    if val is None or val < 0:
        return None
    return int(val)


def _coerce_positive_int(val: Any) -> Optional[int]:
    """Coerce a value to a positive int; None / -1 / non-int → None."""
    if val is None:
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _csv_last_value(series: Any) -> Optional[int]:
    """Return the last non-sentinel value in a Keepa csv time-series.

    Keepa's `csv[N]` arrays are interleaved `[time, value, time, value, ...]`
    — the value sits at every odd index. -1 means "no observation".
    This helper walks the value-positions right-to-left and returns
    the most recent real (non-(-1), non-None) observation, or None
    when the series is empty / all sentinels.

    Real Keepa exports are always even-length, but odd-length arrays
    can creep in from manual fixtures or truncated transports — so we
    explicitly start from the largest *value* index (odd) rather than
    `len-1` to avoid treating a stray timestamp as a value.

    Used by `market_snapshot()` for `rating` (csv[16]) and
    `review_count` (csv[17]) which Keepa doesn't expose via stats.
    """
    if not isinstance(series, list) or len(series) < 2:
        return None
    # Largest value index — odd. For a length-N array:
    #   N even → start = N-1 (which is odd)
    #   N odd  → start = N-2 (drop trailing dangling timestamp)
    start = len(series) - 1 if len(series) % 2 == 0 else len(series) - 2
    for i in range(start, 0, -2):
        v = series[i]
        if v is None:
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n < 0:
            continue
        return n
    return None


class KeepaSeller(BaseModel):
    """Subset of Keepa /seller response fields used by store-stalking."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    seller_id: str = Field(alias="sellerId")
    seller_name: Optional[str] = Field(default=None, alias="sellerName")
    asin_list: list[str] = Field(default_factory=list, alias="asinList")
