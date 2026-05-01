"""Decide step — SHORTLIST / REVIEW / REJECT verdicts for supplier_pricelist.

Stage 05 of the canonical engine (per
`docs/PRD-sourcing-strategies.md` §4). Takes the calculate step's
output and applies the multi-criteria decision rules in
``sourcing_engine.pipeline.decision.decide`` to each match row.

Pre-decided rows (REJECT from resolve, "No valid market price" REJECT
from calculate) flow through unchanged — they already have a verdict
and reason.

This step is distinct from ``fba_engine.steps.decision_engine`` which
applies BUY/NEGOTIATE/WATCH/KILL verdicts to keepa_niche outputs. The
two strategies have different verdict shapes and different input
columns; co-locating them under the same name would be confusing.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from fba_engine.steps._helpers import is_missing
from sourcing_engine.pipeline.decision import decide as _decide_row

logger = logging.getLogger(__name__)


def decide_rows(
    df: pd.DataFrame, *, overrides: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Set ``decision`` + ``decision_reason`` for each match row.

    REJECT rows from earlier stages are preserved as-is.

    ``overrides`` (optional) — per-call threshold overrides forwarded
    to ``sourcing_engine.pipeline.decision.decide``. See its module
    docstring for supported keys (``min_sales_shortlist``,
    ``min_sales_review``, ``min_profit``, ``target_roi``). Default
    ``None`` keeps every call's existing behaviour intact.
    """
    if df.empty:
        return df

    output_rows: list[dict] = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        existing = row_dict.get("decision")
        if not is_missing(existing) and existing:
            # Pre-decided rows (REJECT from resolve / "No valid market
            # price" REJECT from calculate) flow through unchanged.
            # NaN-aware to avoid the truthy-NaN trap when rows are
            # round-tripped through DataFrame construction.
            output_rows.append(row_dict)
            continue

        try:
            decision_input = {
                "profit_current": row_dict.get("profit_current", 0),
                "profit_conservative": row_dict.get("profit_conservative", 0),
                "margin_current": row_dict.get("margin_current", 0),
                "margin_conservative": row_dict.get("margin_conservative", 0),
                "roi_current": row_dict.get("roi_current", 0),
                "roi_conservative": row_dict.get("roi_conservative", 0),
                "sales_estimate": row_dict.get("sales_estimate"),
                "gated": row_dict.get("gated", "UNKNOWN"),
                "risk_flags": row_dict.get("risk_flags") or [],
                "price_basis": row_dict.get("price_basis"),
                "buy_cost": row_dict.get("buy_cost"),
            }
            decision, reason = _decide_row(decision_input, overrides=overrides)
        except ValueError:
            # _resolve_thresholds raises ValueError on unknown override
            # keys / invariant violations. These are config bugs — we
            # want them loud, not buried per-row.
            raise
        except Exception:
            logger.exception(
                "[%s] [ROW_%s] [%s] decide error",
                row_dict.get("supplier"), idx, row_dict.get("ean"),
            )
            decision = "REVIEW"
            reason = "Decide error — manual review required"

        row_dict["decision"] = decision
        row_dict["decision_reason"] = reason
        output_rows.append(row_dict)

    return pd.DataFrame(output_rows)


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper.

    Recognised ``config`` keys:

      - ``overrides``: dict of per-call threshold overrides. Supported
        keys: ``min_sales_shortlist``, ``min_sales_review``,
        ``min_profit`` (alias ``min_profit_absolute``), ``target_roi``.
        Used by the keepa_finder strategy family — e.g.
        ``no_rank_hidden_gem`` lowers ``min_sales_shortlist`` from 20
        to 5 because no-rank ASINs have lower expected volume.
        Unknown keys raise ValueError. Default = no overrides
        (backwards-compat with every existing strategy).
    """
    overrides = config.get("overrides")
    return decide_rows(df, overrides=overrides)
