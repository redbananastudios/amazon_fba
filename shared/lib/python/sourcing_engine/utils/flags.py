# flags.py — Risk flag constants and helpers.
# All flags surface in output. Flags are strings stored in a list per row.

# --- Flags that block SHORTLIST ---
PRICE_FLOOR_HIT = "PRICE_FLOOR_HIT"              # raw conservative price below break-even
VAT_FIELD_MISMATCH = "VAT_FIELD_MISMATCH"          # supplier inc/ex VAT fields conflict
VAT_UNCLEAR = "VAT_UNCLEAR"                        # cannot determine buy cost from supplier data

# --- Flags that route to REVIEW ---
AMAZON_ON_LISTING = "AMAZON_ON_LISTING"            # Amazon is a seller — Buy Box win probability low
AMAZON_STATUS_UNKNOWN = "AMAZON_STATUS_UNKNOWN"    # cannot confirm Amazon presence on listing
SINGLE_FBA_SELLER = "SINGLE_FBA_SELLER"            # one FBA seller controls listing
HIGH_MOQ = "HIGH_MOQ"                              # capital exposure exceeds threshold
SIZE_TIER_RISK = "SIZE_TIER_RISK"                  # near size tier boundary — fee may increase
SIZE_TIER_UNKNOWN = "SIZE_TIER_UNKNOWN"            # FBA fee unknown — conservative fallback applied
STORAGE_FEE_RISK = "STORAGE_FEE_RISK"              # low velocity — storage fees material to margin
MULTI_ASIN_MATCH = "MULTI_ASIN_MATCH"              # EAN matched multiple ASINs
PRICE_BASIS_AMBIGUOUS = "PRICE_BASIS_AMBIGUOUS"    # cannot determine if price is per unit or per case
CASE_QTY_UNKNOWN = "CASE_QTY_UNKNOWN"              # case quantity not found in supplier data
CASE_MATCH_SKIPPED = "CASE_MATCH_SKIPPED"          # case ASIN match not attempted

# --- Data quality flags ---
PRICE_MISMATCH_RRP = "PRICE_MISMATCH_RRP"          # Amazon price >2x or <0.3x supplier RRP — likely wrong EAN

# --- Informational flags ---
FBM_ONLY = "FBM_ONLY"                              # no FBA sellers — FBM fee path applied
FBM_SHIPPING_ESTIMATED = "FBM_SHIPPING_ESTIMATED"  # FBM fulfilment cost is an estimate
POSSIBLE_PRIVATE_LABEL = "POSSIBLE_PRIVATE_LABEL"  # possible private label product
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"      # <30 days qualifying Keepa FBA history

# --- History-derived REVIEW flags (HANDOFF WS2.3) ---
# Each fires from `calculate.py` based on the corresponding
# market_snapshot field. All thresholds live in
# `decision_thresholds.yaml::data_signals` and are loaded via
# `fba_config_loader.get_data_signals()`. They route to REVIEW (not
# SHORTLIST_BLOCKERS) — the candidate-score step in WS3 already
# penalises listings on these dimensions, and double-penalising via a
# hard SHORTLIST block would over-reject otherwise viable products.
LISTING_TOO_NEW = "LISTING_TOO_NEW"                # listing_age_days < listing_age_min_days (default 365)
COMPETITION_GROWING = "COMPETITION_GROWING"        # joiners_90d >= competition_joiners_critical (default 10)
BSR_DECLINING = "BSR_DECLINING"                    # bsr_slope_90d > bsr_decline_threshold (default 0.05)
HIGH_OOS = "HIGH_OOS"                              # buy_box_oos_pct_90 > oos_threshold_pct (default 0.15)
PRICE_UNSTABLE = "PRICE_UNSTABLE"                  # price_volatility_90d > price_volatility_threshold (default 0.20)
# Lightweight historical-peak signal — fires when the current Buy Box price
# is materially above the 90-day average (threshold in
# decision_thresholds.yaml → buy_box_peak_threshold_pct, default 20%).
# Browser-tier-friendly: reads buy_box_avg90 from the Keepa export, no API
# tokens required. Catches "buying at a peak" scenarios where the
# supplier-negotiation ceiling computed against the current price would
# lock the operator into a position that erodes if the listing reverts.
BUY_BOX_ABOVE_AVG90 = "BUY_BOX_ABOVE_AVG90"
# Fired when the engine had to fall back to Amazon's price as the
# market reference because Keepa's Buy Box (idx 18) and FBA-only (idx 10)
# stats were both empty. Common for niche or freshly-listed products
# Keepa hasn't profiled into those buckets yet. Operator should treat
# the resulting economics as indicative only — when Amazon competes on
# the listing, 3rd-party FBA may actually be priced lower than the
# Amazon offer that drove the calculation.
AMAZON_ONLY_PRICE = "AMAZON_ONLY_PRICE"

# --- Sets used by the decision engine ---

# Flags that block SHORTLIST (any one present -> cannot shortlist).
# A blocked row falls through to REVIEW (or REJECT if other gates also
# fail), so this set models "needs operator eyes, can't auto-buy".
SHORTLIST_BLOCKERS = frozenset({
    PRICE_FLOOR_HIT,
    VAT_FIELD_MISMATCH,
    VAT_UNCLEAR,
    PRICE_MISMATCH_RRP,
    # Peak-buying — needs human judgment to confirm the current price
    # isn't a temporary spike before committing supplier-negotiation
    # capital. See flags definition above for the rationale.
    BUY_BOX_ABOVE_AVG90,
})

# Flags that force REVIEW (any one present -> route to REVIEW if not rejected)
REVIEW_FLAGS = frozenset({
    HIGH_MOQ,
    SIZE_TIER_RISK,
    SIZE_TIER_UNKNOWN,
    SINGLE_FBA_SELLER,
    AMAZON_ON_LISTING,
    AMAZON_STATUS_UNKNOWN,
    PRICE_FLOOR_HIT,
    MULTI_ASIN_MATCH,
    STORAGE_FEE_RISK,
    VAT_FIELD_MISMATCH,
    VAT_UNCLEAR,
    PRICE_BASIS_AMBIGUOUS,
    CASE_MATCH_SKIPPED,
    CASE_QTY_UNKNOWN,
    PRICE_MISMATCH_RRP,
    BUY_BOX_ABOVE_AVG90,
    AMAZON_ONLY_PRICE,
    # History-derived signals (HANDOFF WS2.3). Visible flags that
    # route to REVIEW; the candidate-score step in WS3 reads the
    # underlying numeric fields and penalises proportionally.
    LISTING_TOO_NEW,
    COMPETITION_GROWING,
    BSR_DECLINING,
    HIGH_OOS,
    PRICE_UNSTABLE,
})


def has_any_flag(risk_flags, flag_set):
    """Check if any flag in flag_set is present in risk_flags."""
    return bool(set(risk_flags) & flag_set)


def has_flag(risk_flags, flag):
    """Check if a specific flag is present in risk_flags."""
    return flag in risk_flags
