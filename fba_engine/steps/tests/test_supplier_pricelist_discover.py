"""Tests for fba_engine.steps.supplier_pricelist_discover.

The discover step is the first step in the supplier_pricelist strategy.
It loads the supplier adapter, ingests + normalises supplier files, and
runs case_detection to derive unit/case costs. The result is a
DataFrame ready for the resolve step (EAN validation + Amazon match).

Deeper end-to-end behaviour is covered by
``shared/lib/python/sourcing_engine/tests/test_integration_pipeline.py``;
these tests pin the step boundary contract.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fba_engine.steps.supplier_pricelist_discover import (
    discover_supplier_pricelist,
    run_step,
)


_SUPPLIER_CSV = """\
SKU,Manufacturer,Title,Category,Barcode,Case Size,Units Available,Unit Price (GBP),Case Price (GBP)
SKU-A,Acme,Widget A,Tools,5012345678900,12,500,5.00,60.00
SKU-B,Acme,Widget B,Tools,5012345678917,1,100,4.00,4.00
"""


def _write_supplier_dir(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "pricelist.csv").write_text(_SUPPLIER_CSV, encoding="utf-8")
    return raw


class TestDiscoverSupplierPricelist:
    def test_returns_normalised_df_with_cost_columns(self, tmp_path):
        raw_dir = _write_supplier_dir(tmp_path)
        df = discover_supplier_pricelist(
            supplier="connect-beauty", input_path=str(raw_dir)
        )
        assert isinstance(df, pd.DataFrame)
        assert len(df) >= 2
        # case_detection populated cost columns
        for col in ("unit_cost_ex_vat", "unit_cost_inc_vat", "case_qty"):
            assert col in df.columns, f"missing {col}"
        # canonical schema columns
        assert "ean" in df.columns
        assert "supplier" in df.columns
        assert "supplier_price_ex_vat" in df.columns

    def test_unit_costs_are_positive(self, tmp_path):
        raw_dir = _write_supplier_dir(tmp_path)
        df = discover_supplier_pricelist(
            supplier="connect-beauty", input_path=str(raw_dir)
        )
        # Every row's unit_cost should be derived (>0) — case detection
        # must run, not just normalise.
        assert (df["unit_cost_ex_vat"] > 0).all()
        assert (df["unit_cost_inc_vat"] > 0).all()

    def test_empty_input_directory_returns_empty_df(self, tmp_path):
        empty_dir = tmp_path / "empty_raw"
        empty_dir.mkdir()
        df = discover_supplier_pricelist(
            supplier="connect-beauty", input_path=str(empty_dir)
        )
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_single_file_path_works(self, tmp_path):
        # input_path can be a single file, not just a directory.
        raw_dir = _write_supplier_dir(tmp_path)
        single = raw_dir / "pricelist.csv"
        df = discover_supplier_pricelist(
            supplier="connect-beauty", input_path=str(single)
        )
        assert len(df) >= 2


class TestRunStep:
    def test_run_step_uses_config_keys(self, tmp_path):
        raw_dir = _write_supplier_dir(tmp_path)
        df = run_step(
            pd.DataFrame(),
            {"supplier": "connect-beauty", "input_path": str(raw_dir)},
        )
        assert isinstance(df, pd.DataFrame)
        assert len(df) >= 2

    def test_run_step_missing_supplier_raises(self):
        with pytest.raises(ValueError, match="supplier"):
            run_step(pd.DataFrame(), {"input_path": "/tmp/x"})

    def test_run_step_missing_input_path_raises(self):
        with pytest.raises(ValueError, match="input_path"):
            run_step(pd.DataFrame(), {"supplier": "connect-beauty"})

    def test_run_step_ignores_input_df(self, tmp_path):
        # The discover step is special: df_in is ignored — discovery
        # creates the df. A non-empty input shouldn't change the output.
        raw_dir = _write_supplier_dir(tmp_path)
        df = run_step(
            pd.DataFrame({"junk": [1, 2, 3]}),
            {"supplier": "connect-beauty", "input_path": str(raw_dir)},
        )
        assert "junk" not in df.columns
        assert len(df) >= 2
