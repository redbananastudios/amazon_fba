"""Browser-CSV enrichment from a per-ASIN Keepa Browser scrape.

When a row's `asin` matches a cache entry at
``.cache/keepa_browser/<asin>.json``, this step merges the
chart-level signals into the row — overriding the API-derived
versions where the Browser data is more accurate.

Specifically Browser data wins for:
- ``buy_box_seller_stats`` — the per-seller %BB-won breakdown
  with FBA flag attached. Replaces API's ``buyBoxStats`` dict
  (which is keyed by anonymous merchant IDs without seller
  names; Browser carries the human-readable name).
- ``buy_box_min_365d`` — Browser's precomputed 365-day low (more
  accurate than what we'd derive from the API's csv[18] window).
- ``sales_rank_avg_365d`` — same.
- ``bsr_drops_30d`` — Browser's precomputed count.
- ``buy_box_oos_pct_90`` — Browser's precomputed.
- Per-seller ``active_offers`` (current stock + sold-30d) —
  exposed as ``browser_active_offers`` for the operator's
  printer + any future per-seller-aware logic.

When the cache is absent, the step is a silent no-op — every
existing row passes through unchanged. The validator's
fallbacks then kick in (equal-split velocity, API-derived
signals).

For the operator's how-do-I-populate-the-cache workflow, see
``docs/KEEPA_BROWSER_SCRAPE.md``.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from keepa_client.browser_cache import read as read_cache

logger = logging.getLogger(__name__)


# Columns this step adds / overrides on each row. Documented here
# so downstream output writers can opt-in.
BROWSER_ENRICH_COLUMNS: tuple[str, ...] = (
    # Bool — whether the cache was loaded for this row.
    "browser_scrape_present",
    # ISO timestamp of the scrape (when present).
    "browser_scrape_at",
    # Browser-derived per-seller share dict (replaces API
    # buy_box_seller_stats with the human-readable Browser version).
    "buy_box_seller_stats_browser",
    # Per-seller active-offers snapshot from the Browser Offers tab.
    "browser_active_offers",
    # Operator-readable summary of the dominant seller (the seller
    # with the largest %BB-won) — printed in the verdict block.
    "browser_top_seller",
    "browser_top_seller_pct",
    "browser_top_seller_is_fba",
    # Convenience flags for the validator to consume.
    "browser_active_seller_count",
    "browser_active_fba_seller_count",
)


def _share_decimal(pct: float | None) -> float:
    """Convert a Browser raw-percent value to a 0–1 fraction.

    Browser convention: ``pct_won`` is always raw percent
    (0–100). 0.5 means 0.5% (a real value seen in the live
    Keepa data for tail sellers). The previous heuristic
    ``pct/100 if pct > 1 else pct`` was a bug: it treated 0.5%
    as 50%.
    """
    if pct is None:
        return 0.0
    return float(pct) / 100.0


def _merge_browser_into_row(row: dict, scrape) -> dict:
    """Apply Browser-derived fields to a single row dict.

    Mutates and returns the row. Fields the API path already
    populated are ONLY overridden when the Browser version is
    materially better (e.g. precomputed 365d low vs derived;
    seller-share with names vs anonymous IDs).
    """
    pd_block = scrape.product_details

    # Precomputed long-term signals — Browser wins.
    if pd_block.buy_box_lowest_365d is not None:
        row["buy_box_min_365d"] = pd_block.buy_box_lowest_365d
    if pd_block.buy_box_avg_365d is not None:
        row["buy_box_avg365"] = pd_block.buy_box_avg_365d
    if pd_block.buy_box_avg_180d is not None:
        row["buy_box_avg180"] = pd_block.buy_box_avg_180d
    if pd_block.buy_box_avg_30d is not None:
        row["buy_box_avg30"] = pd_block.buy_box_avg_30d
    if pd_block.buy_box_oos_pct_90d is not None:
        row["buy_box_oos_pct_90"] = pd_block.buy_box_oos_pct_90d
    if pd_block.sales_rank_avg_365d is not None:
        row["sales_rank_avg365"] = pd_block.sales_rank_avg_365d
    if pd_block.sales_rank_drops_30d is not None:
        row["bsr_drops_30d"] = pd_block.sales_rank_drops_30d

    # Per-seller breakdown — convert to the validator's expected
    # shape (dict keyed by seller_id with percentageWon / isFBA).
    if scrape.buy_box_seller_stats:
        # Build the dict in API-compatible shape so
        # predict_seller_velocity's existing buy_box_seller_stats
        # logic just works.
        api_shape: dict[str, dict[str, Any]] = {}
        for s in scrape.buy_box_seller_stats:
            # `pct_won` is raw percent (0–100). The validator's
            # predict_seller_velocity divides percentageWon by 100
            # itself, so we pass it through unchanged here.
            api_shape[s.seller_id] = {
                "percentageWon": s.pct_won,
                "avgPrice": s.avg_price,
                "avgNewOfferCount": s.avg_offer_count,
                "isFBA": bool(s.is_fba) if s.is_fba is not None else False,
                "stock": s.stock,
            }
        row["buy_box_seller_stats"] = api_shape
        row["buy_box_seller_stats_browser"] = api_shape   # alias for output

        # Top seller surface for the printer.
        top = max(
            scrape.buy_box_seller_stats,
            key=lambda s: s.pct_won if s.pct_won is not None else 0,
        )
        row["browser_top_seller"] = top.seller_id
        row["browser_top_seller_pct"] = _share_decimal(top.pct_won)
        row["browser_top_seller_is_fba"] = bool(top.is_fba) if top.is_fba is not None else False

    # Active offers — currently-listing sellers.
    if scrape.active_offers:
        row["browser_active_offers"] = [
            o.model_dump(exclude_none=True) for o in scrape.active_offers
        ]
        row["browser_active_seller_count"] = len(scrape.active_offers)
        row["browser_active_fba_seller_count"] = sum(
            1 for o in scrape.active_offers if o.is_fba
        )

    row["browser_scrape_present"] = True
    row["browser_scrape_at"] = scrape.scraped_at
    return row


def add_browser_enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Apply Browser cache to every row in the DataFrame.

    Pure additive — never touches existing fields when the cache
    is missing. Returns a new DataFrame.
    """
    if df.empty:
        out = df.copy()
        for col in BROWSER_ENRICH_COLUMNS:
            out[col] = pd.Series(dtype=object)
        return out

    rows = []
    hit_count = 0
    for _, row in df.iterrows():
        d = row.to_dict()
        asin = str(d.get("asin", "")).upper()
        if not asin:
            d["browser_scrape_present"] = False
            rows.append(d)
            continue
        try:
            entry = read_cache(asin, allow_stale=True)
        except Exception:
            logger.exception(
                "keepa_browser_enrich: failed to read cache for %s", asin,
            )
            entry = None
        if entry is None:
            d["browser_scrape_present"] = False
            rows.append(d)
            continue
        d = _merge_browser_into_row(d, entry.scrape)
        hit_count += 1
        rows.append(d)

    if hit_count > 0:
        logger.info(
            "keepa_browser_enrich: applied to %d/%d rows", hit_count, len(rows),
        )
    return pd.DataFrame(rows)


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper.

    No config keys consumed; cache root is auto-detected from the
    repo root. The step is intentionally permissive — missing
    cache for an ASIN is not an error.
    """
    return add_browser_enrich(df)
