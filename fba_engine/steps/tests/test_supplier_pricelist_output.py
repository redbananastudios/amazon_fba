"""Tests for fba_engine.steps.supplier_pricelist_output.

Stage 06 of the canonical engine: writes the timestamped run folder
with shortlist CSV + XLSX + report MD. Returns the input df unchanged
so the step composes cleanly in a strategy chain.

The actual writers (csv_writer, excel_writer, markdown_report) live in
the canonical engine and are tested independently. These tests pin the
boundary contract: required config, file presence, df pass-through.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fba_engine.steps.supplier_pricelist_output import (
    run_step,
    write_outputs,
)


def _row(**overrides) -> dict:
    base = {
        "supplier": "test-supplier", "supplier_sku": "SKU-A",
        "ean": "5012345678900", "asin": "B0CLEAN001",
        "match_type": "UNIT", "buy_cost": 5.0, "market_price": 14.5,
        "decision": "SHORTLIST", "decision_reason": "ROI gate cleared",
        "raw_conservative_price": 13.0, "floored_conservative_price": 13.0,
        "fees_current": 4.0, "fees_conservative": 4.5,
        "profit_current": 5.5, "profit_conservative": 4.0,
        "margin_current": 0.38, "margin_conservative": 0.31,
        "roi_current": 1.10, "roi_conservative": 0.80,
        "breakeven_price": 9.5, "capital_exposure": 5.0,
        "sales_estimate": 200, "gated": "N",
        "price_basis": "FBA", "risk_flags": [],
    }
    base.update(overrides)
    return base


class TestWriteOutputs:
    def test_writes_csv_xlsx_and_md(self, tmp_path):
        df = pd.DataFrame([_row()])
        write_outputs(
            df, run_dir=tmp_path, timestamp="20260429_120000",
            supplier_label="Test Supplier",
        )
        assert (tmp_path / "shortlist_20260429_120000.csv").exists()
        assert (tmp_path / "shortlist_20260429_120000.xlsx").exists()
        assert (tmp_path / "report_20260429_120000.md").exists()

    def test_empty_df_produces_no_files(self, tmp_path):
        write_outputs(
            pd.DataFrame(), run_dir=tmp_path, timestamp="20260429_120000",
            supplier_label="Test Supplier",
        )
        assert not any(tmp_path.iterdir())

    def test_run_dir_created_if_missing(self, tmp_path):
        nested = tmp_path / "out" / "20260429_120000"
        df = pd.DataFrame([_row()])
        write_outputs(
            df, run_dir=nested, timestamp="20260429_120000",
            supplier_label="Test Supplier",
        )
        assert nested.is_dir()
        assert (nested / "shortlist_20260429_120000.csv").exists()


class TestRunStep:
    def test_run_step_passes_df_through_unchanged(self, tmp_path):
        df = pd.DataFrame([_row()])
        out = run_step(df, {
            "run_dir": str(tmp_path),
            "timestamp": "20260429_120000",
            "supplier_label": "Test",
        })
        # Same shape returned for downstream chaining.
        assert len(out) == len(df)
        assert list(out.columns) == list(df.columns)

    def test_run_step_writes_files(self, tmp_path):
        df = pd.DataFrame([_row()])
        run_step(df, {
            "run_dir": str(tmp_path),
            "timestamp": "20260429_120000",
            "supplier_label": "Test",
        })
        assert (Path(tmp_path) / "shortlist_20260429_120000.csv").exists()

    def test_run_step_requires_run_dir(self):
        df = pd.DataFrame([_row()])
        with pytest.raises(ValueError, match="run_dir"):
            run_step(df, {"timestamp": "20260429_120000"})

    def test_run_step_requires_timestamp(self, tmp_path):
        df = pd.DataFrame([_row()])
        with pytest.raises(ValueError, match="timestamp"):
            run_step(df, {"run_dir": str(tmp_path)})

    def test_run_step_empty_df_passes_through(self, tmp_path):
        out = run_step(pd.DataFrame(), {
            "run_dir": str(tmp_path),
            "timestamp": "20260429_120000",
            "supplier_label": "Test",
        })
        assert out.empty
