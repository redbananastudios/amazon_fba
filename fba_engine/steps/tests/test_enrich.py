"""Tests for fba_engine.steps.enrich.

Stage 03 of the canonical engine: applies the SP-API preflight
annotation (restrictions, FBA eligibility, live Buy Box, catalog
brand, hazmat, etc.) to the result rows.

The actual MCP CLI invocation is deferred to
``sourcing_engine.pipeline.preflight.annotate_with_preflight``; these
tests verify the runner contract — config gating, df conversion, and
that the function is callable with empty / non-empty inputs without
needing live SP-API credentials.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from fba_engine.steps.enrich import enrich_with_preflight, run_step


def _row(**overrides) -> dict:
    base = {
        "supplier": "test", "supplier_sku": "SKU-A",
        "ean": "5012345678900", "asin": "B0CLEAN001",
        "decision": "SHORTLIST", "market_price": 14.5,
    }
    base.update(overrides)
    return base


class TestEnrichWithPreflight:
    def test_empty_df_passes_through(self):
        out = enrich_with_preflight(pd.DataFrame(), enabled=True)
        assert out.empty

    def test_disabled_passes_through_unchanged(self):
        # When disabled, no MCP call is made and df is returned as-is.
        df = pd.DataFrame([_row()])
        with patch(
            "fba_engine.steps.enrich.annotate_with_preflight"
        ) as m:
            out = enrich_with_preflight(df, enabled=False)
        m.assert_not_called()
        assert len(out) == 1

    def test_enabled_invokes_annotator(self):
        # With enabled=True the wrapper hands the rows to annotate_with_preflight
        # and returns a DataFrame built from its mutated output.
        df = pd.DataFrame([_row()])

        def fake_annotate(rows, **kwargs):
            for r in rows:
                r["restriction_status"] = "approved"
            return rows

        with patch(
            "fba_engine.steps.enrich.annotate_with_preflight",
            side_effect=fake_annotate,
        ) as m:
            out = enrich_with_preflight(df, enabled=True)
        m.assert_called_once()
        assert "restriction_status" in out.columns
        assert out.iloc[0]["restriction_status"] == "approved"


class TestSurvivorsOnly:
    """`survivors_only=True` skips the MCP call for REJECT rows. A
    typical 5720-row supplier run has ~5700 REJECTs we don't need
    SP-API data for; without this filter the run takes 10+ minutes
    on the SP-API side."""

    def test_only_non_reject_rows_passed_to_annotator(self):
        df = pd.DataFrame([
            _row(asin="B0KILL00001", decision="REJECT"),
            _row(asin="B0LIVE00001", decision="SHORTLIST"),
            _row(asin="B0KILL00002", decision="REJECT"),
            _row(asin="B0LIVE00002", decision="REVIEW"),
        ])

        seen_asins = []

        def fake_annotate(rows, **kwargs):
            for r in rows:
                seen_asins.append(r["asin"])
                r["restriction_status"] = "approved"
            return rows

        with patch(
            "fba_engine.steps.enrich.annotate_with_preflight",
            side_effect=fake_annotate,
        ):
            out = enrich_with_preflight(df, enabled=True, survivors_only=True)
        # MCP saw only the survivors.
        assert set(seen_asins) == {"B0LIVE00001", "B0LIVE00002"}
        # All 4 rows still in output, in original order.
        assert list(out["asin"]) == [
            "B0KILL00001", "B0LIVE00001", "B0KILL00002", "B0LIVE00002",
        ]
        # Survivors got the preflight column populated.
        assert out[out["asin"] == "B0LIVE00001"].iloc[0]["restriction_status"] == "approved"
        # REJECT rows have None for the preflight column (seeded by union).
        reject_status = out[out["asin"] == "B0KILL00001"].iloc[0]["restriction_status"]
        assert reject_status is None or pd.isna(reject_status)

    def test_no_survivors_returns_input_unchanged(self):
        df = pd.DataFrame([
            _row(asin="B0KILL00001", decision="REJECT"),
            _row(asin="B0KILL00002", decision="REJECT"),
        ])
        with patch(
            "fba_engine.steps.enrich.annotate_with_preflight"
        ) as m:
            out = enrich_with_preflight(df, enabled=True, survivors_only=True)
        m.assert_not_called()
        assert len(out) == 2

    def test_no_decision_column_falls_back_to_full_pass(self):
        # A chain that hasn't run `decide` yet — survivors_only is a
        # no-op fallback rather than a hard error, so older chains
        # adding the flag accidentally don't break.
        df = pd.DataFrame([_row(asin="B0NODECIDE", decision=None)])
        df = df.drop(columns=["decision"])

        def fake_annotate(rows, **kwargs):
            for r in rows:
                r["restriction_status"] = "approved"
            return rows

        with patch(
            "fba_engine.steps.enrich.annotate_with_preflight",
            side_effect=fake_annotate,
        ) as m:
            out = enrich_with_preflight(df, enabled=True, survivors_only=True)
        m.assert_called_once()
        assert "restriction_status" in out.columns

    def test_run_step_truthy_string_survivors_only(self):
        df = pd.DataFrame([
            _row(asin="B0KILL00001", decision="REJECT"),
            _row(asin="B0LIVE00001", decision="SHORTLIST"),
        ])
        seen = []

        def fake_annotate(rows, **kwargs):
            for r in rows:
                seen.append(r["asin"])
            return rows

        with patch(
            "fba_engine.steps.enrich.annotate_with_preflight",
            side_effect=fake_annotate,
        ):
            run_step(df, {"survivors_only": "true"})
        assert seen == ["B0LIVE00001"]


class TestRunStep:
    def test_run_step_default_enabled_true(self):
        # Default to enabled=True (matches legacy behaviour). MCP failure
        # is swallowed by annotate_with_preflight itself.
        df = pd.DataFrame([_row()])
        with patch(
            "fba_engine.steps.enrich.annotate_with_preflight",
            return_value=df.to_dict("records"),
        ) as m:
            run_step(df, {})
        m.assert_called_once()

    def test_run_step_explicit_disabled(self):
        df = pd.DataFrame([_row()])
        with patch(
            "fba_engine.steps.enrich.annotate_with_preflight"
        ) as m:
            run_step(df, {"enabled": False})
        m.assert_not_called()

    def test_run_step_empty(self):
        out = run_step(pd.DataFrame(), {})
        assert out.empty

    def test_run_step_include_leads_alias_expands(self):
        # `include: leads` is the YAML-friendly alias for the
        # restrictions+fba+catalog subset used by ASIN-only chains.
        df = pd.DataFrame([_row()])
        with patch(
            "fba_engine.steps.enrich.annotate_with_preflight",
            return_value=df.to_dict("records"),
        ) as m:
            run_step(df, {"include": "leads"})
        m.assert_called_once()
        # The expanded list lands in annotate_with_preflight's kwargs.
        kwargs = m.call_args.kwargs
        assert kwargs.get("include") == ["restrictions", "fba", "catalog"]

    def test_run_step_include_passthrough_for_explicit_list(self):
        df = pd.DataFrame([_row()])
        with patch(
            "fba_engine.steps.enrich.annotate_with_preflight",
            return_value=df.to_dict("records"),
        ) as m:
            run_step(df, {"include": ["restrictions"]})
        kwargs = m.call_args.kwargs
        assert kwargs.get("include") == ["restrictions"]


class TestLeadsModeAllowsAsinOnly:
    """When include excludes pricing sources, ASIN-only rows (no
    market_price) preflight successfully — the contract that lets
    seller_storefront and other leads chains use this step."""

    def test_asin_only_row_with_leads_include_passes_through_to_annotate(self):
        # Row has asin but no market_price — under leads mode
        # (include = restrictions+fba+catalog), annotate_with_preflight
        # is still called with the row included. The actual MCP call
        # is mocked.
        df = pd.DataFrame([{
            "asin": "B0LEAD",
            "source": "seller_storefront",
            "brand": "Acme",
        }])
        captured: list = []

        def fake_annotate(rows, **kwargs):
            captured.append((list(rows), kwargs))
            for r in rows:
                r["restriction_status"] = "approved"
            return rows

        with patch(
            "fba_engine.steps.enrich.annotate_with_preflight",
            side_effect=fake_annotate,
        ):
            out = enrich_with_preflight(
                df, enabled=True,
                include=["restrictions", "fba", "catalog"],
            )
        # annotate received the row (the leads-mode allow_no_price
        # branch handled the missing market_price).
        rows_passed, kwargs = captured[0]
        assert len(rows_passed) == 1
        assert rows_passed[0]["asin"] == "B0LEAD"
        assert kwargs.get("include") == ["restrictions", "fba", "catalog"]
        # Annotation columns are on the output.
        assert out.iloc[0]["restriction_status"] == "approved"
