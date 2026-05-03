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
    with_offers: bool = False,
) -> pd.DataFrame:
    """Re-enrich non-REJECT rows with live Keepa data.

    Args:
        df: full DataFrame from upstream steps. Must already carry
            a ``decision`` column (set by `decide`).
        asin_col / decision_col: column name overrides.
        client: pre-built ``KeepaClient`` (test injection).
        config_path: override ``shared/config/keepa_client.yaml``.
        with_offers: include the live offer list (default False).
            Defaulting False keeps per-product cost low (~3 tokens
            with stats=90 instead of ~7) so a typical 5-50 survivor
            batch fits in the 100-token bucket without chunking. The
            current Buy Box and 90d-aggregated stats — which is what
            the analyst layer actually reads — come through fine
            without offers. Operators wanting the live offer table
            (per-seller share, FBA-flag-per-seller) should run
            `single_asin` for that ASIN.

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

    try:
        enriched = enrich_with_keepa(
            survivors,
            asin_col=asin_col,
            client=client,
            config_path=config_path,
            overwrite=True,
            with_offers=with_offers,
        )
    except Exception:
        # Best-effort fallback. Live Keepa can fail for many reasons
        # (missing API key, network, rate-limit overflow, transient
        # Keepa-side error) and a rejected survivor refresh shouldn't
        # take down the whole strategy run — operators can still act
        # on the stale-but-usable data, with the divergence-from-
        # single_asin caveat documented in the buyer report.
        # Same fallback whether the step runs via legacy run_pipeline
        # or via the YAML strategy runner (asymmetry surfaced in
        # PR #82 code review).
        logger.warning(
            "keepa_enrich_survivors: live Keepa call failed; "
            "returning input unchanged — verdicts may diverge from "
            "single_asin against this run's data",
            exc_info=True,
        )
        return df

    out = df.copy()
    # Merge live values into the survivor rows. Strategy:
    #   - If the live value is non-null, overwrite the stale value.
    #   - If the live value IS null (sparse Keepa response), keep the
    #     stale value rather than nullifying. This matters for
    #     buy_box_price / new_fba_price / amazon_price — sparse-history
    #     ASINs return -1 sentinels that market_snapshot maps to None,
    #     and overwriting with None would force `_pick_market_price`
    #     to fall through to "No valid market price" REJECT, killing
    #     every survivor that has any sparse signal.
    #
    # Coerce target column to object dtype before assignment to dodge
    # the float64/int64 + None TypeError (PR #82 reviewer issue 2).
    survivor_idx = out.index[survivor_mask]
    for col in KEEPA_ENRICH_COLUMNS:
        if col not in out.columns:
            out[col] = pd.Series([None] * len(out), index=out.index, dtype=object)
        elif out[col].dtype != object:
            out[col] = out[col].astype(object)
        if col not in enriched.columns:
            continue
        # enriched.index aligns with survivors.index (preserved through
        # enrich_with_keepa). Walk pairwise so we can keep stale where
        # live is None.
        live_values = enriched[col]
        for full_idx, live_idx in zip(survivor_idx, enriched.index, strict=True):
            live = live_values.loc[live_idx]
            if live is None or pd.isna(live):
                continue
            out.at[full_idx, col] = live
    return out


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper.

    Recognised config keys:
      - ``asin_col`` (default ``"asin"``)
      - ``decision_col`` (default ``"decision"``)
      - ``with_offers`` (default ``False``) — include live offers (+4
        tokens/ASIN). Default off keeps a typical 5-50 survivor batch
        within the 100-token bucket without chunking.
      - ``client``: pre-built KeepaClient (test injection)
      - ``config_path``: override default ``keepa_client.yaml`` path
    """
    raw_with_offers = config.get("with_offers", False)
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


# `decide` step's verdict universe. Anything else flowing through this
# predicate is a contract violation worth logging — but not raising,
# since coarse-edges contracts shouldn't take down operator runs.
_KNOWN_DECISIONS: frozenset[str] = frozenset({"SHORTLIST", "REVIEW", "REJECT"})


def _is_reject(v: Any) -> bool:
    """REJECT-row predicate. NaN-aware; non-string values count as
    'not rejected' (the row is a survivor by default if no decision
    is set)."""
    if is_missing(v):
        return False
    if not isinstance(v, str):
        return False
    norm = v.strip().upper()
    if norm and norm not in _KNOWN_DECISIONS:
        logger.warning(
            "keepa_enrich_survivors: unexpected decision value %r "
            "(treating as survivor)", v,
        )
    return norm == "REJECT"
