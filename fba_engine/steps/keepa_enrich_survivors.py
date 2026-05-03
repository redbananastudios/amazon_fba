"""Refresh Keepa data on supplier_pricelist survivors only.

The supplier_pricelist chain's market data comes from a static Keepa
Browser CSV (``keepa_combined.csv``) that's typically weeks old by
the time the operator joins it against a fresh supplier pricelist.
That's fine as a coarse filter — most rows REJECT on basic economics
regardless of price-staleness — but for the handful of rows that
SHORTLIST / REVIEW, the analyst layer needs current Buy Box prices
and the per-ASIN history signals (`bsr_slope_*`, `joiners_90d`,
`buy_box_oos_pct_90`, `listing_age_days`) the static CSV doesn't
carry. Without this, the bulk-supplier path's verdicts diverge from
the single_asin path on the same product.

This step bridges the gap: filter to non-REJECT rows, call live Keepa
for that small set, merge fresh `KEEPA_ENRICH_COLUMNS` back into the
full DataFrame. Token cost is bounded — typically 5-50 ASINs × ~7
tokens — well within the 60-token bucket. Downstream `calculate`
re-runs with `recalculate=True` to recompute economics from the
refreshed market price; `decide` re-runs with `force=True` to apply
the updated verdict (a SHORTLIST against stale data may flip to
REJECT once the live BB price erodes the margin).

REJECT rows pass through untouched — we never resurrect rows the
engine already killed at the resolve / first-pass calculate stage.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from fba_engine.steps._helpers import is_missing
from fba_engine.steps.keepa_enrich import (
    KEEPA_ENRICH_COLUMNS,
    enrich_with_keepa,
)

logger = logging.getLogger(__name__)


def refresh_survivors(
    df: pd.DataFrame,
    *,
    asin_col: str = "asin",
    decision_col: str = "decision",
    client: Any = None,
    config_path: Any = None,
    with_offers: bool = True,
) -> pd.DataFrame:
    """Re-enrich non-REJECT rows with live Keepa data.

    Args:
        df: full DataFrame from upstream steps. Must already carry
            a ``decision`` column (set by `decide`).
        asin_col / decision_col: column name overrides.
        client: pre-built ``KeepaClient`` (test injection).
        config_path: override ``shared/config/keepa_client.yaml``.
        with_offers: include the live offer list (default True). +4
            tokens per ASIN but unlocks the lowest-live-FBA market
            price — the difference between "stale BB" and "current BB"
            is exactly what motivates this step.

    Returns:
        Full DataFrame. Survivor rows have ``KEEPA_ENRICH_COLUMNS``
        overwritten with live Keepa data; non-survivors (REJECT)
        retain their original values. Row order preserved.
    """
    if df.empty:
        return df

    if decision_col not in df.columns:
        # No decision column — nothing to filter on. Treat as a no-op
        # rather than raising; this lets the step be safely added to
        # chains that haven't run `decide` yet.
        logger.warning(
            "keepa_enrich_survivors: no '%s' column found; skipping refresh",
            decision_col,
        )
        return df

    survivor_mask = ~df[decision_col].apply(_is_reject)
    if not survivor_mask.any():
        logger.info("keepa_enrich_survivors: no survivors to refresh")
        return df

    survivors = df.loc[survivor_mask].copy()
    n_unique = survivors[asin_col].nunique() if asin_col in survivors.columns else 0
    logger.info(
        "keepa_enrich_survivors: refreshing %d rows (%d unique ASINs)",
        len(survivors), n_unique,
    )

    enriched = enrich_with_keepa(
        survivors,
        asin_col=asin_col,
        client=client,
        config_path=config_path,
        overwrite=True,
        with_offers=with_offers,
    )

    out = df.copy()
    # Write fresh KEEPA_ENRICH_COLUMNS values into the survivor rows.
    # Initialize any missing columns on the full df first so the
    # `.loc` assignment doesn't broadcast into a non-existent column.
    for col in KEEPA_ENRICH_COLUMNS:
        if col not in out.columns:
            out[col] = pd.Series([None] * len(out), index=out.index, dtype=object)
        if col in enriched.columns:
            out.loc[survivor_mask, col] = enriched[col].values
    return out


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper.

    Recognised config keys:
      - ``asin_col`` (default ``"asin"``)
      - ``decision_col`` (default ``"decision"``)
      - ``with_offers`` (default ``True``) — include live offers (+4 tokens/ASIN)
      - ``client``: pre-built KeepaClient (test injection)
      - ``config_path``: override default ``keepa_client.yaml`` path
    """
    raw_with_offers = config.get("with_offers", True)
    if isinstance(raw_with_offers, str):
        with_offers = raw_with_offers.strip().lower() in ("true", "1", "yes")
    else:
        with_offers = bool(raw_with_offers)
    return refresh_survivors(
        df,
        asin_col=config.get("asin_col", "asin"),
        decision_col=config.get("decision_col", "decision"),
        client=config.get("client"),
        config_path=config.get("config_path"),
        with_offers=with_offers,
    )


def _is_reject(v: Any) -> bool:
    """REJECT-row predicate. NaN-aware; non-string values count as
    'not rejected' (the row is a survivor by default if no decision
    is set)."""
    if is_missing(v):
        return False
    if not isinstance(v, str):
        return False
    return v.strip().upper() == "REJECT"
