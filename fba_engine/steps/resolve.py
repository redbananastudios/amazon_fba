"""Resolve step — EAN validation + Amazon market match.

Stage 02 of the canonical engine (per
`docs/PRD-sourcing-strategies.md` §4). Takes a normalised supplier
DataFrame (output of supplier_pricelist_discover) and produces a flat
DataFrame where:

  - Each input row with a valid EAN that matches market data emits one
    or two rows (UNIT match + optional CASE match — multi-match
    explosion mirrors the legacy `match_product`).
  - Each input row with an invalid/missing EAN emits a single REJECT
    row with ``decision_reason = "Invalid or missing EAN"``.
  - Each input row with a valid EAN but no market match emits a single
    REJECT row with ``decision_reason = "No Amazon match found"``.

REJECT-row reason wording is verbatim with the legacy pipeline — the
integration test pins these strings, and operators may filter by them.

Runner contract: ``run_step(df, config) -> df``.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from sourcing_engine.pipeline.market_data import load_market_data
from sourcing_engine.pipeline.match import match_product
from sourcing_engine.utils.ean_validator import validate_ean

logger = logging.getLogger(__name__)


def resolve_matches(
    df: pd.DataFrame,
    market_data_path: str | None = None,
    *,
    market_data: dict | None = None,
) -> pd.DataFrame:
    """Validate EAN + match to market data; return a flat result df.

    Args:
        df: input DataFrame from supplier_pricelist_discover (normalised
            supplier rows with case-derived costs).
        market_data_path: optional Keepa CSV path. ``None`` produces an
            empty market dict (every row will be a no-match REJECT) —
            matches the existing `load_market_data(None) -> {}`
            contract.
        market_data: optional pre-loaded market dict. Takes precedence
            over ``market_data_path`` when both are passed — used by
            ``run_pipeline`` to share a single load across this step
            and the Excel writer.

    Returns:
        DataFrame with one row per (input × match) plus REJECT rows for
        invalid-EAN / no-match inputs. The caller is responsible for
        downstream calculate / decide / output stages.
    """
    if df.empty:
        return df

    if market_data is None:
        market_data = load_market_data(market_data_path)
    output_rows: list[dict] = []

    for idx, row in df.iterrows():
        try:
            row_dict = row.to_dict()
            if not row_dict.get("ean") or not validate_ean(row_dict["ean"]):
                output_rows.append({
                    **row_dict,
                    "decision": "REJECT",
                    "decision_reason": "Invalid or missing EAN",
                    "match_type": "UNIT",
                })
                continue

            matches = match_product(row_dict, market_data)
            if not matches:
                output_rows.append({
                    **row_dict,
                    "decision": "REJECT",
                    "decision_reason": "No Amazon match found",
                    "match_type": "UNIT",
                })
                continue

            output_rows.extend(matches)
        except Exception:
            # A defensive catch — preserves the legacy run_pipeline's
            # behaviour where a per-row error doesn't kill the whole
            # batch. We seed a REVIEW row so downstream stages know
            # this row needs manual inspection.
            logger.exception(
                "[%s] [ROW_%s] [%s] resolve error",
                row.get("supplier"), idx, row.get("ean"),
            )
            output_rows.append({
                **row.to_dict(),
                "decision": "REVIEW",
                "decision_reason": "Resolve error — manual review required",
                "match_type": "UNIT",
            })

    return pd.DataFrame(output_rows)


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper. Recognised config keys:

      - ``market_data_path``: optional path to a Keepa CSV. Omitted or
        empty-string produces an empty market dict (a YAML strategy
        often resolves a missing context value to "" through
        ``interpolate``; treat that the same as not-provided so the
        underlying ``load_market_data`` doesn't log a misleading
        OSError trying to open "").
    """
    path = config.get("market_data_path")
    return resolve_matches(df, market_data_path=path or None)
