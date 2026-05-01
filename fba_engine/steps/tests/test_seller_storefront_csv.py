"""Tests for fba_engine.steps.seller_storefront_csv.

The step delegates column mapping to keepa_finder_csv (because the
Keepa Browser export schema is identical between the Product Finder
and Seller Storefront pages). These tests verify the storefront-
specific tagging — `source`, `discovery_strategy`, and `seller_id`
columns — and the run_step config contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# Make shared/lib/python importable for fba_config_loader.
HERE = Path(__file__).resolve()
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "shared" / "lib" / "python"))

import fba_config_loader  # noqa: E402

from fba_engine.steps import seller_storefront_csv as step  # noqa: E402


# ────────────────────────────────────────────────────────────────────────
# Fixtures.
# ────────────────────────────────────────────────────────────────────────


# Same Keepa Browser column shape as the Product Finder export — verified
# by inspecting a real `KeepaExport-...-SellerOverview-2-<seller>.csv`
# downloaded from Keepa's Pro → Seller Lookup → Storefront tab.
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
]


def _fixture_row(**overrides) -> dict[str, str]:
    base = {
        "ASIN": "B0SELLER01",
        "Title": "Henry Genuine NVM-1CH 907075 HepaFlo Vacuum Bags",
        "Brand": "Henry",
        "Manufacturer": "Numatic International",
        "Categories: Root": "Home & Kitchen",
        "Categories: Sub": "Cylinder Bags",
        "Categories: Tree": "Home & Kitchen > Vacuum Cleaners > Cylinder Bags",
        "Product Codes: EAN": "5012345678901",
        "Product Codes: UPC": "012345678905",
        "Buy Box: Current": "10.69",
        "Buy Box: 90 days avg.": "11.84",
        "New, 3rd Party FBA: Current": "10.99",
        "Amazon: Current": "",
        "FBA Pick&Pack Fee": "3.35",
        "Referral Fee %": "15 %",
        "Bought in past month": "3000",
        "New FBA Offer Count: Current": "4",
        "Sales Rank: Current": "294",
        "Sales Rank: 90 days avg.": "231",
        "Buy Box: % Amazon 90 days": "0 %",
        "Buy Box: 90 days OOS": "0",
        "Buy Box: 30 days drop %": "0",
        "Buy Box: 90 days drop %": "0",
    }
    base.update(overrides)
    return base


def _write_fixture(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    csv_path = tmp_path / "keepa_storefront.csv"
    df = pd.DataFrame(rows, columns=_KEEPA_HEADER)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


def _write_canonical_config(tmp_path: Path) -> Path:
    cdir = tmp_path / "config"
    cdir.mkdir(exist_ok=True)
    src_cdir = REPO / "shared" / "config"
    (cdir / "business_rules.yaml").write_text((src_cdir / "business_rules.yaml").read_text())
    (cdir / "decision_thresholds.yaml").write_text(
        (src_cdir / "decision_thresholds.yaml").read_text()
    )
    (cdir / "global_exclusions.yaml").write_text(
        "hazmat_strict: true\n"
        "categories_excluded:\n"
        '  - "Clothing, Shoes & Jewellery"\n'
        "title_keywords_excluded:\n"
        "  - clothing\n"
        "  - shoe\n"
    )
    return cdir


def _write_empty_exclusions(tmp_path: Path) -> Path:
    p = tmp_path / "exclusions.csv"
    p.write_text("ASIN,Niche,Verdict,Reason,Date Added,Source Phase\n")
    return p


def setup_function():
    fba_config_loader.reset_cache()


# ────────────────────────────────────────────────────────────────────────
# Tagging — the storefront-specific bits.
# ────────────────────────────────────────────────────────────────────────


def test_source_tag_is_seller_storefront(tmp_path):
    """source must be "seller_storefront" — distinct from keepa_finder
    so downstream artefacts (CSV / XLSX / Sheet) make lineage explicit."""
    csv = _write_fixture(tmp_path, [_fixture_row()])
    df = step.discover_seller_storefront_csv(
        csv_path=csv,
        seller_id="AR5NTANTFUHVI",
        exclusions_path=_write_empty_exclusions(tmp_path),
        config_dir=_write_canonical_config(tmp_path),
    )
    assert (df["source"] == "seller_storefront").all()


def test_discovery_strategy_tag_includes_seller_id(tmp_path):
    """discovery_strategy = seller_storefront_<seller_id> so concatenated
    storefront walks against multiple competitors stay distinguishable."""
    csv = _write_fixture(tmp_path, [_fixture_row()])
    df = step.discover_seller_storefront_csv(
        csv_path=csv,
        seller_id="AR5NTANTFUHVI",
        exclusions_path=_write_empty_exclusions(tmp_path),
        config_dir=_write_canonical_config(tmp_path),
    )
    assert (df["discovery_strategy"] == "seller_storefront_AR5NTANTFUHVI").all()


def test_seller_id_column_added_to_every_row(tmp_path):
    """seller_id is a per-row column (vs run-level metadata) so row-level
    filtering / pivoting works after concatenating outputs."""
    csv = _write_fixture(tmp_path, [
        _fixture_row(ASIN="B0SELLER01"),
        _fixture_row(ASIN="B0SELLER02", Title="Henry Cylinder Bag Pack"),
    ])
    df = step.discover_seller_storefront_csv(
        csv_path=csv,
        seller_id="AR5NTANTFUHVI",
        exclusions_path=_write_empty_exclusions(tmp_path),
        config_dir=_write_canonical_config(tmp_path),
    )
    assert len(df) == 2
    assert (df["seller_id"] == "AR5NTANTFUHVI").all()


def test_canonical_columns_inherited_from_keepa_finder_csv(tmp_path):
    """The mapper delegation must yield the full canonical schema —
    asin, buy_box_price, sales_estimate, etc. — proving the underlying
    column-mapping contract still holds."""
    csv = _write_fixture(tmp_path, [_fixture_row()])
    df = step.discover_seller_storefront_csv(
        csv_path=csv,
        seller_id="AR5NTANTFUHVI",
        exclusions_path=_write_empty_exclusions(tmp_path),
        config_dir=_write_canonical_config(tmp_path),
    )
    row = df.iloc[0]
    assert row["asin"] == "B0SELLER01"
    assert row["buy_box_price"] == 10.69
    assert row["sales_estimate"] == 3000
    assert row["amazon_status"] == "OFF_LISTING"  # Amazon: Current empty
    assert row["referral_fee_pct"] == pytest.approx(0.15)
    assert row["buy_cost"] == 0.0  # wholesale flow default
    assert row["moq"] == 1


def test_empty_csv_returns_empty_df_with_seller_id_column(tmp_path):
    """Empty input still returns a DataFrame with the seller_id column
    present so downstream steps that read df["seller_id"] don't blow up."""
    csv = _write_fixture(tmp_path, [])
    df = step.discover_seller_storefront_csv(
        csv_path=csv,
        seller_id="AR5NTANTFUHVI",
        exclusions_path=_write_empty_exclusions(tmp_path),
        config_dir=_write_canonical_config(tmp_path),
    )
    assert df.empty
    assert "seller_id" in df.columns


def test_empty_csv_still_carries_storefront_source_tag(tmp_path):
    """Even with zero rows, the column-level lineage (source + discovery_strategy)
    must reflect storefront tagging — otherwise an empty result inherits the
    underlying mapper's `source="keepa_finder"` schema and confuses any tool that
    reads schema separately from row data (test fixtures, schema linters)."""
    csv = _write_fixture(tmp_path, [])
    df = step.discover_seller_storefront_csv(
        csv_path=csv,
        seller_id="AR5NTANTFUHVI",
        exclusions_path=_write_empty_exclusions(tmp_path),
        config_dir=_write_canonical_config(tmp_path),
    )
    assert df.empty
    # Columns exist; assigning a scalar to an empty DataFrame works because
    # pandas broadcasts to zero rows, not because the value got stored.
    assert "source" in df.columns
    assert "discovery_strategy" in df.columns
    assert "seller_id" in df.columns


# ────────────────────────────────────────────────────────────────────────
# run_step config contract — used by the strategy runner.
# ────────────────────────────────────────────────────────────────────────


def test_run_step_requires_csv_path(tmp_path):
    with pytest.raises(ValueError, match="csv_path"):
        step.run_step(pd.DataFrame(), {"seller_id": "AR5NTANTFUHVI"})


def test_run_step_requires_seller_id(tmp_path):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    with pytest.raises(ValueError, match="seller_id"):
        step.run_step(pd.DataFrame(), {"csv_path": str(csv)})


def test_discover_helper_requires_seller_id(tmp_path):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    with pytest.raises(ValueError, match="seller_id"):
        step.discover_seller_storefront_csv(
            csv_path=csv, seller_id="",
            exclusions_path=_write_empty_exclusions(tmp_path),
            config_dir=_write_canonical_config(tmp_path),
        )


def test_run_step_ignores_input_df(tmp_path):
    """Discovery steps create the DataFrame — input df is ignored, mirroring
    the keepa_finder_csv / oa_csv discovery contract."""
    csv = _write_fixture(tmp_path, [_fixture_row()])
    pre = pd.DataFrame({"asin": ["B0PREEXIST"], "junk": ["data"]})
    out = step.run_step(pre, {
        "csv_path": str(csv),
        "seller_id": "AR5NTANTFUHVI",
        "exclusions_path": str(_write_empty_exclusions(tmp_path)),
        "config_dir": str(_write_canonical_config(tmp_path)),
    })
    # Output is the CSV's content, not the input DataFrame's.
    assert "B0PREEXIST" not in out["asin"].tolist()
    assert "B0SELLER01" in out["asin"].tolist()


def test_run_step_default_recipe_is_seller_storefront(tmp_path, monkeypatch):
    """When no recipe is configured, the step defaults to "seller_storefront"
    so the recipe lookup in cli.strategy still resolves to a sensible
    default decision-overrides JSON."""
    csv = _write_fixture(tmp_path, [_fixture_row()])
    captured: dict = {}

    real_discover = step._kf.discover_keepa_finder

    def spy(**kwargs):
        captured.update(kwargs)
        return real_discover(**kwargs)

    monkeypatch.setattr(step._kf, "discover_keepa_finder", spy)
    step.run_step(pd.DataFrame(), {
        "csv_path": str(csv),
        "seller_id": "AR5NTANTFUHVI",
        "exclusions_path": str(_write_empty_exclusions(tmp_path)),
        "config_dir": str(_write_canonical_config(tmp_path)),
    })
    assert captured["recipe"] == "seller_storefront"


def test_run_step_explicit_recipe_overrides_default(tmp_path, monkeypatch):
    csv = _write_fixture(tmp_path, [_fixture_row()])
    captured: dict = {}
    real_discover = step._kf.discover_keepa_finder

    def spy(**kwargs):
        captured.update(kwargs)
        return real_discover(**kwargs)

    monkeypatch.setattr(step._kf, "discover_keepa_finder", spy)
    step.run_step(pd.DataFrame(), {
        "csv_path": str(csv),
        "seller_id": "AR5NTANTFUHVI",
        "recipe": "custom_recipe_id",
        "exclusions_path": str(_write_empty_exclusions(tmp_path)),
        "config_dir": str(_write_canonical_config(tmp_path)),
    })
    assert captured["recipe"] == "custom_recipe_id"
