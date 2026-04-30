"""Enrich step — SP-API preflight annotation.

Stage 03 of the canonical engine (per
`docs/PRD-sourcing-strategies.md` §4). Annotates rows with
informational SP-API columns:

  - restriction_status (gated / approved / unknown)
  - fba_eligibility
  - live Buy Box price (sanity-check vs Keepa)
  - catalog brand + hazmat classification

This is informational — does NOT affect the SHORTLIST/REVIEW/REJECT
decisions made by the decide step. If the MCP CLI isn't built or
SP-API creds aren't set, the underlying helper no-ops silently and
seeds rows with ``None`` columns so the output schema stays stable.

The legacy ``run_pipeline`` performs preflight AFTER decide; we keep
that ordering. Strategies that want a different order can compose
steps differently.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from sourcing_engine.pipeline.preflight import annotate_with_preflight

logger = logging.getLogger(__name__)


def enrich_with_preflight(
    df: pd.DataFrame, *, enabled: bool = True,
) -> pd.DataFrame:
    """Apply preflight annotation to the result rows.

    Args:
        df: result DataFrame from earlier stages.
        enabled: skip preflight entirely when False (used by tests and
            by callers that don't have SP-API creds).
    """
    if df.empty:
        return df
    if not enabled:
        return df

    rows = df.to_dict("records")
    annotated = annotate_with_preflight(rows)
    return pd.DataFrame(annotated)


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper. Recognised config keys:

      - ``enabled``: default ``True``. Set ``False`` to skip preflight
        (e.g. for offline / no-creds environments).
    """
    enabled = config.get("enabled", True)
    return enrich_with_preflight(df, enabled=enabled)
