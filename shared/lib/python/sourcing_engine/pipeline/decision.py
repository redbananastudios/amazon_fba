"""Decision engine — SHORTLIST / REVIEW / REJECT.

Step 1 change: SHORTLIST gate is now ROI-based (TARGET_ROI), not margin-based.
The margin field is still computed and shown in output for human reference,
but it does not gate decisions. Rationale: ROI = profit/buy_cost is a truer
measure of capital efficiency for a reseller.

Per-call overrides: ``decide(row, overrides={...})`` lets a strategy
relax or tighten thresholds for one call without mutating global config
or polluting the canonical YAML. Used by the keepa_finder strategy
family — e.g. ``no_rank_hidden_gem`` lowers ``min_sales_shortlist``
from 20/mo to 5/mo because no-rank ASINs have lower expected volume.
Supported override keys (lowercase snake_case to match YAML / recipe
JSON conventions):

  - ``min_sales_shortlist``
  - ``min_sales_review``
  - ``min_profit``  (alias: ``min_profit_absolute``)
  - ``target_roi``

Unknown keys raise ValueError so a typo in a recipe / YAML is caught
loud at first run, not silently ignored.
"""
import logging
import sys
from pathlib import Path

from sourcing_engine.config import (
    MIN_PROFIT, MIN_SALES_SHORTLIST, MIN_SALES_REVIEW, TARGET_ROI,
)
from sourcing_engine.utils.flags import (
    SHORTLIST_BLOCKERS, REVIEW_FLAGS, has_any_flag,
)

# Bring the ROI gate into scope. The shim in config.py has already added
# shared/lib/python to sys.path, so a plain import works after config import.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_SHARED_LIB = _REPO_ROOT / "shared" / "lib" / "python"
if str(_SHARED_LIB) not in sys.path:
    sys.path.insert(0, str(_SHARED_LIB))

from fba_roi_gate import passes_decision_gates  # noqa: E402

logger = logging.getLogger(__name__)


# Whitelist of override keys → (default constant, type-coerce fn). New
# tunables get added here; ad-hoc keys are rejected so typos surface.
_SUPPORTED_OVERRIDES: dict[str, type] = {
    "min_sales_shortlist": int,
    "min_sales_review": int,
    "min_profit": float,
    "min_profit_absolute": float,   # alias for min_profit
    "target_roi": float,
}


def _resolve_thresholds(overrides: dict | None) -> tuple[int, int, float, float]:
    """Apply optional per-call overrides over module-level defaults.

    Returns a 4-tuple ``(min_sales_review, min_sales_shortlist,
    min_profit, target_roi)`` ready for use in ``decide``. Raises
    ``ValueError`` on unknown keys — a typo in a recipe / strategy YAML
    must fail loud at the first row processed, not silently flip a
    threshold that won't be noticed for weeks.
    """
    review = MIN_SALES_REVIEW
    shortlist = MIN_SALES_SHORTLIST
    profit = MIN_PROFIT
    roi = TARGET_ROI

    if not overrides:
        return review, shortlist, profit, roi

    unknown = set(overrides) - set(_SUPPORTED_OVERRIDES)
    if unknown:
        raise ValueError(
            f"decide overrides: unknown key(s) {sorted(unknown)}. "
            f"Supported: {sorted(_SUPPORTED_OVERRIDES)}"
        )

    if "min_sales_review" in overrides:
        review = int(overrides["min_sales_review"])
    if "min_sales_shortlist" in overrides:
        shortlist = int(overrides["min_sales_shortlist"])
    # min_profit / min_profit_absolute are aliases — last-write-wins if both set.
    if "min_profit" in overrides:
        profit = float(overrides["min_profit"])
    if "min_profit_absolute" in overrides:
        profit = float(overrides["min_profit_absolute"])
    if "target_roi" in overrides:
        roi = float(overrides["target_roi"])

    if review > shortlist:
        # Validation parity with fba_config_loader._validate. A recipe
        # that flips this invariant is a logic bug — fail loud.
        raise ValueError(
            f"decide overrides: min_sales_review ({review}) cannot exceed "
            f"min_sales_shortlist ({shortlist})"
        )

    return review, shortlist, profit, roi


def decide(row: dict, overrides: dict | None = None) -> tuple[str, str]:
    """Returns (decision, decision_reason).

    ``overrides`` (optional) — per-call threshold overrides. See module
    docstring for supported keys. Default ``None`` = use the YAML-loaded
    canonical thresholds, preserving every call site's existing
    behaviour.
    """
    risk_flags = row.get("risk_flags", [])
    profit_current = row.get("profit_current", 0)
    profit_conservative = row.get("profit_conservative", 0)
    sales_estimate = row.get("sales_estimate", 0)
    gated = str(row.get("gated", "UNKNOWN")).upper()
    buy_cost = row.get("buy_cost")

    min_sales_review, min_sales_shortlist, min_profit, target_roi = (
        _resolve_thresholds(overrides)
    )

    reasons = []

    # Gated check — flag but do not reject
    if gated == "Y":
        reasons.append("GATED — requires ungating before selling")
    if gated == "UNKNOWN":
        reasons.append("Gated status unknown")
    if "PRICE_MISMATCH_RRP" in risk_flags:
        return "REJECT", "Amazon price vs supplier RRP mismatch — likely wrong EAN"
    if "VAT_UNCLEAR" in risk_flags and buy_cost is None:
        return "REJECT", "VAT_UNCLEAR — no valid buy cost"
    if sales_estimate is not None and sales_estimate < min_sales_review:
        return "REJECT", f"Sales estimate {sales_estimate}/month below minimum {min_sales_review}"
    if profit_current < min_profit and profit_conservative < min_profit:
        return "REJECT", (
            f"Unprofitable — current £{profit_current:.2f}, "
            f"conservative £{profit_conservative:.2f} (min £{min_profit:.2f})"
        )

    # SHORTLIST checks
    can_shortlist = True

    # Two-gate check via shared module: absolute profit floor + ROI target.
    # Replaces the old (profit_conservative >= MIN_PROFIT and margin_conservative >= MIN_MARGIN)
    # combination. The MIN_PROFIT check above already catches profit < floor as REJECT;
    # here we re-check it under SHORTLIST scope so a row with profit_current >= MIN_PROFIT
    # but profit_conservative < MIN_PROFIT goes to REVIEW (not SHORTLIST), preserving
    # the previous "passes current but fails conservative" → REVIEW behaviour.
    gate = passes_decision_gates(
        profit_conservative=profit_conservative,
        buy_cost=buy_cost,
        target_roi=target_roi,
        min_profit_absolute=min_profit,
    )

    if not gate.passes:
        can_shortlist = False
        if gate.reason == "profit_below_floor":
            reasons.append(
                f"Conservative profit £{profit_conservative:.2f} below £{min_profit:.2f}"
            )
        elif gate.reason == "roi_below_target":
            reasons.append(
                f"Conservative ROI {gate.roi:.1%} below target {target_roi:.0%}"
            )
        elif gate.reason == "no_buy_cost":
            reasons.append("Buy cost missing — cannot compute ROI")

    if sales_estimate is not None and sales_estimate < min_sales_shortlist:
        can_shortlist = False
        reasons.append(
            f"Sales {sales_estimate}/month below shortlist threshold {min_sales_shortlist}"
        )

    if has_any_flag(risk_flags, SHORTLIST_BLOCKERS):
        can_shortlist = False
        blocking = [f for f in risk_flags if f in SHORTLIST_BLOCKERS]
        reasons.append(f"Blocked by: {', '.join(blocking)}")

    if can_shortlist:
        # Include gated indicator in the reason even for shortlisted items
        shortlist_reason = "Passes all thresholds at conservative price"
        if gated == "Y":
            shortlist_reason += " | GATED — requires ungating"
        elif gated == "UNKNOWN":
            shortlist_reason += " | Gated status unknown — check before buying"
        return "SHORTLIST", shortlist_reason

    # REVIEW
    # Exclude flags that already appeared in the "Blocked by:" line above.
    # Some flags (PRICE_FLOOR_HIT, VAT_*, PRICE_MISMATCH_RRP,
    # BUY_BOX_ABOVE_AVG90) are deliberately members of both SHORTLIST_BLOCKERS
    # (to prevent auto-shortlist) AND REVIEW_FLAGS (to surface in reason
    # text). Without this filter, the operator sees the flag twice in the
    # same row's decision_reason — once as "Blocked by:" and once as
    # "Review flags:" — which is noise, not signal.
    review_flag_hits = [
        f for f in risk_flags
        if f in REVIEW_FLAGS and f not in SHORTLIST_BLOCKERS
    ]
    if review_flag_hits:
        reasons.append(f"Review flags: {', '.join(review_flag_hits)}")
    reason_str = "; ".join(reasons) if reasons else "Does not meet shortlist criteria"
    return "REVIEW", reason_str
