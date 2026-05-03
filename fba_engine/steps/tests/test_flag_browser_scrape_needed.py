"""Tests for fba_engine.steps.flag_browser_scrape_needed.

Detects actionable rows that lack the historical Browser-derived
signals AND have no cache file. Writes a manifest the operator
acts on. Lowers data_confidence so the validator routes the row
away from BUY.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from fba_engine.steps.flag_browser_scrape_needed import (
    BROWSER_REQUIRED_SIGNALS,
    flag_browser_scrape_needed,
    run_step,
)


def _row(**overrides) -> dict:
    base = {
        "asin": "B0NICHE0001", "decision": "SHORTLIST",
        "buy_cost": 5.0, "buy_box_price": 15.0,
        "browser_scrape_present": False,
        "data_confidence": "MEDIUM",
        "data_confidence_reasons": [],
        "risk_flags": [],
        # All Browser-required signals missing — defaults to flagging.
        "amazon_bb_pct_90": None,
        "buy_box_drop_pct_90": None,
        "buy_box_min_365d": None,
        "buy_box_oos_pct_90": None,
    }
    base.update(overrides)
    return base


class TestFlagBrowserScrapeNeeded:
    def test_flags_row_with_all_browser_signals_missing(self):
        df = pd.DataFrame([_row()])
        out = flag_browser_scrape_needed(df)
        flags = out.iloc[0]["risk_flags"]
        assert "BROWSER_SCRAPE_NEEDED" in flags
        assert out.iloc[0]["data_confidence"] == "LOW"

    def test_does_not_flag_when_cache_present(self):
        # When the Keepa Browser scrape cache populated the row, no
        # need to flag — the data is already merged in.
        df = pd.DataFrame([_row(browser_scrape_present=True)])
        out = flag_browser_scrape_needed(df)
        flags = out.iloc[0]["risk_flags"]
        assert "BROWSER_SCRAPE_NEEDED" not in flags

    def test_does_not_flag_when_only_one_signal_missing(self):
        # Default min_missing_to_flag=3 — a single missing signal
        # might be a transient gap, not a fundamental Keepa-doesn't-
        # carry-this issue.
        df = pd.DataFrame([_row(
            amazon_bb_pct_90=0.05,
            buy_box_drop_pct_90=0.0,
            buy_box_min_365d=10.0,
            # buy_box_oos_pct_90 missing — only 1 of 4
        )])
        out = flag_browser_scrape_needed(df)
        assert "BROWSER_SCRAPE_NEEDED" not in out.iloc[0]["risk_flags"]

    def test_flags_when_three_or_more_missing(self):
        # 3 of 4 missing → genuinely Keepa-doesn't-carry-this case.
        df = pd.DataFrame([_row(
            amazon_bb_pct_90=0.05,
            # 3 still missing → flag
        )])
        out = flag_browser_scrape_needed(df)
        assert "BROWSER_SCRAPE_NEEDED" in out.iloc[0]["risk_flags"]

    def test_reject_rows_pass_through_untouched(self):
        df = pd.DataFrame([_row(decision="REJECT")])
        out = flag_browser_scrape_needed(df)
        assert "BROWSER_SCRAPE_NEEDED" not in (out.iloc[0]["risk_flags"] or [])

    def test_writes_manifest_when_rows_flagged(self, tmp_path: Path):
        df = pd.DataFrame([
            _row(asin="B0FLAG00001"),
            _row(asin="B0FLAG00002", buy_cost=10.0, buy_box_price=20.0),
        ])
        out = flag_browser_scrape_needed(df, run_dir=tmp_path)
        manifest_path = tmp_path / "keepa_browser_scrape_needed.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["scrape_required"] is True
        assert manifest["n_rows"] == 2
        asins = {a["asin"] for a in manifest["asins"]}
        assert asins == {"B0FLAG00001", "B0FLAG00002"}

    def test_no_manifest_when_no_rows_flagged(self, tmp_path: Path):
        # All rows have either cache or sufficient signals.
        df = pd.DataFrame([_row(browser_scrape_present=True)])
        flag_browser_scrape_needed(df, run_dir=tmp_path)
        manifest_path = tmp_path / "keepa_browser_scrape_needed.json"
        assert not manifest_path.exists()

    def test_appends_data_confidence_reason(self):
        df = pd.DataFrame([_row(
            data_confidence_reasons=["existing reason"],
        )])
        out = flag_browser_scrape_needed(df)
        reasons = out.iloc[0]["data_confidence_reasons"]
        assert "existing reason" in reasons
        assert any("BROWSER_SCRAPE_NEEDED" in r for r in reasons)

    def test_idempotent_on_re_run(self):
        # Re-running shouldn't double-add the flag or the reason.
        df = pd.DataFrame([_row()])
        out1 = flag_browser_scrape_needed(df)
        out2 = flag_browser_scrape_needed(out1)
        flags = out2.iloc[0]["risk_flags"]
        assert flags.count("BROWSER_SCRAPE_NEEDED") == 1

    def test_empty_df_passes_through(self):
        out = flag_browser_scrape_needed(pd.DataFrame())
        assert out.empty


class TestRunStep:
    def test_run_step_dispatches(self, tmp_path: Path):
        df = pd.DataFrame([_row()])
        out = run_step(df, {"run_dir": str(tmp_path)})
        assert "BROWSER_SCRAPE_NEEDED" in out.iloc[0]["risk_flags"]
        assert (tmp_path / "keepa_browser_scrape_needed.json").exists()

    def test_run_step_no_run_dir_skips_manifest(self, tmp_path: Path):
        df = pd.DataFrame([_row()])
        out = run_step(df, {})
        # Flag still added, just no manifest file.
        assert "BROWSER_SCRAPE_NEEDED" in out.iloc[0]["risk_flags"]


class TestBrowserRequiredSignals:
    def test_required_signals_pinned(self):
        # Pin the contract — adding a signal here should be a deliberate
        # decision because it changes the gate definition.
        assert BROWSER_REQUIRED_SIGNALS == (
            "amazon_bb_pct_90",
            "buy_box_drop_pct_90",
            "buy_box_min_365d",
            "buy_box_oos_pct_90",
        )
