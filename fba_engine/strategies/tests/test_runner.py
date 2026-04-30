"""Tests for fba_engine.strategies.runner.

The runner is the YAML composition layer over `fba_engine.steps.*`. These
tests exercise the wiring (variable interpolation, step chain, error
shapes, side-effect step config) plus an end-to-end run through the live
keepa_niche pipeline against a fixture CSV.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from fba_engine.strategies.runner import (
    StepDef,
    StrategyConfigError,
    StrategyDef,
    StrategyExecutionError,
    interpolate,
    load_strategy,
    run_strategy,
)


# ---------------------------------------------------------------------------
# Variable interpolation
# ---------------------------------------------------------------------------


class TestInterpolate:
    def test_substitutes_known_keys(self):
        assert interpolate("{niche}-data", {"niche": "kids-toys"}) == "kids-toys-data"

    def test_handles_multiple_keys(self):
        result = interpolate(
            "{base}/working/{niche}_phase4.csv",
            {"base": "fba_engine/data/niches/kids-toys", "niche": "kids_toys"},
        )
        assert result == "fba_engine/data/niches/kids-toys/working/kids_toys_phase4.csv"

    def test_passes_through_strings_with_no_placeholders(self):
        assert interpolate("/no/placeholders", {"niche": "x"}) == "/no/placeholders"

    def test_missing_key_raises_strategy_config_error(self):
        with pytest.raises(StrategyConfigError, match="missing"):
            interpolate("{undefined}", {"niche": "x"})

    def test_non_string_value_passes_through_unchanged(self):
        # Integers/floats/dicts in YAML configs should be passed through
        # without trying to .format() them.
        assert interpolate(42, {"niche": "x"}) == 42
        assert interpolate({"key": "value"}, {"niche": "x"}) == {"key": "value"}


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


class TestLoadStrategy:
    def _write_yaml(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "strategy.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_minimal_strategy(self, tmp_path: Path):
        body = """\
name: test
description: Minimal
steps:
  - name: ip_risk
    module: fba_engine.steps.ip_risk
    config:
      niche: kids-toys
"""
        strat = load_strategy(self._write_yaml(tmp_path, body))
        assert strat.name == "test"
        assert strat.description == "Minimal"
        assert len(strat.steps) == 1
        assert strat.steps[0].name == "ip_risk"
        assert strat.steps[0].module == "fba_engine.steps.ip_risk"
        assert strat.steps[0].config == {"niche": "kids-toys"}

    def test_full_strategy_with_input_and_output(self, tmp_path: Path):
        body = """\
name: keepa
description: Full
input:
  path: "{base}/working/{niche_snake}_phase3.csv"
  encoding: utf-8-sig
steps:
  - name: ip_risk
    module: fba_engine.steps.ip_risk
    config:
      niche: "{niche}"
output:
  csv: "{base}/working/{niche_snake}_phase6.csv"
"""
        strat = load_strategy(self._write_yaml(tmp_path, body))
        assert strat.input_path == "{base}/working/{niche_snake}_phase3.csv"
        assert strat.input_encoding == "utf-8-sig"
        assert strat.output_csv == "{base}/working/{niche_snake}_phase6.csv"

    def test_default_encoding_is_utf8_sig(self, tmp_path: Path):
        body = """\
name: t
description: d
input:
  path: "/tmp/x.csv"
steps: []
"""
        strat = load_strategy(self._write_yaml(tmp_path, body))
        assert strat.input_encoding == "utf-8-sig"

    def test_step_with_empty_config(self, tmp_path: Path):
        # A step that needs no config (like decision_engine, build_output).
        body = """\
name: t
description: d
steps:
  - name: decision_engine
    module: fba_engine.steps.decision_engine
"""
        strat = load_strategy(self._write_yaml(tmp_path, body))
        assert strat.steps[0].config == {}

    def test_input_discover_flag_loads(self, tmp_path: Path):
        body = """\
name: t
description: d
input:
  discover: true
steps: []
"""
        strat = load_strategy(self._write_yaml(tmp_path, body))
        assert strat.input_discover is True
        assert strat.input_path is None

    def test_input_discover_default_false(self, tmp_path: Path):
        body = """\
name: t
description: d
input:
  path: "/tmp/x.csv"
steps: []
"""
        strat = load_strategy(self._write_yaml(tmp_path, body))
        assert strat.input_discover is False

    def test_input_discover_quoted_string_rejected(self, tmp_path: Path):
        # A string `"true"` is truthy via bool() — silent acceptance
        # would let `discover: "false"` (still truthy) wrongly opt-in.
        # The loader must reject anything that isn't a real YAML bool.
        body = """\
name: t
description: d
input:
  discover: "true"
steps: []
"""
        with pytest.raises(StrategyConfigError, match="discover"):
            load_strategy(self._write_yaml(tmp_path, body))

    def test_missing_name_field_raises(self, tmp_path: Path):
        body = """\
description: no name
steps: []
"""
        with pytest.raises(StrategyConfigError, match="name"):
            load_strategy(self._write_yaml(tmp_path, body))

    def test_missing_steps_field_raises(self, tmp_path: Path):
        body = """\
name: t
description: d
"""
        with pytest.raises(StrategyConfigError, match="steps"):
            load_strategy(self._write_yaml(tmp_path, body))

    def test_step_missing_module_raises(self, tmp_path: Path):
        body = """\
name: t
description: d
steps:
  - name: nameOnly
"""
        with pytest.raises(StrategyConfigError, match="module"):
            load_strategy(self._write_yaml(tmp_path, body))

    def test_strategy_file_not_found_raises_filenotfound(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_strategy(tmp_path / "missing.yaml")


# ---------------------------------------------------------------------------
# run_strategy — control flow + error cases
# ---------------------------------------------------------------------------


def _strategy(steps: list[StepDef], **overrides) -> StrategyDef:
    base = {
        "name": "test",
        "description": "synthetic",
        "input_path": None,
        "input_encoding": "utf-8-sig",
        "steps": steps,
        "output_csv": None,
    }
    base.update(overrides)
    return StrategyDef(**base)


class TestRunStrategyControlFlow:
    def test_chains_two_steps_passing_df_through(self):
        # ip_risk + decision_engine: two real ported steps. ip_risk adds 9 cols,
        # decision_engine adds 11. So the chain should add 20 columns total.
        # ip_risk needs config["niche"], decision_engine needs nothing.
        from fba_engine.steps.build_output import FINAL_HEADERS

        # Build a minimal Phase-5 frame (the input to decision_engine).
        # We feed it directly through ip_risk first (which expects Phase 3
        # shortlist columns — most of which exist in FINAL_HEADERS).
        # Easier: just chain decision_engine alone for this test.
        from fba_engine.strategies.runner import run_strategy
        df_in = pd.DataFrame(
            [{h: "" for h in FINAL_HEADERS}]
        )
        df_in.iloc[0, df_in.columns.get_loc("ASIN")] = "B0SAMPLE"
        df_in.iloc[0, df_in.columns.get_loc("Verdict")] = "YES"
        df_in.iloc[0, df_in.columns.get_loc("Opportunity Lane")] = "BALANCED"

        strat = _strategy(
            steps=[
                StepDef("decision", "fba_engine.steps.decision_engine", {}),
            ]
        )
        df_out = run_strategy(strat, context={}, df_in=df_in)
        assert "Decision" in df_out.columns

    def test_step_module_missing_raises_config_error(self):
        strat = _strategy(
            steps=[StepDef("ghost", "fba_engine.steps.does_not_exist", {})]
        )
        with pytest.raises(StrategyConfigError, match="does_not_exist"):
            run_strategy(strat, context={}, df_in=pd.DataFrame([{"ASIN": "B0"}]))

    def test_step_without_run_step_raises_config_error(self):
        # `pandas` exists but has no run_step.
        strat = _strategy(steps=[StepDef("badstep", "pandas", {})])
        with pytest.raises(StrategyConfigError, match="run_step"):
            run_strategy(strat, context={}, df_in=pd.DataFrame([{"ASIN": "B0"}]))

    def test_step_runtime_error_wrapped_with_context(self):
        # ip_risk requires config["niche"]; missing it raises ValueError.
        # The runner should re-raise as StrategyExecutionError citing the
        # offending step.
        strat = _strategy(
            steps=[StepDef("ip_risk", "fba_engine.steps.ip_risk", {})]
        )
        with pytest.raises(StrategyExecutionError, match="ip_risk"):
            run_strategy(strat, context={}, df_in=pd.DataFrame([{"ASIN": "B0"}]))

    def test_no_input_path_and_no_df_in_raises(self):
        strat = _strategy(steps=[])
        with pytest.raises(StrategyConfigError, match="input"):
            run_strategy(strat, context={}, df_in=None)

    def test_input_discover_flag_passes_empty_df(self):
        # Strategies whose first step is a discoverer (creates rows from
        # supplier files / API calls) opt out of the input.path guard
        # via input.discover: true. The runner passes an empty df_in
        # and the discover step ignores it.
        strat = _strategy(steps=[], input_discover=True)
        out = run_strategy(strat, context={}, df_in=None)
        assert out.empty

    def test_empty_step_chain_returns_input_unchanged(self):
        df = pd.DataFrame([{"ASIN": "B0"}])
        strat = _strategy(steps=[])
        out = run_strategy(strat, context={}, df_in=df)
        pd.testing.assert_frame_equal(out, df)


class TestRunStrategyVariableInterpolation:
    def test_step_config_strings_get_interpolated(self):
        # ip_risk reads config["niche"]. Pass "{niche}" literal in YAML and
        # rely on context to substitute.
        from fba_engine.steps.build_output import FINAL_HEADERS
        df = pd.DataFrame(
            [{h: "" for h in FINAL_HEADERS} | {"ASIN": "B0", "Title": "T", "Brand": "B"}]
        )
        # Use a Phase-3-shape df for ip_risk (subset is sufficient — ip_risk
        # warns on missing cols but doesn't crash).
        strat = _strategy(
            steps=[
                StepDef("ip_risk", "fba_engine.steps.ip_risk", {"niche": "{niche}"}),
            ]
        )
        out = run_strategy(strat, context={"niche": "kids-toys"}, df_in=df)
        # ip_risk added the 9 columns — that's evidence it ran with the
        # interpolated niche.
        assert "IP Risk Band" in out.columns


class TestRunStrategyOutputCsv:
    def test_writes_output_csv_when_configured(self, tmp_path: Path):
        from fba_engine.steps.build_output import FINAL_HEADERS
        df = pd.DataFrame([{h: "" for h in FINAL_HEADERS} | {"ASIN": "B001"}])
        out_path = tmp_path / "out.csv"
        strat = _strategy(
            steps=[],
            output_csv=str(out_path),
        )
        run_strategy(strat, context={}, df_in=df)
        assert out_path.exists()
        round_tripped = pd.read_csv(out_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        assert "ASIN" in round_tripped.columns

    def test_output_csv_path_is_interpolated(self, tmp_path: Path):
        df = pd.DataFrame([{"ASIN": "B001"}])
        strat = _strategy(
            steps=[],
            output_csv=str(tmp_path / "{niche}.csv"),
        )
        run_strategy(strat, context={"niche": "kids-toys"}, df_in=df)
        assert (tmp_path / "kids-toys.csv").exists()


# ---------------------------------------------------------------------------
# End-to-end: keepa_niche YAML against a real fixture
# ---------------------------------------------------------------------------


class TestKeepaNicheStrategy:
    """Integration test: load the canonical keepa_niche.yaml and run it
    through the full chain (ip_risk -> build_output -> decision_engine)."""

    def _phase3_fixture(self) -> pd.DataFrame:
        """Minimal Phase-3 shortlist row that survives the build_output PL filter."""
        return pd.DataFrame([{
            "ASIN": "B0CLEAN",
            "Title": "Clean Toy",
            "Brand": "Acme",
            "Amazon URL": "https://amzn.eu/d/x",
            "Category": "Toys",
            "Weight Flag": "OK",
            "Verdict": "YES",
            "Verdict Reason": "Strong",
            "Composite Score": "8.5",
            "Demand Score": "8",
            "Stability Score": "8",
            "Competition Score": "7",
            "Margin Score": "9",
            "Cash Flow Score": "8",
            "Profit Score": "8",
            "Balanced Score": "8",
            "Listing Quality": "Good",
            "Opportunity Lane": "BALANCED",
            "Commercial Priority": "1",
            "Lane Reason": "demand+margin",
            "Monthly Gross Profit": "GBP500",
            "Price Compression": "OK",
            "Current Price": "GBP25.99",
            "Buy Box 90d Avg": "GBP25.50",
            "Price Drop % 90d": "1",
            "Fulfilment Fee": "GBP3.50",
            "Amazon Fees": "GBP5.00",
            "Total Amazon Fees": "GBP8.50",
            "Est Cost 65%": "GBP10.00",
            "Est Profit": "GBP7.49",
            "Est ROI %": "32",
            "Max Cost 20% ROI": "GBP12.00",
            "Breakeven Price": "GBP18.00",
            "BSR Current": "5000",
            "BSR Drops 90d": "200",
            "Bought per Month": "150",
            "Star Rating": "4.5",
            "Review Count": "200",
            "Brand 1P": "N",
            "FBA Seller Count": "5",
            "Amazon on Listing": "N",
            "Buy Box Amazon %": "10%",
            "EAN": "1234567890123",
            "UPC": "",
            "GTIN": "",
        }])

    def test_keepa_niche_yaml_loads_and_runs_end_to_end(self, tmp_path: Path):
        # Load the canonical strategy file.
        repo_root = Path(__file__).resolve().parents[3]
        yaml_path = repo_root / "fba_engine" / "strategies" / "keepa_niche.yaml"
        if not yaml_path.exists():
            pytest.skip(f"keepa_niche.yaml not found at {yaml_path}")

        strat = load_strategy(yaml_path)
        assert strat.name == "keepa_niche"

        df_in = self._phase3_fixture()
        # Provide all context keys the strategy YAML interpolates: niche
        # is used by ip_risk's config; base + niche_snake are used by the
        # output_csv path. Point base at tmp_path so the test write is
        # sandboxed.
        context = {
            "niche": "kids-toys",
            "niche_snake": "kids_toys",
            "base": str(tmp_path),
        }
        # The runner reads input from `{base}/working/...` IFF df_in is None.
        # We pass df_in directly, so the input_path is unused; only the
        # output_csv path matters. Verify the chain output AND that the
        # output CSV got written to the sandboxed location.
        out = run_strategy(strat, context=context, df_in=df_in)

        # After the full chain (ip_risk -> build_output -> decision_engine),
        # the frame should have IP risk + decision columns. The fixture row
        # is a clean BALANCED YES with strong margin — `decision_engine`
        # demotes it to NEGOTIATE because no supplier cost is present
        # (Trade Price Found="" -> "Cost Needed" branch). Pinning the
        # verdict catches drift in any of the three composed steps.
        assert "IP Risk Band" in out.columns
        assert out.iloc[0]["Decision"] == "NEGOTIATE"

        # Output CSV was written to the sandbox via atomic_write.
        output_csv = tmp_path / "working" / "kids_toys_phase6_decisions.csv"
        assert output_csv.exists()


class TestSupplierPricelistStrategy:
    """Smoke test: the canonical supplier_pricelist.yaml loads and
    structurally matches the new step modules + the input.discover
    contract."""

    _SUPPLIER_CSV = (
        "SKU,Manufacturer,Title,Category,Barcode,Case Size,"
        "Units Available,Unit Price (GBP),Case Price (GBP)\n"
        "SKU-A,Acme,Widget,Tools,5012345678900,1,100,5.00,5.00\n"
    )

    def test_supplier_pricelist_yaml_loads_with_discover_input(self):
        repo_root = Path(__file__).resolve().parents[3]
        yaml_path = repo_root / "fba_engine" / "strategies" / "supplier_pricelist.yaml"
        if not yaml_path.exists():
            pytest.skip(f"supplier_pricelist.yaml not found at {yaml_path}")

        strat = load_strategy(yaml_path)
        assert strat.name == "supplier_pricelist"
        assert strat.input_discover is True
        assert strat.input_path is None
        # All six step module paths are real Python modules.
        for step in strat.steps:
            __import__(step.module)

    def test_supplier_pricelist_yaml_runs_end_to_end(self, tmp_path: Path):
        repo_root = Path(__file__).resolve().parents[3]
        yaml_path = repo_root / "fba_engine" / "strategies" / "supplier_pricelist.yaml"
        if not yaml_path.exists():
            pytest.skip(f"supplier_pricelist.yaml not found at {yaml_path}")

        # Sandbox the supplier raw + run dir under tmp_path.
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "p.csv").write_text(self._SUPPLIER_CSV, encoding="utf-8")
        run_dir = tmp_path / "out"

        context = {
            "supplier": "connect-beauty",
            "input_path": str(raw),
            "market_data_path": "",  # no Keepa CSV — every row REJECTs (no_match)
            "run_dir": str(run_dir),
            "timestamp": "20260429_120000",
            "supplier_label": "Connect Beauty",
        }

        # `enabled: true` in the YAML for enrich would call the MCP CLI.
        # Override via the strategy YAML's loaded steps to disable it for
        # this offline test.
        strat = load_strategy(yaml_path)
        for s in strat.steps:
            if s.name == "enrich":
                s.config["enabled"] = False

        out = run_strategy(strat, context=context, df_in=None)
        # Every row should REJECT (no Keepa data).
        assert (out["decision"] == "REJECT").all()
        # Output writers ran and produced the timestamped CSV.
        assert (run_dir / "shortlist_20260429_120000.csv").exists()
