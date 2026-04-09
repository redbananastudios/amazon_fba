"""EAN -> ASIN matching. In v1 this is a lookup against pre-loaded market data."""
import logging
from sourcing_engine.utils.flags import CASE_MATCH_SKIPPED, CASE_QTY_UNKNOWN

logger = logging.getLogger(__name__)


def match_product(row: dict, market_data: dict) -> list[dict]:
    """Match a normalised row against market data. Returns 0, 1, or 2 match dicts."""
    matches = []
    ean = row.get("ean")
    risk_flags = list(row.get("risk_flags", []))
    if not ean:
        return []

    # Unit match
    unit_data = market_data.get(ean)
    if unit_data:
        match = _build_match(row, unit_data, "UNIT")
        if match:
            matches.append(match)

    # Case match
    case_qty = row.get("case_qty", 1)
    price_basis = row.get("supplier_price_basis", "UNIT")
    skip_case = False
    if price_basis == "AMBIGUOUS":
        skip_case = True
        risk_flags.append(CASE_MATCH_SKIPPED)
    if case_qty is None or case_qty <= 1:
        skip_case = True
    if CASE_QTY_UNKNOWN in risk_flags:
        skip_case = True
        if CASE_MATCH_SKIPPED not in risk_flags:
            risk_flags.append(CASE_MATCH_SKIPPED)

    if not skip_case:
        case_ean = row.get("case_ean") or ean
        case_data = market_data.get(f"{ean}_case") or market_data.get(case_ean)
        if case_data and case_data.get("asin") != (unit_data or {}).get("asin"):
            match = _build_match(row, case_data, "CASE")
            if match:
                matches.append(match)

    for m in matches:
        m["risk_flags"] = list(set(m.get("risk_flags", []) + risk_flags))
    return matches


def _build_match(row, market_row, match_type):
    buy_cost = row.get("unit_cost_inc_vat") if match_type == "UNIT" else row.get("case_cost_inc_vat")
    if buy_cost is None:
        return None
    return {
        "supplier": row.get("supplier"), "source_file": row.get("source_file"),
        "supplier_sku": row.get("supplier_sku"), "ean": row.get("ean"),
        "case_ean": row.get("case_ean") if match_type == "CASE" else None,
        "product_name": market_row.get("title") or row.get("product_name"),
        "match_type": match_type,
        "supplier_price_basis": row.get("supplier_price_basis"),
        "case_qty": row.get("case_qty", 1),
        "unit_cost_ex_vat": row.get("unit_cost_ex_vat"),
        "unit_cost_inc_vat": row.get("unit_cost_inc_vat"),
        "case_cost_ex_vat": row.get("case_cost_ex_vat"),
        "case_cost_inc_vat": row.get("case_cost_inc_vat"),
        "buy_cost": buy_cost, "rrp_inc_vat": row.get("rrp_inc_vat"),
        "moq": row.get("moq", 1),
        "asin": market_row.get("asin"),
        "amazon_url": f"https://www.amazon.co.uk/dp/{market_row.get('asin')}" if market_row.get("asin") else None,
        "brand": market_row.get("brand") or row.get("brand"),
        "buy_box_price": market_row.get("buy_box_price"),
        "amazon_price": market_row.get("amazon_price"),
        "new_fba_price": market_row.get("new_fba_price"),
        "amazon_status": market_row.get("amazon_status"),
        "fba_seller_count": market_row.get("fba_seller_count"),
        "sales_estimate": market_row.get("monthly_sales_estimate"),
        "price_history": market_row.get("price_history"),
        "history_days": market_row.get("history_days"),
        "size_tier": market_row.get("size_tier"),
        "gated": market_row.get("gated", "UNKNOWN"),
        "fba_pick_pack_fee": market_row.get("fba_pick_pack_fee"),
        "referral_fee_pct": market_row.get("referral_fee_pct"),
        "risk_flags": list(row.get("risk_flags", [])),
    }
