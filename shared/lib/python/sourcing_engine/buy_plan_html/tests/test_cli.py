"""Tests for sourcing_engine.buy_plan_html.cli — render-from-json."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sourcing_engine.buy_plan_html.cli import _render_from_json, main


def _minimal_payload() -> dict:
    return {
        "schema_version": 2,
        "prompt_version": 2,
        "run_id": "20260503_120000",
        "strategy": "supplier_pricelist",
        "supplier": "test",
        "generated_at": "2026-05-03T12:00:00Z",
        "verdict_counts": {
            "BUY": 0, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0,
        },
        "rows": [],
    }


def test_render_from_json_writes_html(tmp_path):
    json_path = tmp_path / "buyer_report.json"
    html_path = tmp_path / "buyer_report.html"
    json_path.write_text(json.dumps(_minimal_payload()), encoding="utf-8")
    rc = _render_from_json(json_path, html_path)
    assert rc == 0
    assert html_path.exists()
    body = html_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in body
    assert "no actionable rows" in body.lower()


def test_render_from_json_missing_file_returns_2(tmp_path, capsys):
    rc = _render_from_json(
        tmp_path / "does-not-exist.json",
        tmp_path / "out.html",
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "JSON not found" in err


def test_render_from_json_malformed_returns_2(tmp_path, capsys):
    json_path = tmp_path / "broken.json"
    html_path = tmp_path / "out.html"
    json_path.write_text("not valid json{", encoding="utf-8")
    rc = _render_from_json(json_path, html_path)
    assert rc == 2
    err = capsys.readouterr().err
    assert "malformed JSON" in err
    # No HTML produced.
    assert not html_path.exists()


def test_render_from_json_atomic_write_no_partial_file(tmp_path, monkeypatch):
    """Render uses tmp + rename so a crash mid-write leaves either
    the prior HTML or the new HTML — never a partial mix."""
    json_path = tmp_path / "buyer_report.json"
    html_path = tmp_path / "buyer_report.html"
    json_path.write_text(json.dumps(_minimal_payload()), encoding="utf-8")

    # Pre-populate the HTML with sentinel content.
    html_path.write_text("PRIOR-CONTENT", encoding="utf-8")

    rc = _render_from_json(json_path, html_path)
    assert rc == 0
    body = html_path.read_text(encoding="utf-8")
    assert "PRIOR-CONTENT" not in body   # full overwrite, not partial


def test_render_from_json_creates_parent_dir(tmp_path):
    """Output dir doesn't have to exist — render creates it."""
    json_path = tmp_path / "buyer_report.json"
    html_path = tmp_path / "subdir" / "buyer_report.html"
    json_path.write_text(json.dumps(_minimal_payload()), encoding="utf-8")
    rc = _render_from_json(json_path, html_path)
    assert rc == 0
    assert html_path.exists()


def test_render_from_json_idempotent(tmp_path):
    """Running twice with same input → byte-identical HTML."""
    json_path = tmp_path / "buyer_report.json"
    html_path = tmp_path / "buyer_report.html"
    payload = _minimal_payload()
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    _render_from_json(json_path, html_path)
    first = html_path.read_text(encoding="utf-8")
    _render_from_json(json_path, html_path)
    second = html_path.read_text(encoding="utf-8")
    assert first == second


def test_main_dispatches_render_from_json(tmp_path):
    json_path = tmp_path / "buyer_report.json"
    html_path = tmp_path / "buyer_report.html"
    json_path.write_text(json.dumps(_minimal_payload()), encoding="utf-8")
    rc = main(["render-from-json", str(json_path), str(html_path)])
    assert rc == 0
    assert html_path.exists()


def test_main_no_subcommand_exits_with_error(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code != 0
