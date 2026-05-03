"""Analyst layer — buyer's read of one row payload.

This is the layer the user (Peter) referred to as "Claude as analyst":
the engine produces signals; the analyst reads them like a human
reading a Keepa chart and forms a verdict + score + reasoning.

Two execution paths:

1. **Cowork-orchestrated**: Cowork's agent step reads the JSON
   payload, calls Claude per row with the analyst prompt, writes
   the analyst output back into the JSON's `analyst` block. The
   engine then re-renders the HTML from the now-populated JSON.
   The engine never makes the LLM call directly.

2. **Deterministic fallback (`fallback_analyse`)**: when the
   analyst block is still null at HTML-render time (engine-alone
   runs, dev / test, no Cowork in the loop), produce a
   contextually-sensible analyst output via deterministic rules.
   The fallback is intentionally rich — it reads the actual signals
   and makes calls based on them. It's not a placeholder; it's a
   "chart-reader written as code" that handles the cases the engine
   can confidently judge from rule-based logic.

The fallback isn't a substitute for the Claude version — it can't
weigh ambiguous combinations the way an LLM can. But it produces a
useful, defensible verdict + reasoning for clear-cut cases (clear
BUY, clear SKIP) and routes uncertain cases to WAIT.

Module structure:
    analyst.py      — verdict routing + narrative + public entry (this file)
    scoring.py      — 4 dimension sub-score functions
    trend.py        — direction arrows + 1-line trend-story synthesis
    _helpers.py     — internal `_num` / `_safe_get`
"""
from __future__ import annotations

from sourcing_engine.buy_plan_html._helpers import _num
from sourcing_engine.buy_plan_html.scoring import (
    _score_competition,
    _score_operational,
    _score_profit,
    _score_stability,
)
from sourcing_engine.buy_plan_html.trend import (
    _build_trend_story,
    _direction_arrow,
    _price_arrow,
    _seller_arrow,
)


# ────────────────────────────────────────────────────────────────────────
# Verdict taxonomy (Q4 from brainstorm).
# ────────────────────────────────────────────────────────────────────────


VERDICT_BUY = "BUY"
VERDICT_NEGOTIATE = "NEGOTIATE"
VERDICT_SOURCE = "SOURCE"
VERDICT_WAIT = "WAIT"
VERDICT_SKIP = "SKIP"

ANALYST_VERDICTS = (
    VERDICT_BUY, VERDICT_NEGOTIATE, VERDICT_SOURCE, VERDICT_WAIT, VERDICT_SKIP,
)


# ────────────────────────────────────────────────────────────────────────
# Verdict logic — fallback decision rules (until Claude takes over).
# ────────────────────────────────────────────────────────────────────────


def _fallback_verdict(payload_row: dict, dims: list[dict]) -> tuple[str, str, str]:
    """Decide verdict + confidence from dimension scores + payload signals.

    Returns (verdict, confidence, action_prompt).
    The Claude-driven version replaces this entirely; this is the
    fallback when no analyst step has run.
    """
    eco = payload_row.get("economics") or {}
    risk_flags = payload_row.get("risk_flags") or []

    cost = _num(eco.get("buy_cost_gbp"))
    target = _num(eco.get("target_buy_cost_gbp"))

    profit_score = next((d["score"] for d in dims if d["name"] == "Profit"), 0)
    comp_score = next((d["score"] for d in dims if d["name"] == "Competition"), 0)
    stab_score = next((d["score"] for d in dims if d["name"] == "Stability"), 0)
    op_score = next((d["score"] for d in dims if d["name"] == "Operational"), 0)
    total = profit_score + comp_score + stab_score + op_score

    has_history_flag = (
        "INSUFFICIENT_HISTORY" in risk_flags
        or "LISTING_TOO_NEW" in risk_flags
    )

    # SOURCE — no buy_cost yet
    if cost is None or cost <= 0:
        if total >= 50:
            return (
                VERDICT_SOURCE, "MEDIUM",
                f"Find a supplier; aim for ≤ £{target:.2f} inc-VAT."
                if target else "Find a supplier and re-run with --buy-cost.",
            )
        return (
            VERDICT_WAIT, "LOW",
            "Source-only signals are weak; monitor before opening supplier outreach.",
        )

    # NEGOTIATE — has cost but above ceiling
    if target is not None and cost > target and total >= 55:
        gap = (cost - target) / cost if cost else 0
        return (
            VERDICT_NEGOTIATE, "MEDIUM",
            f"Push supplier to ≤ £{target:.2f} (currently £{cost:.2f}, "
            f"{gap:.0%} above ceiling).",
        )

    # BUY — strong total + cost ≤ ceiling
    if total >= 70 and (target is None or cost <= target) and not has_history_flag:
        return (
            VERDICT_BUY, "HIGH",
            "Place a test order at the size suggested in the buy plan.",
        )
    if total >= 60 and (target is None or cost <= target) and not has_history_flag:
        return (
            VERDICT_BUY, "MEDIUM",
            "Place a small test order; revisit after a sell-through cycle.",
        )

    # WAIT — has data flags or borderline scores
    if has_history_flag or total >= 50:
        return (
            VERDICT_WAIT, "LOW",
            "Re-check in 4 weeks; data confidence and history will firm up.",
        )

    # SKIP — bad combination
    return (
        VERDICT_SKIP, "MEDIUM",
        "Better opportunities exist; don't open this thread.",
    )


def _fallback_narrative(
    payload_row: dict, verdict: str, dims: list[dict], trend: dict,
) -> str:
    """2-3 sentence buyer's read built from the actual signals.

    Not a stand-in for an LLM — but contextual enough to be useful
    on its own. Reads the dimension scores + trend story and weaves
    them into a paragraph.
    """
    eco = payload_row.get("economics") or {}
    bp = payload_row.get("buy_plan") or {}
    cost = _num(eco.get("buy_cost_gbp"))
    target = _num(eco.get("target_buy_cost_gbp"))
    profit = _num(eco.get("profit_per_unit_gbp"))
    units = _num(bp.get("projected_30d_units"))

    # Pick the 2 strongest + 1 weakest dimension for the narrative.
    sorted_dims = sorted(dims, key=lambda d: -d["score"])
    strong = [d for d in sorted_dims if d["score"] >= d["max"] * 0.6][:2]
    weak = next((d for d in sorted_dims if d["score"] < d["max"] * 0.5), None)

    # Sentence 1: lead with verdict + 1-line reason.
    s1_map = {
        VERDICT_BUY: "BUY signal — economics work and the chart looks healthy.",
        VERDICT_NEGOTIATE: (
            f"Currently £{cost:.2f}, ceiling £{target:.2f}; close that gap "
            f"and this becomes BUY-grade."
            if cost is not None and target is not None
            else "Cost above the BUY ceiling — push the supplier down."
        ),
        VERDICT_SOURCE: (
            f"Worth sourcing — listing demand and competition look right; "
            f"target supplier at ≤ £{target:.2f} inc-VAT."
            if target is not None
            else "Demand looks workable; find a supplier and price-check."
        ),
        VERDICT_WAIT: "Not actionable today, but worth monitoring.",
        VERDICT_SKIP: "Skip — the story doesn't justify the time.",
    }
    s1 = s1_map.get(verdict, "")

    # Sentence 2: trend story.
    s2 = trend.get("story_line") or ""

    # Sentence 3: highlight a strength + a concern.
    parts3 = []
    if strong:
        parts3.append(
            f"Strong on {' and '.join(d['name'].lower() for d in strong)}"
        )
    if weak:
        parts3.append(
            f"weaker on {weak['name'].lower()} ({weak['rationale']})"
        )
    s3 = (" — ".join(parts3) + ".") if parts3 else ""

    # If verdict is BUY, append the projected take.
    if verdict == VERDICT_BUY and units is not None and profit is not None:
        s3 += (
            f" At your share, ~{int(units)} units/mo would clear "
            f"~£{units * profit:.0f}."
        )

    return " ".join(s for s in [s1, s2, s3] if s).strip()


# ────────────────────────────────────────────────────────────────────────
# Public entry point.
# ────────────────────────────────────────────────────────────────────────


def fallback_analyse(payload_row: dict) -> dict:
    """Compose the analyst block deterministically from the payload.

    Used when no Cowork orchestration runs (engine-alone runs) or
    when Cowork hasn't yet populated the analyst block. Returns a
    dict matching the `analyst` block shape in payload.py.
    """
    dims = [
        _score_profit(payload_row),
        _score_competition(payload_row),
        _score_stability(payload_row),
        _score_operational(payload_row),
    ]
    total_score = sum(d["score"] for d in dims)
    trend = _build_trend_story(payload_row)
    verdict, confidence, action = _fallback_verdict(payload_row, dims)
    narrative = _fallback_narrative(payload_row, verdict, dims, trend)
    return {
        "verdict": verdict,
        "verdict_confidence": confidence,
        "score": total_score,
        "dimensions": dims,
        "trend_arrows": {
            "sales": trend["sales_arrow"],
            "sellers": trend["sellers_arrow"],
            "price": trend["price_arrow"],
        },
        "trend_story": trend["story_line"],
        "narrative": narrative,
        "action_prompt": action,
    }
