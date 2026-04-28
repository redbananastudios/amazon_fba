"""
config.py — Supplier engine config (now a shim).

Real values live in shared/config/business_rules.yaml and decision_thresholds.yaml.
This file re-exports them under the legacy constant names so existing pipeline
code continues to work unchanged.

Step 2 of the reorganisation will eliminate this shim by moving the supplier
sourcing engine into shared/lib/python/, at which point all suppliers will
import directly from fba_config_loader.

NOTE: MIN_MARGIN is intentionally NOT exported. The decision engine now uses
an ROI-based gate (TARGET_ROI) — see pipeline/decision.py and the shared
fba_roi_gate module.
"""
import sys
from pathlib import Path

# Resolve the repo root from this file's path:
#   .../<repo>/supplier_pricelist_finder/pricelists/<supplier>/sourcing_engine/config.py
#   parents: [0]=sourcing_engine, [1]=<supplier>, [2]=pricelists, [3]=supplier_pricelist_finder, [4]=<repo>
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SHARED_LIB = _REPO_ROOT / "shared" / "lib" / "python"

if str(_SHARED_LIB) not in sys.path:
    sys.path.insert(0, str(_SHARED_LIB))

# Re-export legacy constants. fba_config_loader exposes:
#   MIN_PROFIT, MIN_PROFIT_ABSOLUTE, MIN_SALES_SHORTLIST, MIN_SALES_REVIEW,
#   CAPITAL_EXPOSURE_LIMIT, HISTORY_MINIMUM_DAYS, HISTORY_WINDOW_DAYS,
#   LOWER_BAND_PERCENTILE, SIZE_TIER_BOUNDARY_PCT, FBA_FEE_CONSERVATIVE_FALLBACK,
#   STORAGE_RISK_THRESHOLD, FBM_SHIPPING_ESTIMATE, FBM_PACKAGING_ESTIMATE,
#   VAT_RATE, VAT_MISMATCH_TOLERANCE, MIN_PLAUSIBLE_UNIT_PRICE,
#   DEFAULT_REFERRAL_FEE_PCT, TARGET_ROI
from fba_config_loader import (  # noqa: E402, F401
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
