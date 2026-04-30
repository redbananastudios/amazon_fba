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
