"""Schema normalisation and VAT resolution for supplier price lists.

Handles multiple supplier formats:
  - Abgee PDFs: trade_price is ex-VAT, no inc-VAT column
  - CSV imports: trade_price may be ex-VAT or inc-VAT depending on supplier
    (Shure Cosmetics: price is ex-VAT)
  - Connect Beauty CSV: "Unit Price (GBP)" is per-unit ex-VAT,
    "Case Price (GBP)" is total case ex-VAT, "Case Size" is numeric qty
"""
import logging
import re

import pandas as pd

from sourcing_engine.config import VAT_RATE, VAT_MISMATCH_TOLERANCE
from sourcing_engine.utils.ean_validator import validate_ean
from sourcing_engine.utils.flags import VAT_FIELD_MISMATCH, VAT_UNCLEAR

logger = logging.getLogger(__name__)


def resolve_buy_cost(
    cost_ex_vat: float | None,
    cost_inc_vat: float | None,
    vat_rate: float = VAT_RATE,
    tolerance: float = VAT_MISMATCH_TOLERANCE,
) -> tuple[float | None, str | None]:
    """Resolve buy cost from supplier VAT fields.
    Returns (buy_cost, flag_or_none).
    See PRD section 2.4 for the four states.
    """
    has_ex = cost_ex_vat is not None
    has_inc = cost_inc_vat is not None

    if has_ex and has_inc:
        expected_inc = cost_ex_vat * (1 + vat_rate)
        if abs(cost_inc_vat - expected_inc) > tolerance:
            return cost_inc_vat, VAT_FIELD_MISMATCH
        return cost_inc_vat, None

    if has_inc and not has_ex:
        return cost_inc_vat, None

    if has_ex and not has_inc:
        return cost_ex_vat * (1 + vat_rate), None

    return None, VAT_UNCLEAR


def normalise(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a raw DataFrame into the standard schema."""
    if raw_df.empty:
        return pd.DataFrame()

    rows = []
    for idx, raw in raw_df.iterrows():
        try:
            row = _normalise_row(raw)
            if row is not None:
                rows.append(row)
        except Exception:
            logger.exception(
                "[%s] [ROW_%s] [%s] — normalisation error",
                raw.get("supplier", "UNKNOWN"), idx, raw.get("barcode", "NO_EAN"),
            )
            continue

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _normalise_row(raw: pd.Series) -> dict | None:
    """Normalise a single raw row into the standard schema."""
    trade_price = _parse_price(raw.get("trade_price"))
    if trade_price is None or trade_price <= 0:
        return None

    rrp = _parse_price(raw.get("retail_price"))
    barcode = str(raw.get("barcode", "")).strip() if raw.get("barcode") else ""

    # --- Case size / pack size handling ---
    # Connect Beauty: "Case Size" is a plain integer (e.g. "12", "110")
    # Abgee: pack_size is "EA" or "PK<n>"
    pack_size_raw = str(raw.get("pack_size", "")).strip().upper()
    case_qty, price_basis = _parse_pack_size(pack_size_raw)

    # --- Case price handling ---
    # Connect Beauty provides a separate "Case Price (GBP)" column (ex-VAT, total case cost)
    case_price_ex_vat = _parse_price(raw.get("case_price"))

    # Trade price is ex-VAT for all current suppliers (including Connect Beauty)
    supplier_price_ex_vat = trade_price
    buy_cost, vat_flag = resolve_buy_cost(cost_ex_vat=supplier_price_ex_vat, cost_inc_vat=None)
    supplier_price_inc_vat = buy_cost

    ean_valid = validate_ean(barcode) if barcode else False
    risk_flags = []
    if vat_flag:
        risk_flags.append(vat_flag)

    # Brand — some CSV formats provide it directly (Connect Beauty: "manufacturer" -> "brand")
    brand = str(raw.get("brand", "")).strip() if raw.get("brand") else ""

    # Stock status — from comments or availability field
    stock_status = str(raw.get("comments", "")).strip() if raw.get("comments") else None

    # Category — Connect Beauty provides a category column
    category = str(raw.get("category", "")).strip() if raw.get("category") else None

    return {
        "supplier": raw.get("supplier", ""),
        "source_file": raw.get("source_file", ""),
        "supplier_sku": str(raw.get("part_code", "")).strip(),
        "ean": barcode if barcode else None,
        "ean_valid": ean_valid,
        "product_name": str(raw.get("description", "")).strip(),
        "brand": brand,
        "category": category,
        "supplier_price_ex_vat": supplier_price_ex_vat,
        "supplier_price_inc_vat": supplier_price_inc_vat,
        "supplier_price_basis": price_basis,
        "case_qty": case_qty,
        "case_price_ex_vat": case_price_ex_vat,
        "rrp_inc_vat": rrp,
        "moq": 1,
        "stock_status": stock_status,
        "carton_size": _parse_int(raw.get("carton_size")),
        "risk_flags": risk_flags,
    }


def _parse_pack_size(pack_size: str) -> tuple[int, str]:
    """Parse Pack Size.

    Connect Beauty: plain integer (e.g. "12", "110") -> (n, UNIT)
      The unit price column is per-unit, so price_basis is always UNIT.
    Abgee: EA -> (1, UNIT). PK<n> -> (n, CASE).
    """
    if not pack_size or pack_size == "EA":
        return 1, "UNIT"

    # Abgee PK<n> format: price is per case
    match = re.match(r"PK(\d+)", pack_size)
    if match:
        qty = int(match.group(1))
        if qty <= 1:
            return 1, "UNIT"
        return qty, "CASE"

    # Connect Beauty / generic: plain integer means case size,
    # but the unit price column is per-unit pricing, so basis = UNIT
    int_match = re.match(r"^(\d+)$", pack_size)
    if int_match:
        qty = int(int_match.group(1))
        if qty <= 1:
            return 1, "UNIT"
        return qty, "UNIT"

    return 1, "UNIT"


def _parse_price(val) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    s = re.sub(r"[^\d.]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(val) -> int | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None
