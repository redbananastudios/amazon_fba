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
