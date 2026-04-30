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

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


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
_CSV_COUNT_NEW: int = 11       # New offer count (proxy for FBA seller count)
_CSV_BUY_BOX: int = 18         # Buy Box Shipping (winner price + shipping)


class KeepaStats(BaseModel):
    """Subset of `stats` block from `/product?stats=N`.

    Each list is indexed by the Keepa CSV enum. `current` holds the
    most-recent observation; `avg90` holds the rolling 90-day average
    (set when the request includes ``stats=90``).
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    current: list[int] = Field(default_factory=list)
    avg90: list[int] = Field(default_factory=list)


class KeepaProduct(BaseModel):
    """Subset of Keepa /product response fields actually used by the engine."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    asin: str
    title: Optional[str] = None
    brand: Optional[str] = None
    category_tree: list[dict[str, Any]] = Field(
        default_factory=list, alias="categoryTree"
    )
    # Keepa's `csv` is a list of 30+ parallel time-series. We surface it as
    # opaque list-of-lists; callers that need a specific series index by
    # Keepa's documented enum positions (kept out of this model to avoid
    # coupling to indices that may shift).
    csv: list[Any] = Field(default_factory=list)

    # Stats requires `stats=N` query param on the Keepa request. Missing
    # for ASINs Keepa hasn't profiled yet — `market_snapshot` handles None.
    stats: Optional[KeepaStats] = None
    monthly_sold: Optional[int] = Field(default=None, alias="monthlySold")

    def market_snapshot(self) -> dict[str, Any]:
        """Extract the canonical engine's market-data columns from `stats`.

        Returns a dict shaped to match `sourcing_engine.pipeline.match._build_match`
        + the columns `calculate.calculate_economics` consumes. Values are
        in pounds (cents/100), with -1 sentinels and missing-stats both
        coerced to None so downstream code can rely on the
        ``v is None or v <= 0`` pattern.

        Returns market-data columns only — descriptive fields like title
        and brand belong to the discovery step (oa_csv, seller_storefront)
        and shouldn't be silently overwritten by enrichment. Callers
        that need title/brand read them directly off this object.
        """
        return {
            "asin": self.asin,
            "amazon_price": _stat_money(self.stats, _CSV_AMAZON),
            "new_fba_price": _stat_money(self.stats, _CSV_NEW_FBA),
            "buy_box_price": _stat_money(self.stats, _CSV_BUY_BOX),
            "buy_box_avg90": _stat_money(self.stats, _CSV_BUY_BOX, avg=True),
            # Note: Keepa doesn't expose FBA-only count via stats; index 11
            # (COUNT_NEW) is the total new-offer count (FBM + FBA combined).
            # We surface it as `fba_seller_count` to match the legacy
            # CSV-export schema (`load_market_data` → "New Offer Count: Current").
            "fba_seller_count": _stat_int(self.stats, _CSV_COUNT_NEW),
            "sales_rank": _stat_int(self.stats, _CSV_SALES_RANK),
            "monthly_sales_estimate": _coerce_positive_int(self.monthly_sold),
        }


def _stat_money(stats: Optional[KeepaStats], idx: int, *, avg: bool = False) -> Optional[float]:
    """Pull a money cell out of stats.current[idx] (or avg90 when ``avg=True``).

    Keepa stores prices as integer cents; we return pounds. -1 / missing
    stats / out-of-range index → None.
    """
    if stats is None:
        return None
    arr = stats.avg90 if avg else stats.current
    if idx >= len(arr):
        return None
    cents = arr[idx]
    if cents is None or cents < 0:
        return None
    return cents / 100.0


def _stat_int(stats: Optional[KeepaStats], idx: int) -> Optional[int]:
    """Pull an integer cell (rank, count) out of stats.current[idx]."""
    if stats is None:
        return None
    if idx >= len(stats.current):
        return None
    val = stats.current[idx]
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


class KeepaSeller(BaseModel):
    """Subset of Keepa /seller response fields used by store-stalking."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    seller_id: str = Field(alias="sellerId")
    seller_name: Optional[str] = Field(default=None, alias="sellerName")
    asin_list: list[str] = Field(default_factory=list, alias="asinList")
