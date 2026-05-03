"""09_buy_plan_html — emits buyer_report_{ts}.json + .html.

Runs after the existing CSV/XLSX/MD writers. Pure additive: writes
two new artefacts but never mutates the DataFrame. Per-row exceptions
are caught and logged; the run never aborts.

Honours `buy_plan_html.enabled` config — silent no-op when disabled.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from fba_config_loader import get_buy_plan_html
from sourcing_engine.buy_plan_html.analyst import fallback_analyse
from sourcing_engine.buy_plan_html.payload import build_payload
from sourcing_engine.buy_plan_html.renderer import render_html

logger = logging.getLogger(__name__)

BUYER_REPORT_OUTPUTS = (
    "buyer_report_{ts}.json",
    "buyer_report_{ts}.html",
)


def add_buy_plan_html(
    df: pd.DataFrame,
    *,
    run_dir: Path | str,
    timestamp: str,
    strategy: str,
    supplier: str | None,
    asin: str | None = None,
) -> pd.DataFrame:
    """Write JSON + HTML artefacts. Returns df unchanged.

    Args:
        df: DataFrame post-buy_plan (or any post-validate_opportunity df).
        run_dir: directory the artefacts land in. Created if missing.
        timestamp: filename stem.
        strategy: name of the strategy that produced the df.
        supplier: friendly supplier label or None for non-supplier strategies.
        asin: optional — when set (single-ASIN strategies), filename
            includes the ASIN as a prefix for filesystem grouping.

    Honours `buy_plan_html.enabled` config — silent no-op when disabled.
    Per-row exceptions in payload-building or template-prose are caught
    and logged; the run never aborts.
    """
    cfg = get_buy_plan_html()
    if not cfg.enabled:
        logger.info("buy_plan_html: disabled by config; skipping")
        return df

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    if asin:
        json_path = run_dir / f"buyer_report_{asin}_{timestamp}.json"
        html_path = run_dir / f"buyer_report_{asin}_{timestamp}.html"
    else:
        json_path = run_dir / f"buyer_report_{timestamp}.json"
        html_path = run_dir / f"buyer_report_{timestamp}.html"

    try:
        payload = build_payload(df, run_id=timestamp, strategy=strategy, supplier=supplier)
    except Exception:
        logger.exception("buy_plan_html: payload build failed; skipping")
        return df

    # Populate the analyst block per row using the deterministic
    # fallback. When Cowork orchestration is in the loop, the
    # orchestration step overwrites this with Claude's analysis
    # before the HTML is finalised — see orchestration/runs/
    # buyer_report_prose.yaml for the wire-up.
    for row in payload.get("rows") or []:
        try:
            row["analyst"] = fallback_analyse(row)
        except Exception:
            logger.exception(
                "buy_plan_html: analyst fallback failed for asin=%s",
                row.get("asin"),
            )
            # Leave analyst block as-is (nulls); renderer falls through.

    # Atomic JSON write — payload now contains analyst block.
    tmp_json = json_path.with_suffix(".json.tmp")
    tmp_json.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    tmp_json.replace(json_path)

    # Render HTML from the populated payload.
    try:
        html = render_html(payload)
    except Exception:
        logger.exception("buy_plan_html: HTML render failed; skipping HTML")
        return df

    tmp_html = html_path.with_suffix(".html.tmp")
    tmp_html.write_text(html, encoding="utf-8")
    tmp_html.replace(html_path)

    logger.info(
        "buy_plan_html: wrote %s + %s (%d rows)",
        json_path, html_path, len(payload.get("rows") or []),
    )
    return df


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper.

    Required config keys: ``run_dir`` (or ``output_dir`` alias), ``timestamp``.
    Optional: ``strategy``, ``supplier``, ``asin``.
    """
    run_dir = config.get("run_dir") or config.get("output_dir")
    timestamp = config.get("timestamp")
    strategy = config.get("strategy") or ""
    raw_supplier = config.get("supplier")
    supplier = raw_supplier if raw_supplier else None
    asin = config.get("asin") or None
    if not run_dir or not timestamp:
        logger.warning(
            "buy_plan_html: missing run_dir/timestamp in config — skipping",
        )
        return df
    return add_buy_plan_html(
        df,
        run_dir=run_dir,
        timestamp=timestamp,
        strategy=strategy,
        supplier=supplier,
        asin=asin,
    )
