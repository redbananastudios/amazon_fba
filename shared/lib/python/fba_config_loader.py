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
    buy_box_peak_threshold_pct: float


@dataclass(frozen=True)
class OpportunityValidation:
    """Final-validation thresholds (07_validate_opportunity).

    Loaded from ``decision_thresholds.yaml::opportunity_validation``.
    Drives the BUY / SOURCE_ONLY / NEGOTIATE / WATCH / KILL verdict
    in `sourcing_engine.opportunity`. Pure additive — no field here
    affects SHORTLIST/REVIEW/REJECT.
    """

    target_monthly_sales: int
    min_candidate_score_buy: int
    min_data_confidence_buy: str
    min_profit_absolute_buy: float
    min_roi_buy: float
    max_amazon_bb_share_buy: float
    max_amazon_bb_share_watch: float
    max_price_volatility_buy: float
    max_buy_box_oos_buy: float
    max_competition_joiners_buy: int
    max_fba_sellers_low_sales: int
    max_fba_sellers_100_sales: int
    max_fba_sellers_200_sales: int
    allow_gated_buy: bool
    allow_restricted_buy: bool
    kill_min_sales: int
    kill_min_roi: float
    kill_amazon_bb_share: float
    kill_price_volatility: float
    kill_bsr_decline_slope: float
    source_only_min_sales: int
    source_only_min_candidate_score: int
    source_only_max_volatility: float
    source_only_max_amazon_bb_share: float
    negotiate_min_sales: int
    negotiate_min_candidate_score: int
    strong_score: int
    watch_score: int


@dataclass(frozen=True)
class DataSignals:
    """History + competition thresholds added in HANDOFF WS2.4.

    Loaded from ``decision_thresholds.yaml::data_signals``. Defaults
    are conservative — operators tune per-niche over time.

    Used by:
      - flag-firing in `fba_engine/steps/calculate.py` (LISTING_TOO_NEW,
        COMPETITION_GROWING, BSR_DECLINING, HIGH_OOS, PRICE_UNSTABLE)
      - candidate-score data-confidence calc in WS3
        (history_days_high/medium_confidence)
      - candidate-score competition dimension in WS3
        (amazon_bb_share_*, competition_joiners_warn)
    """

    listing_age_min_days: int
    history_days_high_confidence: int
    history_days_medium_confidence: int
    competition_joiners_warn: int
    competition_joiners_critical: int
    bsr_decline_threshold: float
    oos_threshold_pct: float
    price_volatility_threshold: float
    amazon_bb_share_warn_pct: float
    amazon_bb_share_block_pct: float


@lru_cache(maxsize=1)
def _load_all(
    config_dir_str: str | None = None,
) -> tuple[BusinessRules, DecisionThresholds, DataSignals, OpportunityValidation]:
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
        # Default 20.0 keeps backwards-compat for any operator config
        # that hasn't added the new key yet — the flag simply doesn't
        # fire below 20% of avg90 movement until the YAML is bumped.
        buy_box_peak_threshold_pct=float(
            thresh_data.get("buy_box_peak_threshold_pct", 20.0)
        ),
    )

    # data_signals block added in HANDOFF WS2.4. Permissive defaults
    # so older config files without the block load cleanly.
    ds_data = thresh_data.get("data_signals") or {}
    data_signals = DataSignals(
        listing_age_min_days=int(ds_data.get("listing_age_min_days", 365)),
        history_days_high_confidence=int(
            ds_data.get("history_days_high_confidence", 90)
        ),
        history_days_medium_confidence=int(
            ds_data.get("history_days_medium_confidence", 30)
        ),
        competition_joiners_warn=int(ds_data.get("competition_joiners_warn", 5)),
        competition_joiners_critical=int(
            ds_data.get("competition_joiners_critical", 10)
        ),
        bsr_decline_threshold=float(ds_data.get("bsr_decline_threshold", 0.05)),
        oos_threshold_pct=float(ds_data.get("oos_threshold_pct", 0.15)),
        price_volatility_threshold=float(
            ds_data.get("price_volatility_threshold", 0.20)
        ),
        amazon_bb_share_warn_pct=float(
            ds_data.get("amazon_bb_share_warn_pct", 0.30)
        ),
        amazon_bb_share_block_pct=float(
            ds_data.get("amazon_bb_share_block_pct", 0.70)
        ),
    )

    # opportunity_validation block (HANDOFF: Final Opportunity Validation).
    # Permissive defaults so older configs without the block still load.
    ov_data = thresh_data.get("opportunity_validation") or {}
    opportunity = OpportunityValidation(
        target_monthly_sales=int(ov_data.get("target_monthly_sales", 100)),
        min_candidate_score_buy=int(ov_data.get("min_candidate_score_buy", 75)),
        min_data_confidence_buy=str(ov_data.get("min_data_confidence_buy", "HIGH")),
        min_profit_absolute_buy=float(ov_data.get("min_profit_absolute_buy", 2.50)),
        min_roi_buy=float(ov_data.get("min_roi_buy", 0.30)),
        max_amazon_bb_share_buy=float(ov_data.get("max_amazon_bb_share_buy", 0.30)),
        max_amazon_bb_share_watch=float(ov_data.get("max_amazon_bb_share_watch", 0.70)),
        max_price_volatility_buy=float(ov_data.get("max_price_volatility_buy", 0.20)),
        max_buy_box_oos_buy=float(ov_data.get("max_buy_box_oos_buy", 0.15)),
        max_competition_joiners_buy=int(ov_data.get("max_competition_joiners_buy", 5)),
        max_fba_sellers_low_sales=int(ov_data.get("max_fba_sellers_low_sales", 3)),
        max_fba_sellers_100_sales=int(ov_data.get("max_fba_sellers_100_sales", 8)),
        max_fba_sellers_200_sales=int(ov_data.get("max_fba_sellers_200_sales", 12)),
        allow_gated_buy=bool(ov_data.get("allow_gated_buy", False)),
        allow_restricted_buy=bool(ov_data.get("allow_restricted_buy", False)),
        kill_min_sales=int(ov_data.get("kill_min_sales", 20)),
        kill_min_roi=float(ov_data.get("kill_min_roi", 0.15)),
        kill_amazon_bb_share=float(ov_data.get("kill_amazon_bb_share", 0.90)),
        kill_price_volatility=float(ov_data.get("kill_price_volatility", 0.40)),
        kill_bsr_decline_slope=float(ov_data.get("kill_bsr_decline_slope", 0.10)),
        source_only_min_sales=int(ov_data.get("source_only_min_sales", 100)),
        source_only_min_candidate_score=int(
            ov_data.get("source_only_min_candidate_score", 75)
        ),
        source_only_max_volatility=float(
            ov_data.get("source_only_max_volatility", 0.20)
        ),
        source_only_max_amazon_bb_share=float(
            ov_data.get("source_only_max_amazon_bb_share", 0.70)
        ),
        negotiate_min_sales=int(ov_data.get("negotiate_min_sales", 100)),
        negotiate_min_candidate_score=int(
            ov_data.get("negotiate_min_candidate_score", 65)
        ),
        strong_score=int(ov_data.get("strong_score", 80)),
        watch_score=int(ov_data.get("watch_score", 60)),
    )

    _validate(business, thresh)
    _validate_data_signals(data_signals)
    _validate_opportunity_validation(opportunity)
    return business, thresh, data_signals, opportunity


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
    assert thresh.buy_box_peak_threshold_pct > 0, "peak threshold must be positive"


def _validate_data_signals(ds: DataSignals) -> None:
    """Sanity-check data_signals ranges. Same defensive style as `_validate`."""
    assert ds.listing_age_min_days > 0, "listing_age_min_days must be positive"
    assert (
        ds.history_days_medium_confidence
        <= ds.history_days_high_confidence
    ), "medium-confidence history window above high-confidence"
    assert (
        ds.competition_joiners_warn
        <= ds.competition_joiners_critical
    ), "joiners warn threshold above critical"
    assert 0 < ds.oos_threshold_pct < 1, "oos_threshold_pct not a fraction"
    assert (
        0 < ds.price_volatility_threshold < 5
    ), "price_volatility_threshold implausible"
    assert 0 < ds.amazon_bb_share_warn_pct < 1, "amazon_bb_share_warn_pct not a fraction"
    assert 0 < ds.amazon_bb_share_block_pct < 1, "amazon_bb_share_block_pct not a fraction"
    assert (
        ds.amazon_bb_share_warn_pct <= ds.amazon_bb_share_block_pct
    ), "amazon_bb_share_warn_pct above block_pct"


def get_business_rules(config_dir: Path | None = None) -> BusinessRules:
    """Get business constants. Cached."""
    key = str(config_dir.resolve()) if config_dir else None
    return _load_all(key)[0]


def get_thresholds(config_dir: Path | None = None) -> DecisionThresholds:
    """Get decision thresholds. Cached."""
    key = str(config_dir.resolve()) if config_dir else None
    return _load_all(key)[1]


def get_data_signals(config_dir: Path | None = None) -> DataSignals:
    """Get history + competition thresholds. Cached.

    Loaded from ``decision_thresholds.yaml::data_signals``.
    Returns permissive defaults if the block is absent (added in
    HANDOFF WS2.4 — older configs without the block keep working).
    """
    key = str(config_dir.resolve()) if config_dir else None
    return _load_all(key)[2]


def get_opportunity_validation(
    config_dir: Path | None = None,
) -> OpportunityValidation:
    """Get final-validation thresholds. Cached.

    Loaded from ``decision_thresholds.yaml::opportunity_validation``.
    Permissive defaults when the block is absent (older configs).
    """
    key = str(config_dir.resolve()) if config_dir else None
    return _load_all(key)[3]


def _validate_opportunity_validation(ov: OpportunityValidation) -> None:
    """Sanity-check opportunity_validation thresholds. Same defensive
    style as ``_validate`` and ``_validate_data_signals``."""
    assert ov.target_monthly_sales > 0, "target_monthly_sales must be positive"
    assert 0 < ov.min_roi_buy < 5, f"min_roi_buy {ov.min_roi_buy} implausible"
    assert (
        ov.kill_min_roi <= ov.min_roi_buy
    ), "kill_min_roi above buy threshold (would never KILL)"
    assert (
        ov.kill_min_sales <= ov.target_monthly_sales
    ), "kill_min_sales above buy target (would never KILL)"
    assert 0 < ov.max_amazon_bb_share_buy < 1, (
        "max_amazon_bb_share_buy not a fraction"
    )
    assert (
        ov.max_amazon_bb_share_buy <= ov.max_amazon_bb_share_watch
        <= ov.kill_amazon_bb_share
    ), "BB-share thresholds inverted (buy ≤ watch ≤ kill)"
    assert (
        ov.max_price_volatility_buy <= ov.kill_price_volatility
    ), "max_price_volatility_buy above kill threshold"
    assert ov.min_data_confidence_buy in ("HIGH", "MEDIUM", "LOW"), (
        f"min_data_confidence_buy={ov.min_data_confidence_buy} not one of HIGH/MEDIUM/LOW"
    )
    assert (
        ov.watch_score <= ov.strong_score
    ), "watch_score above strong_score"


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

    # New: peak-buying detection (browser-tier-friendly historical signal)
    BUY_BOX_PEAK_THRESHOLD_PCT: float = _t.buy_box_peak_threshold_pct

    # NB: MIN_MARGIN deliberately not exported — see module docstring.
except (FileNotFoundError, KeyError) as e:
    # Module is being inspected (e.g. by a doc generator) outside a real repo.
    # Defer the error until something tries to use it.
    import warnings
    warnings.warn(f"FBA config not loaded at import time: {e}. Call get_*() to retry.")


__all__ = [
    # Typed accessors (preferred)
    "BusinessRules",
    "DataSignals",
    "DecisionThresholds",
    "GlobalExclusions",
    "OpportunityValidation",
    "get_business_rules",
    "get_data_signals",
    "get_opportunity_validation",
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
    "BUY_BOX_PEAK_THRESHOLD_PCT",
]
