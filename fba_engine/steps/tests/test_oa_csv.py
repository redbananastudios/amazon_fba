"""Tests for fba_engine.steps.oa_csv — OA discovery step.

The importer abstraction is tested in
`shared/lib/python/oa_importers/tests/test_oa_importers.py`.
This file tests the discovery-step layer: feed dispatch, exclusions
filtering, run_step contract, CLI shape.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fba_engine.steps.oa_csv import (
    OA_DISCOVERY_COLUMNS,
    discover_oa_candidates,
    load_exclusions,
    run_step,
)


def _write_selleramp_csv(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    """Write a minimal SellerAmp 2DSorter-shaped CSV with ASIN + Buy Cost."""
    path = tmp_path / "feed.csv"
    body = "ASIN,Buy Cost\n"
    for asin, cost in rows:
        body += f"{asin},{cost}\n"
    path.write_text(body, encoding="utf-8")
    return path


def _write_exclusions_csv(tmp_path: Path, asins: list[str]) -> Path:
    path = tmp_path / "exclusions.csv"
    body = "ASIN,Niche,Verdict,Reason,Date Added,Source Phase\n"
    for asin in asins:
        body += f"{asin},test,NO,test reason,2026-04-30,Phase 3\n"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# discover_oa_candidates
# ---------------------------------------------------------------------------


class TestDiscoverOaCandidates:
    def test_returns_df_with_canonical_columns(self, tmp_path: Path):
        csv = _write_selleramp_csv(tmp_path, [("B0001", "9.99")])
        df = discover_oa_candidates(
            feed="selleramp", csv_path=csv,
            exclusions_path=tmp_path / "missing_excl.csv",
        )
        assert list(df.columns) == list(OA_DISCOVERY_COLUMNS)

    def test_unknown_feed_raises(self, tmp_path: Path):
        csv = _write_selleramp_csv(tmp_path, [("B0", "1.00")])
        with pytest.raises(ValueError, match="Unknown OA feed"):
            discover_oa_candidates(
                feed="not_a_real_feed", csv_path=csv,
                exclusions_path=tmp_path / "missing.csv",
            )

    def test_source_column_constant_oa_csv(self, tmp_path: Path):
        csv = _write_selleramp_csv(tmp_path, [("B0001", "9.99"), ("B0002", "10")])
        df = discover_oa_candidates(
            feed="selleramp", csv_path=csv,
            exclusions_path=tmp_path / "missing.csv",
        )
        assert (df["source"] == "oa_csv").all()

    def test_feed_column_propagates_importer_id(self, tmp_path: Path):
        csv = _write_selleramp_csv(tmp_path, [("B0001", "9.99")])
        df = discover_oa_candidates(
            feed="selleramp", csv_path=csv,
            exclusions_path=tmp_path / "missing.csv",
        )
        assert df.iloc[0]["feed"] == "selleramp"

    def test_exclusions_filter_drops_excluded_asins(self, tmp_path: Path):
        csv = _write_selleramp_csv(
            tmp_path, [("B0KEEP", "9.99"), ("B0DROP", "10")]
        )
        excl = _write_exclusions_csv(tmp_path, ["B0DROP"])
        df = discover_oa_candidates(
            feed="selleramp", csv_path=csv, exclusions_path=excl,
        )
        assert list(df["asin"]) == ["B0KEEP"]

    def test_exclusions_match_is_case_insensitive(self, tmp_path: Path):
        # Importer yields uppercase ASINs (Amazon convention); exclusions
        # CSV may store them lowercase. Verify the comparison is case-blind.
        csv = _write_selleramp_csv(tmp_path, [("B0DROP", "9.99")])
        excl_path = tmp_path / "excl.csv"
        excl_path.write_text(
            "ASIN,Niche,Verdict,Reason,Date Added,Source Phase\n"
            "b0drop,test,NO,r,2026-04-30,Phase 3\n",
            encoding="utf-8",
        )
        df = discover_oa_candidates(
            feed="selleramp", csv_path=csv, exclusions_path=excl_path,
        )
        assert len(df) == 0

    def test_missing_exclusions_file_treated_as_empty_set(self, tmp_path: Path):
        csv = _write_selleramp_csv(tmp_path, [("B0KEEP", "9.99")])
        df = discover_oa_candidates(
            feed="selleramp", csv_path=csv,
            exclusions_path=tmp_path / "does-not-exist.csv",
        )
        assert len(df) == 1

    def test_empty_input_returns_empty_df_with_columns(self, tmp_path: Path):
        csv = _write_selleramp_csv(tmp_path, [])
        df = discover_oa_candidates(
            feed="selleramp", csv_path=csv,
            exclusions_path=tmp_path / "missing.csv",
        )
        assert len(df) == 0
        assert list(df.columns) == list(OA_DISCOVERY_COLUMNS)


class TestLoadExclusions:
    def test_returns_uppercase_asin_set(self, tmp_path: Path):
        path = _write_exclusions_csv(tmp_path, ["B0AAAA", "b0bbbb"])
        excl = load_exclusions(path)
        assert "B0AAAA" in excl
        assert "B0BBBB" in excl  # uppercased

    def test_missing_file_returns_empty_set(self, tmp_path: Path):
        excl = load_exclusions(tmp_path / "does-not-exist.csv")
        assert excl == set()

    def test_no_asin_column_returns_empty_set(self, tmp_path: Path):
        path = tmp_path / "no_asin.csv"
        path.write_text("Foo,Bar\n1,2\n", encoding="utf-8")
        assert load_exclusions(path) == set()


# ---------------------------------------------------------------------------
# run_step contract
# ---------------------------------------------------------------------------


class TestRunStep:
    def test_run_step_requires_feed(self):
        with pytest.raises(ValueError, match="feed"):
            run_step(pd.DataFrame(), {"csv_path": "x"})

    def test_run_step_requires_csv_path(self):
        with pytest.raises(ValueError, match="csv_path"):
            run_step(pd.DataFrame(), {"feed": "selleramp"})

    def test_run_step_returns_canonical_columns(self, tmp_path: Path):
        csv = _write_selleramp_csv(tmp_path, [("B0001", "9.99")])
        out = run_step(
            pd.DataFrame(),  # ignored — discovery creates the df
            {
                "feed": "selleramp", "csv_path": str(csv),
                "exclusions_path": str(tmp_path / "missing.csv"),
            },
        )
        assert list(out.columns) == list(OA_DISCOVERY_COLUMNS)
