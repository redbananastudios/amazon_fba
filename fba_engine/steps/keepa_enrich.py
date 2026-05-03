"""Keepa enrichment step — fetch market data per ASIN.

The missing connector that lets ASIN-only sources (oa_csv,
seller_storefront, future Keepa Finder) chain into the canonical
calculate -> decide pipeline. Takes a DataFrame with an ASIN column,
calls ``KeepaClient.get_products`` once for the deduped set, and
joins the canonical engine's market columns onto each row.

Output columns appended (per `market_snapshot()` in
``shared/lib/python/keepa_client/models.py``):

  - amazon_price          (current Amazon offer)
  - new_fba_price         (lowest 3rd-party FBA)
  - buy_box_price         (current Buy Box winner)
  - buy_box_avg30         (30-day Buy Box average)
  - buy_box_avg90         (90-day Buy Box average)
  - fba_seller_count      (FBA-only count from offers list; falls back
                           to COUNT_NEW when ``with_offers=False``)
  - total_offer_count     (FBM + FBA combined from stats.current[11])
  - sales_rank
  - sales_rank_avg90      (90-day average rank)
  - sales_estimate        (Keepa "Bought in past month" — what calculate reads)
  - rating                (Reviews: rating, e.g. 4.5)
  - review_count          (Reviews: total review count)
  - parent_asin           (variation parent — None when not a variation)
  - package_weight_g      (grams)
  - package_volume_cm3    (derived from packageHeight × Length × Width)
  - category_root         (categoryTree[0].name when present)

Naming aligns with the Keepa CSV-export reader at
`shared/lib/python/sourcing_engine/pipeline/market_data.py` so both
enrichment paths produce the same schema.

Market-data columns only. Descriptive fields (product_name, brand,
category) belong to the discovery step (oa_csv, seller_storefront)
and aren't overwritten here — that lets discovery → keepa_enrich
chain naturally without an explicit ``overwrite`` flag.

ASINs Keepa hasn't tracked or that the batch dropped (stale-on-error,
null filter) get None-filled rows — the input row stays in the output
so the caller can filter downstream by ``buy_box_price.notna()``.

Standalone CLI:

    python -m fba_engine.steps.keepa_enrich \\
        --csv path/to/asin-list.csv \\
        --out path/to/enriched.csv
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from fba_engine.steps._helpers import atomic_write
from keepa_client import KeepaClient, load_keepa_config

logger = logging.getLogger(__name__)


KEEPA_ENRICH_COLUMNS: tuple[str, ...] = (
    "amazon_price",
    "new_fba_price",
    "buy_box_price",
    "buy_box_avg30",
    "buy_box_avg90",
    "fba_seller_count",
    "total_offer_count",
    "sales_rank",
    "sales_rank_avg90",
    "sales_estimate",
    "rating",
    "review_count",
    "parent_asin",
    "package_weight_g",
    "package_volume_cm3",
    "category_root",
    # History-derived signals (added in PR 4 — wire WS2.2 into the
    # canonical enrichment schema). None when input data insufficient.
    "bsr_slope_30d",
    "bsr_slope_90d",
    "bsr_slope_365d",
    "fba_offer_count_90d_start",
    "fba_offer_count_90d_joiners",
    "buy_box_oos_pct_90",
    "price_volatility_90d",
    "listing_age_days",
    "yoy_bsr_ratio",
    # Review velocity (PR 5) — net change in review_count over 90d.
    # Drives the candidate_score Demand dimension.
    "review_velocity_90d",
    # Amazon BB share % over 90d (PR A) — derived from csv[18] vs
    # csv[0] when API path; sourced from "Buy Box: % Amazon 90 days"
    # column on Browser CSV path. Drives competition-safety scoring
    # and candidate_score data-confidence calculation.
    "amazon_bb_pct_90",
    # Long-term BB floor + rank consistency (PR B).
    # buy_box_min_365d: 12-month BB minimum in pounds — peak-buying
    # detection beyond the 90d-avg signal.
    # sales_rank_cv_90d: rank consistency CV over 90d — distinguishes
    # steady sellers from spiky ones at the same average rank.
    "buy_box_min_365d",
    "sales_rank_cv_90d",
    # Variation cluster size (PR C). 1 = standalone; >1 = parent or
    # member of a variant cluster. Surfaces niche-looking parents
    # whose aggregated demand across children may be much higher.
    "variation_count",
    # BSR-drop count over 30 days (PR F). Conservative sales proxy
    # (each rank improvement ≈ 1 sale). sales_estimate uses Keepa's
    # monthlySold which over-estimates niche listings; this field
    # carries the chart-readable count so the validator can fall
    # back to the lower number when they disagree by >50%.
    "bsr_drops_30d",
    # Per-seller BB stats dict from Keepa stats.buyBoxStats (PR G).
    # Mirrors Browser CSV's BB Statistics tab. Used by the share-aware
    # velocity predictor to replace equal-split with real distribution.
    # None when listing has no BB history.
    "buy_box_seller_stats",
    # Recent price-drop magnitude vs 90d average (raw percent — 25.0
    # means current is 25% below avg). Falls back to Amazon series
    # when BB is empty for niche listings. Was previously only
    # populated by the Browser CSV path; API path now has parity.
    "buy_box_drop_pct_90",
    # Which time-series fed the BB-derived signals: "BB" / "AMAZON" /
    # None. Buyer-report metrics layer reads this to label signals
    # "(Amazon-tracked)" suffix when basis is "AMAZON" so the
    # operator knows the source.
    "price_history_basis",
)

DEFAULT_KEEPA_CONFIG_PATH: Path = (
    Path(__file__).resolve().parents[2]
    / "shared"
    / "config"
    / "keepa_client.yaml"
)


# ────────────────────────────────────────────────────────────────────────
# Public API.
# ────────────────────────────────────────────────────────────────────────


def enrich_with_keepa(
    df: pd.DataFrame,
    *,
    asin_col: str = "asin",
    client: KeepaClient | None = None,
    config_path: Path | str | None = None,
    overwrite: bool = False,
    with_offers: bool = False,
) -> pd.DataFrame:
    """Append Keepa market columns to `df`, joined by ASIN.

    Args:
        df: input DataFrame. Must have a column matching ``asin_col``.
        asin_col: name of the ASIN column. Defaults to ``"asin"`` to
            match the canonical discovery schema; pass ``"ASIN"`` for
            CSVs that use the upper-case header.
        client: pre-built ``KeepaClient`` (test injection).
        config_path: override ``shared/config/keepa_client.yaml`` when
            building a fresh client. Ignored if ``client`` is given.
        overwrite: when False (default), raise if any of
            ``KEEPA_ENRICH_COLUMNS`` already exist on `df` — re-enrichment
            could silently mask stale data. Set True if the caller
            explicitly wants fresh data.

    Returns:
        New DataFrame with the original rows + canonical market columns
        appended. Row order preserved. Missing ASINs (filtered nulls,
        stale-fallback misses) get None-filled market columns.
    """
    if df.empty:
        out = df.copy()
        for col in KEEPA_ENRICH_COLUMNS:
            out[col] = pd.Series(dtype=object)
        return out

    if asin_col not in df.columns:
        raise ValueError(
            f"keepa_enrich: input df missing required asin column "
            f"'{asin_col}' (got: {list(df.columns)})"
        )

    if not overwrite:
        clashing = [c for c in KEEPA_ENRICH_COLUMNS if c in df.columns]
        if clashing:
            raise ValueError(
                f"keepa_enrich: columns already present in input df: "
                f"{clashing}. Pass overwrite=True if you want to refresh."
            )

    if client is None:
        client = _build_client(config_path)

    asins_unique = list(dict.fromkeys(df[asin_col].dropna().astype(str)))
    products = client.get_products(asins_unique, with_offers=with_offers)
    by_asin = {p.asin: p.market_snapshot() for p in products}

    # Build the snapshot DataFrame in input row order. `by_asin.get`
    # returns None for missing entries; we fill those with empty dicts
    # so the resulting columns are uniformly None-filled.
    enriched_rows = [
        by_asin.get(str(asin), {}) for asin in df[asin_col]
    ]
    enriched = pd.DataFrame(enriched_rows, index=df.index)

    out = df.copy()
    for col in KEEPA_ENRICH_COLUMNS:
        # `enriched` may not have every column for every row (empty dict
        # for misses) — use `enriched.get` semantics via reindex.
        if col in enriched.columns:
            out[col] = enriched[col]
        else:
            out[col] = None
    return out


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper.

    Recognised config keys:
      - ``asin_col``: name of the ASIN column (default ``"asin"``)
      - ``client``: pre-built KeepaClient (test injection)
      - ``config_path``: override the default keepa_client.yaml path
      - ``overwrite``: re-enrich even when canonical columns are present
      - ``with_offers``: request the live offer list (default False).
        +4 tokens per ASIN but unlocks the lowest-live-FBA market-price
        path. Single-ASIN strategies typically want True; bulk
        storefront walks default False to keep token spend down.
    """
    # YAML interpolation produces strings — accept both bool and the
    # canonical truthy strings ("true", "1") so the strategy YAML can
    # use either style.
    raw_with_offers = config.get("with_offers", False)
    if isinstance(raw_with_offers, str):
        with_offers = raw_with_offers.strip().lower() in ("true", "1", "yes")
    else:
        with_offers = bool(raw_with_offers)
    return enrich_with_keepa(
        df,
        asin_col=config.get("asin_col", "asin"),
        client=config.get("client"),
        config_path=config.get("config_path"),
        overwrite=bool(config.get("overwrite", False)),
        with_offers=with_offers,
    )


# ────────────────────────────────────────────────────────────────────────
# Helpers.
# ────────────────────────────────────────────────────────────────────────


def _build_client(config_path: Path | str | None) -> KeepaClient:
    cfg_path = Path(config_path) if config_path else DEFAULT_KEEPA_CONFIG_PATH
    cfg = load_keepa_config(cfg_path)
    api_key = os.environ.get("KEEPA_API_KEY")
    if not api_key:
        raise RuntimeError(
            "KEEPA_API_KEY env var is not set. "
            "Add it to F:/My Drive/workspace/credentials.env and run "
            "the sync script (see global CLAUDE.md)."
        )
    return KeepaClient(api_key=api_key, config=cfg)


# ────────────────────────────────────────────────────────────────────────
# CLI.
# ────────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Enrich a CSV of ASINs with Keepa market data (price, "
            "sales rank, FBA seller count, monthly sold estimate)."
        )
    )
    parser.add_argument("--csv", required=True, type=Path, help="Input CSV with an asin column.")
    parser.add_argument("--asin-col", default="asin")
    parser.add_argument("--out", type=Path, help="Output CSV.")
    parser.add_argument("--config", type=Path, help="Override keepa_client.yaml path.")
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Re-enrich even if canonical columns already exist on input.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    df = pd.read_csv(args.csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    enriched = enrich_with_keepa(
        df,
        asin_col=args.asin_col,
        config_path=args.config,
        overwrite=args.overwrite,
    )
    print(f"Enriched {len(enriched)} rows from {args.csv}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            args.out,
            lambda p: enriched.to_csv(p, index=False, encoding="utf-8-sig"),
        )
        print(f"Saved: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
