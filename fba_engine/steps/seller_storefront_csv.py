"""Discovery step: browser-exported Keepa Seller Storefront CSV → canonical schema.

Reads the CSV that Keepa Browser produces from
``Pro → Seller Lookup → <seller> → Storefront tab → Export``. Reuses
the column mapper from :mod:`fba_engine.steps.keepa_finder_csv` because
the export schemas are identical between Keepa's Product Finder and
Seller Storefront pages — same Buy Box / Sales Rank / Monthly Sales
/ Referral Fee column names, same row shape.

Difference from ``keepa_finder_csv``:

* Tags ``source="seller_storefront"`` and
  ``discovery_strategy="seller_storefront_<seller_id>"`` so downstream
  artefacts (CSV / XLSX / Sheet) make the lineage explicit. Operators
  can tell at a glance whether a row came from a category-wide Product
  Finder pull or a single-seller storefront walk.
* Adds a ``seller_id`` column to every row — same value on each, but
  cheap, and lets the operator filter / pivot if multiple
  storefront-walk outputs are concatenated.

The wholesale-flow defaults (``buy_cost=0.0``, ``moq=1``) and the
post-export filters (title keywords, category-root exclusions, ASIN
dedup against ``data/niches/exclusions.csv``) are inherited from the
underlying mapper. Same hazmat / clothing exclusions, same global
exclusions config — keeping one mapper for both pages avoids a second
silent-drift surface.

Pairs with the strategy YAML at
``fba_engine/strategies/seller_storefront_csv.yaml``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from fba_engine.steps import keepa_finder_csv as _kf

logger = logging.getLogger(__name__)


def discover_seller_storefront_csv(
    csv_path: Path | str,
    seller_id: str,
    *,
    recipe: str = "seller_storefront",
    exclusions_path: Path | str | None = None,
    metadata_path: Path | str | None = None,
    config_dir: Path | str | None = None,
) -> pd.DataFrame:
    """Read a Keepa Seller Storefront CSV export and emit canonical rows.

    Args:
        csv_path: path to the Keepa Browser export.
        seller_id: Amazon merchant ID for the storefront (e.g.
            ``"AR5NTANTFUHVI"``). Tags every output row's ``seller_id``
            and ``discovery_strategy`` columns; flows through to
            output filenames + Sheet titles via the strategy YAML.
        recipe: recipe id forwarded to the underlying mapper. Defaults
            to ``"seller_storefront"``; recipe JSON lives at
            ``fba_engine/_legacy_keepa/skills/keepa-product-finder/recipes/``.
        exclusions_path: ASIN dedup list. Inherits the mapper's default.
        metadata_path: optional sidecar JSON.
        config_dir: override for ``shared/config/`` (test injection).

    Returns:
        DataFrame with ``KEEPA_FINDER_CANONICAL_COLUMNS`` plus a
        ``seller_id`` column. Empty DataFrame (with the canonical
        schema) when input is empty or all rows are filtered out.
    """
    if not seller_id:
        raise ValueError("seller_storefront_csv: seller_id is required")

    df = _kf.discover_keepa_finder(
        csv_path=csv_path,
        recipe=recipe,
        exclusions_path=exclusions_path,
        metadata_path=metadata_path,
        config_dir=config_dir,
    )

    # Re-tag — keepa_finder_csv writes source="keepa_finder", but for
    # storefront walking we want a distinct lineage so downstream tools
    # (and the operator reading the CSV) can tell rows apart. Pandas
    # broadcasts a scalar to a zero-row DataFrame fine, so the same
    # three assignments cover both populated and empty cases — no
    # branching needed, no risk of an empty-df result inheriting the
    # wrong `source` tag from the underlying mapper.
    df = df.copy()
    df["source"] = "seller_storefront"
    df["discovery_strategy"] = f"seller_storefront_{seller_id}"
    df["seller_id"] = seller_id
    return df


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible discovery wrapper.

    Discovery steps create the DataFrame; the ``df`` arg is ignored.

    Required ``config`` keys:
      - ``csv_path``: path to the Keepa Seller Storefront CSV export.
      - ``seller_id``: Amazon merchant ID for the storefront.

    Optional ``config`` keys:
      - ``recipe``: recipe id (default ``"seller_storefront"``).
      - ``metadata_path``, ``exclusions_path``, ``config_dir``: as per
        ``keepa_finder_csv.run_step``.
    """
    csv_path = config.get("csv_path")
    seller_id = config.get("seller_id")
    if not csv_path:
        raise ValueError("seller_storefront_csv step requires config['csv_path']")
    if not seller_id:
        raise ValueError("seller_storefront_csv step requires config['seller_id']")

    return discover_seller_storefront_csv(
        csv_path=csv_path,
        seller_id=seller_id,
        recipe=config.get("recipe", "seller_storefront"),
        exclusions_path=config.get("exclusions_path"),
        metadata_path=config.get("metadata_path"),
        config_dir=config.get("config_dir"),
    )
