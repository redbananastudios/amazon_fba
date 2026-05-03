"""08_buy_plan — verdict-driven order-list rollup.

Runs after ``07_validate_opportunity`` and before any output writer.
Pure additive: appends 11 columns per row but never mutates an
upstream column. The step is a transformation, not an enrichment —
every input it needs is already on the row by the time
validate_opportunity finishes.

Output columns (see ``sourcing_engine.buy_plan.BUY_PLAN_COLUMNS``):
    order_qty_recommended, capital_required, projected_30d_units,
    projected_30d_revenue, projected_30d_profit, payback_days,
    target_buy_cost_buy, target_buy_cost_stretch, gap_to_buy_gbp,
    gap_to_buy_pct, buy_plan_status

Core logic in ``shared/lib/python/sourcing_engine/buy_plan.py`` so
all strategies share the same rules. This module is the runner-
compatible wrapper.

Per-row exception → log + ``buy_plan_status = INSUFFICIENT_DATA`` +
continue. Mirrors the existing ``validate_opportunity`` pattern:
the pipeline must not abort on a single bad row.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from sourcing_engine.buy_plan import (
    BUY_PLAN_COLUMNS,
    STATUS_INSUFFICIENT_DATA,
    compute_buy_plan,
)

logger = logging.getLogger(__name__)


# Re-export for callers that want the column tuple.
__all__ = ["BUY_PLAN_COLUMNS", "add_buy_plan", "run_step"]


def _empty_row_result() -> dict:
    """Per-row error fallback — every numeric blank, status set."""
    out = {col: None for col in BUY_PLAN_COLUMNS}
    out["buy_plan_status"] = STATUS_INSUFFICIENT_DATA
    return out


def add_buy_plan(
    df: pd.DataFrame,
    config: Any | None = None,
    opportunity_validation: Any | None = None,
    order_mode: str = "first",
) -> pd.DataFrame:
    """Append the 11 buy-plan columns to every row in ``df``.

    Pure-function semantics — does not mutate the input. Per-row
    exceptions are caught and logged; the row gets
    ``buy_plan_status = INSUFFICIENT_DATA`` and the run continues.

    Args:
        df: validated DataFrame (post-validate_opportunity).
        config: optional ``BuyPlan`` (defaults to ``get_buy_plan()``).
        opportunity_validation: optional ``OpportunityValidation``
            (defaults to ``get_opportunity_validation()``).
        order_mode: ``"first"`` (default) or ``"reorder"``.
    """
    if df.empty:
        out = df.copy()
        for col in BUY_PLAN_COLUMNS:
            out[col] = pd.Series(dtype=object)
        return out

    rows = []
    for _, row in df.iterrows():
        d = row.to_dict()
        try:
            d.update(
                compute_buy_plan(
                    d,
                    config=config,
                    opportunity_validation=opportunity_validation,
                    order_mode=order_mode,
                )
            )
        except Exception:
            logger.exception(
                "buy_plan: failed on row asin=%s — defaulting to INSUFFICIENT_DATA",
                d.get("asin"),
            )
            d.update(_empty_row_result())
        rows.append(d)
    return pd.DataFrame(rows)


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper.

    Reads ``order_mode`` from ``config["context"]`` (forwarded by the
    runner's interpolation) or directly from ``config["order_mode"]``.
    Default ``"first"``.

    YAML wiring example::

        - name: buy_plan
          module: fba_engine.steps.buy_plan
          config:
            order_mode: "{order_mode}"

    Thresholds always come from ``decision_thresholds.yaml::buy_plan``.
    The runner only forwards the per-run mode toggle.
    """
    order_mode = _resolve_order_mode(config)
    return add_buy_plan(df, order_mode=order_mode)


def _resolve_order_mode(config: dict[str, Any]) -> str:
    """Pick ``order_mode`` from config in a forgiving way.

    Accepts either ``config["order_mode"]`` (direct) or the runner's
    interpolated string. Anything other than ``"reorder"`` (case-
    insensitive) collapses to ``"first"`` — the conservative default
    matches the operator's bias toward small first orders on
    untested ASINs.
    """
    raw = config.get("order_mode")
    if not raw:
        return "first"
    val = str(raw).strip().lower()
    if val == "reorder":
        return "reorder"
    return "first"
