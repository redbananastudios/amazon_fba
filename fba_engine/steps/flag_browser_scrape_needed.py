"""Detect actionable rows that lack Browser-scraped data, write a
manifest file the operator can act on.

After the survivor refresh + Keepa Browser cache merge, some niche
listings still lack the historical signals only the Keepa Browser
chart carries — most notably the per-seller %BB-won data
(`buy_box_seller_stats_browser`) and the chart-derived 90d signals
when Keepa-API never tracked BB and Amazon-fallback is also empty.

For Peter's "we need solid data to make decisions" requirement:
this step refuses to ship a confident buyer card on rows missing
the Browser data. It instead:

  1. Adds a `BROWSER_SCRAPE_NEEDED` flag to the row's risk_flags.
  2. Lowers data_confidence to LOW (so the validator routes the
     row away from BUY).
  3. Writes a manifest file at
     ``<run_dir>/keepa_browser_scrape_needed.json`` listing the
     ASINs the operator needs to scrape via the Claude+MCP browser
     workflow documented in ``docs/KEEPA_BROWSER_SCRAPE.md``.
  4. Logs a prominent end-of-run summary so the operator can't miss it.

REJECT rows pass through untouched.

When all actionable rows already have Browser scrapes (cache hit
on every row), this step is a silent no-op.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from fba_engine.steps._helpers import is_missing

logger = logging.getLogger(__name__)


# Signals only the Keepa Browser scrape provides — used to decide
# whether a row genuinely needs the manual scrape workflow.
BROWSER_REQUIRED_SIGNALS: tuple[str, ...] = (
    # Per-seller BB share (the "Just This Retail 46%, MRPM 29%..."
    # data). Drives velocity prediction's share_source from
    # equal-split to median-of-N-sellers.
    "amazon_bb_pct_90",
    # 90d historical drop magnitude — the "buy the dip" tell.
    # When neither Keepa-API BB nor Amazon-fallback has data, this
    # is None and the buyer report's price arrow shows "?".
    "buy_box_drop_pct_90",
    # 12-month BB floor — peak-buying-risk anchor.
    "buy_box_min_365d",
    # 90d OOS pattern — does this listing go dark frequently?
    "buy_box_oos_pct_90",
)


def flag_browser_scrape_needed(
    df: pd.DataFrame,
    *,
    run_dir: str | Path | None = None,
    min_missing_to_flag: int = 3,
) -> pd.DataFrame:
    """Flag actionable rows lacking Browser-derived signals + write
    the operator's scrape manifest.

    Args:
        df: full DataFrame post-Keepa-Browser-enrich.
        run_dir: where to write `keepa_browser_scrape_needed.json`.
            None means skip manifest write (used by tests).
        min_missing_to_flag: minimum number of `BROWSER_REQUIRED_SIGNALS`
            that must be missing before we flag the row. Default 3
            of 4 — a row missing one signal might be a transient gap
            that re-running with a fresh Keepa cache resolves;
            missing 3 of 4 means Keepa fundamentally doesn't carry
            the data and only Browser scrape will help.
    """
    if df.empty:
        return df

    out = df.copy()
    if "risk_flags" in out.columns and out["risk_flags"].dtype != object:
        out["risk_flags"] = out["risk_flags"].astype(object)
    elif "risk_flags" not in out.columns:
        out["risk_flags"] = pd.Series([[] for _ in range(len(out))], dtype=object)

    needed_asins: list[dict[str, Any]] = []
    n_flagged = 0
    for idx, row in out.iterrows():
        decision = row.get("decision")
        if (
            not is_missing(decision)
            and isinstance(decision, str)
            and decision.strip().upper() == "REJECT"
        ):
            continue

        # Cache already populated? Browser data already merged in
        # by the upstream step. No action needed.
        if row.get("browser_scrape_present"):
            continue

        missing = [
            f for f in BROWSER_REQUIRED_SIGNALS
            if not _present(row.get(f))
        ]
        if len(missing) < min_missing_to_flag:
            continue

        # Append flag.
        existing = row.get("risk_flags") or []
        if not isinstance(existing, list):
            existing = []
        if "BROWSER_SCRAPE_NEEDED" not in existing:
            existing = list(existing) + ["BROWSER_SCRAPE_NEEDED"]
        out.at[idx, "risk_flags"] = existing

        # Lower data_confidence so the validator routes this row away
        # from BUY. The operator should not commit case money on a
        # listing whose history we can't read.
        out.at[idx, "data_confidence"] = "LOW"

        # Existing data_confidence_reasons + our addition.
        reasons = row.get("data_confidence_reasons") or []
        if not isinstance(reasons, list):
            reasons = []
        new_reason = f"BROWSER_SCRAPE_NEEDED: {len(missing)}/{len(BROWSER_REQUIRED_SIGNALS)} historical signals missing"
        if new_reason not in reasons:
            reasons = list(reasons) + [new_reason]
        out.at[idx, "data_confidence_reasons"] = reasons

        n_flagged += 1
        needed_asins.append({
            "asin": row.get("asin"),
            "title": (row.get("product_name") or row.get("title") or "")[:80],
            "buy_cost": _safe_float(row.get("buy_cost")),
            "buy_box_price": _safe_float(row.get("buy_box_price")),
            "missing_signals": missing,
            "current_decision": decision,
            "amazon_url": row.get("amazon_url"),
        })

    if n_flagged:
        logger.warning(
            "flag_browser_scrape_needed: %d actionable row(s) lack "
            "historical Browser data — Browser scrape recommended",
            n_flagged,
        )
        # Operator-facing summary: print at end of step so it shows
        # in the run log right above the pipeline summary.
        logger.warning(
            "  ASINs needing scrape: %s",
            ", ".join(a["asin"] for a in needed_asins[:10])
            + ("..." if len(needed_asins) > 10 else ""),
        )

        if run_dir is not None:
            manifest_path = Path(run_dir) / "keepa_browser_scrape_needed.json"
            try:
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(
                    json.dumps(
                        {
                            "scrape_required": True,
                            "n_rows": n_flagged,
                            "workflow": (
                                "Run the Claude+MCP Keepa Browser scrape "
                                "for each ASIN below — see "
                                "docs/KEEPA_BROWSER_SCRAPE.md. The cache "
                                "writes to .cache/keepa_browser/<asin>.json. "
                                "Re-run the engine afterwards."
                            ),
                            "asins": needed_asins,
                        },
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                logger.warning(
                    "  Manifest written: %s — operator should run scrape, then re-run engine",
                    manifest_path,
                )
            except Exception:
                logger.exception(
                    "flag_browser_scrape_needed: failed to write manifest",
                )

    return out


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper.

    Recognised config keys:
      - ``run_dir``: directory to write the scrape manifest into.
        When omitted, no manifest is written.
      - ``min_missing_to_flag``: see `flag_browser_scrape_needed`.
        Default 3.
    """
    run_dir = config.get("run_dir")
    raw_min = config.get("min_missing_to_flag", 3)
    try:
        min_missing = int(raw_min)
    except (TypeError, ValueError):
        min_missing = 3
    return flag_browser_scrape_needed(
        df, run_dir=run_dir, min_missing_to_flag=min_missing,
    )


def _present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    if isinstance(v, float):
        try:
            from math import isnan
            if isnan(v):
                return False
        except Exception:
            pass
    return True


def _safe_float(v: Any) -> float | None:
    try:
        f = float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
    if f is None:
        return None
    try:
        from math import isnan
        if isnan(f):
            return None
    except Exception:
        pass
    return f
