"""Keepa Product Finder CSV discovery step.

Ingests a Keepa Product Finder UI export (175+ columns) and emits a
canonical engine DataFrame ready to chain into
``02_resolve → 03_enrich → 04_calculate → 05_decide → 06_output``.

Sibling of ``oa_csv.py`` and ``seller_storefront.py``; the difference is
that Keepa's UI export is rich enough to populate everything the engine
needs (market_price, sales_estimate, fba_seller_count, BSR, brand,
category, EAN) directly — no live API call required.

Per ``docs/PRD-keepa-sourcing-strategies.md``: this is the bridge between
the manually-driven ``$keepa-product-finder`` skill (Cowork's discovery
task) and the canonical engine (Cowork's engine task). It does not call
Keepa or SP-API itself.

Output columns (canonical schema consumed downstream by 04_calculate):
    Identity / metadata
        asin                    — primary key
        source                  — constant ``"keepa_finder"``
        discovery_strategy      — recipe id (``amazon_oos_wholesale`` etc.)
        product_name            — Keepa "Title"
        brand                   — Keepa "Brand"
        manufacturer            — Keepa "Manufacturer"
        category                — leaf of "Categories: Tree"
        category_root           — Keepa "Categories: Root"
        ean / upc               — Keepa "Product Codes: EAN" / "UPC"
        amazon_url              — built from ASIN

    Prices (calculate.py reads these directly)
        buy_box_price           ← Buy Box: Current
        new_fba_price           ← New, 3rd Party FBA: Current
        buy_box_avg90           ← Buy Box: 90 days avg.

    Velocity / competition (read by calculate.py)
        sales_estimate          ← Bought in past month
        fba_seller_count        ← New FBA Offer Count: Current

    Fee inputs (read by fees.calculate_fees_fba)
        fba_pick_pack_fee       ← FBA Pick&Pack Fee
        referral_fee_pct        ← Referral Fee % (Keepa exports "15 %",
                                  divided by 100 → 0.15)

    Engine flags
        amazon_status           — derived: "ON_LISTING" if Amazon: Current
                                  > 0 else "OFF_LISTING"
        buy_cost                — 0.0 (wholesale flow — engine emits
                                  max_buy_price as the negotiation ceiling)
        moq                     — 1 (Keepa-finder flow is leads, no MOQ)

    Informational + validator signals (PR H — names align with API path)
        sales_rank, sales_rank_avg90, bsr_drops_30d,
        amazon_bb_pct_90, buy_box_oos_pct_90, buy_box_avg30,
        buy_box_min_365d, rating, review_count,
        delta_buy_box_30d_pct, delta_buy_box_90d_pct

Filters applied (in order):
    1. ASIN dedup against ``data/niches/exclusions.csv`` (the global
       "we already rejected this" list — same file used by oa_csv).
    2. Category exclusion via ``shared/config/global_exclusions.yaml``
       — drops any row whose Keepa "Categories: Root" matches one of
       ``categories_excluded`` (case-insensitive, whitespace-trimmed).
    3. Title keyword exclusion — drops any row whose "Title" contains
       any of ``title_keywords_excluded`` (case-insensitive substring).

Hazmat handling is NOT here: the recipe has already set
``isHazMat: No`` in the Keepa filter (when ``hazmat_strict: true``);
post-enrich, ``LEADS_INCLUDE`` returns ``catalog_hazmat`` from SP-API
which the engine flags downstream. Two layers of belt-and-braces.

Standalone CLI:

    python -m fba_engine.steps.keepa_finder_csv \\
        --csv ./output/2026-05-02/keepa_amazon_oos.csv \\
        --recipe amazon_oos_wholesale \\
        --out ./output/2026-05-02/discovery.csv
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

import fba_config_loader
from fba_engine.steps._helpers import atomic_write, coerce_str, is_missing, parse_money

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Constants.
# ────────────────────────────────────────────────────────────────────────

KEEPA_FINDER_CANONICAL_COLUMNS: tuple[str, ...] = (
    # Identity / metadata
    "asin",
    "source",
    "discovery_strategy",
    "product_name",
    "brand",
    "manufacturer",
    "category",
    "category_root",
    "ean",
    "upc",
    # Price columns calculate.py reads directly
    "buy_box_price",            # ← Buy Box: Current
    "new_fba_price",            # ← New, 3rd Party FBA: Current
    "buy_box_avg90",            # informational; calculate has its own conservative-price logic
    # Velocity + competition columns calculate.py reads
    "sales_estimate",           # ← Bought in past month
    "fba_seller_count",         # ← New FBA Offer Count: Current
    # Keepa-supplied fee inputs (used by _fees() — see calculate.py L130-131)
    "fba_pick_pack_fee",        # ← FBA Pick&Pack Fee
    "referral_fee_pct",         # ← Referral Fee % (parsed and divided by 100)
    # Amazon presence flag derived in _row_from_keepa
    "amazon_status",            # "ON_LISTING" / "OFF_LISTING"
    # Wholesale-flow defaults — buy_cost=0 tells the engine to emit
    # max_buy_price (the supplier-negotiation ceiling); moq=1 because
    # Keepa-finder strategies are leads, not pre-negotiated pricelists.
    "buy_cost",
    "moq",
    # Informational + validator signals (PRs A-G — names align with the
    # API-path schema in `keepa_client.models.market_snapshot()` so the
    # validator's flag-firing + scoring logic works on Browser-CSV runs
    # too).
    "sales_rank",                  # ← Sales Rank: Current (was bsr_current)
    "sales_rank_avg90",            # ← Sales Rank: 90 days avg. (was bsr_avg90)
    "amazon_bb_pct_90",            # ← Buy Box: % Amazon 90 days
    "buy_box_oos_pct_90",          # ← Buy Box: 90 days OOS
    "buy_box_avg30",               # ← Buy Box: 30 days avg. (PR B)
    "buy_box_min_365d",            # ← Buy Box: Lowest 365 days (PR B)
    "bsr_drops_30d",               # ← Sales Rank: Drops last 30 days (PR F)
    "rating",                      # ← Reviews: Rating
    "review_count",                # ← Reviews: Rating Count
    "delta_buy_box_30d_pct",
    "delta_buy_box_90d_pct",
    "amazon_url",
)

# Source → canonical map. LHS is the Keepa export column name (exactly
# as exported — UTF-8 BOM stripped by pandas, but spaces / colons / dots
# preserved). RHS is the canonical field name. Adding a new column to
# the schema means: append to KEEPA_FINDER_CANONICAL_COLUMNS, append a
# row here, decide on numeric vs string coercion in _row_from_keepa.
_KEEPA_TO_CANONICAL: dict[str, str] = {
    "ASIN": "asin",
    "Title": "product_name",
    "Brand": "brand",
    "Manufacturer": "manufacturer",
    "Categories: Root": "category_root",
    "Product Codes: EAN": "ean",
    "Product Codes: UPC": "upc",
    "Buy Box: Current": "buy_box_price",
    "Buy Box: 90 days avg.": "buy_box_avg90",
    "New, 3rd Party FBA: Current": "new_fba_price",
    "FBA Pick&Pack Fee": "fba_pick_pack_fee",
    # Keepa renamed this column some time after 2026-04 from
    # "Bought in past month" → "Monthly Sales Trends: Bought in past
    # month". Both spellings are mapped — the first one Keepa emits
    # wins, the absent one is silently ignored. Drop the alias once
    # all live exports are confirmed on the new schema.
    "Bought in past month": "sales_estimate",
    "Monthly Sales Trends: Bought in past month": "sales_estimate",
    "New FBA Offer Count: Current": "fba_seller_count",
    # Sales rank columns — names align with the API-path
    # market_snapshot schema so validator signals fire identically
    # on both paths.
    "Sales Rank: Current": "sales_rank",
    "Sales Rank: 90 days avg.": "sales_rank_avg90",
    # PR F — chart-readable BSR drop count = conservative sales proxy.
    "Sales Rank: Drops last 30 days": "bsr_drops_30d",
    # Validator-naming aligned (was buy_box_pct_amazon_90d / buy_box_oos_90).
    "Buy Box: % Amazon 90 days": "amazon_bb_pct_90",
    "Buy Box: 90 days OOS": "buy_box_oos_pct_90",
    "Buy Box: 30 days avg.": "buy_box_avg30",
    "Buy Box: Lowest 365 days": "buy_box_min_365d",
    "Buy Box: 30 days drop %": "delta_buy_box_30d_pct",
    "Buy Box: 90 days drop %": "delta_buy_box_90d_pct",
    # PR 2 — listing quality signals already on the API path; same
    # column names so validator + candidate_score read consistently.
    "Reviews: Rating": "rating",
    "Reviews: Rating Count": "review_count",
    # NB: "Referral Fee %" is mapped specially in _row_from_keepa
    # because Keepa formats it as "15 %" and calculate.py expects
    # the fraction (0.15). parse_money strips the % sign but
    # doesn't divide.
}

# Numeric canonical fields (coerced via parse_money — tolerant of
# Keepa's "-" sentinel for missing, "GBP" prefix on prices, etc.).
_NUMERIC_CANONICAL_FIELDS: frozenset[str] = frozenset({
    "buy_box_price",
    "new_fba_price",
    "buy_box_avg90",
    "buy_box_avg30",
    "buy_box_min_365d",
    "sales_estimate",
    "fba_seller_count",
    "fba_pick_pack_fee",
    "sales_rank",
    "sales_rank_avg90",
    "bsr_drops_30d",
    "amazon_bb_pct_90",
    "buy_box_oos_pct_90",
    "delta_buy_box_30d_pct",
    "delta_buy_box_90d_pct",
    "rating",
    "review_count",
})

DEFAULT_EXCLUSIONS_PATH: Path = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "niches"
    / "exclusions.csv"
)

AMAZON_UK_DP: str = "https://www.amazon.co.uk/dp/"


# ────────────────────────────────────────────────────────────────────────
# Sidecar metadata — recipe_metadata.json written by the skill.
# ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecipeMetadata:
    """Sidecar produced by the keepa-product-finder skill alongside the CSV.

    The canonical layout is documented in the skill's SKILL.md ("Output
    sidecar"). Fields not present in the file default to None — this step
    treats the metadata as additive context, never as load-bearing
    config.

    NB: ``calculate_config`` and ``decide_overrides`` are deliberately
    NOT exposed here even though the sidecar JSON includes them. The
    canonical source of those configs is the recipe JSON consumed by
    ``cli.strategy._apply_recipe_to_strategy`` — having two sources of
    truth would just create drift.
    """

    recipe: str | None = None
    category: str | None = None
    brands: list[str] | None = None
    rows_exported: int | None = None
    rendered_url: str | None = None


def _load_metadata(metadata_path: Path | None) -> RecipeMetadata:
    """Load the recipe metadata sidecar. Missing file = empty metadata.

    The CSV is the source of truth; metadata is informational. We don't
    raise if the file is absent because the step must work on
    hand-curated CSVs that didn't go through the skill.
    """
    if metadata_path is None or not metadata_path.exists():
        return RecipeMetadata()
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("recipe metadata at %s unreadable (%s); ignoring", metadata_path, e)
        return RecipeMetadata()
    if not isinstance(data, dict):
        logger.warning("recipe metadata at %s is not an object; ignoring", metadata_path)
        return RecipeMetadata()
    return RecipeMetadata(
        recipe=data.get("recipe"),
        category=data.get("category"),
        brands=data.get("brands"),
        rows_exported=data.get("rows_exported"),
        rendered_url=data.get("rendered_url"),
    )


# ────────────────────────────────────────────────────────────────────────
# Exclusions helpers — same shape as oa_csv to share semantics.
# ────────────────────────────────────────────────────────────────────────


def load_exclusions(path: Path | str | None) -> set[str]:
    """Read the global ASIN-dedup CSV and return the set of excluded ASINs.

    Format: same as ``data/niches/exclusions.csv`` (column header ``ASIN``).
    Missing file or missing column returns an empty set so the engine
    runs cleanly out-of-the-box on a fresh checkout.
    """
    p = Path(path) if path else DEFAULT_EXCLUSIONS_PATH
    if not p.exists():
        return set()
    try:
        df = pd.read_csv(
            p, dtype=str, keep_default_na=False, encoding="utf-8-sig",
        )
    except (pd.errors.EmptyDataError, OSError):
        return set()
    asin_col = next(
        (c for c in df.columns if c.strip().lower() == "asin"), None,
    )
    if asin_col is None:
        return set()
    return {coerce_str(v).upper() for v in df[asin_col] if coerce_str(v)}


# ────────────────────────────────────────────────────────────────────────
# Pure helpers — column mapping.
# ────────────────────────────────────────────────────────────────────────


def _row_from_keepa(
    row: pd.Series, recipe: str, exclusions: fba_config_loader.GlobalExclusions,
) -> dict[str, Any] | None:
    """Map one Keepa export row to the canonical schema.

    Returns ``None`` if the row should be dropped (post-export filters).
    Otherwise returns a dict with all KEEPA_FINDER_CANONICAL_COLUMNS keys
    populated (numeric fields default to 0.0 / 0; strings default to "").
    """
    asin = coerce_str(row.get("ASIN")).upper()
    if not asin or len(asin) != 10:
        return None  # malformed row — drop silently; CSV-row count drift logged by caller

    title = coerce_str(row.get("Title"))
    if exclusions.title_is_excluded(title):
        return None

    category_root = coerce_str(row.get("Categories: Root"))
    if exclusions.category_is_excluded(category_root):
        return None

    out: dict[str, Any] = {
        "asin": asin,
        "source": "keepa_finder",
        "discovery_strategy": recipe,
        "amazon_url": f"{AMAZON_UK_DP}{asin}",
        "category": _leaf_category(row),
    }
    # Group source columns by destination so we can pick the first
    # source column that actually has data. Some canonical fields have
    # multiple source aliases (e.g. sales_estimate ← either
    # "Bought in past month" or "Monthly Sales Trends: Bought in past
    # month" depending on Keepa export vintage).
    aliases: dict[str, list[str]] = {}
    for src_col, dst_col in _KEEPA_TO_CANONICAL.items():
        aliases.setdefault(dst_col, []).append(src_col)
    for dst_col, src_cols in aliases.items():
        if dst_col in out:
            continue  # already populated by the constants above
        raw = None
        for src in src_cols:
            v = row.get(src)
            if v is not None and not is_missing(v) and coerce_str(v):
                raw = v
                break
        if dst_col in _NUMERIC_CANONICAL_FIELDS:
            out[dst_col] = parse_money(raw)
        else:
            out[dst_col] = coerce_str(raw)

    # Referral fee: Keepa exports "15 %" or "15.01 %"; calculate.py and
    # fees.calculate_fees_fba expect the fraction (0.15). parse_money
    # strips the percent sign but doesn't divide.
    out["referral_fee_pct"] = parse_money(row.get("Referral Fee %")) / 100.0

    # Amazon presence flag — derived. Empty / 0 / "-" in "Amazon: Current"
    # means Amazon is NOT currently selling (the OOS pattern that
    # amazon_oos_wholesale targets). Anything > 0 means Amazon IS on the
    # listing right now. We don't emit "UNKNOWN" — Keepa always returns
    # a value; absence is meaningful (= OFF_LISTING).
    amz_current = parse_money(row.get("Amazon: Current"))
    out["amazon_status"] = "ON_LISTING" if amz_current > 0 else "OFF_LISTING"

    # Wholesale-flow defaults. buy_cost=0 is the load-bearing convention
    # that tells calculate.calculate_profit to emit max_buy_price (the
    # supplier-negotiation ceiling) instead of a literal ROI. moq=1
    # because Keepa-finder strategies are leads, not pre-negotiated
    # pricelists with bulk-buy minimums.
    out["buy_cost"] = 0.0
    out["moq"] = 1
    return out


def _leaf_category(row: pd.Series) -> str:
    """Extract the most-specific category string from a Keepa row.

    Preference order: "Categories: Tree" leaf > "Categories: Sub"
    > "Categories: Root" > "". Tree separator is ``" > "`` per Keepa
    convention.
    """
    tree = coerce_str(row.get("Categories: Tree"))
    if tree:
        # Keepa's tree string is ``"Root > Sub > Leaf"``.
        leaf = tree.rsplit(" > ", 1)[-1].strip()
        if leaf:
            return leaf
    sub = coerce_str(row.get("Categories: Sub"))
    if sub:
        # Sub may itself be a comma-separated list of leaf categories;
        # keep the first one — the engine doesn't multi-classify.
        return sub.split(",", 1)[0].strip()
    return coerce_str(row.get("Categories: Root"))


# ────────────────────────────────────────────────────────────────────────
# Public API.
# ────────────────────────────────────────────────────────────────────────


def discover_keepa_finder(
    csv_path: Path | str,
    recipe: str,
    *,
    exclusions_path: Path | str | None = None,
    metadata_path: Path | str | None = None,
    config_dir: Path | str | None = None,
) -> pd.DataFrame:
    """Read a Keepa Product Finder CSV export and emit canonical rows.

    Args:
        csv_path: path to the Keepa export.
        recipe: recipe id (e.g. ``"amazon_oos_wholesale"``). Tags every
            output row's ``discovery_strategy`` column.
        exclusions_path: ASIN dedup list. Defaults to
            ``fba_engine/data/niches/exclusions.csv``.
        metadata_path: recipe sidecar (informational only — drift between
            metadata.recipe and the recipe arg is logged, not fatal).
        config_dir: override for ``shared/config/`` (used by tests).

    Returns:
        DataFrame with ``KEEPA_FINDER_CANONICAL_COLUMNS`` columns.
        Empty DataFrame (with the canonical schema) when input is empty
        or all rows are filtered out.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Keepa export not found: {csv_path}")

    if not recipe:
        raise ValueError("recipe is required (used to tag discovery_strategy)")

    # Sidecar — informational. Validate recipe consistency if available.
    meta = _load_metadata(Path(metadata_path) if metadata_path else None)
    if meta.recipe and meta.recipe != recipe:
        logger.warning(
            "recipe arg %r != metadata recipe %r at %s — using arg",
            recipe, meta.recipe, metadata_path,
        )

    # Read the Keepa export. UTF-8 BOM is the usual encoding (matches
    # the live exports in fba_engine/data/niches/<niche>/working/).
    # `on_bad_lines='skip'` — production Keepa exports occasionally
    # contain rows with unbalanced quotes or rogue commas; skipping is
    # better than crashing the entire run when the engine sees one.
    try:
        raw = pd.read_csv(
            csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig",
            on_bad_lines="skip",
        )
    except pd.errors.EmptyDataError:
        logger.info("keepa_finder_csv: %s is empty", csv_path)
        return _empty_df()

    if raw.empty:
        return _empty_df()

    # Sanity-check: the ASIN column must exist or the file isn't a Keepa
    # Product Finder export at all (different tools export different
    # column sets — fail loud here rather than emit silent garbage).
    if "ASIN" not in raw.columns:
        raise ValueError(
            f"{csv_path}: not a Keepa Product Finder export — missing 'ASIN' column. "
            f"Columns present: {list(raw.columns)[:8]}..."
        )

    excl_set = load_exclusions(exclusions_path)
    g_excl = (
        fba_config_loader.get_global_exclusions(Path(config_dir) if config_dir else None)
    )

    # Map row-by-row. _row_from_keepa returns None for rows that fail
    # the post-export filters (title kw, category, malformed ASIN).
    out_rows: list[dict[str, Any]] = []
    for _, row in raw.iterrows():
        canonical = _row_from_keepa(row, recipe, g_excl)
        if canonical is None:
            continue
        if canonical["asin"] in excl_set:
            continue
        out_rows.append(canonical)

    if not out_rows:
        return _empty_df()

    df = pd.DataFrame(out_rows)
    # Re-order to canonical column order; any drift between row dicts is
    # caught by the explicit column list (KeyError is louder than silent reorder).
    return df[list(KEEPA_FINDER_CANONICAL_COLUMNS)]


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible discovery wrapper.

    Discovery steps create the DataFrame; the ``df`` arg is ignored.

    Required ``config`` keys:
      - ``csv_path``: path to the Keepa Product Finder CSV export.
      - ``recipe``: recipe id (e.g. ``"amazon_oos_wholesale"``).

    Optional ``config`` keys:
      - ``metadata_path``: recipe_metadata.json sidecar path.
      - ``exclusions_path``: override the ASIN dedup CSV.
      - ``config_dir``: override the shared/config/ dir (test injection).
    """
    csv_path = config.get("csv_path")
    recipe = config.get("recipe")
    if not csv_path:
        raise ValueError("keepa_finder_csv step requires config['csv_path']")
    if not recipe:
        raise ValueError("keepa_finder_csv step requires config['recipe']")
    return discover_keepa_finder(
        csv_path=csv_path,
        recipe=recipe,
        exclusions_path=config.get("exclusions_path"),
        metadata_path=config.get("metadata_path"),
        config_dir=config.get("config_dir"),
    )


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=list(KEEPA_FINDER_CANONICAL_COLUMNS))


# ────────────────────────────────────────────────────────────────────────
# CLI.
# ────────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest a Keepa Product Finder CSV export and emit a "
            "canonical engine DataFrame."
        )
    )
    parser.add_argument("--csv", required=True, type=Path, dest="csv_path")
    parser.add_argument(
        "--recipe", required=True,
        help="Recipe id (e.g. amazon_oos_wholesale). Tags discovery_strategy.",
    )
    parser.add_argument("--metadata", type=Path, default=None)
    parser.add_argument("--exclusions", type=Path, default=None)
    parser.add_argument("--out", type=Path, help="Write canonical CSV to this path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    df = discover_keepa_finder(
        csv_path=args.csv_path,
        recipe=args.recipe,
        exclusions_path=args.exclusions,
        metadata_path=args.metadata,
    )
    print(f"Discovered {len(df)} ASINs from Keepa export {args.csv_path}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            args.out, lambda p: df.to_csv(p, index=False, encoding="utf-8-sig"),
        )
        print(f"Saved: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
