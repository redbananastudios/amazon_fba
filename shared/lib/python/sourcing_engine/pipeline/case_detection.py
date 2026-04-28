"""Unit vs case price detection and cost derivation.

For Abgee: EA -> UNIT, PK<n> -> CASE (trade price is per case of n units).
General detection logic from PRD 3.2b also implemented.
"""
from sourcing_engine.config import VAT_RATE, MIN_PLAUSIBLE_UNIT_PRICE
from sourcing_engine.utils.flags import PRICE_BASIS_AMBIGUOUS, CASE_QTY_UNKNOWN, CASE_MATCH_SKIPPED


def detect_price_basis(
    supplier_price_ex_vat: float,
    case_qty: int | None,
    rrp_inc_vat: float | None,
    column_hint: str | None,
) -> str:
    """Detect whether the supplier price is per UNIT or per CASE.
    Priority: 1. column hint, 2. implied unit price heuristic, 3. RRP comparison, 4. AMBIGUOUS.
    """
    if column_hint:
        hint = column_hint.upper()
        if hint in ("UNIT", "CASE"):
            return hint

    if case_qty is None or case_qty <= 1:
        return "UNIT"

    implied_unit_price = supplier_price_ex_vat / case_qty
    if implied_unit_price < MIN_PLAUSIBLE_UNIT_PRICE:
        return "CASE"

    if rrp_inc_vat is not None and rrp_inc_vat > 0:
        if supplier_price_ex_vat > (rrp_inc_vat * 0.90):
            return "UNIT"

    return "AMBIGUOUS"


def derive_costs(
    supplier_price_ex_vat: float,
    supplier_price_basis: str,
    case_qty: int | None,
    rrp_inc_vat: float | None,
    vat_rate: float = VAT_RATE,
) -> dict:
    """Derive unit and case costs from supplier price and basis.
    Returns dict with unit_cost_ex/inc_vat, case_cost_ex/inc_vat, case_qty, flags.
    """
    flags = []

    if case_qty is None:
        flags.append(CASE_QTY_UNKNOWN)
        case_qty = 1
    elif case_qty <= 0:
        case_qty = 1

    if supplier_price_basis == "AMBIGUOUS":
        flags.append(PRICE_BASIS_AMBIGUOUS)
        flags.append(CASE_MATCH_SKIPPED)
        return {
            "unit_cost_ex_vat": None, "unit_cost_inc_vat": None,
            "case_cost_ex_vat": None, "case_cost_inc_vat": None,
            "case_qty": case_qty, "flags": flags,
        }

    if supplier_price_basis == "UNIT":
        unit_ex = supplier_price_ex_vat
        unit_inc = unit_ex * (1 + vat_rate)
        if case_qty > 1:
            case_ex = unit_ex * case_qty
            case_inc = case_ex * (1 + vat_rate)
        else:
            case_ex = None
            case_inc = None

    elif supplier_price_basis == "CASE":
        case_ex = supplier_price_ex_vat
        case_inc = case_ex * (1 + vat_rate)
        unit_ex = supplier_price_ex_vat / case_qty
        unit_inc = unit_ex * (1 + vat_rate)
        if case_qty == 1:
            case_ex = None
            case_inc = None
    else:
        flags.append(PRICE_BASIS_AMBIGUOUS)
        return {
            "unit_cost_ex_vat": None, "unit_cost_inc_vat": None,
            "case_cost_ex_vat": None, "case_cost_inc_vat": None,
            "case_qty": case_qty, "flags": flags,
        }

    return {
        "unit_cost_ex_vat": unit_ex, "unit_cost_inc_vat": unit_inc,
        "case_cost_ex_vat": case_ex, "case_cost_inc_vat": case_inc,
        "case_qty": case_qty, "flags": flags,
    }
