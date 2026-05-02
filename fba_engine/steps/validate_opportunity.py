"""07_validate_opportunity — final operator-facing verdict per row.

Runs after candidate_score + decide and before output. Pure additive:
appends 6 columns to every row but never mutates the SHORTLIST/REVIEW/
REJECT decision.

Output columns:
  - opportunity_verdict      BUY | SOURCE_ONLY | NEGOTIATE | WATCH | KILL
  - opportunity_score        0-100 (independent of verdict)
  - opportunity_confidence   HIGH | MEDIUM | LOW (input-presence based)
  - opportunity_reasons      list[str] short contributors
  - opportunity_blockers     list[str] (KILL reasons or BUY blockers)
  - next_action              operator playbook string per verdict

Core logic lives in
``shared/lib/python/sourcing_engine/opportunity.py`` so both
``supplier_pricelist`` and ``keepa_niche`` strategies share the
same rules. This module is the runner-compatible wrapper.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from sourcing_engine.opportunity import (
    NEXT_ACTIONS,
    VERDICT_KILL,
    validate_opportunity,
)

logger = logging.getLogger(__name__)


OPPORTUNITY_COLUMNS: tuple[str, ...] = (
    "opportunity_verdict",
    "opportunity_score",
    "opportunity_confidence",
    "opportunity_reasons",
    "opportunity_blockers",
    "next_action",
)


def add_opportunity_verdict(df: pd.DataFrame) -> pd.DataFrame:
    """Append the 6 opportunity columns to every row in `df`.

    Pure-function semantics — does not mutate the input. REJECT rows
    are scored too (they always become KILL); the operator may still
    want to see why, and the next_action makes the disposition
    explicit on the output.
    """
    if df.empty:
        out = df.copy()
        for col in OPPORTUNITY_COLUMNS:
            out[col] = pd.Series(dtype=object)
        return out

    rows = []
    for _, row in df.iterrows():
        d = row.to_dict()
        try:
            d.update(validate_opportunity(d))
        except Exception:
            # Never crash the pipeline on a bad row — surface as KILL
            # with a generic reason. Mirrors the existing calculate.py
            # error-handling pattern (catch + flag REVIEW + continue).
            logger.exception(
                "validate_opportunity: failed on row asin=%s — defaulting to KILL",
                d.get("asin"),
            )
            d.update({
                "opportunity_verdict": VERDICT_KILL,
                "opportunity_score": 0,
                "opportunity_confidence": "LOW",
                "opportunity_reasons": [],
                "opportunity_blockers": ["validate_opportunity error — see logs"],
                "next_action": NEXT_ACTIONS[VERDICT_KILL],
            })
        rows.append(d)
    return pd.DataFrame(rows)


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper.

    No config keys consumed — thresholds live in
    ``decision_thresholds.yaml::opportunity_validation``. Keeps the
    signature consistent with other steps in the canonical engine.
    """
    return add_opportunity_verdict(df)
