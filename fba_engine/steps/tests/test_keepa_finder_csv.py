"""Tests for fba_engine.steps.keepa_finder_csv.

Covers:
  - canonical column mapping from Keepa export → engine schema
  - global category + title keyword exclusion
  - ASIN dedup against exclusions.csv
  - numeric coercion (money, integer, percent fields)
  - sidecar metadata load + drift warning
  - empty / malformed input tolerance
  - smoke test against a real Keepa export (column-name validation)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

# Make shared/lib/python importable for fba_config_loader (used by the step).
HERE = Path(__file__).resolve()
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "shared" / "lib" / "python"))

import fba_config_loader  # noqa: E402

from fba_engine.steps import keepa_finder_csv as step  # noqa: E402


# ────────────────────────────────────────────────────────────────────────
# Fixtures.
# ────────────────────────────────────────────────────────────────────────


# Subset of the real Keepa export header (175+ columns). The step only
# reads ~21 columns by name; we include those plus a couple extras to
# prove the step ignores the rest gracefully.
_KEEPA_HEADER = [
    "ASIN", "Title", "Brand", "Manufacturer",
    "Categories: Root", "Categories: Sub", "Categories: Tree",
    "Product Codes: EAN", "Product Codes: UPC",
    "Buy Box: Current", "Buy Box: 90 days avg.",
    "New, 3rd Party FBA: Current",
    "Amazon: Current",
    "FBA Pick&Pack Fee", "Referral Fee %",
    "Bought in past month",
    "New FBA Offer Count: Current",
    "Sales Rank: Current", "Sales Rank: 90 days avg.",
    "Buy Box: % Amazon 90 days", "Buy Box: 90 days OOS",
    "Buy Box: 30 days drop %", "Buy Box: 90 days drop %",
    # Extras the step ignores (proves we don't choke on full exports):
    "Last Price Change", "Reviews: Rating Count",
]


def _fixture_row(**overrides) -> dict[str, str]:
    """Build a Keepa export row. All values stringly-typed (matches a real CSV)."""
    base = {
        "ASIN": "B0EXAMPLE1",
        "Title": "Acme Widget Pro 200ml",
        "Brand": "Acme",
        "Manufacturer": "Acme Manufacturing Ltd",
        "Categories: Root": "Toys & Games",
        "Categories: Sub": "Action Figures",
        "Categories: Tree": "Toys & Games > Action Figures > Collectables",
        "Product Codes: EAN": "5012345678901",
        "Product Codes: UPC": "012345678905",
        "Buy Box: Current": "24.99",
        "Buy Box: 90 days avg.": "26.50",
        "New, 3rd Party FBA: Current": "25.49",
        "Amazon: Current": "",                   # OFF_LISTING (Amazon not selling)
        "FBA Pick&Pack Fee": "3.35",
        "Referral Fee %": "15 %",                # Keepa-formatted percent
        "Bought in past month": "150",
        "New FBA Offer Count: Current": "5",
        "Sales Rank: Current": "12345",
        "Sales Rank: 90 days avg.": "13500",
        "Buy Box: % Amazon 90 days": "5 %",
        "Buy Box: 90 days OOS": "2",
        "Buy Box: 30 days drop %": "3",
        "Buy Box: 90 days drop %": "5",
        "Last Price Change": "2026-04-30",
        "Reviews: Rating Count": "234",
    }
    base.update(overrides)
    return base


def _write_fixture(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    """Write a Keepa-shaped CSV fixture and return its path."""
    csv_path = tmp_path / "keepa_export.csv"
    df = pd.DataFrame(rows, columns=_KEEPA_HEADER)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


def _write_canonical_config(tmp_path: Path, *, hazmat=True, cats=None, kws=None) -> Path:
    """Write a config-dir fixture with the three required YAMLs."""
    cdir = tmp_path / "config"
    cdir.mkdir(exist_ok=True)
    src_cdir = REPO / "shared" / "config"
    (cdir / "business_rules.yaml").write_text((src_cdir / "business_rules.yaml").read_text())
    (cdir / "decision_thresholds.yaml").write_text(
        (src_cdir / "decision_thresholds.yaml").read_text()
    )
    cats = cats if cats is not None else ["Clothing, Shoes & Jewellery"]
    kws = kws if kws is not None else ["clothing", "apparel", "shoe", "boot", "footwear"]
    (cdir / "global_exclusions.yaml").write_text(f"""
hazmat_strict: {str(hazmat).lower()}
categories_excluded:
{chr(10).join('  - "' + c + '"' for c in cats) if cats else "  []"}
title_keywords_excluded:
{chr(10).join('  - ' + k for k in kws) if kws else "  []"}
""")
    return cdir


def _write_exclusions(tmp_path: Path, asins: list[str]) -> Path:
    """Write an ASIN dedup CSV in the exclusions.csv format."""
    p = tmp_path / "exclusions.csv"
    lines = ["ASIN,Niche,Verdict,Reason,Date Added,Source Phase"]
    for a in asins:
        lines.append(f"{a},test,NO,test,2026-05-01,Phase 3")
    p.write_text("\n".join(lines) + "\n")
    return p


# ────────────────────────────────────────────────────────────────────────
# Cache reset between tests — global_exclusions is cached.
# ────────────────────────────────────────────────────────────────────────


def setup_function():
    fba_config_loader.reset_cache()


# ────────────────────────────────────────────────────────────────────────
# Canonical column mapping.
# ────────────────────────────────────────────────────────────────────────


def test_clean_row_maps_to_canonical_schema(tmp_path):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "amazon_oos_wholesale", config_dir=cdir)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["asin"] == "B0EXAMPLE1"
    assert row["source"] == "keepa_finder"
    assert row["discovery_strategy"] == "amazon_oos_wholesale"
    assert row["product_name"] == "Acme Widget Pro 200ml"
    assert row["brand"] == "Acme"
    assert row["manufacturer"] == "Acme Manufacturing Ltd"
    assert row["category"] == "Collectables"          # leaf of Tree
    assert row["category_root"] == "Toys & Games"
    assert row["ean"] == "5012345678901"
    assert row["upc"] == "012345678905"
    assert row["amazon_url"] == "https://www.amazon.co.uk/dp/B0EXAMPLE1"


def test_numeric_coercion_money_fields(tmp_path):
    """Money fields (and percent fields) parse via parse_money — tolerant of strings."""
    csv = _write_fixture(tmp_path, [
        _fixture_row(**{
            "Buy Box: Current": "24.99",
            "Buy Box: 90 days avg.": "GBP26.50",
            "New, 3rd Party FBA: Current": "25.49",
            "Sales Rank: Current": "12345",
            "Buy Box: % Amazon 90 days": "8 %",
        })
    ])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    row = df.iloc[0]
    assert row["buy_box_price"] == 24.99
    assert row["buy_box_avg90"] == 26.5
    assert row["new_fba_price"] == 25.49
    assert row["bsr_current"] == 12345.0
    assert row["buy_box_pct_amazon_90d"] == 8.0


def test_keepa_dash_sentinel_becomes_zero(tmp_path):
    """Keepa exports '-' for missing values. parse_money returns 0.0."""
    csv = _write_fixture(tmp_path, [
        _fixture_row(**{
            "Buy Box: Current": "-",
            "Bought in past month": "-",
        })
    ])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    row = df.iloc[0]
    assert row["buy_box_price"] == 0.0
    assert row["sales_estimate"] == 0.0


def test_referral_fee_pct_divides_by_100(tmp_path):
    """Keepa exports '15 %' / '15.01 %'; calculate.py expects fraction (0.15)."""
    csv = _write_fixture(tmp_path, [
        _fixture_row(**{"Referral Fee %": "15 %"}),
        _fixture_row(ASIN="B0FRACTION", **{"Referral Fee %": "15.01 %"}),
    ])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    assert df.iloc[0]["referral_fee_pct"] == 0.15
    assert abs(df.iloc[1]["referral_fee_pct"] - 0.1501) < 1e-9


def test_amazon_status_derivation(tmp_path):
    """Amazon: Current > 0 → ON_LISTING; empty / 0 / '-' → OFF_LISTING."""
    csv = _write_fixture(tmp_path, [
        _fixture_row(ASIN="B0AMZON001", **{"Amazon: Current": "29.99"}),  # selling
        _fixture_row(ASIN="B0AMZOFF01", **{"Amazon: Current": ""}),        # not selling
        _fixture_row(ASIN="B0AMZOFF02", **{"Amazon: Current": "-"}),       # not selling (sentinel)
        _fixture_row(ASIN="B0AMZOFF03", **{"Amazon: Current": "0"}),       # not selling
    ])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    assert df.set_index("asin").loc["B0AMZON001", "amazon_status"] == "ON_LISTING"
    assert df.set_index("asin").loc["B0AMZOFF01", "amazon_status"] == "OFF_LISTING"
    assert df.set_index("asin").loc["B0AMZOFF02", "amazon_status"] == "OFF_LISTING"
    assert df.set_index("asin").loc["B0AMZOFF03", "amazon_status"] == "OFF_LISTING"


def test_wholesale_defaults_buy_cost_zero_moq_one(tmp_path):
    """Keepa-finder is wholesale-leads — buy_cost=0 tells calculate.py
    to emit max_buy_price; moq=1 because no MOQ at lead-stage."""
    csv = _write_fixture(tmp_path, [_fixture_row()])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    row = df.iloc[0]
    assert row["buy_cost"] == 0.0
    assert row["moq"] == 1


def test_canonical_columns_include_calculate_inputs(tmp_path):
    """Sanity: every column calculate.py reads must be present and the order
    matches KEEPA_FINDER_CANONICAL_COLUMNS."""
    csv = _write_fixture(tmp_path, [_fixture_row()])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    required = {
        # Read directly by calculate.calculate_economics + _calculate_match
        "buy_box_price", "new_fba_price", "fba_seller_count",
        "amazon_status", "buy_cost", "sales_estimate",
        "fba_pick_pack_fee", "referral_fee_pct", "moq",
    }
    missing = required - set(df.columns)
    assert not missing, f"calculate.py inputs missing from canonical schema: {missing}"


def test_canonical_column_order_is_stable(tmp_path):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    assert tuple(df.columns) == step.KEEPA_FINDER_CANONICAL_COLUMNS


def test_leaf_category_falls_back_through_tree_sub_root(tmp_path):
    csv = _write_fixture(tmp_path, [
        _fixture_row(**{
            "Categories: Tree": "",
            "Categories: Sub": "Building Toys, Construction Sets",
            "Categories: Root": "Toys & Games",
        })
    ])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    # Sub is comma-separated → take first.
    assert df.iloc[0]["category"] == "Building Toys"


def test_leaf_category_falls_back_to_root_when_sub_empty(tmp_path):
    csv = _write_fixture(tmp_path, [
        _fixture_row(**{
            "Categories: Tree": "",
            "Categories: Sub": "",
            "Categories: Root": "Toys & Games",
        })
    ])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    assert df.iloc[0]["category"] == "Toys & Games"


# ────────────────────────────────────────────────────────────────────────
# Global exclusions — title keywords.
# ────────────────────────────────────────────────────────────────────────


def test_title_with_excluded_keyword_is_dropped(tmp_path):
    csv = _write_fixture(tmp_path, [
        _fixture_row(ASIN="B0KEEP0001", Title="Acme Action Figure"),                # keep
        _fixture_row(ASIN="B0DROP0001", Title="Mens Shoes Size 10"),                # drop: shoe
        _fixture_row(ASIN="B0DROP0002", Title="Premium Apparel Hanger"),            # drop: apparel
        _fixture_row(ASIN="B0DROP0003", Title="Hiking Boots — All Terrain"),        # drop: boot
        _fixture_row(ASIN="B0KEEP0002", Title="Bootstrap Programming Book"),        # drop: boot (substring) — this proves the limitation
    ])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    # Substring match catches "Bootstrap" too — that's the documented behaviour.
    # Future tuning lives in global_exclusions.yaml, not in step code.
    assert len(df) == 1
    assert df.iloc[0]["asin"] == "B0KEEP0001"


def test_title_keyword_exclusion_is_case_insensitive(tmp_path):
    csv = _write_fixture(tmp_path, [
        _fixture_row(ASIN="B0DROP0001", Title="MENS CLOTHING SET"),
        _fixture_row(ASIN="B0DROP0002", Title="womens clothing"),
        _fixture_row(ASIN="B0KEEP0001", Title="Construction Toy"),
    ])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    assert len(df) == 1
    assert df.iloc[0]["asin"] == "B0KEEP0001"


# ────────────────────────────────────────────────────────────────────────
# Global exclusions — categories.
# ────────────────────────────────────────────────────────────────────────


def test_excluded_category_root_is_dropped(tmp_path):
    csv = _write_fixture(tmp_path, [
        _fixture_row(
            ASIN="B0CLOTH001", Title="Cool TShirt",
            **{"Categories: Root": "Clothing, Shoes & Jewellery"},
        ),
        _fixture_row(ASIN="B0KEEP0001", Title="Action Figure"),
    ])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    assert len(df) == 1
    assert df.iloc[0]["asin"] == "B0KEEP0001"


def test_excluded_category_match_is_case_insensitive(tmp_path):
    csv = _write_fixture(tmp_path, [
        _fixture_row(
            ASIN="B0DROP0001",
            **{"Categories: Root": "  CLOTHING, SHOES & JEWELLERY  "},
        ),
        _fixture_row(ASIN="B0KEEP0001"),
    ])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    assert len(df) == 1
    assert df.iloc[0]["asin"] == "B0KEEP0001"


# ────────────────────────────────────────────────────────────────────────
# ASIN exclusions list.
# ────────────────────────────────────────────────────────────────────────


def test_asin_exclusions_drops_listed_asins(tmp_path):
    csv = _write_fixture(tmp_path, [
        _fixture_row(ASIN="B0DROP0001"),
        _fixture_row(ASIN="B0KEEP0001"),
    ])
    excl = _write_exclusions(tmp_path, ["B0DROP0001"])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(
        csv, "x", exclusions_path=excl, config_dir=cdir,
    )
    assert len(df) == 1
    assert df.iloc[0]["asin"] == "B0KEEP0001"


def test_asin_exclusions_match_is_case_insensitive(tmp_path):
    csv = _write_fixture(tmp_path, [_fixture_row(ASIN="b0lower001")])
    excl = _write_exclusions(tmp_path, ["B0LOWER001"])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(
        csv, "x", exclusions_path=excl, config_dir=cdir,
    )
    assert len(df) == 0


def test_missing_exclusions_file_is_no_op(tmp_path):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(
        csv, "x",
        exclusions_path=tmp_path / "does_not_exist.csv",
        config_dir=cdir,
    )
    assert len(df) == 1


# ────────────────────────────────────────────────────────────────────────
# Malformed input tolerance.
# ────────────────────────────────────────────────────────────────────────


def test_empty_csv_returns_empty_canonical_df(tmp_path):
    csv = tmp_path / "empty.csv"
    csv.write_text(",".join(_KEEPA_HEADER) + "\n")
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    assert df.empty
    assert tuple(df.columns) == step.KEEPA_FINDER_CANONICAL_COLUMNS


def test_missing_asin_column_raises(tmp_path):
    csv = tmp_path / "wrong.csv"
    csv.write_text("Title,Brand\nA Widget,Acme\n")
    cdir = _write_canonical_config(tmp_path)
    with pytest.raises(ValueError, match="not a Keepa Product Finder export"):
        step.discover_keepa_finder(csv, "x", config_dir=cdir)


def test_malformed_asin_is_silently_dropped(tmp_path):
    csv = _write_fixture(tmp_path, [
        _fixture_row(ASIN=""),                  # empty
        _fixture_row(ASIN="B0SHORT"),           # too short
        _fixture_row(ASIN="B0TOOLONG0001"),     # too long
        _fixture_row(ASIN="B0VALID001"),       # 10 chars — keep
    ])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(csv, "x", config_dir=cdir)
    assert len(df) == 1
    assert df.iloc[0]["asin"] == "B0VALID001"


def test_missing_csv_file_raises(tmp_path):
    cdir = _write_canonical_config(tmp_path)
    with pytest.raises(FileNotFoundError):
        step.discover_keepa_finder(tmp_path / "nope.csv", "x", config_dir=cdir)


def test_empty_recipe_arg_raises(tmp_path):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    cdir = _write_canonical_config(tmp_path)
    with pytest.raises(ValueError, match="recipe is required"):
        step.discover_keepa_finder(csv, "", config_dir=cdir)


# ────────────────────────────────────────────────────────────────────────
# Recipe metadata sidecar.
# ────────────────────────────────────────────────────────────────────────


def test_metadata_sidecar_loads_when_present(tmp_path):
    """Metadata sidecar is informational — load shouldn't fail or alter output."""
    csv = _write_fixture(tmp_path, [_fixture_row()])
    meta = tmp_path / "recipe_metadata.json"
    meta.write_text(json.dumps({
        "recipe": "amazon_oos_wholesale",
        "category": "Toys & Games",
        "rows_exported": 1,
        "calculate_config": {"compute_stability_score": True},
    }))
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(
        csv, "amazon_oos_wholesale",
        metadata_path=meta, config_dir=cdir,
    )
    assert len(df) == 1


def test_metadata_recipe_mismatch_warns_but_runs(tmp_path, caplog):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    meta = tmp_path / "recipe_metadata.json"
    meta.write_text(json.dumps({"recipe": "stable_price_low_volatility"}))
    cdir = _write_canonical_config(tmp_path)
    with caplog.at_level("WARNING"):
        df = step.discover_keepa_finder(
            csv, "amazon_oos_wholesale",
            metadata_path=meta, config_dir=cdir,
        )
    assert len(df) == 1
    assert df.iloc[0]["discovery_strategy"] == "amazon_oos_wholesale"  # arg wins
    assert any("metadata recipe" in rec.message for rec in caplog.records)


def test_missing_metadata_sidecar_is_tolerated(tmp_path):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    cdir = _write_canonical_config(tmp_path)
    df = step.discover_keepa_finder(
        csv, "x",
        metadata_path=tmp_path / "no_such_metadata.json",
        config_dir=cdir,
    )
    assert len(df) == 1


def test_unreadable_metadata_logs_and_continues(tmp_path, caplog):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    bad_meta = tmp_path / "recipe_metadata.json"
    bad_meta.write_text("not valid json {{{")
    cdir = _write_canonical_config(tmp_path)
    with caplog.at_level("WARNING"):
        df = step.discover_keepa_finder(
            csv, "x", metadata_path=bad_meta, config_dir=cdir,
        )
    assert len(df) == 1
    assert any("unreadable" in rec.message for rec in caplog.records)


# ────────────────────────────────────────────────────────────────────────
# run_step — the strategy-runner contract.
# ────────────────────────────────────────────────────────────────────────


def test_run_step_requires_csv_path(tmp_path):
    with pytest.raises(ValueError, match="csv_path"):
        step.run_step(pd.DataFrame(), {"recipe": "x"})


def test_run_step_requires_recipe(tmp_path):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    with pytest.raises(ValueError, match="recipe"):
        step.run_step(pd.DataFrame(), {"csv_path": str(csv)})


def test_run_step_returns_canonical_df(tmp_path):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    cdir = _write_canonical_config(tmp_path)
    df = step.run_step(pd.DataFrame(), {
        "csv_path": str(csv),
        "recipe": "amazon_oos_wholesale",
        "config_dir": str(cdir),
    })
    assert len(df) == 1
    assert df.iloc[0]["discovery_strategy"] == "amazon_oos_wholesale"


# ────────────────────────────────────────────────────────────────────────
# Smoke test against a real Keepa Product Finder export.
# Validates that the column-name mapping holds against production data —
# if Keepa renames a column, this test catches it before strategies fail
# silently.
# ────────────────────────────────────────────────────────────────────────


REAL_KEEPA_EXPORT = (
    REPO / "fba_engine" / "data" / "niches" / "kids-toys"
    / "working" / "kids_toys_phase1_raw.csv"
)


@pytest.mark.skipif(
    not REAL_KEEPA_EXPORT.exists(),
    reason="real Keepa export not present (gitignored data folder)",
)
def test_real_keepa_export_produces_canonical_df():
    """Smoke: feed a real Keepa Product Finder export and confirm the
    column-mapper produces a non-empty canonical DataFrame."""
    fba_config_loader.reset_cache()
    df = step.discover_keepa_finder(REAL_KEEPA_EXPORT, "amazon_oos_wholesale")
    # Real export has ~10k rows; after global exclusions some get dropped.
    # Key invariants:
    #   - canonical schema preserved
    #   - all ASINs are 10 chars
    #   - product_name populated for >90% of rows (Keepa always exports a title)
    #   - source / discovery_strategy populated for every row
    assert tuple(df.columns) == step.KEEPA_FINDER_CANONICAL_COLUMNS
    assert len(df) > 100, f"real export produced only {len(df)} rows after filters"
    assert (df["asin"].str.len() == 10).all()
    assert (df["source"] == "keepa_finder").all()
    assert (df["discovery_strategy"] == "amazon_oos_wholesale").all()
    assert (df["product_name"].str.len() > 0).mean() > 0.9
    # buy_box_price + sales_estimate are the load-bearing signals for the
    # downstream calculate step. If these are all 0 something's wrong with
    # the column-name mapping (Keepa may have renamed).
    assert (df["buy_box_price"] > 0).any(), \
        "no rows have buy_box_price — Buy Box: Current column name may have drifted"
    assert (df["sales_estimate"] > 0).any(), \
        "no rows have sales_estimate — Bought in past month column name may have drifted"
    # Sanity: the wholesale-default columns are populated for every row.
    assert (df["buy_cost"] == 0.0).all()
    assert (df["moq"] == 1).all()
    # Referral fee was divided by 100 (Keepa "15 %" → 0.15)
    assert (df["referral_fee_pct"] <= 1.0).all()
    assert (df["referral_fee_pct"] > 0.0).any()
