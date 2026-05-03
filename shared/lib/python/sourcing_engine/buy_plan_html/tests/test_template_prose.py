"""Tests for template_prose — deterministic fallback prose."""
from __future__ import annotations

import pytest

from sourcing_engine.buy_plan_html.template_prose import render_template_prose


def _payload(verdict: str, **overrides) -> dict:
    base = {
        "asin": "B0TEST00001",
        "title": "Test product",
        "verdict": verdict,
        "verdict_confidence": "HIGH",
        "next_action": "test action",
        "economics": {
            "buy_cost_gbp": 4.00,
            "profit_per_unit_gbp": 8.35,
            "roi_conservative_pct": 1.114,
            "target_buy_cost_gbp": 9.50,
            "target_buy_cost_stretch_gbp": 8.52,
        },
        "buy_plan": {
            "order_qty_recommended": 13,
            "capital_required_gbp": 52.00,
            "projected_30d_units": 18,
            "projected_30d_revenue_gbp": 303.30,
            "projected_30d_profit_gbp": 150.30,
            "payback_days": 21.7,
            "gap_to_buy_gbp": None,
            "gap_to_buy_pct": None,
            "buy_plan_status": "OK",
        },
        "metrics": [],
        "engine_reasons": [],
        "engine_blockers": [],
        "risk_flags": [],
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("verdict", ["BUY", "SOURCE_ONLY", "NEGOTIATE", "WATCH"])
def test_template_prose_non_empty_per_verdict(verdict):
    out = render_template_prose(_payload(verdict))
    assert isinstance(out, str)
    assert len(out) > 0


@pytest.mark.parametrize("verdict", ["BUY", "SOURCE_ONLY", "NEGOTIATE", "WATCH"])
def test_template_prose_deterministic(verdict):
    p = _payload(verdict)
    assert render_template_prose(p) == render_template_prose(p)


def test_template_prose_buy_mentions_order_qty():
    out = render_template_prose(_payload("BUY"))
    assert "13" in out
    assert "buy" in out.lower() or "order" in out.lower()


def test_template_prose_source_only_mentions_target():
    p = _payload("SOURCE_ONLY", buy_plan={
        "order_qty_recommended": None, "capital_required_gbp": None,
        "projected_30d_units": 42, "projected_30d_revenue_gbp": 710.00,
        "projected_30d_profit_gbp": 136.00, "payback_days": None,
        "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
        "buy_plan_status": "NO_BUY_COST",
    })
    p["economics"]["buy_cost_gbp"] = None
    p["economics"]["target_buy_cost_gbp"] = 4.85
    out = render_template_prose(p)
    assert "4.85" in out
    assert "supplier" in out.lower() or "source" in out.lower()


def test_template_prose_negotiate_mentions_gap():
    p = _payload("NEGOTIATE", buy_plan={
        "order_qty_recommended": None, "capital_required_gbp": None,
        "projected_30d_units": 18, "projected_30d_revenue_gbp": 303.30,
        "projected_30d_profit_gbp": 42.30, "payback_days": None,
        "gap_to_buy_gbp": 0.62, "gap_to_buy_pct": 0.124,
        "buy_plan_status": "OK",
    })
    p["economics"]["buy_cost_gbp"] = 5.00
    p["economics"]["target_buy_cost_gbp"] = 4.38
    out = render_template_prose(p)
    assert "0.62" in out or "12.4" in out
    assert "negotiat" in out.lower() or "down" in out.lower()


def test_template_prose_watch_mentions_monitoring():
    p = _payload("WATCH", risk_flags=["INSUFFICIENT_HISTORY"])
    out = render_template_prose(p)
    assert "watch" in out.lower() or "monitor" in out.lower()


def test_template_prose_watch_mentions_blocker_when_present():
    p = _payload("WATCH", engine_blockers=["sales=70 < 100", "data_confidence=LOW"])
    out = render_template_prose(p)
    assert "sales" in out.lower() or "blocker" in out.lower()


def test_template_prose_minimal_data_does_not_crash():
    minimal = {"verdict": "BUY", "asin": "B0TEST00001"}
    out = render_template_prose(minimal)
    assert isinstance(out, str)
    assert len(out) > 0


def test_template_prose_unknown_verdict_does_not_crash():
    out = render_template_prose({"verdict": "FOO"})
    assert isinstance(out, str)
    assert len(out) > 0


def test_template_prose_empty_input_does_not_crash():
    out = render_template_prose({})
    assert isinstance(out, str)
