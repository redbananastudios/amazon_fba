"""CSV output — all rows, all decisions, full schema."""
import logging
import pandas as pd

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = [
    "supplier", "supplier_sku", "ean", "case_ean", "asin", "amazon_url", "product_name", "match_type",
    "supplier_price_basis", "case_qty", "unit_cost_ex_vat", "unit_cost_inc_vat",
    "case_cost_ex_vat", "case_cost_inc_vat", "buy_cost", "market_price",
    "raw_conservative_price", "floored_conservative_price", "price_basis",
    "fees_current", "fees_conservative", "profit_current", "profit_conservative",
    "margin_current", "margin_conservative", "sales_estimate", "max_buy_price",
    "capital_exposure", "size_tier", "history_days", "gated",
    "decision", "decision_reason", "risk_flags",
    # SP-API preflight columns (informational only — appended at end per spec).
    # Populated when MCP CLI is built and SP_API creds are set; None otherwise.
    "restriction_status", "restriction_reasons", "restriction_links",
    "fba_eligible", "fba_ineligibility",
    "live_buy_box", "live_buy_box_seller",
    "live_offer_count_new", "live_offer_count_fba",
    "catalog_brand", "keepa_brand", "catalog_hazmat",
    "preflight_errors",
]


def write_csv(df: pd.DataFrame, path: str):
    try:
        cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
        out = df[cols].copy()
        if "risk_flags" in out.columns:
            out["risk_flags"] = out["risk_flags"].apply(
                lambda x: "; ".join(x) if isinstance(x, list) else str(x))
        out.to_csv(path, index=False)
        logger.info("CSV written: %s (%d rows)", path, len(out))
    except Exception:
        logger.exception("Failed to write CSV: %s", path)
