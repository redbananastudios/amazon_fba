"""Tests for fba_engine.steps.single_asin.

Discovery step that builds a 1-row DataFrame from CLI args. Verifies
schema seeding, ASIN validation, buy_cost coercion (string interpolation
from YAML + direct float calls), and the wholesale-flow defaults.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fba_engine.steps import single_asin as step


# ────────────────────────────────────────────────────────────────────────
# discover_single_asin — direct helper
# ────────────────────────────────────────────────────────────────────────


def test_returns_one_row_with_canonical_schema():
    df = step.discover_single_asin("B0EXAMPLE1")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["asin"] == "B0EXAMPLE1"
    assert row["source"] == "single_asin"
    assert row["discovery_strategy"] == "single_asin"
    assert "amazon.co.uk/dp/B0EXAMPLE1" in row["amazon_url"]


def test_buy_cost_zero_default_triggers_wholesale_flow():
    """buy_cost=0 is the load-bearing convention that tells calculate.py
    to emit max_buy_price instead of computing literal ROI. Default must
    stay at 0.0 so unsupplied costs go through the wholesale path."""
    df = step.discover_single_asin("B0EXAMPLE1")
    assert df.iloc[0]["buy_cost"] == 0.0
    assert df.iloc[0]["moq"] == 1


def test_buy_cost_nonzero_passes_through():
    df = step.discover_single_asin("B0EXAMPLE1", buy_cost=4.50)
    assert df.iloc[0]["buy_cost"] == 4.50


def test_asin_normalised_to_uppercase():
    """Operator might paste a lowercase ASIN; the engine's downstream
    10-char check is case-sensitive (uppercase) so we normalise here."""
    df = step.discover_single_asin("b0example1")
    assert df.iloc[0]["asin"] == "B0EXAMPLE1"


def test_invalid_asin_raises_loudly():
    """Don't silently let a malformed ASIN through — the Keepa API will
    return 'not found' and the row will be None-filled, which masks
    the original typo. Fail fast at discovery."""
    for bad in ("", "   ", "TOO_SHORT", "WAY_TOO_LONG_FOR_ASIN", "lowercase!"):
        with pytest.raises(ValueError, match="ASIN|asin"):
            step.discover_single_asin(bad)


# ────────────────────────────────────────────────────────────────────────
# run_step — runner-compatible config contract
# ────────────────────────────────────────────────────────────────────────


def test_run_step_minimal_config():
    out = step.run_step(pd.DataFrame(), {"asin": "B0EXAMPLE1"})
    assert len(out) == 1
    assert out.iloc[0]["asin"] == "B0EXAMPLE1"
    assert out.iloc[0]["buy_cost"] == 0.0


def test_run_step_requires_asin():
    with pytest.raises(ValueError, match="asin"):
        step.run_step(pd.DataFrame(), {})


def test_run_step_with_float_buy_cost():
    """Direct programmatic call — buy_cost is a Python float."""
    out = step.run_step(pd.DataFrame(), {"asin": "B0EXAMPLE1", "buy_cost": 6.75})
    assert out.iloc[0]["buy_cost"] == 6.75


def test_run_step_with_string_buy_cost_from_yaml_interpolation():
    """Strategy YAML interpolation produces strings — `{buy_cost}` becomes
    `"6.75"` after format(). Step must parse that back to float."""
    out = step.run_step(pd.DataFrame(), {"asin": "B0EXAMPLE1", "buy_cost": "6.75"})
    assert out.iloc[0]["buy_cost"] == 6.75


def test_run_step_buy_cost_empty_string_collapses_to_zero():
    """When --buy-cost is omitted, the context dict has no `buy_cost`
    key; the YAML interpolation produces `""`. That must collapse to 0.0
    rather than blowing up the float cast."""
    out = step.run_step(pd.DataFrame(), {"asin": "B0EXAMPLE1", "buy_cost": ""})
    assert out.iloc[0]["buy_cost"] == 0.0


def test_run_step_buy_cost_string_none_collapses_to_zero():
    """Some interpolation paths produce the string "None" when the value
    was Python None. Same handling as empty string."""
    out = step.run_step(pd.DataFrame(), {"asin": "B0EXAMPLE1", "buy_cost": "None"})
    assert out.iloc[0]["buy_cost"] == 0.0


def test_run_step_buy_cost_invalid_string_raises():
    with pytest.raises(ValueError, match="not a number"):
        step.run_step(pd.DataFrame(), {"asin": "B0EXAMPLE1", "buy_cost": "abc"})


def test_run_step_buy_cost_negative_raises():
    """Negative cost is operator typo — surface loud rather than silently
    inverting profit calculations."""
    with pytest.raises(ValueError, match="negative"):
        step.run_step(pd.DataFrame(), {"asin": "B0EXAMPLE1", "buy_cost": -1.0})


def test_run_step_ignores_input_df():
    """Discovery steps create the DataFrame — input df is ignored,
    matching the keepa_finder_csv / oa_csv / seller_storefront_csv contract."""
    pre = pd.DataFrame({"asin": ["B0PREEXIS1"], "junk": ["data"]})
    out = step.run_step(pre, {"asin": "B0EXAMPLE1"})
    assert "B0PREEXIS1" not in out["asin"].tolist()
    assert out.iloc[0]["asin"] == "B0EXAMPLE1"
