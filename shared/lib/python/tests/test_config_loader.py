"""Tests for fba_config_loader.

Cover:
  - Loads canonical YAML files successfully
  - Backward-compat constants match YAML values
  - Type coercion (int vs float)
  - Validation catches drift (e.g. margin out of range)
  - Cache behaviour
  - Custom config_dir parameter and FBA_CONFIG_DIR env var
  - MIN_MARGIN is intentionally absent
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Make the lib importable without packaging
import sys
HERE = Path(__file__).resolve().parent
LIB_DIR = HERE.parent
sys.path.insert(0, str(LIB_DIR))

import fba_config_loader as cfg


def setup_function():
    cfg.reset_cache()


# --------------------------------------------------------------------------- #
# Loading                                                                     #
# --------------------------------------------------------------------------- #

def test_business_rules_loads_from_default_location():
    b = cfg.get_business_rules()
    assert b.marketplace_id == "A1F83G8C2ARO7P"
    assert b.currency == "GBP"
    assert b.vat_rate == 0.20
    assert b.seller_vat_registered is False


def test_thresholds_loads_from_default_location():
    t = cfg.get_thresholds()
    assert t.target_roi == 0.30
    assert t.min_profit_absolute == 2.50
    assert t.min_sales_shortlist == 20
    assert t.min_sales_review == 10
    assert t.buy_box_peak_threshold_pct == 20.0


def test_legacy_constants_match_yaml():
    """The shim constants must match the typed accessor."""
    t = cfg.get_thresholds()
    b = cfg.get_business_rules()
    assert cfg.MIN_PROFIT == t.min_profit_absolute
    assert cfg.MIN_PROFIT_ABSOLUTE == t.min_profit_absolute
    assert cfg.MIN_SALES_SHORTLIST == t.min_sales_shortlist
    assert cfg.MIN_SALES_REVIEW == t.min_sales_review
    assert cfg.VAT_RATE == b.vat_rate
    assert cfg.TARGET_ROI == t.target_roi
    assert cfg.BUY_BOX_PEAK_THRESHOLD_PCT == t.buy_box_peak_threshold_pct


def test_buy_box_peak_threshold_defaults_when_yaml_missing_key(tmp_path):
    """Backwards compat — operator configs without the new key must
    still load (default 20.0). Important: a stale supplier checkout
    that hasn't pulled this commit shouldn't break on threshold load."""
    (tmp_path / "business_rules.yaml").write_text(
        "marketplace_id: A1F83G8C2ARO7P\n"
        "currency: GBP\n"
        "vat_rate: 0.20\n"
        "seller_vat_registered: true\n"
        "vat_mismatch_tolerance: 0.05\n"
        "price_range:\n  min: 5.0\n  max: 100.0\n"
    )
    (tmp_path / "decision_thresholds.yaml").write_text(
        "target_roi: 0.30\n"
        "min_profit_absolute: 2.50\n"
        "min_sales_shortlist: 20\n"
        "min_sales_review: 10\n"
        "capital_exposure_limit: 200.0\n"
        "history_minimum_days: 30\n"
        "history_window_days: 90\n"
        "lower_band_percentile: 15\n"
        "size_tier_boundary_pct: 0.10\n"
        "fba_fee_conservative_fallback: 4.50\n"
        "storage_risk_threshold_sales: 20\n"
        "fbm_shipping_estimate: 3.50\n"
        "fbm_packaging_estimate: 0.50\n"
        "min_plausible_unit_price: 0.50\n"
        "default_referral_fee_pct: 0.15\n"
        # buy_box_peak_threshold_pct intentionally omitted — should default.
    )
    (tmp_path / "global_exclusions.yaml").write_text(
        "hazmat_strict: true\ncategories_excluded: []\ntitle_keywords_excluded: []\n"
    )
    cfg.reset_cache()
    t = cfg.get_thresholds(config_dir=tmp_path)
    assert t.buy_box_peak_threshold_pct == 20.0
    cfg.reset_cache()


def test_min_margin_is_not_exported():
    """Legacy MIN_MARGIN must be gone — replaced by TARGET_ROI."""
    assert not hasattr(cfg, "MIN_MARGIN"), \
        "MIN_MARGIN should not be exported. Use TARGET_ROI via fba_roi_gate."


def test_types_are_coerced():
    """Ints stay ints, floats stay floats — no string drift from YAML."""
    t = cfg.get_thresholds()
    assert isinstance(t.target_roi, float)
    assert isinstance(t.min_sales_shortlist, int)
    assert isinstance(t.min_profit_absolute, float)
    assert isinstance(t.history_minimum_days, int)


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #

def test_validation_catches_inverted_price_range(tmp_path):
    """Inverted price range fails validation, not loaded silently."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text("""
marketplace_id: TEST
currency: GBP
vat_rate: 0.20
seller_vat_registered: false
vat_mismatch_tolerance: 0.02
price_range:
  min: 70
  max: 20
""")
    (config_dir / "decision_thresholds.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    )
    cfg.reset_cache()
    with pytest.raises(AssertionError, match="price_range inverted"):
        cfg.get_business_rules(config_dir=config_dir)


def test_validation_catches_implausible_roi(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    bad_thresh = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    bad_thresh = bad_thresh.replace("target_roi: 0.30", "target_roi: 50")
    (config_dir / "decision_thresholds.yaml").write_text(bad_thresh)
    cfg.reset_cache()
    with pytest.raises(AssertionError, match="implausible"):
        cfg.get_thresholds(config_dir=config_dir)


def test_validation_catches_review_above_shortlist(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    bad = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    bad = bad.replace("min_sales_shortlist: 20", "min_sales_shortlist: 5")
    (config_dir / "decision_thresholds.yaml").write_text(bad)
    cfg.reset_cache()
    with pytest.raises(AssertionError, match="review threshold above shortlist"):
        cfg.get_thresholds(config_dir=config_dir)


# --------------------------------------------------------------------------- #
# Path resolution                                                             #
# --------------------------------------------------------------------------- #

def test_custom_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text("""
marketplace_id: CUSTOM
currency: USD
vat_rate: 0.10
seller_vat_registered: true
vat_mismatch_tolerance: 0.02
price_range:
  min: 5
  max: 100
""")
    (config_dir / "decision_thresholds.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    )
    cfg.reset_cache()
    b = cfg.get_business_rules(config_dir=config_dir)
    assert b.marketplace_id == "CUSTOM"
    assert b.currency == "USD"


def test_missing_config_dir_raises(tmp_path):
    cfg.reset_cache()
    with pytest.raises(FileNotFoundError):
        cfg.get_business_rules(config_dir=tmp_path / "does_not_exist")


# --------------------------------------------------------------------------- #
# GlobalExclusions                                                            #
# --------------------------------------------------------------------------- #

def test_global_exclusions_loads_canonical_yaml():
    """Canonical global_exclusions.yaml ships with hazmat strict + Clothing root."""
    g = cfg.get_global_exclusions()
    assert g.hazmat_strict is True
    assert "Clothing, Shoes & Jewellery" in g.categories_excluded
    # Canonical keyword list — these must remain (extending OK; removing without sign-off NOT).
    for kw in ("clothing", "apparel", "shoe", "boot", "footwear"):
        assert kw in g.title_keywords_excluded, f"missing canonical keyword {kw!r}"


def test_global_exclusions_is_frozen():
    """GlobalExclusions is immutable — callers cannot mutate."""
    g = cfg.get_global_exclusions()
    with pytest.raises((AttributeError, TypeError)):
        g.hazmat_strict = False  # type: ignore[misc]


def test_global_exclusions_uses_tuples_for_lists():
    """Tuples, not lists — hashable, immutable, safe for caching."""
    g = cfg.get_global_exclusions()
    assert isinstance(g.categories_excluded, tuple)
    assert isinstance(g.title_keywords_excluded, tuple)


def test_global_exclusions_missing_file_returns_permissive_defaults(tmp_path):
    """Without global_exclusions.yaml present, callers see no exclusions —
    legacy supplier flow keeps working."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    # Write only the two mandatory files; deliberately omit global_exclusions.yaml.
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    (config_dir / "decision_thresholds.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    )
    cfg.reset_cache()
    g = cfg.get_global_exclusions(config_dir=config_dir)
    assert g.hazmat_strict is False
    assert g.categories_excluded == ()
    assert g.title_keywords_excluded == ()


def test_global_exclusions_custom_config_dir(tmp_path):
    """Custom YAML in a temp dir loads correctly — supports per-test override."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    (config_dir / "decision_thresholds.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    )
    (config_dir / "global_exclusions.yaml").write_text("""
hazmat_strict: false
categories_excluded:
  - "Health & Personal Care"
title_keywords_excluded:
  - banana
  - shoehorn
""")
    cfg.reset_cache()
    g = cfg.get_global_exclusions(config_dir=config_dir)
    assert g.hazmat_strict is False
    assert g.categories_excluded == ("Health & Personal Care",)
    assert g.title_keywords_excluded == ("banana", "shoehorn")


def test_title_is_excluded_substring_match():
    """Substring match catches plurals and compounds."""
    g = cfg.get_global_exclusions()
    assert g.title_is_excluded("Mens Shoes Size 10") is True       # 'shoe' substring
    assert g.title_is_excluded("Boots for Hiking") is True          # 'boot' substring
    assert g.title_is_excluded("Apparel rack") is True              # exact
    assert g.title_is_excluded("Action Figure Set") is False        # clean


def test_title_is_excluded_case_insensitive():
    g = cfg.get_global_exclusions()
    assert g.title_is_excluded("CLOTHING ITEM") is True
    assert g.title_is_excluded("clothing item") is True
    assert g.title_is_excluded("Clothing Item") is True


def test_title_is_excluded_handles_none_and_empty():
    """None / empty title isn't an exclusion match — let other validation catch missing data."""
    g = cfg.get_global_exclusions()
    assert g.title_is_excluded(None) is False
    assert g.title_is_excluded("") is False


def test_category_is_excluded_exact_match():
    """Exact-match (case-insensitive, whitespace-trimmed) — subcategories not implied."""
    g = cfg.get_global_exclusions()
    assert g.category_is_excluded("Clothing, Shoes & Jewellery") is True
    assert g.category_is_excluded("clothing, shoes & jewellery") is True
    assert g.category_is_excluded("  Clothing, Shoes & Jewellery  ") is True
    assert g.category_is_excluded("Clothing") is False               # not a substring match
    assert g.category_is_excluded("Toys & Games") is False


def test_category_is_excluded_handles_none():
    g = cfg.get_global_exclusions()
    assert g.category_is_excluded(None) is False
    assert g.category_is_excluded("") is False


def test_reset_cache_clears_global_exclusions(tmp_path):
    """reset_cache() must clear the global_exclusions cache too — otherwise
    test isolation breaks across tests that mutate the config dir."""
    # Step 1: load canonical
    cfg.reset_cache()
    g1 = cfg.get_global_exclusions()
    assert g1.hazmat_strict is True
    # Step 2: switch to a custom dir without resetting → cache should still
    # return the canonical (proving cache works), but ONLY because we haven't reset.
    # Step 3: reset, switch dir → must read fresh.
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    (config_dir / "decision_thresholds.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    )
    (config_dir / "global_exclusions.yaml").write_text("hazmat_strict: false\n")
    cfg.reset_cache()
    g2 = cfg.get_global_exclusions(config_dir=config_dir)
    assert g2.hazmat_strict is False
    assert g1 is not g2



# --------------------------------------------------------------------------- #
# DataSignals (HANDOFF WS2.4)                                                 #
# --------------------------------------------------------------------------- #


def test_data_signals_loads_from_canonical_yaml():
    """Canonical decision_thresholds.yaml carries the data_signals
    block as of WS2.4. Pin the values so a typo gets caught early."""
    cfg.reset_cache()
    ds = cfg.get_data_signals()
    assert ds.listing_age_min_days == 365
    assert ds.history_days_high_confidence == 90
    assert ds.history_days_medium_confidence == 30
    assert ds.competition_joiners_warn == 5
    assert ds.competition_joiners_critical == 10
    assert ds.bsr_decline_threshold == 0.05
    assert ds.oos_threshold_pct == 0.15
    assert ds.price_volatility_threshold == 0.20
    assert ds.amazon_bb_share_warn_pct == 0.30
    assert ds.amazon_bb_share_block_pct == 0.70


def test_data_signals_uses_defaults_when_block_missing(tmp_path):
    """Backwards compat: an older decision_thresholds.yaml without
    the data_signals block must still load. Defaults match the
    handoff spec."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    # Strip the data_signals block from the canonical thresholds yaml.
    raw = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    stripped = raw.split("# === Data signals")[0].rstrip() + "\n"
    (config_dir / "decision_thresholds.yaml").write_text(stripped)
    cfg.reset_cache()
    ds = cfg.get_data_signals(config_dir=config_dir)
    assert ds.listing_age_min_days == 365
    assert ds.competition_joiners_critical == 10
    assert ds.price_volatility_threshold == 0.20
    cfg.reset_cache()


def test_data_signals_validates_warn_below_critical(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    raw = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    bad = raw.replace(
        "competition_joiners_warn: 5",
        "competition_joiners_warn: 20",
    )
    (config_dir / "decision_thresholds.yaml").write_text(bad)
    cfg.reset_cache()
    with pytest.raises(AssertionError, match="joiners warn"):
        cfg.get_data_signals(config_dir=config_dir)


def test_data_signals_validates_oos_threshold_is_fraction(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    raw = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    bad = raw.replace(
        "oos_threshold_pct: 0.15",
        "oos_threshold_pct: 15",  # operator typo: percent vs fraction
    )
    (config_dir / "decision_thresholds.yaml").write_text(bad)
    cfg.reset_cache()
    with pytest.raises(AssertionError, match="oos_threshold_pct"):
        cfg.get_data_signals(config_dir=config_dir)


# --------------------------------------------------------------------------- #
# BuyPlan (08_buy_plan)                                                       #
# --------------------------------------------------------------------------- #


def test_buy_plan_loads_from_canonical_yaml():
    """Canonical decision_thresholds.yaml ships with the buy_plan block.
    Pin the documented defaults so a typo gets caught."""
    cfg.reset_cache()
    bp = cfg.get_buy_plan()
    assert bp.first_order_days == 21
    assert bp.reorder_days == 45
    assert bp.min_test_qty == 5
    assert bp.max_first_order_capital == 200.0
    assert bp.risk_low_confidence == 0.70
    assert bp.risk_medium_confidence == 0.85
    assert bp.risk_floor == 0.50
    assert bp.stretch_roi_multiplier == 1.5


def test_buy_plan_uses_defaults_when_block_missing(tmp_path):
    """Backwards compat — older decision_thresholds.yaml without the
    buy_plan block must still load with documented defaults."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    # Strip the buy_plan block from the canonical thresholds yaml.
    raw = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    stripped = raw.split("# === Buy plan")[0].rstrip() + "\n"
    (config_dir / "decision_thresholds.yaml").write_text(stripped)
    cfg.reset_cache()
    bp = cfg.get_buy_plan(config_dir=config_dir)
    assert bp.first_order_days == 21
    assert bp.reorder_days == 45
    assert bp.min_test_qty == 5
    assert bp.max_first_order_capital == 200.0
    assert bp.risk_floor == 0.50
    cfg.reset_cache()


def test_buy_plan_custom_values_override_defaults(tmp_path):
    """Operator override of the buy_plan block flows through cleanly."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    raw = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    custom = raw.replace(
        "first_order_days: 21", "first_order_days: 28"
    ).replace(
        "max_first_order_capital: 200", "max_first_order_capital: 350"
    )
    (config_dir / "decision_thresholds.yaml").write_text(custom)
    cfg.reset_cache()
    bp = cfg.get_buy_plan(config_dir=config_dir)
    assert bp.first_order_days == 28
    assert bp.max_first_order_capital == 350.0
    cfg.reset_cache()


def test_buy_plan_validates_risk_floor_in_range(tmp_path):
    """A negative or zero risk_floor amplifies / zeroes mid — must be
    rejected at load."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    raw = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    bad = raw.replace("risk_floor: 0.50", "risk_floor: -0.1")
    (config_dir / "decision_thresholds.yaml").write_text(bad)
    cfg.reset_cache()
    with pytest.raises(AssertionError, match="risk_floor"):
        cfg.get_buy_plan(config_dir=config_dir)
    cfg.reset_cache()


def test_buy_plan_validates_first_order_below_reorder(tmp_path):
    """First-order cover should not exceed reorder cover (untested ASIN
    holds less stock, not more)."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    raw = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    bad = raw.replace("first_order_days: 21", "first_order_days: 60")
    (config_dir / "decision_thresholds.yaml").write_text(bad)
    cfg.reset_cache()
    with pytest.raises(AssertionError, match="first_order_days above reorder_days"):
        cfg.get_buy_plan(config_dir=config_dir)
    cfg.reset_cache()


def test_buy_plan_validates_stretch_multiplier_at_least_one(tmp_path):
    """A stretch multiplier below 1.0 makes the stretch target above the
    buy ceiling — that's nonsense."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    raw = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    bad = raw.replace("stretch_roi_multiplier: 1.5", "stretch_roi_multiplier: 0.8")
    (config_dir / "decision_thresholds.yaml").write_text(bad)
    cfg.reset_cache()
    with pytest.raises(AssertionError, match="stretch_roi_multiplier"):
        cfg.get_buy_plan(config_dir=config_dir)
    cfg.reset_cache()


def test_buy_plan_validates_stretch_multiplier_upper_bound(tmp_path):
    """An absurd multiplier (>5.0) produces wildly negative stretch
    targets on thin-margin listings — reject at config load."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    raw = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    bad = raw.replace("stretch_roi_multiplier: 1.5", "stretch_roi_multiplier: 100.0")
    (config_dir / "decision_thresholds.yaml").write_text(bad)
    cfg.reset_cache()
    with pytest.raises(AssertionError, match="stretch_roi_multiplier"):
        cfg.get_buy_plan(config_dir=config_dir)
    cfg.reset_cache()
