"""Fee calculation — FBA and FBM paths are strictly separate.
FBA: referral_fee + fba_fulfilment_fee + storage_fee
FBM: referral_fee + shipping + packaging (NO fba_fee, NO storage_fee)

IMPORTANT: Amazon fees from Keepa/SP-API are quoted ex-VAT.
Non-VAT registered sellers pay fees + 20% VAT and cannot reclaim.
All fee totals include VAT to reflect the actual cost to the seller.
"""
from sourcing_engine.config import (
    DEFAULT_REFERRAL_FEE_PCT, FBA_FEE_CONSERVATIVE_FALLBACK,
    FBM_SHIPPING_ESTIMATE, FBM_PACKAGING_ESTIMATE, STORAGE_RISK_THRESHOLD,
    VAT_RATE,
)
from sourcing_engine.utils.flags import SIZE_TIER_UNKNOWN, FBM_SHIPPING_ESTIMATED, STORAGE_FEE_RISK

_FBA_FEES_BY_TIER = {
    "small_envelope": 1.78, "standard_envelope": 2.10, "large_envelope": 2.45,
    "small_parcel": 3.07, "standard_parcel": 3.68,
    "small_oversize": 5.90, "standard_oversize": 7.31, "large_oversize": 13.84,
}
_STORAGE_RATE_PER_CBFT = 0.75


def calculate_fees_fba(
    sell_price: float, size_tier: str | None,
    product_volume_cbft: float | None = None, sales_estimate: float | None = None,
    referral_fee_pct: float = DEFAULT_REFERRAL_FEE_PCT,
    keepa_fba_fee: float | None = None,
    keepa_referral_fee_pct: float | None = None,
) -> dict:
    flags = []

    # Referral fee — use Keepa's real rate if available, otherwise default
    if keepa_referral_fee_pct and keepa_referral_fee_pct > 0:
        referral_fee = sell_price * keepa_referral_fee_pct
    else:
        referral_fee = sell_price * referral_fee_pct

    # FBA fulfilment fee — prefer Keepa's real fee, then size tier lookup, then fallback
    if keepa_fba_fee and keepa_fba_fee > 0:
        fba_fee = keepa_fba_fee
    elif size_tier and size_tier.lower() != "unknown":
        fba_fee = _FBA_FEES_BY_TIER.get(size_tier.lower(), FBA_FEE_CONSERVATIVE_FALLBACK)
    else:
        fba_fee = FBA_FEE_CONSERVATIVE_FALLBACK
        flags.append(SIZE_TIER_UNKNOWN)

    storage_fee = 0.0
    if product_volume_cbft and sales_estimate and sales_estimate > 0:
        storage_fee = (product_volume_cbft * _STORAGE_RATE_PER_CBFT) / sales_estimate
    if sales_estimate is not None and sales_estimate < STORAGE_RISK_THRESHOLD:
        flags.append(STORAGE_FEE_RISK)
    # Amazon fees are ex-VAT. Non-VAT registered seller pays +20% and cannot reclaim.
    fees_ex_vat = referral_fee + fba_fee + storage_fee
    total = fees_ex_vat * (1 + VAT_RATE)
    return {"referral_fee": referral_fee, "fba_fee": fba_fee, "storage_fee": storage_fee,
            "fees_ex_vat": fees_ex_vat, "vat_on_fees": fees_ex_vat * VAT_RATE, "total": total, "flags": flags}


def calculate_fees_fbm(
    sell_price: float, referral_fee_pct: float = DEFAULT_REFERRAL_FEE_PCT,
    shipping: float = FBM_SHIPPING_ESTIMATE, packaging: float = FBM_PACKAGING_ESTIMATE,
) -> dict:
    referral_fee = sell_price * referral_fee_pct
    # Referral fee is ex-VAT, add 20% for non-VAT registered seller.
    # Shipping/packaging are the seller's own costs (already real cost, no VAT to add).
    referral_fee_inc_vat = referral_fee * (1 + VAT_RATE)
    total = referral_fee_inc_vat + shipping + packaging
    return {"referral_fee": referral_fee, "shipping": shipping, "packaging": packaging,
            "fba_fee": 0.0, "storage_fee": 0.0, "fees_ex_vat": referral_fee,
            "vat_on_fees": referral_fee * VAT_RATE, "total": total, "flags": [FBM_SHIPPING_ESTIMATED]}
