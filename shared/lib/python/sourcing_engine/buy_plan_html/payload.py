"""Buyer-report JSON payload builder.

Pure transformation: pandas DataFrame → JSON-serialisable dict.
No I/O. Reads only existing engine columns; produces the per-row
payload spec'd in PRD §4.

Top-level shape: {schema_version, prompt_version, run_id, strategy,
supplier, generated_at, verdict_counts, rows: [...]}.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd


SCHEMA_VERSION = 1
PROMPT_VERSION = 1

VERDICTS = ("BUY", "SOURCE_ONLY", "NEGOTIATE", "WATCH", "KILL")
ACTIONABLE_VERDICTS = ("BUY", "SOURCE_ONLY", "NEGOTIATE", "WATCH")


def _is_present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and v != v:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


def _num(v: Any) -> Optional[float]:
    if not _is_present(v):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n:
        return None
    return n


def _to_list(v: Any) -> list:
    if isinstance(v, list):
        return [str(x) for x in v if x]
    if isinstance(v, str) and v.strip():
        return [s.strip() for s in v.replace(",", ";").split(";") if s.strip()]
    return []


def _public_image_url(asin: str) -> str:
    """Empirical Amazon URL pattern (PRD §4.4).

    Returns a real product image for most UK ASINs; some return a
    broken/missing image — handled by the renderer's onerror rule.
    """
    return f"https://images-na.ssl-images-amazon.com/images/P/{asin}.jpg"


def build_row_payload(row: dict) -> dict:
    """Build the JSON payload for one row. Pure function.

    Identity / verdict / economics / buy_plan blocks are populated
    from existing engine columns. Metrics traffic-light judgments
    are filled in by `_build_metrics` (Task 3).
    """
    asin = row.get("asin") or ""
    score_v = row.get("opportunity_score")
    return {
        "asin": asin,
        "title": row.get("product_name") or "",
        "brand": row.get("brand") or "",
        "supplier": row.get("supplier"),
        "supplier_sku": row.get("supplier_sku"),
        "amazon_url": row.get("amazon_url") or "",
        "image_url": _public_image_url(asin) if asin else None,

        "verdict": row.get("opportunity_verdict") or "",
        "verdict_confidence": row.get("opportunity_confidence") or "",
        "opportunity_score": int(score_v) if _is_present(score_v) else None,
        "next_action": row.get("next_action") or "",

        "economics": {
            "buy_cost_gbp": _num(row.get("buy_cost")),
            "market_price_gbp": _num(row.get("market_price")),
            "profit_per_unit_gbp": _num(row.get("profit_conservative")),
            "roi_conservative_pct": _num(row.get("roi_conservative")),
            "target_buy_cost_gbp": _num(row.get("target_buy_cost_buy")),
            "target_buy_cost_stretch_gbp": _num(row.get("target_buy_cost_stretch")),
        },

        "buy_plan": {
            "order_qty_recommended": int(row["order_qty_recommended"]) if _is_present(row.get("order_qty_recommended")) else None,
            "capital_required_gbp": _num(row.get("capital_required")),
            "projected_30d_units": int(row["projected_30d_units"]) if _is_present(row.get("projected_30d_units")) else None,
            "projected_30d_revenue_gbp": _num(row.get("projected_30d_revenue")),
            "projected_30d_profit_gbp": _num(row.get("projected_30d_profit")),
            "payback_days": _num(row.get("payback_days")),
            "gap_to_buy_gbp": _num(row.get("gap_to_buy_gbp")),
            "gap_to_buy_pct": _num(row.get("gap_to_buy_pct")),
            "buy_plan_status": row.get("buy_plan_status") or "",
        },

        "metrics": [],   # filled in Task 3

        "engine_reasons": _to_list(row.get("opportunity_reasons")),
        "engine_blockers": _to_list(row.get("opportunity_blockers")),
        "risk_flags": _to_list(row.get("risk_flags")),
    }


def build_payload(
    df: pd.DataFrame,
    *,
    run_id: str,
    strategy: str,
    supplier: Optional[str],
) -> dict:
    """Build the top-level payload dict. Filters out KILL rows.

    Returns a JSON-serialisable dict matching PRD §4.1.
    """
    counts = {v: 0 for v in VERDICTS}
    rows: list[dict] = []

    if not df.empty and "opportunity_verdict" in df.columns:
        for _, row in df.iterrows():
            d = row.to_dict()
            verdict = str(d.get("opportunity_verdict") or "").upper().strip()
            if verdict in counts:
                counts[verdict] += 1
            if verdict in ACTIONABLE_VERDICTS:
                rows.append(build_row_payload(d))

    return {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "run_id": run_id,
        "strategy": strategy,
        "supplier": supplier,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict_counts": counts,
        "rows": rows,
    }
