"""Dimension scoring — Profit / Competition / Stability / Operational.

Per the v2 spec (Q1 answer A from the brainstorm), the analyst score
is the sum of 4 dimension sub-scores, each capped at 25 → 0-100 total.
**Sales volume is intentionally NOT a dimension** — high-profit-low-
volume items shouldn't be penalised; the operator filters by velocity
manually after seeing the report.

Each `_score_*` function returns:

    {"name": <dimension>, "score": int 0..max, "max": 25, "rationale": str}

Pure functions; no I/O. Consumed by `analyst.fallback_analyse`.
"""
from __future__ import annotations

from sourcing_engine.buy_plan_html._helpers import _num


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
    # BB drop trend (5 points) — bb_drop is in raw percent (engine
    # stores as fraction; payload._bb_drop_pct converts at boundary).
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
