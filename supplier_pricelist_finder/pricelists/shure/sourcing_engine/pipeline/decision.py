"""Decision engine — SHORTLIST / REVIEW / REJECT. Implements PRD section 3.10."""
from sourcing_engine.config import MIN_PROFIT, MIN_MARGIN, MIN_SALES_SHORTLIST, MIN_SALES_REVIEW
from sourcing_engine.utils.flags import SHORTLIST_BLOCKERS, REVIEW_FLAGS, has_any_flag


def decide(row: dict) -> tuple[str, str]:
    """Returns (decision, decision_reason)."""
    risk_flags = row.get("risk_flags", [])
    profit_current = row.get("profit_current", 0)
    profit_conservative = row.get("profit_conservative", 0)
    margin_conservative = row.get("margin_conservative", 0)
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
        return "REJECT", f"Unprofitable — current £{profit_current:.2f}, conservative £{profit_conservative:.2f} (min £{MIN_PROFIT:.2f})"

    # SHORTLIST checks
    can_shortlist = True
    if profit_conservative < MIN_PROFIT:
        can_shortlist = False
        reasons.append(f"Conservative profit £{profit_conservative:.2f} below £{MIN_PROFIT:.2f}")
    if margin_conservative < MIN_MARGIN:
        can_shortlist = False
        reasons.append(f"Conservative margin {margin_conservative:.1%} below {MIN_MARGIN:.0%}")
    if sales_estimate is not None and sales_estimate < MIN_SALES_SHORTLIST:
        can_shortlist = False
        reasons.append(f"Sales {sales_estimate}/month below shortlist threshold {MIN_SALES_SHORTLIST}")
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
