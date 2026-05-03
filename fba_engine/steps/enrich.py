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
    survivors_only: bool = False,
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
        survivors_only: when True, call the MCP only for non-REJECT
            rows. REJECT rows pass through with the preflight columns
            seeded as None. Used by `supplier_pricelist` after the
            first-pass `decide` filters down to ~5-50 actionable rows;
            no point spending SP-API quota on the 5700 rows the engine
            already structurally killed. Without the filter, a 5720-
            row ABGEE run takes 10+ minutes on the SP-API side because
            preflight runs on every input ASIN. Default False keeps
            existing chains' behaviour intact.
    """
    if df.empty:
        return df
    if not enabled:
        return df

    if survivors_only and "decision" in df.columns:
        from fba_engine.steps._helpers import is_missing
        survivor_mask = df["decision"].apply(
            lambda d: is_missing(d) or not (
                isinstance(d, str) and d.strip().upper() == "REJECT"
            )
        )
        if not survivor_mask.any():
            logger.info("enrich: no survivors to preflight; passing through")
            return df

        survivors = df.loc[survivor_mask].copy()
        rejects = df.loc[~survivor_mask].copy()
        logger.info(
            "enrich: preflight on %d survivors (skipping %d REJECT rows)",
            len(survivors), len(rejects),
        )
        annotated_survivors_rows = annotate_with_preflight(
            survivors.to_dict("records"), include=include,
        )
        annotated_survivors = pd.DataFrame(annotated_survivors_rows)
        # Stitch back: preserve original row order via the index. The
        # rejects keep whatever columns they had (annotate_with_preflight
        # would have seeded preflight cols as None for them anyway).
        annotated_survivors.index = survivors.index
        # Union the column set so the output schema matches what a
        # full-pass call would produce — REJECT rows just have None
        # for the new preflight columns.
        all_cols = list(dict.fromkeys(
            list(rejects.columns) + list(annotated_survivors.columns)
        ))
        for col in all_cols:
            if col not in rejects.columns:
                rejects[col] = None
            if col not in annotated_survivors.columns:
                annotated_survivors[col] = None
        merged = pd.concat([rejects, annotated_survivors], axis=0)
        return merged.sort_index().reset_index(drop=True)

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
      - ``survivors_only``: default False. When True, call the MCP
        only for non-REJECT rows. Use when the chain has already run
        `decide` and only the actionable subset needs SP-API data.
    """
    enabled = config.get("enabled", True)
    include = config.get("include")
    if include == "leads":
        include = list(LEADS_INCLUDE)
    raw_survivors_only = config.get("survivors_only", False)
    if isinstance(raw_survivors_only, str):
        survivors_only = raw_survivors_only.strip().lower() in ("true", "1", "yes")
    else:
        survivors_only = bool(raw_survivors_only)
    return enrich_with_preflight(
        df, enabled=enabled, include=include, survivors_only=survivors_only,
    )
