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

        "metrics": _build_metrics(row),

        "engine_reasons": _to_list(row.get("opportunity_reasons")),
        "engine_blockers": _to_list(row.get("opportunity_blockers")),
        "risk_flags": _to_list(row.get("risk_flags")),
    }


# ────────────────────────────────────────────────────────────────────────
# Traffic-light metric judgments (PRD §4.3)
# ────────────────────────────────────────────────────────────────────────


def _grey(key: str, label: str) -> dict:
    """Signal-missing entry — grey traffic light."""
    return {
        "key": key, "label": label, "value_display": "—",
        "verdict": "grey", "rationale": "signal missing",
    }


def _judge_fba_seller_count(row: dict) -> dict:
    from fba_config_loader import get_opportunity_validation
    cfg = get_opportunity_validation()
    fba = _num(row.get("fba_seller_count"))
    sales = _num(row.get("sales_estimate")) or 0
    if fba is None:
        return _grey("fba_seller_count", "FBA Sellers")
    if sales >= 200:
        ceiling = cfg.max_fba_sellers_200_sales
    elif sales >= 100:
        ceiling = cfg.max_fba_sellers_100_sales
    else:
        ceiling = cfg.max_fba_sellers_low_sales
    amber_top = ceiling * 1.5
    if fba <= ceiling:
        return {
            "key": "fba_seller_count", "label": "FBA Sellers",
            "value_display": str(int(fba)), "verdict": "green",
            "rationale": f"≤ {int(ceiling)} ceiling at this volume",
        }
    if fba <= amber_top:
        return {
            "key": "fba_seller_count", "label": "FBA Sellers",
            "value_display": str(int(fba)), "verdict": "amber",
            "rationale": f"over {int(ceiling)} ceiling but within 50%",
        }
    return {
        "key": "fba_seller_count", "label": "FBA Sellers",
        "value_display": str(int(fba)), "verdict": "red",
        "rationale": f"far above {int(ceiling)} ceiling",
    }


def _judge_amazon_on_listing(row: dict) -> dict:
    raw = row.get("amazon_on_listing")
    s = str(raw or "").upper().strip()
    if s == "Y":
        return {
            "key": "amazon_on_listing", "label": "Amazon on Listing",
            "value_display": "Yes", "verdict": "red",
            "rationale": "Amazon competes on the Buy Box",
        }
    if s == "UNKNOWN":
        return {
            "key": "amazon_on_listing", "label": "Amazon on Listing",
            "value_display": "Unknown", "verdict": "amber",
            "rationale": "Amazon-on-listing status unverified",
        }
    return {
        "key": "amazon_on_listing", "label": "Amazon on Listing",
        "value_display": "No", "verdict": "green",
        "rationale": "Buy Box rotation safe",
    }


def _judge_amazon_bb_share(row: dict) -> dict:
    from fba_config_loader import get_opportunity_validation
    cfg = get_opportunity_validation()
    bb = _num(row.get("amazon_bb_pct_90"))
    if bb is None:
        return _grey("amazon_bb_pct_90", "Amazon BB Share 90d")
    pct_str = f"{bb:.0%}"
    if bb < cfg.max_amazon_bb_share_buy:
        return {
            "key": "amazon_bb_pct_90", "label": "Amazon BB Share 90d",
            "value_display": pct_str, "verdict": "green",
            "rationale": f"below {cfg.max_amazon_bb_share_buy:.0%} buy threshold",
        }
    if bb < cfg.max_amazon_bb_share_watch:
        return {
            "key": "amazon_bb_pct_90", "label": "Amazon BB Share 90d",
            "value_display": pct_str, "verdict": "amber",
            "rationale": "between buy and watch thresholds",
        }
    return {
        "key": "amazon_bb_pct_90", "label": "Amazon BB Share 90d",
        "value_display": pct_str, "verdict": "red",
        "rationale": f"≥ {cfg.max_amazon_bb_share_watch:.0%} — Amazon dominates",
    }


def _judge_price_volatility(row: dict) -> dict:
    from fba_config_loader import get_opportunity_validation
    cfg = get_opportunity_validation()
    vol = _num(row.get("price_volatility_90d"))
    if vol is None:
        return _grey("price_volatility", "Price Consistency")
    val = f"{vol:.2f}"
    if vol < cfg.max_price_volatility_buy:
        return {
            "key": "price_volatility", "label": "Price Consistency",
            "value_display": val, "verdict": "green",
            "rationale": f"stable (< {cfg.max_price_volatility_buy:.2f} cap)",
        }
    if vol < cfg.kill_price_volatility:
        return {
            "key": "price_volatility", "label": "Price Consistency",
            "value_display": val, "verdict": "amber",
            "rationale": "moderate volatility",
        }
    return {
        "key": "price_volatility", "label": "Price Consistency",
        "value_display": val, "verdict": "red",
        "rationale": f"≥ {cfg.kill_price_volatility:.2f} — severe volatility",
    }


def _judge_sales_estimate(row: dict) -> dict:
    from fba_config_loader import get_opportunity_validation
    cfg = get_opportunity_validation()
    sales = _num(row.get("sales_estimate"))
    label = "Listing Sales/mo"
    if sales is None:
        return _grey("sales_estimate", label)
    val = f"{int(sales)}"
    # Traffic-light boundaries are operator-visual cues, independent of
    # the kill_min_sales engine gate (which the operator may have set to
    # 0 to disable auto-KILL on volume). 20/mo is the "very low" mark
    # where a buyer should pause regardless of engine config.
    LOW_VOLUME_RED = 20.0
    if sales >= cfg.target_monthly_sales:
        return {
            "key": "sales_estimate", "label": label,
            "value_display": val, "verdict": "green",
            "rationale": f"strong listing demand (≥ {cfg.target_monthly_sales}/mo target)",
        }
    if sales >= LOW_VOLUME_RED:
        return {
            "key": "sales_estimate", "label": label,
            "value_display": val, "verdict": "amber",
            "rationale": f"moderate listing demand (under {cfg.target_monthly_sales}/mo target)",
        }
    return {
        "key": "sales_estimate", "label": label,
        "value_display": val, "verdict": "red",
        "rationale": f"very low listing demand (< {int(LOW_VOLUME_RED)}/mo)",
    }


def _judge_predicted_velocity(row: dict) -> dict:
    sales = _num(row.get("sales_estimate"))
    bb = _num(row.get("amazon_bb_pct_90"))
    mid = _num(row.get("predicted_velocity_mid"))
    label = "Your Share/mo"
    if sales is None or bb is None or mid is None:
        return _grey("predicted_velocity", label)
    non_amazon_share = sales * (1 - bb)
    if non_amazon_share <= 0:
        return _grey("predicted_velocity", label)
    val = f"{int(mid)} /mo"
    pct = mid / non_amazon_share if non_amazon_share > 0 else 0
    if mid >= 0.5 * non_amazon_share:
        return {
            "key": "predicted_velocity", "label": label,
            "value_display": val, "verdict": "green",
            "rationale": f"~{pct:.0%} of {int(non_amazon_share)} non-Amazon sales — strong slice",
        }
    if mid >= 0.25 * non_amazon_share:
        return {
            "key": "predicted_velocity", "label": label,
            "value_display": val, "verdict": "amber",
            "rationale": f"~{pct:.0%} of {int(non_amazon_share)} non-Amazon sales — mid-tier slice",
        }
    return {
        "key": "predicted_velocity", "label": label,
        "value_display": val, "verdict": "red",
        "rationale": f"~{pct:.0%} of {int(non_amazon_share)} non-Amazon sales — small slice",
    }


def _judge_bsr_drops(row: dict) -> dict:
    """BSR drops = sales-event count Amazon's BSR registered in 30 days.
    Higher number = more frequent sales activity = healthier listing.
    """
    drops = _num(row.get("bsr_drops_30d"))
    sales = _num(row.get("sales_estimate")) or 0
    label = "Sales Activity (30d)"
    if drops is None:
        return _grey("bsr_drops_30d", label)
    val = f"{int(drops)} sales"
    green_floor = max(20.0, sales * 0.5)
    amber_floor = max(10.0, sales * 0.25)
    if drops >= green_floor:
        return {
            "key": "bsr_drops_30d", "label": label,
            "value_display": val, "verdict": "green",
            "rationale": f"frequent sales (≥ {int(green_floor)} BSR drops/30d)",
        }
    if drops >= amber_floor:
        return {
            "key": "bsr_drops_30d", "label": label,
            "value_display": val, "verdict": "amber",
            "rationale": f"moderate sales (≥ {int(amber_floor)} BSR drops/30d)",
        }
    return {
        "key": "bsr_drops_30d", "label": label,
        "value_display": val, "verdict": "red",
        "rationale": f"slow listing (< {int(amber_floor)} BSR drops/30d)",
    }


def _build_metrics(row: dict) -> list[dict]:
    """Compose the 7 traffic-light metric entries per PRD §4.3.

    Order is contractual — tests pin it.
    """
    return [
        _judge_fba_seller_count(row),
        _judge_amazon_on_listing(row),
        _judge_amazon_bb_share(row),
        _judge_price_volatility(row),
        _judge_sales_estimate(row),
        _judge_predicted_velocity(row),
        _judge_bsr_drops(row),
    ]


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
