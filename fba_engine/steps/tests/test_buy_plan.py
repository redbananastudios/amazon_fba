"""Tests for fba_engine.steps.buy_plan.

Covers the DataFrame wrapper + runner integration. Pure-function logic
itself is tested in
``shared/lib/python/sourcing_engine/tests/test_buy_plan.py``.
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from fba_engine.steps.buy_plan import (
    BUY_PLAN_COLUMNS,
    add_buy_plan,
    run_step,
)
from sourcing_engine.buy_plan import (
    STATUS_BLOCKED_BY_VERDICT,
    STATUS_INSUFFICIENT_DATA,
    STATUS_NO_BUY_COST,
    STATUS_OK,
)


# ────────────────────────────────────────────────────────────────────────
# Row builders.
# ────────────────────────────────────────────────────────────────────────


def _buy_row(**overrides) -> dict:
    base = {
        "asin": "B000BUY01TEST",
        "opportunity_verdict": "BUY",
        "opportunity_confidence": "HIGH",
        "risk_flags": [],
        "predicted_velocity_mid": 18,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        "profit_conservative": 8.35,
        "buy_cost": 4.0,
    }
    base.update(overrides)
    return base


def _source_only_row(**overrides) -> dict:
    base = {
        "asin": "B000SRC01TEST",
        "opportunity_verdict": "SOURCE_ONLY",
        "opportunity_confidence": "HIGH",
        "risk_flags": [],
        "predicted_velocity_mid": 42,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        "profit_conservative": None,
        "buy_cost": 0.0,
    }
    base.update(overrides)
    return base


def _negotiate_row(**overrides) -> dict:
    base = {
        "asin": "B000NEG01TEST",
        "opportunity_verdict": "NEGOTIATE",
        "opportunity_confidence": "HIGH",
        "risk_flags": [],
        "predicted_velocity_mid": 18,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        "profit_conservative": 2.35,
        "buy_cost": 10.00,
    }
    base.update(overrides)
    return base


def _watch_row(**overrides) -> dict:
    base = {
        "asin": "B000WAT01TEST",
        "opportunity_verdict": "WATCH",
        "opportunity_confidence": "MEDIUM",
        "risk_flags": ["INSUFFICIENT_HISTORY"],
        "predicted_velocity_mid": 18,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        "profit_conservative": 8.35,
        "buy_cost": 4.0,
    }
    base.update(overrides)
    return base


def _kill_row(**overrides) -> dict:
    base = {
        "asin": "B000KIL01TEST",
        "opportunity_verdict": "KILL",
        "opportunity_confidence": "LOW",
        "risk_flags": ["PRICE_FLOOR_HIT"],
        "predicted_velocity_mid": 5,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        "profit_conservative": -1.0,
        "buy_cost": 8.0,
    }
    base.update(overrides)
    return base


# ────────────────────────────────────────────────────────────────────────
# DataFrame wrapper.
# ────────────────────────────────────────────────────────────────────────


class TestAddBuyPlan:
    def test_appends_all_eleven_columns(self):
        df = pd.DataFrame([_buy_row()])
        out = add_buy_plan(df)
        for col in BUY_PLAN_COLUMNS:
            assert col in out.columns
        assert len(out) == 1
        assert out.iloc[0]["buy_plan_status"] == STATUS_OK

    def test_empty_dataframe_returns_columns(self):
        out = add_buy_plan(pd.DataFrame())
        assert out.empty
        for col in BUY_PLAN_COLUMNS:
            assert col in out.columns

    def test_does_not_mutate_input_df(self):
        df = pd.DataFrame([_buy_row()])
        before_cols = list(df.columns)
        before_rows = df.copy(deep=True)
        add_buy_plan(df)
        assert list(df.columns) == before_cols
        # Compare values (not just shape) to confirm row data preserved.
        pd.testing.assert_frame_equal(df, before_rows)

    def test_does_not_mutate_decision_or_verdict(self):
        # Decision invariant — buy_plan must never alter the verdict
        # or any upstream column. Same shape as the candidate-score /
        # validate_opportunity decision-invariant test.
        rows = [
            _buy_row(decision="SHORTLIST"),
            _negotiate_row(decision="REVIEW"),
            _kill_row(decision="REJECT"),
        ]
        df = pd.DataFrame(rows)
        before_decision = list(df["decision"])
        before_verdict = list(df["opportunity_verdict"])
        out = add_buy_plan(df)
        assert list(out["decision"]) == before_decision
        assert list(out["opportunity_verdict"]) == before_verdict

    def test_per_verdict_population_matrix(self):
        # One row per verdict — assert which fields are populated/blank
        # per PRD §5.4. Pandas coerces None → NaN on heterogeneous-type
        # columns, so use pd.isna() for blank checks.
        df = pd.DataFrame([
            _buy_row(),
            _source_only_row(),
            _negotiate_row(),
            _watch_row(),
            _kill_row(),
        ])
        out = add_buy_plan(df)
        # Index by verdict so the asserts read clean.
        by_v = {r["opportunity_verdict"]: r for _, r in out.iterrows()}

        def _blank(v):
            return v is None or pd.isna(v)

        def _present(v):
            return not _blank(v)

        # BUY: everything populated except gap.
        buy = by_v["BUY"]
        assert buy["buy_plan_status"] == STATUS_OK
        assert _present(buy["order_qty_recommended"])
        assert _present(buy["capital_required"])
        assert _present(buy["payback_days"])
        assert _present(buy["target_buy_cost_buy"])
        assert _present(buy["projected_30d_units"])
        assert _blank(buy["gap_to_buy_gbp"])
        assert _blank(buy["gap_to_buy_pct"])

        # SOURCE_ONLY: targets + projections; sizing blank.
        src = by_v["SOURCE_ONLY"]
        assert src["buy_plan_status"] == STATUS_NO_BUY_COST
        assert _blank(src["order_qty_recommended"])
        assert _present(src["target_buy_cost_buy"])
        assert _present(src["projected_30d_units"])

        # NEGOTIATE: targets + gap; sizing blank.
        neg = by_v["NEGOTIATE"]
        assert neg["buy_plan_status"] == STATUS_OK
        assert _blank(neg["order_qty_recommended"])
        assert _present(neg["target_buy_cost_buy"])
        assert _present(neg["gap_to_buy_gbp"])
        assert _present(neg["gap_to_buy_pct"])

        # WATCH: targets + projections; sizing + gap blank.
        watch = by_v["WATCH"]
        assert watch["buy_plan_status"] == STATUS_BLOCKED_BY_VERDICT
        assert _blank(watch["order_qty_recommended"])
        assert _present(watch["target_buy_cost_buy"])
        assert _present(watch["projected_30d_units"])
        assert _blank(watch["gap_to_buy_gbp"])

        # KILL: everything blank, status set.
        kill = by_v["KILL"]
        assert kill["buy_plan_status"] == STATUS_BLOCKED_BY_VERDICT
        assert _blank(kill["order_qty_recommended"])
        assert _blank(kill["target_buy_cost_buy"])
        assert _blank(kill["projected_30d_units"])

    def test_per_row_exception_routes_to_insufficient_data(
        self, monkeypatch, caplog,
    ):
        # Force compute_buy_plan to throw on every row and assert the
        # wrapper catches + logs + sets INSUFFICIENT_DATA + continues.
        from fba_engine.steps import buy_plan as step_module

        def raising_compute(*args, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr(step_module, "compute_buy_plan", raising_compute)
        df = pd.DataFrame([_buy_row(), _negotiate_row()])
        with caplog.at_level(logging.ERROR, logger="fba_engine.steps.buy_plan"):
            out = add_buy_plan(df)
        # All rows survived.
        assert len(out) == 2
        for _, row in out.iterrows():
            assert row["buy_plan_status"] == STATUS_INSUFFICIENT_DATA
            assert row["order_qty_recommended"] is None
        # Logger was called.
        assert any("buy_plan: failed" in rec.message for rec in caplog.records)


# ────────────────────────────────────────────────────────────────────────
# run_step contract.
# ────────────────────────────────────────────────────────────────────────


class TestRunStep:
    def test_run_step_basic_first_order(self):
        df = pd.DataFrame([_buy_row()])
        out = run_step(df, {})
        assert out.iloc[0]["buy_plan_status"] == STATUS_OK
        # mid=18, 21d cover → 13.
        assert out.iloc[0]["order_qty_recommended"] == 13

    def test_run_step_reorder_mode_via_config(self):
        df = pd.DataFrame([_buy_row()])
        out = run_step(df, {"order_mode": "reorder"})
        # mid=18, 45d cover → 27.
        assert out.iloc[0]["order_qty_recommended"] == 27

    def test_run_step_unknown_order_mode_collapses_to_first(self):
        df = pd.DataFrame([_buy_row()])
        out = run_step(df, {"order_mode": "weird-value"})
        assert out.iloc[0]["order_qty_recommended"] == 13

    def test_run_step_empty_string_order_mode_uses_first(self):
        # Per the YAML interpolation contract, an unset {order_mode}
        # would resolve to ""; default to first-order.
        df = pd.DataFrame([_buy_row()])
        out = run_step(df, {"order_mode": ""})
        assert out.iloc[0]["order_qty_recommended"] == 13

    def test_run_step_empty_dataframe(self):
        out = run_step(pd.DataFrame(), {})
        assert out.empty
        for col in BUY_PLAN_COLUMNS:
            assert col in out.columns


# ────────────────────────────────────────────────────────────────────────
# Multi-row integration smoke.
# ────────────────────────────────────────────────────────────────────────


class TestMultiRowIntegration:
    def test_all_verdicts_in_single_pass(self):
        rows = [_buy_row(), _source_only_row(), _negotiate_row(),
                _watch_row(), _kill_row()]
        df = pd.DataFrame(rows)
        out = run_step(df, {"order_mode": "first"})
        assert len(out) == 5
        # No row crashed.
        for _, row in out.iterrows():
            assert row["buy_plan_status"] in {
                STATUS_OK,
                STATUS_NO_BUY_COST,
                STATUS_BLOCKED_BY_VERDICT,
                STATUS_INSUFFICIENT_DATA,
            }
