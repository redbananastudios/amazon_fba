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
        # Redirect _RECIPES_DIR to a tmp path so we don't pollute the
        # real recipes directory (which is checked into git — a stray
        # file from a crashed test run would ship in the next commit).
        monkeypatch.setattr(cli_strategy, "_RECIPES_DIR", tmp_path)
        (tmp_path / "test_malformed.json").write_text(
            "not valid json {{{", encoding="utf-8",
        )
        with caplog.at_level("WARNING"):
            data = cli_strategy._resolve_recipe_json("test_malformed")
        assert data == {}
        assert any("malformed" in r.message for r in caplog.records)


# ────────────────────────────────────────────────────────────────────────
# Recipe → strategy config wiring.
# ────────────────────────────────────────────────────────────────────────


class _StubStep:
    """Mimics StepDef just enough for _apply_recipe_to_strategy."""
    def __init__(self, name: str, config: dict | None = None):
        self.name = name
        self.config = config or {}


class _StubStrategy:
    def __init__(self, steps: list[_StubStep], name: str = "test_strategy"):
        self.steps = steps
        self.name = name


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

    def test_recipe_without_calculate_step_warns_and_continues(self):
        """Strategy may legitimately omit calculate (e.g. seller_storefront).
        A recipe with calculate_config logs a WARNING but doesn't raise —
        see TestApplyRecipeMissingStepWarns for the warning assertion;
        here we just confirm no exception + no spurious step added."""
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
                "--recipe", "typo_recipe",
                "--output-dir", str(out_dir),
                "--timestamp", "20260502_smoke",
            ])
        assert rc == 0
        # Output still got written; just no recipe-driven configs applied.
        # discovery_strategy column tagged with whatever was passed in.
        out_csv = out_dir / "keepa_finder_typo_recipe_20260502_smoke.csv"
        assert out_csv.exists()
        df = pd.read_csv(out_csv)
        # Without amazon_oos_wholesale's calculate_config, stability_score
        # is absent — proves the absent-recipe path doesn't accidentally
        # flow defaults through.
        assert "stability_score" not in df.columns

    @staticmethod
    def _synthesize_low_sales_keepa_csv(path: Path) -> None:
        """Two-row export with sales = 8/mo — fails default min_sales_review (10)
        but clears no_rank_hidden_gem's override (min_sales_review = 2,
        min_sales_shortlist = 5). Used to verify decide_overrides flow
        through the full chain."""
        rows = [{
            "ASIN": "B0NORANK01",
            "Title": "Long-tail Office Item",
            "Brand": "Acme", "Manufacturer": "Acme Mfg",
            "Categories: Root": "Office Products", "Categories: Sub": "Office",
            "Categories: Tree": "Office Products > Office > Storage",
            "Product Codes: EAN": "5012345678901",
            "Product Codes: UPC": "012345678905",
            "Buy Box: Current": "29.99", "Buy Box: 90 days avg.": "30.00",
            "New, 3rd Party FBA: Current": "30.49",
            "Amazon: Current": "",
            "FBA Pick&Pack Fee": "3.35", "Referral Fee %": "15 %",
            "Bought in past month": "8",                    # below default 10 floor
            "New FBA Offer Count: Current": "3",
            "Sales Rank: Current": "—", "Sales Rank: 90 days avg.": "—",
            "Buy Box: % Amazon 90 days": "0 %",
            "Buy Box: 90 days OOS": "0",
            "Buy Box: 30 days drop %": "0", "Buy Box: 90 days drop %": "0",
        }]
        pd.DataFrame(rows, columns=TestEndToEndSmoke._KEEPA_COLUMNS).to_csv(
            path, index=False, encoding="utf-8-sig",
        )

    def test_no_rank_hidden_gem_recipe_overrides_flow_through(self, tmp_path):
        """End-to-end: dispatching no_rank_hidden_gem must lower the
        sales-review floor so an 8/mo row doesn't REJECT.

        This test guards the H1 contract — if cli.strategy._apply_recipe_to_strategy
        ever stops forwarding decide_overrides, the row REJECTs at the
        default min_sales_review=10 and this test fails. Direct regression
        coverage of the orchestration path."""
        csv_in = tmp_path / "keepa.csv"
        self._synthesize_low_sales_keepa_csv(csv_in)
        out_dir = tmp_path / "results"
        rc = cli_strategy.main([
            "--strategy", "keepa_finder",
            "--csv", str(csv_in),
            "--recipe", "no_rank_hidden_gem",
            "--output-dir", str(out_dir),
            "--timestamp", "ovr_smoke",
        ])
        assert rc == 0
        out_csv = out_dir / "keepa_finder_no_rank_hidden_gem_ovr_smoke.csv"
        df = pd.read_csv(out_csv)
        assert len(df) == 1
        # Default min_sales_review=10 would REJECT this row with reason
        # "Sales estimate 8/month below minimum 10". no_rank_hidden_gem's
        # decide_overrides flips min_sales_review → 2, so the REJECT
        # gate is no longer tripped. Row should be REVIEW (no SHORTLIST
        # because buy_cost=0 → ROI=None gate also fails).
        row = df.iloc[0]
        assert row["decision"] != "REJECT", \
            f"row REJECTed — decide_overrides did not flow through. " \
            f"reason: {row['decision_reason']}"


# ────────────────────────────────────────────────────────────────────────
# Path-traversal hardening (M1).
# ────────────────────────────────────────────────────────────────────────


class TestNameValidation:
    """Strategy / recipe names must match a strict allowlist regex —
    blocks `--strategy ../../../tmp/evil` and similar path-escape attempts."""

    @pytest.mark.parametrize("bad_name", [
        "../foo", "../../etc/passwd", "foo/bar", "foo\\bar",
        "foo.bar", "foo bar", "", "..",
    ])
    def test_invalid_strategy_name_rejected(self, bad_name):
        with pytest.raises(SystemExit, match="Invalid strategy name"):
            cli_strategy._resolve_strategy_yaml(bad_name)

    @pytest.mark.parametrize("bad_name", [
        "../foo", "../../etc/passwd", "foo/bar", "foo\\bar",
        "foo.bar", "foo bar", "..",
    ])
    def test_invalid_recipe_name_rejected(self, bad_name):
        with pytest.raises(SystemExit, match="Invalid recipe name"):
            cli_strategy._resolve_recipe_json(bad_name)

    def test_valid_names_pass(self):
        """Letters, digits, underscores, hyphens — the canonical recipe
        and strategy names ship with this shape."""
        # These exist on disk so we expect resolution to succeed.
        path = cli_strategy._resolve_strategy_yaml("keepa_finder")
        assert path.name == "keepa_finder.yaml"
        data = cli_strategy._resolve_recipe_json("amazon_oos_wholesale")
        assert data["name"] == "amazon_oos_wholesale"
        # Hyphenated names also match the regex (existing keepa_niche is one form).
        # Not asserting load — just that validation passes (the path-not-found
        # logic surfaces a different error message).
        try:
            cli_strategy._resolve_strategy_yaml("foo-bar-baz")
        except SystemExit as e:
            assert "Invalid" not in str(e), "hyphens should pass the regex"


# ────────────────────────────────────────────────────────────────────────
# Recipe / strategy mismatch logging (M5).
# ────────────────────────────────────────────────────────────────────────


class TestApplyRecipeMissingStepWarns:
    """When a recipe declares calculate_config but the strategy has no
    calculate step, log a warning rather than silently dropping it."""

    def test_calculate_config_with_no_calculate_step_warns(self, caplog):
        strat = _StubStrategy([_StubStep("discover"), _StubStep("decide")])
        with caplog.at_level("WARNING"):
            cli_strategy._apply_recipe_to_strategy(strat, {
                "name": "test_recipe",
                "calculate_config": {"compute_stability_score": True},
            })
        assert any(
            "calculate_config" in r.message and "no 'calculate' step" in r.message
            for r in caplog.records
        ), [r.message for r in caplog.records]

    def test_decide_overrides_with_no_decide_step_warns(self, caplog):
        strat = _StubStrategy([_StubStep("discover"), _StubStep("calculate")])
        with caplog.at_level("WARNING"):
            cli_strategy._apply_recipe_to_strategy(strat, {
                "name": "test_recipe",
                "decide_overrides": {"min_sales_shortlist": 5},
            })
        assert any(
            "decide_overrides" in r.message and "no 'decide' step" in r.message
            for r in caplog.records
        ), [r.message for r in caplog.records]

    def test_both_applied_no_warning(self, caplog):
        strat = _StubStrategy([
            _StubStep("calculate"), _StubStep("decide"),
        ])
        with caplog.at_level("WARNING"):
            cli_strategy._apply_recipe_to_strategy(strat, {
                "name": "test_recipe",
                "calculate_config": {"compute_stability_score": True},
                "decide_overrides": {"min_sales_shortlist": 5},
            })
        assert not any(
            "dropped" in r.message for r in caplog.records
        ), [r.message for r in caplog.records]
