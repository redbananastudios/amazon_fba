"""Deterministic template-prose composer.

Used as the fallback when the engine runs without Cowork
orchestration (no LLM available). Produces a 1-3 sentence
paragraph from row payload data using fixed templates.

Determinism: same input → byte-identical output.
"""
from __future__ import annotations

from typing import Any


def _safe_get(d: dict, path: list[str], default: Any = None) -> Any:
    """Walk nested dict; return default on any missing key."""
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def render_template_prose(payload: dict) -> str:
    """Compose a fallback paragraph for one row payload.

    Routes on verdict; degrades gracefully when fields are missing.
    Never raises.
    """
    verdict = str(payload.get("verdict") or "").upper().strip()

    if verdict == "BUY":
        return _buy(payload)
    if verdict == "SOURCE_ONLY":
        return _source_only(payload)
    if verdict == "NEGOTIATE":
        return _negotiate(payload)
    if verdict == "WATCH":
        return _watch(payload)
    return f"Verdict: {verdict or 'unknown'}. See blockers and flags below."


def _buy(p: dict) -> str:
    qty = _safe_get(p, ["buy_plan", "order_qty_recommended"])
    cap = _safe_get(p, ["buy_plan", "capital_required_gbp"])
    payback = _safe_get(p, ["buy_plan", "payback_days"])
    target = _safe_get(p, ["economics", "target_buy_cost_gbp"])
    flags = p.get("risk_flags") or []
    parts: list[str] = []
    if qty is not None and cap is not None:
        parts.append(f"Order {int(qty)} units at £{cap:.2f} capital.")
    if payback is not None:
        parts.append(f"Sell-through in ~{payback:.0f} days.")
    if target is not None:
        parts.append(f"Target buy cost ≤ £{target:.2f}.")
    if flags:
        parts.append(f"Risk flags: {', '.join(flags)}.")
    else:
        parts.append("No risk flags.")
    return " ".join(parts) if parts else "BUY-grade — see economics below."


def _source_only(p: dict) -> str:
    target = _safe_get(p, ["economics", "target_buy_cost_gbp"])
    stretch = _safe_get(p, ["economics", "target_buy_cost_stretch_gbp"])
    rev = _safe_get(p, ["buy_plan", "projected_30d_revenue_gbp"])
    parts: list[str] = ["Demand looks strong but no supplier cost is on file."]
    if target is not None:
        if stretch is not None:
            parts.append(
                f"Target supplier outreach at ≤ £{target:.2f}/unit (stretch £{stretch:.2f})."
            )
        else:
            parts.append(f"Target supplier outreach at ≤ £{target:.2f}/unit.")
    if rev is not None:
        parts.append(f"At target cost, this projects ~£{rev:.0f}/mo revenue.")
    return " ".join(parts)


def _negotiate(p: dict) -> str:
    cur = _safe_get(p, ["economics", "buy_cost_gbp"])
    target = _safe_get(p, ["economics", "target_buy_cost_gbp"])
    gap_gbp = _safe_get(p, ["buy_plan", "gap_to_buy_gbp"])
    gap_pct = _safe_get(p, ["buy_plan", "gap_to_buy_pct"])
    parts: list[str] = []
    if cur is not None and target is not None:
        parts.append(
            f"Currently £{cur:.2f}; needs to come down to £{target:.2f} to clear the BUY ceiling."
        )
    if gap_gbp is not None and gap_pct is not None:
        parts.append(f"Gap: £{gap_gbp:.2f} ({gap_pct:.1%}). Worth a supplier negotiation.")
    elif gap_gbp is not None:
        parts.append(f"Gap to close: £{gap_gbp:.2f}. Worth a supplier negotiation.")
    return " ".join(parts) if parts else "NEGOTIATE — push the supplier price down."


def _watch(p: dict) -> str:
    flags = p.get("risk_flags") or []
    blockers = p.get("engine_blockers") or []
    target = _safe_get(p, ["economics", "target_buy_cost_gbp"])
    parts: list[str] = ["Not BUY-grade today; worth monitoring."]
    if blockers:
        parts.append(f"Blockers: {'; '.join(blockers[:2])}.")
    elif flags:
        parts.append(f"Risk flags: {', '.join(flags[:3])}.")
    if target is not None:
        parts.append(f"Target ceiling £{target:.2f} if economics improve.")
    return " ".join(parts)
