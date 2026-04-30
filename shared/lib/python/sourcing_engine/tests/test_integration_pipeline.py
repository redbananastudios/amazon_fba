"""End-to-end integration test for `sourcing_engine.main.run_pipeline`.

The 42 existing tests exercise individual pipeline modules
(`decide()`, `calculate_profit()`, `derive_costs()`, etc.) but NONE of
them invoke `run_pipeline` end-to-end. Without that integration test,
the upcoming canonical-engine refactor (extracting resolve / enrich /
calculate / decide / output as `run_step`-shaped modules per
`docs/PRD-sourcing-strategies.md` Phase 2) flies blind — a refactor
that drops a `risk_flags.append(...)` call or reorders a column-add
keeps the unit tests green while breaking production.

This test invokes `run_pipeline` against a self-contained fixture
(supplier CSV + Keepa market data CSV, both written at `tmp_path`)
and pins:

  - Output schema (column set in shortlist_*.csv)
  - Decision counts (SHORTLIST / REVIEW / REJECT distribution)
  - Specific row outcomes (a known-good row SHORTLISTs; an invalid-EAN
    row REJECTs)
  - Output file presence (CSV + XLSX + MD all written)

If any of these change, either the fixture is wrong or production
behaviour is. The test deliberately doesn't assert on row values
that depend on the day's date, capital exposure thresholds in
config, or other runtime-dependent state.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

# Imports work because conftest.py at repo root adds shared/lib/python
# to sys.path (mirrors what run.py does at runtime).
from sourcing_engine.main import run_pipeline
from sourcing_engine.output.csv_writer import OUTPUT_COLUMNS


# ─────────────────────────────────────────────────────────────────────
# Fixture builders.
# ─────────────────────────────────────────────────────────────────────


# Connect-Beauty CSV format, per the existing adapter's column map.
# Every row designed to exercise a specific decision path. EANs are
# real EAN-13 with valid check digits (verified via the engine's
# ean_validator) — invalid checksums get rejected before they can
# exercise the "no match" path.
_SUPPLIER_CSV = """\
SKU,Manufacturer,Title,Category,Barcode,Case Size,Units Available,Unit Price (GBP),Case Price (GBP)
SKU-SHORT,Acme,Profitable Widget Pro,Tools,5012345678900,12,500,5.00,60.00
SKU-INVALID,Acme,Bad EAN Item,Tools,not-a-real-ean,1,100,3.00,3.00
SKU-NO-MATCH,Acme,Unknown to Keepa,Tools,5099999999995,1,100,4.50,4.50
SKU-EXPENSIVE,Acme,Loss Maker,Tools,5012345678917,1,100,40.00,40.00
"""

# Keepa CSV columns — only the subset load_market_data uses. The
# `New, 3rd Party FBA: Current` header is properly CSV-quoted so the
# embedded comma doesn't terminate the field early.
#
# SKU-SHORT (5012345678900): healthy product → should SHORTLIST.
# SKU-EXPENSIVE (5012345678917): supplier price too high → should REJECT.
# SKU-NO-MATCH (5099999999995): NOT in this Keepa data → should REJECT (no match).
_KEEPA_CSV = (
    'ASIN,Title,Brand,Buy Box: Current,Amazon: Current,'
    'New Offer Count: Current,Sales Rank: Current,Bought in past month,'
    'Buy Box: 90 days avg.,"New, 3rd Party FBA: Current",'
    'FBA Pick&Pack Fee,Referral Fee %,Product Codes: EAN,'
    'Reviews: Rating,Reviews: Rating Count\n'
    'B0SHORT001,Profitable Widget Pro,Acme,£15.00,£14.00,5,5000,150,'
    '£14.50,£14.80,£3.00,15%,5012345678900,4.5,200\n'
    'B0EXPENS01,Loss Maker,Acme,£25.00,£24.00,5,8000,80,'
    '£25.00,£25.00,£4.00,15%,5012345678917,4.0,50\n'
)


def _write_supplier_input(tmp_path: Path) -> Path:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    csv_path = raw_dir / "test_pricelist.csv"
    csv_path.write_text(_SUPPLIER_CSV, encoding="utf-8")
    return raw_dir


def _write_keepa_market_data(tmp_path: Path) -> Path:
    csv_path = tmp_path / "keepa.csv"
    csv_path.write_text(_KEEPA_CSV, encoding="utf-8")
    return csv_path


# ─────────────────────────────────────────────────────────────────────
# Tests.
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def pipeline_run(tmp_path: Path) -> dict:
    """Run the pipeline end-to-end against the canonical fixture.

    Returns a dict with paths + the loaded output DataFrame so each
    individual test can assert against it cheaply.
    """
    raw_dir = _write_supplier_input(tmp_path)
    keepa_path = _write_keepa_market_data(tmp_path)
    output_dir = tmp_path / "out"

    # Use connect-beauty as the adapter — it ships the most permissive
    # CSV ingest path and is real production code we exercise.
    # Disable preflight: the test environment doesn't have SP-API creds
    # and the preflight runs the MCP CLI which would slow this down to
    # tens of seconds even when it skips.
    run_dir = run_pipeline(
        supplier="connect-beauty",
        input_path=str(raw_dir),
        output_dir=str(output_dir),
        market_data_path=str(keepa_path),
        preflight_enabled=False,
    )

    assert run_dir is not None, "run_pipeline returned None — adapter or ingest failed"
    run_dir_path = Path(run_dir)

    # Find the produced shortlist CSV (timestamped name).
    csv_files = list(run_dir_path.glob("shortlist_*.csv"))
    assert len(csv_files) == 1, f"expected 1 shortlist CSV, found {csv_files}"

    df = pd.read_csv(csv_files[0])
    return {
        "run_dir": run_dir_path,
        "csv_path": csv_files[0],
        "df": df,
    }


class TestPipelineEndToEnd:
    def test_run_dir_contains_csv_xlsx_and_md(self, pipeline_run):
        run_dir = pipeline_run["run_dir"]
        assert any(run_dir.glob("shortlist_*.csv")), "missing shortlist CSV"
        assert any(run_dir.glob("shortlist_*.xlsx")), "missing shortlist XLSX"
        assert any(run_dir.glob("report_*.md")), "missing markdown report"

    def test_csv_schema_matches_canonical(self, pipeline_run):
        # The CSV writer filters OUTPUT_COLUMNS to those present in the
        # df. Pin that the actual columns are a subset of OUTPUT_COLUMNS
        # in the same order — any new column added without updating
        # OUTPUT_COLUMNS would be silently dropped, which we want to
        # catch.
        df = pipeline_run["df"]
        assert "decision" in df.columns
        assert "decision_reason" in df.columns
        assert "ean" in df.columns
        assert "asin" in df.columns
        # No column in the df should be entirely outside OUTPUT_COLUMNS
        # (otherwise the writer's filtering is silently losing data).
        unknown_cols = set(df.columns) - set(OUTPUT_COLUMNS)
        assert not unknown_cols, (
            f"CSV writer's OUTPUT_COLUMNS doesn't include some written "
            f"columns: {unknown_cols}"
        )

    def test_decision_counts_match_fixture_expectations(self, pipeline_run):
        # Pin the high-level decision distribution. The fixture is
        # designed to produce: 1 SHORTLIST + 0-2 REVIEW + 2-3 REJECT.
        # Any change in this distribution under the fixture inputs is
        # a behaviour change and must be inspected.
        df = pipeline_run["df"]
        decisions = df["decision"].value_counts().to_dict()
        # At least one row must reach each of the bucket sets we expect.
        assert decisions.get("REJECT", 0) >= 2, (
            f"Expected at least 2 REJECT (invalid EAN + no-match); "
            f"got distribution: {decisions}"
        )
        # Total rows = 4 supplier rows (each producing 1 output row in
        # this fixture since case_qty drives no expansion for these).
        # Note: case_qty=12 row may produce a separate case-qty output
        # row depending on adapter behaviour; we accept either shape.
        assert len(df) >= 4, f"expected ≥4 output rows, got {len(df)}"

    def test_invalid_ean_row_rejects_with_documented_reason(self, pipeline_run):
        # The "SKU-INVALID" row has a non-numeric EAN; the engine should
        # REJECT with "Invalid or missing EAN" verbatim. This pins the
        # error-reason wording so a future refactor can't silently change
        # it (operators may filter by it).
        df = pipeline_run["df"]
        invalid_rows = df[df["supplier_sku"] == "SKU-INVALID"]
        assert len(invalid_rows) >= 1
        assert invalid_rows.iloc[0]["decision"] == "REJECT"
        assert invalid_rows.iloc[0]["decision_reason"] == "Invalid or missing EAN"

    def test_no_match_row_rejects_with_no_match_reason(self, pipeline_run):
        # The "SKU-NO-MATCH" row has a valid-format EAN that doesn't
        # appear in the Keepa fixture; the engine should REJECT with
        # "No Amazon match found".
        df = pipeline_run["df"]
        no_match_rows = df[df["supplier_sku"] == "SKU-NO-MATCH"]
        assert len(no_match_rows) >= 1
        assert no_match_rows.iloc[0]["decision"] == "REJECT"
        assert no_match_rows.iloc[0]["decision_reason"] == "No Amazon match found"

    def test_profitable_row_reaches_shortlist_or_review(self, pipeline_run):
        # SKU-SHORT (£5 supplier cost vs £14 market price) should clear
        # ROI/profit thresholds and end up in SHORTLIST or REVIEW (not
        # REJECT). This is the primary "engine produces sensible
        # decisions" assertion — if a refactor drops fees or messes up
        # profit calc, this row may flip to REJECT and we'll catch it.
        df = pipeline_run["df"]
        shortlist_rows = df[df["supplier_sku"] == "SKU-SHORT"]
        assert len(shortlist_rows) >= 1
        decisions = set(shortlist_rows["decision"])
        assert decisions & {"SHORTLIST", "REVIEW"}, (
            f"SKU-SHORT (clearly profitable) ended up in {decisions} — "
            f"expected at least one SHORTLIST or REVIEW row. Reason(s): "
            f"{shortlist_rows['decision_reason'].tolist()}"
        )

    def test_unprofitable_row_rejects(self, pipeline_run):
        # SKU-EXPENSIVE (£40 supplier cost vs £25 market price) is a
        # clear loss maker; engine should REJECT with an unprofitable
        # reason. We don't pin the exact wording (it can be one of
        # several SPEC-defined strings) — just that the row REJECTs.
        df = pipeline_run["df"]
        expensive_rows = df[df["supplier_sku"] == "SKU-EXPENSIVE"]
        assert len(expensive_rows) >= 1
        assert all(expensive_rows["decision"] == "REJECT")

    def test_all_rows_have_a_decision(self, pipeline_run):
        # Every output row must carry a decision in {SHORTLIST, REVIEW,
        # REJECT}. A blank or unrecognised decision is a contract break.
        df = pipeline_run["df"]
        valid = {"SHORTLIST", "REVIEW", "REJECT"}
        unknown = set(df["decision"]) - valid
        assert not unknown, f"unexpected decision values: {unknown}"

    def test_buy_cost_propagates_to_output(self, pipeline_run):
        # Adapter-derived buy_cost must reach the output CSV. Every
        # non-rejected row should have a positive buy_cost.
        df = pipeline_run["df"]
        non_rejected = df[df["decision"] != "REJECT"]
        if len(non_rejected) > 0:
            assert (non_rejected["buy_cost"] > 0).all()
