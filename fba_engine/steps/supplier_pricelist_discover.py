"""Supplier-pricelist discovery step.

The first step in the supplier_pricelist strategy: load the
supplier-specific adapter, ingest raw price-list files, normalise to the
canonical schema, then run case_detection to derive unit/case costs.

The output DataFrame is ready for the next step (resolve — EAN
validation + Amazon match) without any further supplier-specific logic.

Per `docs/PRD-sourcing-strategies.md` §4 (architecture summary): this
is the "01_discover" stage for the supplier_pricelist source.

Standalone CLI invocation:

    python -m fba_engine.steps.supplier_pricelist_discover \\
        --supplier connect-beauty \\
        --input fba_engine/data/pricelists/connect-beauty/raw/

Used by `sourcing_engine.main.run_pipeline` as the discovery stage,
and by `fba_engine/strategies/supplier_pricelist.yaml` via the
`run_step(df, config)` contract.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd

# The canonical engine lives under shared/lib/python/. The repo-root
# `conftest.py` and `run.py` both add it to sys.path; this import works
# transparently in both pytest and runtime contexts.
from sourcing_engine.adapters.loader import (
    AdapterNotFoundError,
    load_supplier_adapter,
)
from sourcing_engine.pipeline.case_detection import derive_costs

logger = logging.getLogger(__name__)


def discover_supplier_pricelist(
    supplier: str,
    input_path: str,
) -> pd.DataFrame:
    """Run adapter ingest + normalise + case_detection.

    Args:
        supplier: supplier folder name under fba_engine/adapters/
            (e.g. "connect-beauty", "abgee").
        input_path: directory or single file to ingest.

    Returns:
        DataFrame in canonical schema with case-derived cost columns
        (unit_cost_ex_vat, unit_cost_inc_vat, case_cost_ex_vat,
        case_cost_inc_vat, case_qty) populated. Empty input returns
        an empty DataFrame, not None.
    """
    ingest_mod, normalise_mod = load_supplier_adapter(supplier)

    if os.path.isdir(input_path):
        raw_df = ingest_mod.ingest_directory(input_path)
    else:
        raw_df = ingest_mod.ingest_file(input_path)

    if raw_df.empty:
        logger.info("discover: no raw rows ingested from %s", input_path)
        return raw_df

    file_count = (
        raw_df["source_file"].nunique() if "source_file" in raw_df else "?"
    )
    logger.info(
        "discover: ingested %d raw rows from %s files",
        len(raw_df), file_count,
    )

    norm_df = normalise_mod.normalise(raw_df)
    logger.info("discover: normalised %d rows", len(norm_df))

    _apply_case_detection(norm_df)
    return norm_df


def _apply_case_detection(norm_df: pd.DataFrame) -> None:
    """Mutate `norm_df` in place: add unit/case costs + case_qty + flags.

    Mirrors the per-row loop in the legacy `run_pipeline`. Case
    detection produces supplier-side derived costs; it doesn't touch
    Amazon-side fields, so it sits naturally inside discovery.
    """
    for idx, row in norm_df.iterrows():
        costs = derive_costs(
            supplier_price_ex_vat=row["supplier_price_ex_vat"],
            supplier_price_basis=row["supplier_price_basis"],
            case_qty=row["case_qty"],
            rrp_inc_vat=row.get("rrp_inc_vat"),
        )
        for key in (
            "unit_cost_ex_vat",
            "unit_cost_inc_vat",
            "case_cost_ex_vat",
            "case_cost_inc_vat",
            "case_qty",
        ):
            norm_df.at[idx, key] = costs[key]
        existing = norm_df.at[idx, "risk_flags"]
        if not isinstance(existing, list):
            existing = []
        norm_df.at[idx, "risk_flags"] = existing + costs["flags"]


def run_step(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Runner-compatible discovery wrapper.

    Discovery steps don't take an input DataFrame — they CREATE it.
    The `df` argument is ignored (mirrors `oa_csv.run_step`).

    Required `config` keys:
      - ``supplier``: adapter folder name
      - ``input_path``: directory or single file to ingest
    """
    supplier = config.get("supplier")
    input_path = config.get("input_path")
    if not supplier:
        raise ValueError(
            "supplier_pricelist_discover step requires config['supplier']"
        )
    if not input_path:
        raise ValueError(
            "supplier_pricelist_discover step requires config['input_path']"
        )
    return discover_supplier_pricelist(supplier=supplier, input_path=input_path)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Supplier-pricelist discovery — load adapter, ingest, "
            "normalise, and derive unit/case costs."
        )
    )
    parser.add_argument("--supplier", required=True)
    parser.add_argument("--input", required=True, dest="input_path")
    parser.add_argument("--out", type=Path, help="Optional output CSV.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        df = discover_supplier_pricelist(
            supplier=args.supplier, input_path=args.input_path
        )
    except AdapterNotFoundError as err:
        print(f"Adapter load failed: {err}", file=sys.stderr)
        return 2
    print(f"Discovered {len(df)} rows for supplier '{args.supplier}'")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"Saved: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
