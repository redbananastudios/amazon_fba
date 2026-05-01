"""Tests for cli.strategy — the `--strategy` dispatch.

Covers:
  - argparse contract (required args, optional args, --context k=v parsing)
  - strategy YAML resolution (existing → loaded; missing → SystemExit)
  - recipe JSON resolution (existing → loaded; missing → warn + empty;
    malformed → warn + empty)
  - recipe → strategy config wiring (calculate_config flows to calculate
    step; decide_overrides flow to decide step's overrides key)
  - context defaulting (timestamp + output_dir auto-fill)
  - end-to-end smoke: dispatch keepa_finder against a synthetic CSV
    and verify a decision CSV lands at the expected path
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Make shared/lib/python importable (matches run.py's sys.path setup).
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
sys.path.insert(0, str(_REPO / "shared" / "lib" / "python"))

from cli import strategy as cli_strategy  # noqa: E402


# ────────────────────────────────────────────────────────────────────────
# Argparse contract.
# ────────────────────────────────────────────────────────────────────────


class TestParseArgs:
    def test_strategy_required(self):
        with pytest.raises(SystemExit):
            cli_strategy._parse_args([])

    def test_minimal_args_strategy_only(self):
        ns = cli_strategy._parse_args(["--strategy", "keepa_finder"])
        assert ns.strategy == "keepa_finder"
        assert ns.csv is None
        assert ns.recipe is None

    def test_full_arg_set(self):
        ns = cli_strategy._parse_args([
            "--strategy", "keepa_finder",
            "--csv", "/tmp/x.csv",
            "--recipe", "amazon_oos_wholesale",
            "--output-dir", "/tmp/out",
            "--timestamp", "20260502_120000",
        ])
        assert ns.strategy == "keepa_finder"
        assert ns.csv == "/tmp/x.csv"
        assert ns.recipe == "amazon_oos_wholesale"
        assert ns.output_dir == "/tmp/out"
        assert ns.timestamp == "20260502_120000"

    def test_context_repeatable(self):
        ns = cli_strategy._parse_args([
            "--strategy", "x",
            "--context", "foo=bar",
            "--context", "baz=qux",
        ])
        assert ns.context == ["foo=bar", "baz=qux"]


# ────────────────────────────────────────────────────────────────────────
# Strategy YAML resolution.
# ────────────────────────────────────────────────────────────────────────


class TestResolveStrategyYaml:
    def test_existing_strategy_resolves(self):
        # keepa_finder.yaml ships with the repo (commit 5).
        path = cli_strategy._resolve_strategy_yaml("keepa_finder")
        assert path.exists()
        assert path.name == "keepa_finder.yaml"

    def test_missing_strategy_raises_with_listing(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli_strategy._resolve_strategy_yaml("does_not_exist")
        msg = str(excinfo.value)
        assert "not found" in msg
        # Error message lists known strategies so the user can spot a typo.
        assert "keepa_finder" in msg


# ────────────────────────────────────────────────────────────────────────
# Recipe JSON resolution.
# ────────────────────────────────────────────────────────────────────────


class TestResolveRecipeJson:
    def test_no_recipe_returns_empty_dict(self):
        assert cli_strategy._resolve_recipe_json(None) == {}
        assert cli_strategy._resolve_recipe_json("") == {}

    def test_existing_recipe_loads(self):
        data = cli_strategy._resolve_recipe_json("amazon_oos_wholesale")
        assert data["name"] == "amazon_oos_wholesale"
        assert "url_filters" in data
        assert data.get("calculate_config", {}).get("compute_stability_score") is True

    def test_missing_recipe_warns_returns_empty(self, caplog):
        with caplog.at_level("WARNING"):
            data = cli_strategy._resolve_recipe_json("nope_not_a_recipe")
        assert data == {}
        assert any("not found" in r.message for r in caplog.records)

    def test_malformed_recipe_warns_returns_empty(self, tmp_path, caplog, monkeypatch):
        # Plant a malformed recipe in the canonical location and verify
        # we don't crash.
        bad = cli_strategy._RECIPES_DIR / "_test_malformed.json"
        bad.write_text("not valid json {{{", encoding="utf-8")
        try:
            with caplog.at_level("WARNING"):
                data = cli_strategy._resolve_recipe_json("_test_malformed")
            assert data == {}
            assert any("malformed" in r.message for r in caplog.records)
        finally:
            bad.unlink()


# ────────────────────────────────────────────────────────────────────────
# Recipe → strategy config wiring.
# ────────────────────────────────────────────────────────────────────────


class _StubStep:
    """Mimics StepDef just enough for _apply_recipe_to_strategy."""
    def __init__(self, name: str, config: dict | None = None):
        self.name = name
        self.config = config or {}


class _StubStrategy:
    def __init__(self, steps: list[_StubStep]):
        self.steps = steps


class TestApplyRecipeToStrategy:
    def test_calculate_config_flows_to_calculate_step(self):
        strat = _StubStrategy([
            _StubStep("discover"),
            _StubStep("calculate"),
            _StubStep("decide"),
        ])
        cli_strategy._apply_recipe_to_strategy(strat, {
            "calculate_config": {"compute_stability_score": True},
        })
        calc = next(s for s in strat.steps if s.name == "calculate")
        assert calc.config["compute_stability_score"] is True

    def test_decide_overrides_wraps_under_overrides_key(self):
        strat = _StubStrategy([
            _StubStep("discover"),
            _StubStep("decide"),
        ])
        cli_strategy._apply_recipe_to_strategy(strat, {
            "decide_overrides": {"min_sales_shortlist": 5, "min_sales_review": 2},
        })
        dec = next(s for s in strat.steps if s.name == "decide")
        assert dec.config["overrides"] == {
            "min_sales_shortlist": 5, "min_sales_review": 2,
        }

    def test_empty_recipe_is_no_op(self):
        strat = _StubStrategy([
            _StubStep("calculate", {"existing": "value"}),
            _StubStep("decide", {}),
        ])
        cli_strategy._apply_recipe_to_strategy(strat, {})
        # Original config untouched, no "overrides" key on decide.
        assert strat.steps[0].config == {"existing": "value"}
        assert "overrides" not in strat.steps[1].config

    def test_recipe_without_calculate_step_is_silent(self):
        """Strategy may legitimately omit calculate (e.g. seller_storefront).
        A recipe with calculate_config is just ignored."""
        strat = _StubStrategy([_StubStep("discover")])
        cli_strategy._apply_recipe_to_strategy(strat, {
            "calculate_config": {"compute_stability_score": True},
        })
        # No exception, no spurious step added.
        assert [s.name for s in strat.steps] == ["discover"]


# ────────────────────────────────────────────────────────────────────────
# Context building — defaults + override merging.
# ────────────────────────────────────────────────────────────────────────


class TestBuildContext:
    def test_timestamp_auto_fills_when_absent(self):
        ns = cli_strategy._parse_args(["--strategy", "keepa_finder"])
        ctx = cli_strategy._build_context(ns)
        assert "timestamp" in ctx
        assert len(ctx["timestamp"]) == 15   # YYYYmmdd_HHMMSS

    def test_explicit_timestamp_wins(self):
        ns = cli_strategy._parse_args([
            "--strategy", "keepa_finder", "--timestamp", "20260502_120000",
        ])
        ctx = cli_strategy._build_context(ns)
        assert ctx["timestamp"] == "20260502_120000"

    def test_output_dir_default_uses_timestamp(self):
        ns = cli_strategy._parse_args([
            "--strategy", "keepa_finder", "--timestamp", "20260502_120000",
        ])
        ctx = cli_strategy._build_context(ns)
        assert ctx["output_dir"].endswith("20260502_120000")

    def test_csv_and_recipe_pass_through(self):
        ns = cli_strategy._parse_args([
            "--strategy", "keepa_finder",
            "--csv", "/tmp/x.csv", "--recipe", "amazon_oos_wholesale",
        ])
        ctx = cli_strategy._build_context(ns)
        assert ctx["csv_path"] == "/tmp/x.csv"
        assert ctx["recipe"] == "amazon_oos_wholesale"

    def test_extra_context_pairs_merge(self):
        ns = cli_strategy._parse_args([
            "--strategy", "keepa_finder",
            "--context", "foo=bar", "--context", "baz=qux",
        ])
        ctx = cli_strategy._build_context(ns)
        assert ctx["foo"] == "bar"
        assert ctx["baz"] == "qux"

    def test_malformed_context_pair_raises(self):
        ns = cli_strategy._parse_args([
            "--strategy", "keepa_finder", "--context", "no_equals_sign",
        ])
        with pytest.raises(SystemExit, match="KEY=VALUE"):
            cli_strategy._build_context(ns)


# ────────────────────────────────────────────────────────────────────────
# End-to-end smoke: dispatch keepa_finder against a synthetic CSV.
# ────────────────────────────────────────────────────────────────────────


class TestEndToEndSmoke:
    """Dispatch the full strategy through `cli.strategy.main` against a
    synthetic Keepa Product Finder CSV. Asserts the output CSV lands at
    the expected path with a populated decision column."""

    _KEEPA_COLUMNS = (
        "ASIN", "Title", "Brand", "Manufacturer",
        "Categories: Root", "Categories: Sub", "Categories: Tree",
        "Product Codes: EAN", "Product Codes: UPC",
        "Buy Box: Current", "Buy Box: 90 days avg.",
        "New, 3rd Party FBA: Current",
        "Amazon: Current", "FBA Pick&Pack Fee", "Referral Fee %",
        "Bought in past month", "New FBA Offer Count: Current",
        "Sales Rank: Current", "Sales Rank: 90 days avg.",
        "Buy Box: % Amazon 90 days", "Buy Box: 90 days OOS",
        "Buy Box: 30 days drop %", "Buy Box: 90 days drop %",
    )

    @staticmethod
    def _synthesize_keepa_csv(path: Path) -> None:
        rows = [{
            "ASIN": "B0SMOKE001",
            "Title": "Action Figure Pro",
            "Brand": "Acme",
            "Manufacturer": "Acme Mfg",
            "Categories: Root": "Toys & Games",
            "Categories: Sub": "Action Figures",
            "Categories: Tree": "Toys & Games > Figures > Sets",
            "Product Codes: EAN": "5012345678901",
            "Product Codes: UPC": "012345678905",
            "Buy Box: Current": "24.99",
            "Buy Box: 90 days avg.": "26.50",
            "New, 3rd Party FBA: Current": "25.49",
            "Amazon: Current": "",
            "FBA Pick&Pack Fee": "3.35",
            "Referral Fee %": "15 %",
            "Bought in past month": "150",
            "New FBA Offer Count: Current": "5",
            "Sales Rank: Current": "12345",
            "Sales Rank: 90 days avg.": "13500",
            "Buy Box: % Amazon 90 days": "0 %",
            "Buy Box: 90 days OOS": "2",
            "Buy Box: 30 days drop %": "3",
            "Buy Box: 90 days drop %": "5",
        }]
        pd.DataFrame(rows, columns=TestEndToEndSmoke._KEEPA_COLUMNS).to_csv(
            path, index=False, encoding="utf-8-sig",
        )

    def test_full_dispatch_writes_decision_csv(self, tmp_path):
        csv_in = tmp_path / "keepa.csv"
        self._synthesize_keepa_csv(csv_in)
        out_dir = tmp_path / "results"

        rc = cli_strategy.main([
            "--strategy", "keepa_finder",
            "--csv", str(csv_in),
            "--recipe", "amazon_oos_wholesale",
            "--output-dir", str(out_dir),
            "--timestamp", "20260502_smoke",
        ])
        assert rc == 0
        # Output CSV at the path keepa_finder.yaml's output.csv interpolates to.
        out_csv = out_dir / "keepa_finder_amazon_oos_wholesale_20260502_smoke.csv"
        assert out_csv.exists(), f"output CSV missing at {out_csv}"
        df = pd.read_csv(out_csv)
        assert len(df) == 1
        assert df.iloc[0]["asin"] == "B0SMOKE001"
        assert df.iloc[0]["decision"] in {"SHORTLIST", "REVIEW", "REJECT"}
        # amazon_oos_wholesale recipe sets compute_stability_score=True
        # — verify it flowed through to calculate.
        assert "stability_score" in df.columns

    def test_dispatch_with_unknown_recipe_still_runs(self, tmp_path, caplog):
        """Unknown recipe = warn + use defaults. Verifies the dispatch
        is robust against typos in Cowork run definitions."""
        csv_in = tmp_path / "keepa.csv"
        self._synthesize_keepa_csv(csv_in)
        out_dir = tmp_path / "results"
        with caplog.at_level("WARNING"):
            rc = cli_strategy.main([
                "--strategy", "keepa_finder",
                "--csv", str(csv_in),
                "--recipe", "typo_recipe_does_not_exist",
                "--output-dir", str(out_dir),
                "--timestamp", "20260502_smoke",
            ])
        assert rc == 0
        # Output still got written; just no recipe-driven configs applied.
        # discovery_strategy column tagged with whatever was passed in.
        out_csv = out_dir / "keepa_finder_typo_recipe_does_not_exist_20260502_smoke.csv"
        assert out_csv.exists()
        df = pd.read_csv(out_csv)
        # Without amazon_oos_wholesale's calculate_config, stability_score
        # is absent — proves the absent-recipe path doesn't accidentally
        # flow defaults through.
        assert "stability_score" not in df.columns
