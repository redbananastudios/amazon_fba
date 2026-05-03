"""Merge SP-API live pricing into canonical engine columns.

Stage 05.55 — sits between the SP-API preflight (`enrich`) and the
Keepa survivor refresh in `supplier_pricelist`. The preflight step
already fetches live Buy Box price and offer counts via SP-API
`getItemOffersBatch`, but writes them to dedicated `live_*` columns
that the engine's `calculate` step doesn't read. This step bridges
the gap: maps the live values into the canonical columns
(`buy_box_price`, `fba_seller_count`, `amazon_status`) so the
second-pass `calculate(recalculate=True)` reads them.

Why "live wins" not "live as fallback":

  - Keepa Buy Box (csv[18]) is whatever was last tracked. For niche
    listings that's "never". For active listings it can still be 30
    days stale on browser-CSV exports. SP-API getItemOffers returns
    *now* — Amazon's authoritative real-time view.
  - For a buy decision the operator cares about NOW, not March 25.
    Stale Keepa BB walking into the verdict gate is exactly the
    failure mode the survivor refresh + this step exist to close.

REJECT rows are passed through untouched — same invariant as every
other survivor-stage step. We never resurrect rows the engine
already structurally killed at resolve / first-pass calculate.

Live columns the SP-API preflight writes (when `pricing` source is
enabled, which it is by default in `enrich`):

  - ``live_buy_box``         current Buy Box landed price (GBP)
  - ``live_buy_box_seller``  "AMZN" | "FBA" | "FBM"
  - ``live_offer_count_new`` total new offers
  - ``live_offer_count_fba`` FBA-only count

This step maps:

  - ``live_buy_box``         → ``buy_box_price`` (when live present)
  - ``live_offer_count_fba`` → ``fba_seller_count``
  - ``live_buy_box_seller == "AMZN"`` → ``amazon_status = "ON_LISTING"``;
    "FBA" or "FBM" → ``amazon_status = "OFF_LISTING"`` (only when the
    upstream value was None or "UNKNOWN" — don't override a positive
    Keepa-derived ON_LISTING with a stale-side OFF_LISTING).
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from fba_engine.steps._helpers import is_missing

logger = logging.getLogger(__name__)


def merge_live_pricing(df: pd.DataFrame) -> pd.DataFrame:
    """Map SP-API `live_*` columns into canonical engine columns.

    Mutates a copy. REJECT rows pass through. Rows missing the
    `live_*` columns (e.g. when SP-API preflight failed for that
    ASIN) are no-ops — canonical columns retain their Keepa-derived
    values.
    """
    if df.empty:
        return df

    out = df.copy()

    # Seed canonical columns to object dtype so we can write None /
    # mixed values without pandas raising on float64 / int64 columns.
    for col in ("buy_box_price", "fba_seller_count", "amazon_status"):
        if col in out.columns and out[col].dtype != object:
            out[col] = out[col].astype(object)
        elif col not in out.columns:
            out[col] = pd.Series([None] * len(out), index=out.index, dtype=object)

    n_updated = 0
    for idx, row in out.iterrows():
        decision = row.get("decision")
        if (
            not is_missing(decision)
            and isinstance(decision, str)
            and decision.strip().upper() == "REJECT"
        ):
            continue

        live_bb = row.get("live_buy_box")
        live_seller_class = row.get("live_buy_box_seller")
        live_fba_count = row.get("live_offer_count_fba")

        updated = False

        # buy_box_price — live wins when present.
        if not is_missing(live_bb) and live_bb is not None:
            try:
                live_bb_f = float(live_bb)
            except (TypeError, ValueError):
                live_bb_f = None
            if live_bb_f is not None and live_bb_f > 0:
                out.at[idx, "buy_box_price"] = live_bb_f
                updated = True

        # fba_seller_count — live wins when present.
        if not is_missing(live_fba_count) and live_fba_count is not None:
            try:
                live_fba_int = int(live_fba_count)
            except (TypeError, ValueError):
                live_fba_int = None
            if live_fba_int is not None and live_fba_int >= 0:
                out.at[idx, "fba_seller_count"] = live_fba_int
                updated = True

        # amazon_status — derived from live_buy_box_seller. Only
        # overrides when the existing value is missing / UNKNOWN to
        # avoid clobbering a confident Keepa-derived ON_LISTING with
        # a stale-side OFF_LISTING. SP-API tells us who's currently
        # holding the BB, which is a different question from "has
        # Amazon ever been on this listing".
        existing_amz = row.get("amazon_status")
        if (
            not is_missing(live_seller_class)
            and isinstance(live_seller_class, str)
            and (is_missing(existing_amz) or existing_amz == "UNKNOWN")
        ):
            cls = live_seller_class.strip().upper()
            if cls == "AMZN":
                out.at[idx, "amazon_status"] = "ON_LISTING"
                updated = True
            elif cls in ("FBA", "FBM"):
                out.at[idx, "amazon_status"] = "OFF_LISTING"
                updated = True

        if updated:
            n_updated += 1

    if n_updated:
        logger.info(
            "merge_live_pricing: updated canonical columns on %d rows from SP-API live data",
            n_updated,
        )
    return out


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper. No config keys consumed."""
    return merge_live_pricing(df)
