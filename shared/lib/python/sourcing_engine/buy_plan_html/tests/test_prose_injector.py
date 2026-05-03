"""Tests for prose_injector — replaces <!-- prose:{asin} --> markers."""
from __future__ import annotations

import logging

from sourcing_engine.buy_plan_html.prose_injector import inject_prose


def test_replaces_marker_with_paragraph():
    html = '<div class="prose" data-asin="B0AAA000001"><!-- prose:B0AAA000001 --></div>'
    out = inject_prose(html, {"B0AAA000001": "Test prose."})
    assert "<!-- prose:B0AAA000001 -->" not in out
    assert '<p class="prose-text">Test prose.</p>' in out


def test_idempotent_on_second_run():
    html = '<!-- prose:B0AAA000001 -->'
    once = inject_prose(html, {"B0AAA000001": "Hi."})
    twice = inject_prose(once, {"B0AAA000001": "Hi."})
    assert once == twice


def test_missing_prose_leaves_marker_and_logs_warning(caplog):
    html = '<!-- prose:B0AAA000001 --> <!-- prose:B0BBB000001 -->'
    with caplog.at_level(logging.WARNING):
        out = inject_prose(html, {"B0AAA000001": "Hi."})
    assert '<p class="prose-text">Hi.</p>' in out
    assert "<!-- prose:B0BBB000001 -->" in out
    assert any("B0BBB000001" in rec.message for rec in caplog.records)


def test_prose_for_unknown_asin_logs_and_ignored(caplog):
    html = '<!-- prose:B0AAA000001 -->'
    with caplog.at_level(logging.WARNING):
        out = inject_prose(html, {"B0AAA000001": "Hi.", "B0NEVER0001": "stranded"})
    assert '<p class="prose-text">Hi.</p>' in out
    assert "B0NEVER0001" not in out
    assert any("B0NEVER0001" in rec.message for rec in caplog.records)


def test_html_escape_in_prose():
    html = '<!-- prose:B0AAA000001 -->'
    out = inject_prose(html, {"B0AAA000001": "Profit > £5 & risk < 1%"})
    assert "&gt;" in out
    assert "&amp;" in out
    assert "&lt;" in out


def test_strips_html_tags_from_prose_input():
    html = '<!-- prose:B0AAA000001 -->'
    out = inject_prose(html, {"B0AAA000001": "<p>Hi <em>there</em></p>"})
    assert "<em>" not in out
    assert "Hi there" in out


def test_caps_prose_at_500_chars():
    long_prose = "a" * 1000
    html = '<!-- prose:B0AAA000001 -->'
    out = inject_prose(html, {"B0AAA000001": long_prose})
    body = out.replace('<p class="prose-text">', "").replace("</p>", "")
    assert len(body) <= 500


def test_collapses_whitespace():
    html = '<!-- prose:B0AAA000001 -->'
    out = inject_prose(html, {"B0AAA000001": "Multiple\n\nspaces  here.\tand\ttabs."})
    body = out.replace('<p class="prose-text">', "").replace("</p>", "")
    assert "  " not in body
    assert "\n" not in body
    assert "\t" not in body


def test_empty_prose_string_treated_as_missing(caplog):
    html = '<!-- prose:B0AAA000001 -->'
    with caplog.at_level(logging.WARNING):
        out = inject_prose(html, {"B0AAA000001": "   "})
    assert "<!-- prose:B0AAA000001 -->" in out


def test_no_markers_no_prose_returns_html_unchanged():
    html = '<html><body>Hello</body></html>'
    assert inject_prose(html, {}) == html


def test_short_asin_pattern_not_matched():
    # Markers below 10 chars don't match — defensive against weird ASINs.
    html = '<!-- prose:SHORT --><!-- prose:B0VALID0001 -->'
    out = inject_prose(html, {"B0VALID0001": "Hi.", "SHORT": "ignored"})
    assert "<!-- prose:SHORT -->" in out
    assert "<!-- prose:B0VALID0001 -->" not in out
