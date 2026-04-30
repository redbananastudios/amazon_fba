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
  - buy_box_avg90         (90-day Buy Box average)
  - fba_seller_count      (current new offer count, proxy for FBA seller count)
  - sales_rank
  - sales_estimate        (Keepa "Bought in past month" — what calculate reads)

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
    "buy_box_avg90",
    "fba_seller_count",
    "sales_rank",
    "sales_estimate",
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
    products = client.get_products(asins_unique)
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
    """
    return enrich_with_keepa(
        df,
        asin_col=config.get("asin_col", "asin"),
        client=config.get("client"),
        config_path=config.get("config_path"),
        overwrite=bool(config.get("overwrite", False)),
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
