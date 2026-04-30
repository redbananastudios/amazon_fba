"""Supplier-pricelist output step — write CSV + XLSX + markdown report.

Stage 06 of the canonical engine (per
`docs/PRD-sourcing-strategies.md` §4). Takes the decided / preflight-
annotated DataFrame and writes the three canonical artefacts into a
timestamped run folder:

  - ``shortlist_<timestamp>.csv`` (full row set, all decisions)
  - ``shortlist_<timestamp>.xlsx`` (styled, SHORTLIST/REVIEW only)
  - ``report_<timestamp>.md`` (human-readable summary by supplier)

Returns the input DataFrame unchanged so the step composes cleanly in
a strategy chain — downstream steps (e.g. a notification step) can
still consume the rows.

The actual writers live in
``sourcing_engine.output.{csv_writer,excel_writer,markdown_report}``
and are tested independently. This step is the I/O boundary, not the
formatting.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from sourcing_engine.output.csv_writer import write_csv
from sourcing_engine.output.excel_writer import write_excel
from sourcing_engine.output.markdown_report import write_report

logger = logging.getLogger(__name__)


def write_outputs(
    df: pd.DataFrame,
    *,
    run_dir: Path | str,
    timestamp: str,
    supplier_label: str | None = None,
    market_data: dict | None = None,
) -> None:
    """Write the three canonical artefacts. No-op for empty input.

    Args:
        df: result DataFrame (post-decide, optionally post-enrich).
        run_dir: timestamped output folder. Created if missing.
        timestamp: filename stem (e.g. ``"20260429_120000"``).
        supplier_label: friendly name for the Excel title bar.
        market_data: optional Keepa market dict for the Excel writer's
            enrichment columns.
    """
    if df.empty:
        logger.info("output: empty df, skipping writes")
        return

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    csv_path = run_dir / f"shortlist_{timestamp}.csv"
    xlsx_path = run_dir / f"shortlist_{timestamp}.xlsx"
    md_path = run_dir / f"report_{timestamp}.md"

    write_csv(df, str(csv_path))
    write_excel(
        df, str(xlsx_path),
        market_data=market_data,
        supplier_label=supplier_label,
    )
    write_report(df, str(md_path))


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper. Required config keys:

      - ``run_dir``: absolute or interpolated path to the output folder.
      - ``timestamp``: filename stem.

    Optional config keys:

      - ``supplier_label``: friendly name for the Excel title bar.
      - ``market_data``: pre-loaded Keepa market dict (rare — most
        callers omit this and the Excel enrichment is skipped).

    Returns the df unchanged so the step composes in a chain.
    """
    run_dir = config.get("run_dir")
    timestamp = config.get("timestamp")
    if not run_dir:
        raise ValueError(
            "supplier_pricelist_output step requires config['run_dir']"
        )
    if not timestamp:
        raise ValueError(
            "supplier_pricelist_output step requires config['timestamp']"
        )

    write_outputs(
        df,
        run_dir=run_dir,
        timestamp=timestamp,
        supplier_label=config.get("supplier_label"),
        market_data=config.get("market_data"),
    )
    return df
