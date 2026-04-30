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

This step replaces the legacy SellerAmp skill (Skill 2) for the
checks that don't require Buy Box %: gating, FBA eligibility,
hazmat, and catalog brand all come from the SP-API MCP. Buy Box %
remains in Keepa's stats (out of scope for this step today).
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from sourcing_engine.pipeline.preflight import annotate_with_preflight

logger = logging.getLogger(__name__)


# `include` set the leads-mode chains use (seller_storefront, OA leads
# without prices yet). Skips pricing/fees/profitability — those need a
# market_price the discovery step doesn't have.
LEADS_INCLUDE: tuple[str, ...] = ("restrictions", "fba", "catalog")


def enrich_with_preflight(
    df: pd.DataFrame,
    *,
    enabled: bool = True,
    include: list[str] | None = None,
) -> pd.DataFrame:
    """Apply preflight annotation to the result rows.

    Args:
        df: result DataFrame from earlier stages.
        enabled: skip preflight entirely when False (used by tests and
            by callers that don't have SP-API creds).
        include: subset of MCP preflight sources to call (see
            ``annotate_with_preflight``). Pass ``LEADS_INCLUDE`` for
            ASIN-only chains where there's no market_price yet.
            Default ``None`` calls everything (legacy contract).
    """
    if df.empty:
        return df
    if not enabled:
        return df

    rows = df.to_dict("records")
    annotated = annotate_with_preflight(rows, include=include)
    return pd.DataFrame(annotated)


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper. Recognised config keys:

      - ``enabled``: default ``True``. Set ``False`` to skip preflight
        (e.g. for offline / no-creds environments).
      - ``include``: list of MCP sources to call. ``"leads"`` is a
        shorthand alias for ``LEADS_INCLUDE`` (restrictions + fba +
        catalog) — the right setting for ASIN-only chains.
    """
    enabled = config.get("enabled", True)
    include = config.get("include")
    if include == "leads":
        include = list(LEADS_INCLUDE)
    return enrich_with_preflight(df, enabled=enabled, include=include)
