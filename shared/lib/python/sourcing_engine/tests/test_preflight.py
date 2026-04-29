"""Tests for the preflight annotation step.

Mocks the MCP CLI subprocess so we don't need SP-API creds. Verifies that
new columns are added correctly, errors surface in preflight_errors, and
the decision gate is NOT touched (SHORTLIST/REVIEW/REJECT counts unchanged).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from sourcing_engine.pipeline import preflight as pf


def _fake_cli_response(asins, *, restricted=None, ineligible=None, buy_box=None):
    """Build a fake CLI JSON response in the shape preflight_asin returns."""
    restricted = restricted or set()
    ineligible = ineligible or set()
    buy_box = buy_box or {}
    results = []
    for asin in asins:
        result = {
            "asin": asin,
            "cached": {},
            "errors": [],
            "restrictions": {
                "asin": asin,
                "status": "BRAND_GATED" if asin in restricted else "UNRESTRICTED",
                "reasons": (
                    [{"reasonCode": "APPROVAL_REQUIRED",
                      "message": "brand approval required"}]
                    if asin in restricted else []
                ),
                "approval_required": asin in restricted,
                "marketplace_id": "A1F83G8C2ARO7P",
            },
            "fba": {
                "asin": asin,
                "eligible": asin not in ineligible,
                "ineligibility_reasons": (
                    [{"code": "FBA_INB_0019", "description": "hazmat"}]
                    if asin in ineligible else []
                ),
                "marketplace_id": "A1F83G8C2ARO7P",
                "program": "INBOUND",
            },
            "fees": None,
            "catalog": {
                "asin": asin,
                "brand": "AcmeCanonical",
                "marketplace_id": "A1F83G8C2ARO7P",
            },
            "pricing": {
                "asin": asin,
                "buy_box_price": buy_box.get(asin),
                "buy_box_seller": "FBA" if buy_box.get(asin) else None,
                "offer_count_new": 3,
                "offer_count_fba": 2,
                "marketplace_id": "A1F83G8C2ARO7P",
            },
            "profitability": None,
        }
        results.append(result)
    return {"results": results}


def _row(asin: str, decision: str = "SHORTLIST") -> dict:
    return {
        "asin": asin,
        "ean": f"E_{asin}",
        "supplier": "test-supplier",
        "product_name": f"Product {asin}",
        "buy_cost": 4.0,
        "market_price": 12.99,
        "raw_conservative_price": 11.99,
        "brand": "KeepaBrand",
        "decision": decision,
        "decision_reason": "ok",
    }


# ──────────────────────────────────────────────────────────────────────────
# is_preflight_available
# ──────────────────────────────────────────────────────────────────────────

def test_is_preflight_unavailable_when_no_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("SP_API_CLIENT_ID", "x")
    available, reason = pf.is_preflight_available(tmp_path)
    assert available is False
    assert "CLI not found" in reason


def test_is_preflight_unavailable_when_no_credentials(tmp_path, monkeypatch):
    cli = tmp_path / "services" / "amazon-fba-fees-mcp" / "dist" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fake")
    (tmp_path / "fba_engine").mkdir()
    monkeypatch.delenv("SP_API_CLIENT_ID", raising=False)
    available, reason = pf.is_preflight_available(tmp_path)
    assert available is False
    assert "SP_API_CLIENT_ID" in reason


# ──────────────────────────────────────────────────────────────────────────
# annotate_with_preflight: skip paths
# ──────────────────────────────────────────────────────────────────────────

def test_annotate_returns_empty_for_empty_rows():
    assert pf.annotate_with_preflight([]) == []


def test_annotate_seeds_columns_when_cli_missing(tmp_path):
    rows = [_row("B001"), _row("B002")]
    pf.annotate_with_preflight(rows, cli_path=tmp_path / "nope.js")
    for row in rows:
        for col in pf.PREFLIGHT_COLUMNS:
            assert col in row
        # keepa_brand is seeded from row.brand
        assert row["keepa_brand"] == "KeepaBrand"
        assert row["restriction_status"] is None
        assert row["fba_eligible"] is None


# ──────────────────────────────────────────────────────────────────────────
# annotate_with_preflight: happy path
# ──────────────────────────────────────────────────────────────────────────

def test_annotate_populates_columns_from_cli_response(tmp_path, monkeypatch):
    cli = tmp_path / "cli.js"
    cli.write_text("// fake")
    monkeypatch.setenv("SP_API_CLIENT_ID", "x")
    rows = [_row("B001"), _row("B002")]
    fake_proc = MagicMock(
        returncode=0,
        stdout=json.dumps(_fake_cli_response(
            ["B001", "B002"],
            restricted={"B002"},
            buy_box={"B001": 14.99, "B002": 13.50},
        )),
        stderr="",
    )
    with patch.object(pf, "_node_executable", return_value="node"), \
         patch.object(pf, "_find_repo_root", return_value=tmp_path), \
         patch("subprocess.run", return_value=fake_proc) as mock_run:
        pf.annotate_with_preflight(rows, cli_path=cli)
    assert mock_run.call_count == 1
    # B001 — unrestricted, eligible, has live BB
    assert rows[0]["restriction_status"] == "UNRESTRICTED"
    assert rows[0]["fba_eligible"] is True
    assert rows[0]["live_buy_box"] == 14.99
    assert rows[0]["catalog_brand"] == "AcmeCanonical"
    assert rows[0]["keepa_brand"] == "KeepaBrand"
    assert rows[0]["live_offer_count_new"] == 3
    # B002 — brand gated
    assert rows[1]["restriction_status"] == "BRAND_GATED"
    assert rows[1]["restriction_reasons"] == "APPROVAL_REQUIRED"


def test_annotate_decision_field_is_untouched(tmp_path, monkeypatch):
    """Verify the preflight annotation does NOT modify decision/decision_reason
    (the central non-goal of the spec: informational only)."""
    cli = tmp_path / "cli.js"
    cli.write_text("// fake")
    monkeypatch.setenv("SP_API_CLIENT_ID", "x")
    rows = [
        _row("B001", decision="SHORTLIST"),
        _row("B002", decision="REJECT"),
        _row("B003", decision="REVIEW"),
    ]
    decisions_before = [r["decision"] for r in rows]
    reasons_before = [r["decision_reason"] for r in rows]
    fake_proc = MagicMock(
        returncode=0,
        stdout=json.dumps(_fake_cli_response(
            ["B001", "B002", "B003"], restricted={"B001"}, ineligible={"B002"}
        )),
        stderr="",
    )
    with patch.object(pf, "_node_executable", return_value="node"), \
         patch.object(pf, "_find_repo_root", return_value=tmp_path), \
         patch("subprocess.run", return_value=fake_proc):
        pf.annotate_with_preflight(rows, cli_path=cli)
    decisions_after = [r["decision"] for r in rows]
    reasons_after = [r["decision_reason"] for r in rows]
    assert decisions_before == decisions_after
    assert reasons_before == reasons_after


def test_annotate_batches_into_groups_of_20(tmp_path, monkeypatch):
    cli = tmp_path / "cli.js"
    cli.write_text("// fake")
    monkeypatch.setenv("SP_API_CLIENT_ID", "x")
    rows = [_row(f"B{i:03d}") for i in range(45)]

    def stdout_for_call(call):
        # subprocess.run signature: run([node, cli, "preflight", "--input", "-"], input=...)
        payload = json.loads(call.kwargs["input"])
        asins = [it["asin"] for it in payload["items"]]
        return json.dumps(_fake_cli_response(asins))

    fake_responses = []

    def runner(*args, **kwargs):
        payload = json.loads(kwargs["input"])
        asins = [it["asin"] for it in payload["items"]]
        return MagicMock(
            returncode=0,
            stdout=json.dumps(_fake_cli_response(asins)),
            stderr="",
        )

    with patch.object(pf, "_node_executable", return_value="node"), \
         patch.object(pf, "_find_repo_root", return_value=tmp_path), \
         patch("subprocess.run", side_effect=runner) as mock_run:
        pf.annotate_with_preflight(rows, cli_path=cli)

    # 45 rows → batches of 20, 20, 5 = 3 calls
    assert mock_run.call_count == 3
    # All rows annotated
    for row in rows:
        assert row["restriction_status"] == "UNRESTRICTED"


def test_annotate_skips_rows_with_no_asin(tmp_path, monkeypatch):
    cli = tmp_path / "cli.js"
    cli.write_text("// fake")
    monkeypatch.setenv("SP_API_CLIENT_ID", "x")
    good = _row("B001")
    bad = _row("B002")
    bad["asin"] = None
    rows = [good, bad]
    fake_proc = MagicMock(
        returncode=0,
        stdout=json.dumps(_fake_cli_response(["B001"])),
        stderr="",
    )
    with patch.object(pf, "_node_executable", return_value="node"), \
         patch.object(pf, "_find_repo_root", return_value=tmp_path), \
         patch("subprocess.run", return_value=fake_proc):
        pf.annotate_with_preflight(rows, cli_path=cli)
    assert rows[0]["restriction_status"] == "UNRESTRICTED"
    # Bad row got seeded with None
    assert rows[1]["restriction_status"] is None
    assert rows[1]["keepa_brand"] == "KeepaBrand"


def test_annotate_seeds_rows_when_cli_fails(tmp_path, monkeypatch):
    cli = tmp_path / "cli.js"
    cli.write_text("// fake")
    monkeypatch.setenv("SP_API_CLIENT_ID", "x")
    rows = [_row("B001"), _row("B002")]
    fake_proc = MagicMock(returncode=1, stdout="", stderr="boom")
    with patch.object(pf, "_node_executable", return_value="node"), \
         patch.object(pf, "_find_repo_root", return_value=tmp_path), \
         patch("subprocess.run", return_value=fake_proc):
        pf.annotate_with_preflight(rows, cli_path=cli)
    for row in rows:
        assert row["restriction_status"] is None
        assert "keepa_brand" in row


def test_annotate_propagates_per_source_errors(tmp_path, monkeypatch):
    cli = tmp_path / "cli.js"
    cli.write_text("// fake")
    monkeypatch.setenv("SP_API_CLIENT_ID", "x")
    rows = [_row("B001")]
    response = _fake_cli_response(["B001"])
    response["results"][0]["errors"] = [
        {"source": "fba", "message": "FBA service down"},
    ]
    fake_proc = MagicMock(returncode=0, stdout=json.dumps(response), stderr="")
    with patch.object(pf, "_node_executable", return_value="node"), \
         patch.object(pf, "_find_repo_root", return_value=tmp_path), \
         patch("subprocess.run", return_value=fake_proc):
        pf.annotate_with_preflight(rows, cli_path=cli)
    assert "fba:FBA service down" in rows[0]["preflight_errors"]


# ──────────────────────────────────────────────────────────────────────────
# restriction_notes_for_shortlist
# ──────────────────────────────────────────────────────────────────────────

def test_restriction_notes_filters_to_shortlist_and_gated():
    rows = [
        {**_row("B001"), "restriction_status": "UNRESTRICTED"},
        {**_row("B002"), "restriction_status": "BRAND_GATED"},
        {**_row("B003", decision="REJECT"), "restriction_status": "BRAND_GATED"},
        {**_row("B004"), "restriction_status": None},
    ]
    notes = pf.restriction_notes_for_shortlist(rows)
    assert len(notes) == 1
    assert notes[0]["asin"] == "B002"
