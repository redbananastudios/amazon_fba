"""Seller-storefront discovery step.

Walks an Amazon seller's storefront via Keepa and emits a canonical
DataFrame of ASINs + product metadata. Per
`docs/PRD-sourcing-strategies.md` §6: this is the "wholesale" sourcing
strategy — find what a competing FBA seller is winning, then go after
those products via your own supplier relationships.

Output schema (canonical):
  - asin
  - source         = "seller_storefront"
  - seller_id      = the source seller's Amazon merchant ID
  - seller_name    = display name from Keepa
  - product_name   = Keepa title
  - brand          = Keepa brand (or "" if missing — supplier_leads needs strings)
  - category       = leaf category from Keepa categoryTree
  - amazon_url     = UK marketplace product page

This step does NOT emit a buy_cost — that comes from cross-referencing
against a supplier price list. Downstream usage:
  1. Run this step → leads CSV with ASINs + metadata
  2. Pipe through ``supplier_leads`` to attach supplier-search URLs
  3. Manually find suppliers, drop into a supplier_pricelist run

Standalone CLI:

    python -m fba_engine.steps.seller_storefront \\
        --seller A1B2C3D4E5 \\
        --out fba_engine/data/strategies/seller_storefront/A1B2C3D4E5/discovery.csv
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from fba_engine.steps._helpers import atomic_write, coerce_str
from keepa_client import KeepaClient, KeepaProduct, load_keepa_config

logger = logging.getLogger(__name__)


SELLER_STOREFRONT_DISCOVERY_COLUMNS: tuple[str, ...] = (
    "asin",
    "source",
    "seller_id",
    "seller_name",
    "product_name",
    "brand",
    "category",
    "amazon_url",
)

DEFAULT_KEEPA_CONFIG_PATH: Path = (
    Path(__file__).resolve().parents[2]
    / "shared"
    / "config"
    / "keepa_client.yaml"
)

AMAZON_UK_DP: str = "https://www.amazon.co.uk/dp/"


# ────────────────────────────────────────────────────────────────────────
# Public API.
# ────────────────────────────────────────────────────────────────────────


def discover_seller_storefront(
    seller_id: str,
    *,
    client: KeepaClient | None = None,
    config_path: Path | str | None = None,
) -> pd.DataFrame:
    """Walk a seller's storefront and emit a canonical discovery DataFrame.

    Args:
        seller_id: Amazon merchant ID (e.g. ``"A1B2C3D4E5"``).
        client: pre-built ``KeepaClient``. Tests inject a stub here so
            no real API key / network is required.
        config_path: optional override for the Keepa config YAML. Used
            only when ``client`` is None to build a fresh client.

    Returns:
        DataFrame with ``SELLER_STOREFRONT_DISCOVERY_COLUMNS`` columns.
        Empty DataFrame (with the canonical schema) when the seller has
        no inventory. Partial loss (some ASINs returned by Keepa, some
        not) yields a shorter DataFrame — the caller can detect this
        by comparing length to ``len(seller.asin_list)``.
    """
    if client is None:
        client = _build_client(config_path)

    seller = client.get_seller(seller_id, storefront=True)
    if not seller.asin_list:
        logger.info("seller_storefront: %s has no ASIN inventory", seller_id)
        return _empty_df()

    products = client.get_products(seller.asin_list)

    # Defensive: filter products NOT in the seller's storefront. The
    # batch get_products is supposed to do this already (it filters
    # extras Keepa returns), but a second-layer guard protects the
    # "this is the seller's portfolio" report contract.
    asin_set = set(seller.asin_list)
    rows = [
        _product_to_row(prod, seller.seller_id, seller.seller_name)
        for prod in products
        if prod.asin in asin_set
    ]

    if not rows:
        return _empty_df()
    return pd.DataFrame(rows)[list(SELLER_STOREFRONT_DISCOVERY_COLUMNS)]


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible discovery wrapper.

    Discovery steps don't take an input DataFrame — they CREATE it.
    The ``df`` argument is ignored (mirrors ``oa_csv.run_step``).

    Required ``config`` keys:
      - ``seller_id``: Amazon merchant ID

    Optional ``config`` keys:
      - ``client``: pre-built KeepaClient (test injection)
      - ``config_path``: override the default keepa_client.yaml path
    """
    seller_id = config.get("seller_id")
    if not seller_id:
        raise ValueError(
            "seller_storefront step requires config['seller_id']"
        )
    return discover_seller_storefront(
        seller_id,
        client=config.get("client"),
        config_path=config.get("config_path"),
    )


# ────────────────────────────────────────────────────────────────────────
# Helpers.
# ────────────────────────────────────────────────────────────────────────


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=list(SELLER_STOREFRONT_DISCOVERY_COLUMNS))


def _product_to_row(
    prod: KeepaProduct, seller_id: str, seller_name: str | None,
) -> dict[str, Any]:
    """Map a KeepaProduct into the canonical discovery row schema."""
    # categoryTree is root → leaf. The leaf is the most specific
    # category, which is what supplier-search queries care about.
    category = ""
    if prod.category_tree:
        category = coerce_str(prod.category_tree[-1].get("name"))
    return {
        "asin": prod.asin,
        "source": "seller_storefront",
        "seller_id": seller_id,
        "seller_name": coerce_str(seller_name),
        "product_name": coerce_str(prod.title),
        "brand": coerce_str(prod.brand),
        "category": category,
        "amazon_url": f"{AMAZON_UK_DP}{prod.asin}",
    }


def _build_client(config_path: Path | str | None) -> KeepaClient:
    """Build a KeepaClient from config + the KEEPA_API_KEY env var."""
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
            "Discover ASINs from an Amazon seller's storefront via Keepa "
            "and emit a canonical leads DataFrame."
        )
    )
    parser.add_argument("--seller", required=True, dest="seller_id")
    parser.add_argument(
        "--config", default=None,
        help="Path to keepa_client.yaml (default: shared/config/keepa_client.yaml).",
    )
    parser.add_argument("--out", type=Path, help="Output CSV path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    df = discover_seller_storefront(
        seller_id=args.seller_id, config_path=args.config,
    )
    print(
        f"Discovered {len(df)} ASINs from seller storefront {args.seller_id}"
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            args.out, lambda p: df.to_csv(p, index=False, encoding="utf-8-sig"),
        )
        print(f"Saved: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
