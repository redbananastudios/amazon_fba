"""
FBA Engine — Centralised Config Loader.

This is the canonical place to read business rules and decision thresholds.
All other modules import from here. Inline values in code or docs are forbidden.

Loads from:
  shared/config/business_rules.yaml
  shared/config/decision_thresholds.yaml
  shared/config/global_exclusions.yaml   (optional — falls back to empty exclusions)

Locations resolved in this order:
  1. Path passed to load_config(config_dir=...)
  2. FBA_CONFIG_DIR environment variable
  3. Walks up from this file's location to find shared/config/

For backward compatibility with existing supplier code that uses
`from sourcing_engine.config import MIN_PROFIT, MIN_MARGIN, ...`, this module
exposes the legacy CONSTANT_NAMES at module level. New code should use the
typed accessor `get_thresholds()` instead.

Note: MIN_MARGIN is intentionally NOT exposed. The decision engine no longer
uses a margin gate — it uses an ROI gate (see roi.py). Code that referenced
MIN_MARGIN must migrate.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


# --------------------------------------------------------------------------- #
# Path resolution                                                             #
# --------------------------------------------------------------------------- #

def _find_config_dir(explicit: Path | None = None) -> Path:
    """
    Locate the shared/config directory.

    Resolution order (first match wins):
      1. explicit parameter
      2. FBA_CONFIG_DIR env var
      3. Walk up from this file looking for a `shared/config` sibling
    """
    if explicit is not None:
        p = Path(explicit).resolve()
        if not p.is_dir():
            raise FileNotFoundError(f"Config dir not found: {p}")
        return p

    env = os.environ.get("FBA_CONFIG_DIR")
    if env:
        p = Path(env).resolve()
        if not p.is_dir():
            raise FileNotFoundError(f"FBA_CONFIG_DIR points to nothing: {p}")
        return p

    # Walk up from this file looking for shared/config/
    here = Path(__file__).resolve()
    for ancestor in [here] + list(here.parents):
        candidate = ancestor / "shared" / "config"
        if candidate.is_dir() and (candidate / "business_rules.yaml").exists():
            return candidate

    raise FileNotFoundError(
        "Could not locate shared/config/. Set FBA_CONFIG_DIR or pass explicit path."
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at top of {path}, got {type(data).__name__}")
    return data


# --------------------------------------------------------------------------- #
# Typed accessors                                                             #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BusinessRules:
    marketplace_id: str
    currency: str
    vat_rate: float
    seller_vat_registered: bool
    vat_mismatch_tolerance: float
    price_min: float
    price_max: float


@dataclass(frozen=True)
class GlobalExclusions:
    """Cross-strategy hard exclusions.

    Loaded from ``shared/config/global_exclusions.yaml``. If the file is
    missing, all fields default to permissive values (no exclusions).

    Used by:
      - Keepa Product Finder recipes (pre-export filter injection)
      - ``fba_engine/steps/keepa_finder_csv.py`` (post-export safety net)
    """

    hazmat_strict: bool
    categories_excluded: tuple[str, ...]
    title_keywords_excluded: tuple[str, ...]

    def title_is_excluded(self, title: str | None) -> bool:
        """Return True if ``title`` contains any blacklisted keyword.

        Case-insensitive substring match. Empty/None title returns False
        (the engine's other validation catches missing-title rows).
        """
        if not title:
            return False
        haystack = title.casefold()
        return any(kw.casefold() in haystack for kw in self.title_keywords_excluded)

    def category_is_excluded(self, category: str | None) -> bool:
        """Return True if ``category`` matches any excluded root.

        Exact case-insensitive match against Keepa's "Categories: Root"
        column. Subcategories are not implied — if you want to exclude
        a sub-tree, add the root.
        """
        if not category:
            return False
        target = category.strip().casefold()
        return any(c.strip().casefold() == target for c in self.categories_excluded)


@dataclass(frozen=True)
class DecisionThresholds:
    target_roi: float
    min_profit_absolute: float
    min_sales_shortlist: int
    min_sales_review: int
    capital_exposure_limit: float
    history_minimum_days: int
    history_window_days: int
    lower_band_percentile: int
    size_tier_boundary_pct: float
    fba_fee_conservative_fallback: float
    storage_risk_threshold_sales: int
    fbm_shipping_estimate: float
    fbm_packaging_estimate: float
    min_plausible_unit_price: float
    default_referral_fee_pct: float


@lru_cache(maxsize=1)
def _load_all(config_dir_str: str | None = None) -> tuple[BusinessRules, DecisionThresholds]:
    """Internal cached loader keyed on resolved path."""
    config_dir = _find_config_dir(Path(config_dir_str) if config_dir_str else None)
    business_data = _load_yaml(config_dir / "business_rules.yaml")
    thresh_data = _load_yaml(config_dir / "decision_thresholds.yaml")

    business = BusinessRules(
        marketplace_id=business_data["marketplace_id"],
        currency=business_data["currency"],
        vat_rate=float(business_data["vat_rate"]),
        seller_vat_registered=bool(business_data["seller_vat_registered"]),
        vat_mismatch_tolerance=float(business_data["vat_mismatch_tolerance"]),
        price_min=float(business_data["price_range"]["min"]),
        price_max=float(business_data["price_range"]["max"]),
    )

    thresh = DecisionThresholds(
        target_roi=float(thresh_data["target_roi"]),
        min_profit_absolute=float(thresh_data["min_profit_absolute"]),
        min_sales_shortlist=int(thresh_data["min_sales_shortlist"]),
        min_sales_review=int(thresh_data["min_sales_review"]),
        capital_exposure_limit=float(thresh_data["capital_exposure_limit"]),
        history_minimum_days=int(thresh_data["history_minimum_days"]),
        history_window_days=int(thresh_data["history_window_days"]),
        lower_band_percentile=int(thresh_data["lower_band_percentile"]),
        size_tier_boundary_pct=float(thresh_data["size_tier_boundary_pct"]),
        fba_fee_conservative_fallback=float(thresh_data["fba_fee_conservative_fallback"]),
        storage_risk_threshold_sales=int(thresh_data["storage_risk_threshold_sales"]),
        fbm_shipping_estimate=float(thresh_data["fbm_shipping_estimate"]),
        fbm_packaging_estimate=float(thresh_data["fbm_packaging_estimate"]),
        min_plausible_unit_price=float(thresh_data["min_plausible_unit_price"]),
        default_referral_fee_pct=float(thresh_data["default_referral_fee_pct"]),
    )

    _validate(business, thresh)
    return business, thresh


def _validate(business: BusinessRules, thresh: DecisionThresholds) -> None:
    """Sanity-check ranges. Catch typos before they become silent bugs."""
    assert 0 < business.vat_rate < 1, f"vat_rate {business.vat_rate} not a fraction"
    assert business.price_min < business.price_max, "price_range inverted"
    assert business.price_min > 0, "price_min must be positive"

    assert 0 < thresh.target_roi < 5, f"target_roi {thresh.target_roi} implausible"
    assert thresh.min_profit_absolute > 0, "min_profit_absolute must be positive"
    assert thresh.min_sales_review <= thresh.min_sales_shortlist, "review threshold above shortlist"
    assert 0 < thresh.lower_band_percentile < 100, "percentile out of range"
    assert thresh.history_minimum_days <= thresh.history_window_days, "min days above window"
    assert 0 < thresh.default_referral_fee_pct < 1, "referral fee not a fraction"


def get_business_rules(config_dir: Path | None = None) -> BusinessRules:
    """Get business constants. Cached."""
    key = str(config_dir.resolve()) if config_dir else None
    return _load_all(key)[0]


def get_thresholds(config_dir: Path | None = None) -> DecisionThresholds:
    """Get decision thresholds. Cached."""
    key = str(config_dir.resolve()) if config_dir else None
    return _load_all(key)[1]


@lru_cache(maxsize=1)
def _load_global_exclusions(config_dir_str: str | None = None) -> GlobalExclusions:
    """Load global_exclusions.yaml. Permissive defaults if file is absent.

    Kept separate from ``_load_all`` so existing tests that mock the config
    dir without this file keep working — global_exclusions is opt-in
    infrastructure, not a hard dependency for the legacy supplier flow.
    """
    config_dir = _find_config_dir(Path(config_dir_str) if config_dir_str else None)
    path = config_dir / "global_exclusions.yaml"

    if not path.exists():
        # Permissive defaults — every existing strategy keeps working.
        return GlobalExclusions(
            hazmat_strict=False,
            categories_excluded=(),
            title_keywords_excluded=(),
        )

    data = _load_yaml(path)
    return GlobalExclusions(
        hazmat_strict=bool(data.get("hazmat_strict", False)),
        categories_excluded=tuple(str(c) for c in (data.get("categories_excluded") or [])),
        title_keywords_excluded=tuple(
            str(k) for k in (data.get("title_keywords_excluded") or [])
        ),
    )


def get_global_exclusions(config_dir: Path | None = None) -> GlobalExclusions:
    """Get cross-strategy hard exclusions. Cached.

    Returns permissive defaults (no exclusions, hazmat_strict=False) when
    ``global_exclusions.yaml`` is absent — preserves backward compatibility
    for any caller running without the new config file.
    """
    key = str(config_dir.resolve()) if config_dir else None
    return _load_global_exclusions(key)


def reset_cache() -> None:
    """Clear the cache. Useful in tests that mutate config."""
    _load_all.cache_clear()
    _load_global_exclusions.cache_clear()


# --------------------------------------------------------------------------- #
# Backward-compat module-level constants                                      #
# --------------------------------------------------------------------------- #
# Existing supplier code does:
#   from sourcing_engine.config import MIN_PROFIT, MIN_SALES_SHORTLIST, ...
# Each supplier's config.py is now a shim that re-exports from here.
# These names match the legacy config.py exactly, EXCEPT:
#   - MIN_MARGIN is removed (replaced by TARGET_ROI in roi.py)
#   - MIN_PROFIT_ABSOLUTE is the new explicit name (MIN_PROFIT alias kept)
# --------------------------------------------------------------------------- #

try:
    _b = get_business_rules()
    _t = get_thresholds()

    # Legacy names (kept identical for shim compatibility)
    MIN_PROFIT: float = _t.min_profit_absolute            # alias
    MIN_PROFIT_ABSOLUTE: float = _t.min_profit_absolute
    MIN_SALES_SHORTLIST: int = _t.min_sales_shortlist
    MIN_SALES_REVIEW: int = _t.min_sales_review
    CAPITAL_EXPOSURE_LIMIT: float = _t.capital_exposure_limit
    HISTORY_MINIMUM_DAYS: int = _t.history_minimum_days
    HISTORY_WINDOW_DAYS: int = _t.history_window_days
    LOWER_BAND_PERCENTILE: int = _t.lower_band_percentile
    SIZE_TIER_BOUNDARY_PCT: float = _t.size_tier_boundary_pct
    FBA_FEE_CONSERVATIVE_FALLBACK: float = _t.fba_fee_conservative_fallback
    STORAGE_RISK_THRESHOLD: int = _t.storage_risk_threshold_sales
    FBM_SHIPPING_ESTIMATE: float = _t.fbm_shipping_estimate
    FBM_PACKAGING_ESTIMATE: float = _t.fbm_packaging_estimate
    VAT_RATE: float = _b.vat_rate
    VAT_MISMATCH_TOLERANCE: float = _b.vat_mismatch_tolerance
    MIN_PLAUSIBLE_UNIT_PRICE: float = _t.min_plausible_unit_price
    DEFAULT_REFERRAL_FEE_PCT: float = _t.default_referral_fee_pct

    # New: ROI gate
    TARGET_ROI: float = _t.target_roi

    # NB: MIN_MARGIN deliberately not exported — see module docstring.
except (FileNotFoundError, KeyError) as e:
    # Module is being inspected (e.g. by a doc generator) outside a real repo.
    # Defer the error until something tries to use it.
    import warnings
    warnings.warn(f"FBA config not loaded at import time: {e}. Call get_*() to retry.")


__all__ = [
    # Typed accessors (preferred)
    "BusinessRules",
    "DecisionThresholds",
    "GlobalExclusions",
    "get_business_rules",
    "get_thresholds",
    "get_global_exclusions",
    "reset_cache",
    # Legacy constants (backward compat)
    "MIN_PROFIT",
    "MIN_PROFIT_ABSOLUTE",
    "MIN_SALES_SHORTLIST",
    "MIN_SALES_REVIEW",
    "CAPITAL_EXPOSURE_LIMIT",
    "HISTORY_MINIMUM_DAYS",
    "HISTORY_WINDOW_DAYS",
    "LOWER_BAND_PERCENTILE",
    "SIZE_TIER_BOUNDARY_PCT",
    "FBA_FEE_CONSERVATIVE_FALLBACK",
    "STORAGE_RISK_THRESHOLD",
    "FBM_SHIPPING_ESTIMATE",
    "FBM_PACKAGING_ESTIMATE",
    "VAT_RATE",
    "VAT_MISMATCH_TOLERANCE",
    "MIN_PLAUSIBLE_UNIT_PRICE",
    "DEFAULT_REFERRAL_FEE_PCT",
    "TARGET_ROI",
]
