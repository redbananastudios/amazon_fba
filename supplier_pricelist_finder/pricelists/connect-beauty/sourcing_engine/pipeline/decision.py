"""Decision engine — SHORTLIST / REVIEW / REJECT.

Step 1 change: SHORTLIST gate is now ROI-based (TARGET_ROI), not margin-based.
The margin field is still computed and shown in output for human reference,
but it does not gate decisions. Rationale: ROI = profit/buy_cost is a truer
measure of capital efficiency for a reseller.
"""
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


def decide(row: dict) -> tuple[str, str]:
    """Returns (decision, decision_reason)."""
    risk_flags = row.get("risk_flags", [])
    profit_current = row.get("profit_current", 0)
    profit_conservative = row.get("profit_conservative", 0)
    sales_estimate = row.get("sales_estimate", 0)
    gated = str(row.get("gated", "UNKNOWN")).upper()
    buy_cost = row.get("buy_cost")

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
    if sales_estimate is not None and sales_estimate < MIN_SALES_REVIEW:
        return "REJECT", f"Sales estimate {sales_estimate}/month below minimum {MIN_SALES_REVIEW}"
    if profit_current < MIN_PROFIT and profit_conservative < MIN_PROFIT:
        return "REJECT", (
            f"Unprofitable — current £{profit_current:.2f}, "
            f"conservative £{profit_conservative:.2f} (min £{MIN_PROFIT:.2f})"
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
        target_roi=TARGET_ROI,
        min_profit_absolute=MIN_PROFIT,
    )

    if not gate.passes:
        can_shortlist = False
        if gate.reason == "profit_below_floor":
            reasons.append(
                f"Conservative profit £{profit_conservative:.2f} below £{MIN_PROFIT:.2f}"
            )
        elif gate.reason == "roi_below_target":
            reasons.append(
                f"Conservative ROI {gate.roi:.1%} below target {TARGET_ROI:.0%}"
            )
        elif gate.reason == "no_buy_cost":
            reasons.append("Buy cost missing — cannot compute ROI")

    if sales_estimate is not None and sales_estimate < MIN_SALES_SHORTLIST:
        can_shortlist = False
        reasons.append(
            f"Sales {sales_estimate}/month below shortlist threshold {MIN_SALES_SHORTLIST}"
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
    review_flag_hits = [f for f in risk_flags if f in REVIEW_FLAGS]
    if review_flag_hits:
        reasons.append(f"Review flags: {', '.join(review_flag_hits)}")
    reason_str = "; ".join(reasons) if reasons else "Does not meet shortlist criteria"
    return "REVIEW", reason_str
