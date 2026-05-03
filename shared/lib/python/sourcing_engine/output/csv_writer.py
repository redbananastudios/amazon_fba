"""CSV output — all rows, all decisions, full schema.

The CSV is the operator's audit trail. Every column the engine
produces should be visible here so the operator can verify what the
analyst layer is reading from. The 2026-05-03 decision-data audit
(scripts/validate_decision_data.py) revealed this writer was
silently dropping ~30 populated columns — operators saw blanks for
fields the engine had computed and the buyer report was using.
This is the fix: expose everything.
"""
import json
import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Order matters: identity → economics → decision → trend signals →
# scoring → final verdict → SP-API → buy_plan → ungate → audit.
# Operators reading the CSV scan left-to-right; group by purpose.
OUTPUT_COLUMNS = [
    # ── Identity ────────────────────────────────────────────────
    "supplier", "supplier_sku", "ean", "case_ean", "asin", "amazon_url",
    "product_name", "match_type", "parent_asin", "category_root",
    "size_tier", "package_weight_g", "package_volume_cm3",

    # ── Supplier economics ──────────────────────────────────────
    "supplier_price_basis", "case_qty", "moq",
    "unit_cost_ex_vat", "unit_cost_inc_vat",
    "case_cost_ex_vat", "case_cost_inc_vat", "buy_cost",

    # ── Market prices (Keepa + SP-API live) ─────────────────────
    "buy_box_price", "new_fba_price", "amazon_price", "amazon_status",
    "fba_seller_count", "buy_box_avg30", "buy_box_avg90",
    "buy_box_min_365d", "price_history_basis",
    "market_price", "price_basis",
    "raw_conservative_price", "floored_conservative_price",

    # ── Computed economics ──────────────────────────────────────
    "fees_current", "fees_conservative",
    "fba_pick_pack_fee", "referral_fee_pct",
    "profit_current", "profit_conservative",
    "margin_current", "margin_conservative",
    "roi_current", "roi_conservative",
    "breakeven_price", "max_buy_price", "capital_exposure",

    # ── Demand & trend signals (analyst-facing) ─────────────────
    "sales_estimate", "sales_rank", "sales_rank_avg90", "sales_rank_cv_90d",
    "bsr_slope_30d", "bsr_slope_90d", "bsr_slope_365d",
    "bsr_drops_30d", "yoy_bsr_ratio",
    "fba_offer_count_90d_start", "fba_offer_count_90d_joiners",
    "buy_box_oos_pct_90", "price_volatility_90d", "buy_box_drop_pct_90",
    "amazon_bb_pct_90",
    "listing_age_days", "history_days",

    # ── Quality signals ─────────────────────────────────────────
    "rating", "review_count", "review_velocity_90d",
    "catalog_image_count", "catalog_has_aplus_content", "catalog_release_date",
    "variation_count",

    # ── First-pass decision (SHORTLIST/REVIEW/REJECT) ───────────
    "gated", "decision", "decision_reason", "risk_flags",

    # ── Candidate score (data-driven 0-100) ─────────────────────
    "candidate_score", "candidate_band", "candidate_reasons",
    "data_confidence", "data_confidence_reasons",
    "stability_score",

    # ── Final operator verdict ──────────────────────────────────
    "opportunity_verdict", "opportunity_score", "opportunity_confidence",
    "opportunity_reasons", "opportunity_blockers", "next_action",

    # ── Predicted velocity (per-seller share model) ─────────────
    "predicted_velocity_low", "predicted_velocity_mid", "predicted_velocity_high",
    "predicted_velocity_share_source", "buy_box_seller_stats",

    # ── SP-API preflight ────────────────────────────────────────
    "restriction_status", "restriction_reasons", "restriction_links",
    "fba_eligible", "fba_ineligibility",
    "live_buy_box", "live_buy_box_seller",
    "live_offer_count_new", "live_offer_count_fba",
    "catalog_brand", "keepa_brand", "catalog_hazmat",
    "preflight_errors",

    # ── Ungate workflow (operator-filled) ───────────────────────
    "ungate_status", "ungate_required_docs", "ungate_brand_required",
    "ungate_attempted_at", "ungate_message",

    # ── Buy plan ────────────────────────────────────────────────
    "order_qty_recommended", "capital_required",
    "projected_30d_units", "projected_30d_revenue", "projected_30d_profit",
    "payback_days",
    "target_buy_cost_buy", "target_buy_cost_stretch",
    "gap_to_buy_gbp", "gap_to_buy_pct",
    "buy_plan_status",
]

# Columns that may carry a list — joined with "; " for CSV.
_LIST_COLUMNS: tuple[str, ...] = (
    "risk_flags",
    "candidate_reasons", "data_confidence_reasons",
    "opportunity_reasons", "opportunity_blockers",
    "restriction_reasons", "restriction_links",
)
# Columns that may carry a dict — JSON-stringified so the operator
# can see the structure (per-seller BB stats etc.) without crashing
# pandas's CSV writer.
_DICT_COLUMNS: tuple[str, ...] = ("buy_box_seller_stats",)


def _serialize_list_cell(v):
    if isinstance(v, list):
        return "; ".join(str(x) for x in v)
    return v


def _serialize_dict_cell(v):
    if isinstance(v, dict):
        try:
            return json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(v)
    return v


def write_csv(df: pd.DataFrame, path: str):
    try:
        cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
        out = df[cols].copy()
        for col in _LIST_COLUMNS:
            if col in out.columns:
                out[col] = out[col].apply(_serialize_list_cell)
        for col in _DICT_COLUMNS:
            if col in out.columns:
                out[col] = out[col].apply(_serialize_dict_cell)
        out.to_csv(path, index=False)
        logger.info("CSV written: %s (%d rows, %d cols)", path, len(out), len(cols))
    except Exception:
        logger.exception("Failed to write CSV: %s", path)
