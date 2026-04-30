"""Entry point — orchestrates the supplier_pricelist sourcing pipeline.

Usage (recommended):
    python -m sourcing_engine.main --supplier abgee

Auto-derives input/output paths from the supplier name. Override with --input
and --output if you need to point elsewhere.

Full form:
    python -m sourcing_engine.main \\
        --supplier abgee \\
        --input  fba_engine/data/pricelists/abgee/raw/ \\
        --output fba_engine/data/pricelists/abgee/results/ \\
        --market-data fba_engine/data/pricelists/abgee/raw/keepa_combined.csv

The PYTHONPATH must include shared/lib/python/. Use the launcher at the repo
root (`run.py`) to handle this automatically:
    python run.py --supplier abgee

Internals (PR #7 canonical refactor): this module is now a thin
orchestrator that composes the per-stage step modules under
``fba_engine.steps.*`` (discover → resolve → calculate → decide →
enrich → output). The same step modules are referenced by
``fba_engine/strategies/supplier_pricelist.yaml`` for runner-driven
invocations.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from sourcing_engine.adapters.loader import AdapterNotFoundError
from sourcing_engine.pipeline.market_data import load_market_data

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sourcing_engine")


def _find_repo_root() -> Path:
    """Walk up from this file looking for the repo root (has fba_engine/)."""
    here = Path(__file__).resolve()
    for ancestor in [here, *here.parents]:
        if (ancestor / "fba_engine").is_dir():
            return ancestor
    raise RuntimeError(
        "Could not locate repo root (no fba_engine/ ancestor)."
    )


def _default_paths(supplier: str, repo_root: Path) -> tuple[Path, Path]:
    """Default input and output paths for a supplier."""
    base = repo_root / "fba_engine" / "data" / "pricelists" / supplier
    return base / "raw", base / "results"


def _ensure_step_imports_resolve():
    """Add shared/lib/python to sys.path so the fba_engine.steps modules
    can resolve their `from sourcing_engine...` imports when this file is
    invoked via `python -m sourcing_engine.main` rather than `run.py`.

    `run.py` already does this; the conftest.py at the repo root does it
    for pytest. This belt-and-braces is for the third invocation path.
    Idempotent — ``str(shared_lib) not in sys.path`` short-circuits on
    every call after the first.
    """
    here = Path(__file__).resolve()
    shared_lib = here.parent.parent.parent  # …/shared/lib/python
    if str(shared_lib) not in sys.path:
        sys.path.insert(0, str(shared_lib))


def run_pipeline(
    supplier: str,
    input_path: str,
    output_dir: str,
    market_data_path: str | None = None,
    preflight_enabled: bool = True,
):
    """Run the full pipeline for one supplier.

    Args:
        supplier: supplier name matching the adapter folder under
            fba_engine/adapters/<supplier>/
        input_path: directory or single file to ingest
        output_dir: where to write the timestamped run folder
        market_data_path: optional Keepa CSV
        preflight_enabled: when True (default) annotate rows with
            informational SP-API columns. No-ops silently if the MCP
            CLI isn't built or SP-API creds aren't set.

    Returns: path to the timestamped run folder, or None if discovery
    found nothing to process.
    """
    _ensure_step_imports_resolve()

    # Imported lazily so tests that monkeypatch the step modules work
    # against the canonical names.
    from fba_engine.steps.calculate import calculate_economics
    from fba_engine.steps.decide import decide_rows
    from fba_engine.steps.enrich import enrich_with_preflight
    from fba_engine.steps.resolve import resolve_matches
    from fba_engine.steps.supplier_pricelist_discover import (
        discover_supplier_pricelist,
    )
    from fba_engine.steps.supplier_pricelist_output import write_outputs

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(output_dir, timestamp)
    os.makedirs(run_dir, exist_ok=True)

    # Stage 01 — discover (adapter ingest + normalise + case_detection).
    logger.info("Discovering supplier rows for %s from %s", supplier, input_path)
    try:
        norm_df = discover_supplier_pricelist(
            supplier=supplier, input_path=input_path,
        )
    except AdapterNotFoundError as e:
        logger.error("Adapter load failed: %s", e)
        sys.exit(2)

    if norm_df.empty:
        logger.error("No data ingested — exiting")
        return None
    logger.info("Discovered %d normalised rows", len(norm_df))

    # Stage 02 — resolve (EAN validation + Amazon market match). Loads
    # market_data once; we re-use the dict for the Excel writer below.
    market_data = load_market_data(market_data_path)
    resolved_df = resolve_matches(norm_df, market_data=market_data)
    logger.info("Resolved %d rows (matches + REJECTs)", len(resolved_df))

    # Stage 04 — calculate (fees + conservative price + profit + risk).
    calculated_df = calculate_economics(resolved_df)

    # Stage 05 — decide (SHORTLIST / REVIEW / REJECT).
    decided_df = decide_rows(calculated_df)

    # Stage 03 — enrich (preflight). Order: legacy run_pipeline performs
    # preflight AFTER decide; preserved here. Strategies that want a
    # different order can compose YAML steps differently.
    enriched_df = enrich_with_preflight(decided_df, enabled=preflight_enabled)

    # Stage 06 — output (CSV + XLSX + MD).
    write_outputs(
        enriched_df,
        run_dir=Path(run_dir),
        timestamp=timestamp,
        supplier_label=_friendly_supplier_label(supplier),
        market_data=market_data,
    )

    _print_summary(enriched_df, norm_df)
    return run_dir


def _friendly_supplier_label(supplier: str) -> str:
    """Turn 'connect-beauty' into 'Connect Beauty' for display."""
    return " ".join(part.capitalize() for part in supplier.replace("_", "-").split("-"))


def _print_summary(output_df: pd.DataFrame, norm_df: pd.DataFrame) -> None:
    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    logger.info("=" * 60)
    logger.info(
        "Suppliers processed: %d",
        norm_df["supplier"].nunique() if not norm_df.empty else 0,
    )
    logger.info("Source rows processed: %d", len(norm_df))
    logger.info("Output rows: %d", len(output_df))
    if not output_df.empty and "decision" in output_df.columns:
        for d in ["SHORTLIST", "REVIEW", "REJECT"]:
            logger.info("%s: %d", d, (output_df["decision"] == d).sum())
        rejects = output_df[output_df["decision"] == "REJECT"]
        if not rejects.empty and "decision_reason" in rejects.columns:
            invalid_ean = (rejects["decision_reason"] == "Invalid or missing EAN").sum()
            no_match = (rejects["decision_reason"] == "No Amazon match found").sum()
            if invalid_ean:
                logger.info("  └─ Invalid EAN: %d", invalid_ean)
            if no_match:
                logger.info("  └─ No Amazon match: %d", no_match)


def main():
    parser = argparse.ArgumentParser(description="Amazon Supplier Shortlist Engine")
    parser.add_argument(
        "--supplier", required=True,
        help="Supplier name (matches the folder name under fba_engine/adapters/)",
    )
    parser.add_argument(
        "--input", default=None,
        help="Supplier file or directory. Defaults to "
             "fba_engine/data/pricelists/<supplier>/raw/",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output directory. Defaults to "
             "fba_engine/data/pricelists/<supplier>/results/",
    )
    parser.add_argument("--market-data", default=None, help="Market data CSV path")
    parser.add_argument(
        "--no-preflight", action="store_true",
        help="Skip the SP-API preflight annotation step "
             "(restrictions, FBA eligibility, live Buy Box, catalog brand). "
             "Default: enabled when MCP CLI is built and SP_API creds are set; "
             "no-ops silently otherwise.",
    )
    args = parser.parse_args()

    repo_root = _find_repo_root()
    default_in, default_out = _default_paths(args.supplier, repo_root)
    input_path = args.input if args.input else str(default_in)
    output_dir = args.output if args.output else str(default_out)

    run_pipeline(
        args.supplier,
        input_path,
        output_dir,
        args.market_data,
        preflight_enabled=not args.no_preflight,
    )


if __name__ == "__main__":
    main()
