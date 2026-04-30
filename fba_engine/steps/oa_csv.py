"""OA CSV discovery step.

Reads a third-party online-arbitrage candidate CSV (SellerAmp 2DSorter
v1; Tactical Arbitrage and OAXray are stubbed out), filters against the
exclusions list, and emits a canonical DataFrame for the canonical
engine's resolve/enrich/calculate/decide chain to consume.

Per `docs/PRD-sourcing-strategies.md` §6.

Output columns (mapped to the canonical engine's schema):
  - asin
  - source              = "oa_csv"
  - feed                = "selleramp" | "tactical_arbitrage" | "oaxray"
  - retail_url
  - retail_cost_inc_vat = canonical `buy_cost` (per PRD §6.4)
  - retail_name

Standalone CLI invocation:

    python -m fba_engine.steps.oa_csv \\
        --feed selleramp \\
        --csv path/to/2dsorter-export.csv \\
        --out fba_engine/data/strategies/oa_csv/selleramp/discovery.csv

Exclusions: filters against the path passed in `config["exclusions_path"]`
(default: `fba_engine/data/niches/exclusions.csv`). Rows whose ASIN
appears in the exclusions ASIN column are dropped.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from fba_engine.steps._helpers import atomic_write, coerce_str

# Importers live under shared/lib/python/oa_importers/.
from oa_importers import IMPORTERS

# ────────────────────────────────────────────────────────────────────────
# Constants.
# ────────────────────────────────────────────────────────────────────────

OA_DISCOVERY_COLUMNS: tuple[str, ...] = (
    "asin",
    "source",
    "feed",
    "retail_url",
    "retail_cost_inc_vat",
    "retail_name",
)

DEFAULT_EXCLUSIONS_PATH: Path = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "niches"
    / "exclusions.csv"
)


# ────────────────────────────────────────────────────────────────────────
# Pure helpers.
# ────────────────────────────────────────────────────────────────────────


def load_exclusions(path: Path | str) -> set[str]:
    """Read the global exclusions CSV and return the set of excluded ASINs.

    The CSV is expected to have an `ASIN` column. Missing file or missing
    column returns an empty set so callers don't need a special path.
    """
    path = Path(path)
    if not path.exists():
        return set()
    try:
        df = pd.read_csv(
            path, dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
    except (pd.errors.EmptyDataError, OSError):
        return set()
    asin_col = next(
        (c for c in df.columns if c.strip().lower() == "asin"), None
    )
    if asin_col is None:
        return set()
    return {coerce_str(v).upper() for v in df[asin_col] if coerce_str(v)}


def _candidates_to_df(candidates: list) -> pd.DataFrame:
    """Convert OaCandidate dataclass list to a DataFrame with the canonical schema."""
    if not candidates:
        return pd.DataFrame(columns=list(OA_DISCOVERY_COLUMNS))
    rows = []
    for c in candidates:
        d = asdict(c)
        # `source` is constant across this discovery step; it identifies
        # the discovery method to downstream steps that may need to
        # branch on origin (e.g. OA price already includes VAT vs
        # supplier_pricelist where it doesn't).
        d["source"] = "oa_csv"
        rows.append(d)
    df = pd.DataFrame(rows)
    # Re-order columns to match the canonical schema.
    return df[list(OA_DISCOVERY_COLUMNS)]


# ────────────────────────────────────────────────────────────────────────
# Entry point.
# ────────────────────────────────────────────────────────────────────────


def discover_oa_candidates(
    feed: str,
    csv_path: Path | str,
    *,
    exclusions_path: Path | str | None = None,
) -> pd.DataFrame:
    """Discover OA candidates from a CSV via the named importer.

    Returns a DataFrame with `OA_DISCOVERY_COLUMNS`. Rows whose ASIN is
    on the exclusions list are filtered out.
    """
    if feed not in IMPORTERS:
        available = ", ".join(sorted(IMPORTERS.keys()))
        raise ValueError(
            f"Unknown OA feed '{feed}'. Registered importers: {available}"
        )

    importer = IMPORTERS[feed]
    candidates = list(importer.parse(Path(csv_path)))
    df = _candidates_to_df(candidates)

    excl_set = load_exclusions(exclusions_path or DEFAULT_EXCLUSIONS_PATH)
    if excl_set and not df.empty:
        df = df[~df["asin"].str.upper().isin(excl_set)].reset_index(drop=True)

    return df


# ────────────────────────────────────────────────────────────────────────
# Step contract.
# ────────────────────────────────────────────────────────────────────────


def run_step(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Runner-compatible discovery wrapper.

    Discovery steps are unusual in that they generally don't take a
    DataFrame as input — they CREATE it. The `df` argument is ignored.

    Required `config` keys:
      - `feed`: importer feed_id ("selleramp" | "tactical_arbitrage" | "oaxray")
      - `csv_path`: path to the third-party export

    Optional `config` keys:
      - `exclusions_path`: override the global exclusions CSV
    """
    feed = config.get("feed")
    csv_path = config.get("csv_path")
    if not feed:
        raise ValueError("oa_csv step requires config['feed']")
    if not csv_path:
        raise ValueError("oa_csv step requires config['csv_path']")
    return discover_oa_candidates(
        feed=feed,
        csv_path=csv_path,
        exclusions_path=config.get("exclusions_path"),
    )


# ────────────────────────────────────────────────────────────────────────
# CLI.
# ────────────────────────────────────────────────────────────────────────


def run(feed: str, csv_path: Path, out: Path | None) -> None:
    df = discover_oa_candidates(feed=feed, csv_path=csv_path)
    print(f"Discovered {len(df)} OA candidates from {feed}: {csv_path}")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            out, lambda p: df.to_csv(p, index=False, encoding="utf-8-sig")
        )
        print(f"Saved: {out}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "OA-CSV discovery step — reads a third-party OA candidate "
            "export (SellerAmp 2DSorter, etc.) and emits a canonical "
            "DataFrame for the engine."
        )
    )
    parser.add_argument(
        "--feed", required=True,
        help="Importer feed_id: selleramp / tactical_arbitrage / oaxray",
    )
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    run(feed=args.feed, csv_path=args.csv, out=args.out)
