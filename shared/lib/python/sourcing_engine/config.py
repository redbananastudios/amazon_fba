"""
sourcing_engine.config — re-exports from fba_config_loader.

This is the canonical version. The per-supplier shims that existed in step 1
are removed in step 2; the engine now lives in shared/lib/python/sourcing_engine
and reads config directly via fba_config_loader.

NOTE: MIN_MARGIN is intentionally NOT exported (replaced by TARGET_ROI gate
in pipeline/decision.py via fba_roi_gate).
"""
from fba_config_loader import (  # noqa: F401
    MIN_PROFIT,
    MIN_PROFIT_ABSOLUTE,
    MIN_SALES_SHORTLIST,
    MIN_SALES_REVIEW,
    CAPITAL_EXPOSURE_LIMIT,
    HISTORY_MINIMUM_DAYS,
    HISTORY_WINDOW_DAYS,
    LOWER_BAND_PERCENTILE,
    SIZE_TIER_BOUNDARY_PCT,
    FBA_FEE_CONSERVATIVE_FALLBACK,
    STORAGE_RISK_THRESHOLD,
    FBM_SHIPPING_ESTIMATE,
    FBM_PACKAGING_ESTIMATE,
    VAT_RATE,
    VAT_MISMATCH_TOLERANCE,
    MIN_PLAUSIBLE_UNIT_PRICE,
    DEFAULT_REFERRAL_FEE_PCT,
    TARGET_ROI,
)
