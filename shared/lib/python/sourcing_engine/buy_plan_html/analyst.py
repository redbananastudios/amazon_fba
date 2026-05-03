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
"""
from __future__ import annotations

from typing import Any, Optional


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
# Helpers — pure read + small numeric helpers.
# ────────────────────────────────────────────────────────────────────────


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n:
        return None
    return n


def _safe_get(d: dict, *path: str, default: Any = None) -> Any:
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _direction_arrow(slope: Optional[float], threshold: float = 0.003) -> str:
    """↗ / → / ↘ from a normalised slope.

    Engine's slope is normalised (mean-fraction-per-day) so values
    well below 0.01 still represent meaningful trends. Threshold of
    0.003 corresponds to ~0.3% BSR change per day = ~9% per month —
    materially trending.
    """
    if slope is None:
        return "?"
    if slope < -threshold:
        return "↗"   # negative slope = improving (BSR going down = sales up)
    if slope > threshold:
        return "↘"   # positive slope = worsening (BSR going up = sales down)
    return "→"


def _seller_arrow(joiners: Optional[float]) -> str:
    """↗ / → / ↘ from net seller joiners over 90d."""
    if joiners is None:
        return "?"
    if joiners >= 2:
        return "↗"
    if joiners <= -2:
        return "↘"
    return "→"


def _price_arrow(drop_pct: Optional[float]) -> str:
    """↗ / → / ↘ from buy_box_drop_pct_90.

    bb_drop_pct measures the magnitude of recent BB drops vs avg90.
    Higher = more recent dropping = price softening.
    """
    if drop_pct is None:
        return "?"
    if drop_pct >= 10:
        return "↘"   # price falling
    if drop_pct >= 3:
        return "→"   # mild softening
    return "→"       # stable (no down-arrow for "going up" because
                     # bb_drop measures only downside moves)


# ────────────────────────────────────────────────────────────────────────
# Trend story — 1-line synthesis of sales / sellers / price direction.
# ────────────────────────────────────────────────────────────────────────


def _build_trend_story(payload_row: dict) -> dict:
    """Return {sales_arrow, sellers_arrow, price_arrow, story_line}."""
    trends = payload_row.get("trends") or {}
    sales = _direction_arrow(trends.get("bsr_slope_90d"))
    sellers = _seller_arrow(trends.get("joiners_90d"))
    price = _price_arrow(trends.get("bb_drop_pct_90"))

    # Synthesis — combine the three arrows into a one-line read.
    if sales == "↗" and sellers != "↗" and price != "↘":
        story = "Demand rising, supply steady — entrance window."
    elif sales == "↗" and sellers == "↗":
        story = "Demand and competition both rising — race to share."
    elif sales == "↘" and sellers == "↗":
        story = "Demand falling and more sellers entering — race to bottom."
    elif sales == "↘" and price == "↘":
        story = "Sales softening and price eroding — declining listing."
    elif sales == "→" and sellers == "→" and price == "→":
        story = "Stable mature listing — no recent movement."
    elif sales == "↗":
        story = "Sales improving."
    elif sales == "↘":
        story = "Sales softening."
    elif sellers == "↘":
        story = "Sellers leaving — possibly less competition ahead."
    else:
        story = "Mixed signals; no clear trend."

    return {
        "sales_arrow": sales,
        "sellers_arrow": sellers,
        "price_arrow": price,
        "story_line": story,
    }


# ────────────────────────────────────────────────────────────────────────
# Dimension scoring — Profit / Competition / Stability / Operational
# (Q1 answer A: 4 dimensions × 25 = 100, sales NOT scored).
# ────────────────────────────────────────────────────────────────────────


def _score_profit(p: dict) -> dict:
    """0-25 — pure profit quality. Rewards real ROI + real ££ profit."""
    eco = p.get("economics") or {}
    profit = _num(eco.get("profit_per_unit_gbp"))
    roi = _num(eco.get("roi_conservative_pct"))
    target = _num(eco.get("target_buy_cost_gbp"))
    cost = _num(eco.get("buy_cost_gbp"))

    if profit is None and roi is None:
        return {
            "name": "Profit", "score": 0, "max": 25,
            "rationale": "no profit signal yet (cost or fees missing)",
        }

    pts = 0
    parts = []
    if profit is not None:
        if profit >= 8.0:
            pts += 12
            parts.append(f"£{profit:.2f}/unit (strong)")
        elif profit >= 4.0:
            pts += 9
            parts.append(f"£{profit:.2f}/unit (healthy)")
        elif profit >= 2.5:
            pts += 6
            parts.append(f"£{profit:.2f}/unit (thin)")
        elif profit > 0:
            pts += 2
            parts.append(f"£{profit:.2f}/unit (marginal)")
    if roi is not None:
        if roi >= 0.50:
            pts += 13
            parts.append(f"{roi:.0%} ROI (excellent)")
        elif roi >= 0.30:
            pts += 10
            parts.append(f"{roi:.0%} ROI (clears target)")
        elif roi >= 0.15:
            pts += 5
            parts.append(f"{roi:.0%} ROI (below target)")
        else:
            parts.append(f"{roi:.0%} ROI (weak)")
    # Add a "negotiate-headroom" note when cost is below ceiling.
    if cost is not None and target is not None and cost < target:
        headroom = (target - cost) / target
        parts.append(f"headroom {headroom:.0%}")

    return {
        "name": "Profit", "score": min(pts, 25), "max": 25,
        "rationale": "; ".join(parts) if parts else "no profit signal",
    }


def _score_competition(p: dict) -> dict:
    """0-25 — Amazon presence + seller count + joiners + BB share."""
    metrics = {m["key"]: m for m in (p.get("metrics") or [])}
    seller = metrics.get("fba_seller_count") or {}
    amz_listing = metrics.get("amazon_on_listing") or {}
    bb_share = metrics.get("amazon_bb_pct_90") or {}
    trends = p.get("trends") or {}
    joiners = _num(trends.get("joiners_90d"))

    pts = 0
    parts = []
    # Amazon-on-listing — most important for safety
    if amz_listing.get("verdict") == "green":
        pts += 8
        parts.append("Amazon not on listing")
    elif amz_listing.get("verdict") == "amber":
        pts += 4
        parts.append("Amazon presence unclear")
    # else 0 — Amazon on listing
    # FBA seller count
    if seller.get("verdict") == "green":
        pts += 6
        parts.append(f"{seller.get('value_display', '?')} FBA sellers (healthy)")
    elif seller.get("verdict") == "amber":
        pts += 3
        parts.append(f"{seller.get('value_display', '?')} FBA sellers (over ceiling)")
    # BB share
    if bb_share.get("verdict") == "green":
        pts += 6
        parts.append(f"Amazon BB {bb_share.get('value_display', '?')}")
    elif bb_share.get("verdict") == "amber":
        pts += 3
        parts.append(f"Amazon BB {bb_share.get('value_display', '?')} (rising)")
    # Joiner trend (5 points)
    if joiners is not None:
        if joiners <= 0:
            pts += 5
            parts.append("no new sellers / sellers leaving")
        elif joiners <= 2:
            pts += 3
            parts.append(f"{int(joiners)} sellers joined 90d")
        else:
            parts.append(f"{int(joiners)} sellers joined 90d (crowding)")

    return {
        "name": "Competition", "score": min(pts, 25), "max": 25,
        "rationale": "; ".join(parts) if parts else "no competition signal",
    }


def _score_stability(p: dict) -> dict:
    """0-25 — price stability + OOS + history depth."""
    metrics = {m["key"]: m for m in (p.get("metrics") or [])}
    price_vol = metrics.get("price_volatility") or {}
    trends = p.get("trends") or {}
    oos_pct = _num(trends.get("buy_box_oos_pct_90"))
    bb_drop = _num(trends.get("bb_drop_pct_90"))
    age_days = _num(trends.get("listing_age_days"))
    flags = p.get("risk_flags") or []

    pts = 0
    parts = []
    # Price stability
    if price_vol.get("verdict") == "green":
        pts += 8
        parts.append("price stable")
    elif price_vol.get("verdict") == "amber":
        pts += 4
        parts.append("price volatile")
    # OOS
    if oos_pct is not None:
        if oos_pct < 0.10:
            pts += 6
            parts.append(f"in stock {(1-oos_pct):.0%} of 90d")
        elif oos_pct < 0.25:
            pts += 3
            parts.append(f"OOS {oos_pct:.0%} of 90d")
        else:
            parts.append(f"OOS {oos_pct:.0%} of 90d (frequent)")
    # Listing age (history depth)
    if age_days is not None:
        if age_days >= 730:
            pts += 6
            parts.append("mature listing (≥ 2 yr)")
        elif age_days >= 365:
            pts += 4
            parts.append("established listing (≥ 1 yr)")
        elif age_days >= 180:
            pts += 2
            parts.append("young listing (6-12 mo)")
        else:
            parts.append("very new listing (< 6 mo)")
    # BB drop trend (5 points)
    if bb_drop is not None:
        if bb_drop < 5:
            pts += 5
            parts.append("BB price holding")
        elif bb_drop < 15:
            pts += 2
            parts.append(f"BB dropped {bb_drop:.0f}% over 90d")
        else:
            parts.append(f"BB dropped {bb_drop:.0f}% — eroding")
    # Penalise for INSUFFICIENT_HISTORY
    if "INSUFFICIENT_HISTORY" in flags:
        pts = max(0, pts - 4)
        parts.append("INSUFFICIENT_HISTORY flag")
    return {
        "name": "Stability", "score": min(pts, 25), "max": 25,
        "rationale": "; ".join(parts) if parts else "no stability signal",
    }


def _score_operational(p: dict) -> dict:
    """0-25 — gating, FBA eligibility, hazmat, restriction.

    Reads the same raw fields the engine consulted. Gating is
    not a kill but it's a real friction; surface it.
    """
    flags = p.get("risk_flags") or []
    blockers = p.get("engine_blockers") or []
    pts = 25
    parts = []
    deductions = []
    # Look for blockers / flags that imply operational friction.
    blocker_text = " ; ".join(blockers).lower()
    if "gated" in blocker_text or "brand_gated" in blocker_text:
        pts -= 8
        deductions.append("brand-gated (ungating required)")
    if "restriction_status=restricted" in blocker_text:
        pts -= 10
        deductions.append("restricted listing")
    if "size_tier_unknown" in [f.lower() for f in flags]:
        pts -= 3
        deductions.append("size tier unknown")
    if "amazon_only_price" in [f.lower() for f in flags]:
        pts -= 4
        deductions.append("Amazon-only price (BB stale)")
    if not deductions:
        parts.append("no operational frictions")
    else:
        parts.append("; ".join(deductions))
    return {
        "name": "Operational", "score": max(0, pts), "max": 25,
        "rationale": parts[0] if parts else "ungated, FBA-eligible",
    }


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
    bp = payload_row.get("buy_plan") or {}
    risk_flags = payload_row.get("risk_flags") or []

    cost = _num(eco.get("buy_cost_gbp"))
    target = _num(eco.get("target_buy_cost_gbp"))
    profit = _num(eco.get("profit_per_unit_gbp"))
    gap_gbp = _num(bp.get("gap_to_buy_gbp"))

    profit_score = next((d["score"] for d in dims if d["name"] == "Profit"), 0)
    comp_score = next((d["score"] for d in dims if d["name"] == "Competition"), 0)
    stab_score = next((d["score"] for d in dims if d["name"] == "Stability"), 0)
    op_score = next((d["score"] for d in dims if d["name"] == "Operational"), 0)
    total = profit_score + comp_score + stab_score + op_score

    has_history_flag = "INSUFFICIENT_HISTORY" in risk_flags or "LISTING_TOO_NEW" in risk_flags

    # SOURCE — no buy_cost yet
    if cost is None or cost <= 0:
        if total >= 50:
            return (
                VERDICT_SOURCE, "MEDIUM",
                f"Find a supplier; aim for ≤ £{target:.2f} inc-VAT." if target else "Find a supplier and re-run with --buy-cost.",
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
            f"Push supplier to ≤ £{target:.2f} (currently £{cost:.2f}, {gap:.0%} above ceiling).",
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


def _fallback_narrative(payload_row: dict, verdict: str, dims: list[dict], trend: dict) -> str:
    """2-3 sentence buyer's read built from the actual signals.

    Not a stand-in for an LLM — but contextual enough to be useful
    on its own. Reads the dimension scores + trend story and weaves
    them into a paragraph.
    """
    title = payload_row.get("title") or payload_row.get("asin") or "this listing"
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
        VERDICT_BUY: f"BUY signal — economics work and the chart looks healthy.",
        VERDICT_NEGOTIATE: (
            f"Currently £{cost:.2f}, ceiling £{target:.2f}; close that gap and this becomes BUY-grade."
            if cost is not None and target is not None
            else "Cost above the BUY ceiling — push the supplier down."
        ),
        VERDICT_SOURCE: (
            f"Worth sourcing — listing demand and competition look right; "
            f"target supplier at ≤ £{target:.2f} inc-VAT." if target is not None
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
        parts3.append(f"Strong on {' and '.join(d['name'].lower() for d in strong)}")
    if weak:
        parts3.append(f"weaker on {weak['name'].lower()} ({weak['rationale']})")
    s3 = (" — ".join(parts3) + ".") if parts3 else ""

    # If verdict is BUY, append the projected take.
    if verdict == VERDICT_BUY and units is not None and profit is not None:
        s3 += f" At your share, ~{int(units)} units/mo would clear ~£{units * profit:.0f}."

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
