"""Decision-data audit — verify every actionable row has the data
required for each decision stage.

Reads a buyer_report.json + the corresponding shortlist.csv from a
run output directory. For each row in the buyer report (i.e. every
non-KILL survivor), checks coverage of the data each decision stage
requires and reports who can / can't make a confident decision.

Usage:
    python scripts/validate_decision_data.py <run_dir>

Example:
    python scripts/validate_decision_data.py out/abgee_full_data/20260503_231605/

Outputs a markdown audit report to stdout + writes
`<run_dir>/data_audit.md` for the operator to review.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any


# Required-data contract per decision stage. If a stage's required
# fields aren't all present, that stage's verdict is data-light and
# the operator shouldn't trust it. OPTIONAL fields refine the
# decision but don't gate it.
STAGE_REQUIREMENTS: list[dict[str, Any]] = [
    {
        "stage": "calculate (economics)",
        "purpose": "Computing profit / ROI / target buy cost",
        "required": ["buy_cost", "buy_box_price"],
        "optional": ["new_fba_price", "amazon_price", "size_tier",
                     "fba_pick_pack_fee", "referral_fee_pct"],
    },
    {
        "stage": "decide (SHORTLIST/REVIEW/REJECT)",
        "purpose": "Gating against profit + ROI + sales thresholds",
        "required": ["profit_conservative", "roi_conservative", "buy_cost"],
        "optional": ["sales_estimate", "gated", "price_basis"],
    },
    {
        "stage": "candidate_score (Demand)",
        "purpose": "0-25 demand strength sub-score",
        "required": ["sales_estimate"],
        "optional": ["bsr_slope_90d", "review_velocity_90d", "sales_rank_cv_90d"],
    },
    {
        "stage": "candidate_score (Stability)",
        "purpose": "0-25 stability sub-score",
        "required": [],
        "optional": ["buy_box_oos_pct_90", "price_volatility_90d", "listing_age_days"],
    },
    {
        "stage": "candidate_score (Competition)",
        "purpose": "0-25 competition sub-score",
        "required": ["fba_seller_count"],
        "optional": ["amazon_bb_pct_90", "fba_offer_count_90d_joiners", "amazon_status"],
    },
    {
        "stage": "validate_opportunity (BUY/SOURCE/NEGOTIATE/WATCH/KILL)",
        "purpose": "Final operator verdict",
        "required": [
            "decision", "profit_conservative", "roi_conservative",
            "candidate_score", "sales_estimate",
        ],
        "optional": [
            "amazon_bb_pct_90", "buy_box_oos_pct_90", "price_volatility_90d",
            "fba_seller_count", "fba_offer_count_90d_joiners",
            "restriction_status", "fba_eligible",
        ],
    },
    {
        "stage": "buy_plan (order qty + capital)",
        "purpose": "Recommended order quantity",
        "required": ["opportunity_verdict", "buy_cost"],
        "optional": ["sales_estimate", "predicted_velocity_mid",
                     "buy_box_seller_stats", "bsr_drops_30d"],
    },
    {
        "stage": "buyer report (analyst dimensions)",
        "purpose": "Profit / Competition / Stability / Operational scoring",
        "required": ["profit_per_unit_gbp", "fba_seller_count"],
        "optional": [
            "buy_box_oos_pct_90", "price_volatility_90d", "buy_box_min_365d",
            "listing_age_days", "amazon_bb_pct_90", "bb_drop_pct_90",
            "buy_box_seller_stats", "bsr_slope_90d", "fba_offer_count_90d_joiners",
        ],
    },
]


def _is_present(v: Any) -> bool:
    """A field 'has data' when it's not None / NaN / empty string."""
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
    if isinstance(v, list) and not v:
        return False
    return True


def _flatten_row(row_payload: dict, csv_row: dict) -> dict:
    """Merge buyer report row + matching CSV row into one flat dict
    for field-presence checks.

    Resolution order (first non-None wins):
      1. CSV row (raw engine columns the operator sees in the export)
      2. Buyer-report payload nested blocks (economics / trends /
         buy_plan / metrics — what the analyst layer reads)

    Many canonical fields are stripped from the CSV by the output
    writer's fixed-schema filter but ARE present in the JSON payload
    (under different names). Read both so the audit doesn't false-
    flag fields that are present in the engine just hidden from the
    CSV.
    """
    out = dict(csv_row)
    out["asin"] = row_payload["asin"]
    out["title"] = row_payload.get("title")

    eco = row_payload.get("economics") or {}
    bp = row_payload.get("buy_plan") or {}
    tr = row_payload.get("trends") or {}

    # Mirror trends fields under the engine column names the audit checks.
    name_map = {
        "buy_box_oos_pct_90": tr.get("buy_box_oos_pct_90"),
        "price_volatility_90d": tr.get("price_volatility_90d"),
        "amazon_bb_pct_90": None,  # filled below if metric verdict != grey
        "fba_offer_count_90d_joiners": tr.get("joiners_90d"),
        "bsr_slope_90d": tr.get("bsr_slope_90d"),
        "listing_age_days": tr.get("listing_age_days"),
        "buy_box_min_365d": tr.get("buy_box_min_365d"),
        "bb_drop_pct_90": tr.get("bb_drop_pct_90"),
        # economics
        "buy_cost": eco.get("buy_cost_gbp"),
        "buy_box_price": eco.get("market_price_gbp"),
        "profit_per_unit_gbp": eco.get("profit_per_unit_gbp"),
        "profit_conservative": eco.get("profit_per_unit_gbp"),
        "roi_conservative": eco.get("roi_conservative_pct"),
        "target_buy_cost_gbp": eco.get("target_buy_cost_gbp"),
        # buy_plan
        "predicted_velocity_mid": bp.get("predicted_velocity_mid"),
        "opportunity_verdict": row_payload.get("engine_verdict"),
    }
    # Pull selected fields from the metrics block (the analyst-facing
    # rendered metric). If grey, signal genuinely missing.
    for m in row_payload.get("metrics", []) or []:
        verdict = m.get("verdict")
        key = m.get("key")
        display = m.get("value_display") or ""
        if verdict == "grey":
            continue
        if key == "fba_seller_count":
            try:
                name_map["fba_seller_count"] = int(display)
            except (TypeError, ValueError):
                pass
        elif key == "amazon_bb_pct_90":
            name_map["amazon_bb_pct_90"] = display
        elif key == "amazon_on_listing":
            name_map["amazon_status"] = (
                "ON_LISTING" if "yes" in display.lower() else "OFF_LISTING"
            )
        elif key == "sales_estimate":
            try:
                name_map["sales_estimate"] = int(display)
            except (TypeError, ValueError):
                pass
        elif key == "price_volatility":
            try:
                name_map["price_volatility_90d"] = float(display)
            except (TypeError, ValueError):
                pass
        elif key == "bsr_drops_30d":
            try:
                # display like "51 sales"
                name_map["bsr_drops_30d"] = int(display.split()[0])
            except (TypeError, ValueError, IndexError):
                pass
        elif key == "predicted_velocity":
            try:
                # display like "5/mo" — pull the integer
                name_map["predicted_velocity_mid"] = int(display.split("/")[0])
            except (TypeError, ValueError):
                pass
    # candidate_score / data_confidence / decision come from buyer
    # payload's metadata (not always in the analyst block). Fall back
    # to engine_opportunity_score as a proxy when score column missing.
    if "candidate_score" not in out or not _is_present(out.get("candidate_score")):
        analyst_score = (row_payload.get("analyst") or {}).get("score")
        if analyst_score is not None:
            name_map["candidate_score"] = analyst_score
    # decision comes from engine_verdict in the payload
    if "decision" not in out or not _is_present(out.get("decision")):
        # The buyer report's engine_verdict reflects the post-recompute
        # decision (SHORTLIST / REVIEW / REJECT)
        name_map["decision"] = row_payload.get("engine_verdict")

    # Apply mappings only when CSV doesn't already have a real value.
    for k, v in name_map.items():
        if not _is_present(out.get(k)) and v is not None:
            out[k] = v

    return out


def audit_row(row_payload: dict, csv_row: dict) -> dict:
    """Return a per-stage coverage summary for one row."""
    flat = _flatten_row(row_payload, csv_row)
    stages = []
    for spec in STAGE_REQUIREMENTS:
        missing_required = [
            f for f in spec["required"] if not _is_present(flat.get(f))
        ]
        missing_optional = [
            f for f in spec["optional"] if not _is_present(flat.get(f))
        ]
        status = "PASS" if not missing_required else "BLOCK"
        stages.append({
            "stage": spec["stage"],
            "purpose": spec["purpose"],
            "status": status,
            "missing_required": missing_required,
            "missing_optional": missing_optional,
        })

    # Aggregate verdict for the row.
    blocks = [s for s in stages if s["status"] == "BLOCK"]
    n_optional_missing = sum(len(s["missing_optional"]) for s in stages)

    if blocks:
        overall = "INSUFFICIENT_DATA"
    elif n_optional_missing >= 6:
        overall = "PROBE_ONLY"
    elif n_optional_missing >= 3:
        overall = "LOW_CONFIDENCE"
    else:
        overall = "FULL_DATA"

    return {
        "asin": row_payload["asin"],
        "title": (row_payload.get("title") or "")[:60],
        "verdict": (row_payload.get("analyst") or {}).get("verdict"),
        "score": (row_payload.get("analyst") or {}).get("score"),
        "overall": overall,
        "stages": stages,
        "n_optional_missing": n_optional_missing,
    }


def render_markdown(run_dir: Path, audits: list[dict]) -> str:
    n_total = len(audits)
    if n_total == 0:
        return f"# Decision-Data Audit — {run_dir}\n\nNo actionable rows.\n"

    bands = OrderedDict([
        ("FULL_DATA", "Every required + most optional signals present"),
        ("LOW_CONFIDENCE", "All required present; some optional gaps"),
        ("PROBE_ONLY", "All required present; many optional gaps — probe only"),
        ("INSUFFICIENT_DATA", "MISSING REQUIRED — verdict not trustworthy"),
    ])
    band_counts = {b: 0 for b in bands}
    for a in audits:
        band_counts[a["overall"]] = band_counts.get(a["overall"], 0) + 1

    lines = [
        f"# Decision-Data Audit — {run_dir.name}",
        "",
        f"**{n_total}** actionable row(s) audited against decision-stage data requirements.",
        "",
        "## Coverage summary",
        "",
        "| Band | Count | % | Meaning |",
        "|---|---|---|---|",
    ]
    for band, desc in bands.items():
        n = band_counts.get(band, 0)
        pct = (n / n_total * 100) if n_total else 0
        lines.append(f"| **{band}** | {n} | {pct:.0f}% | {desc} |")

    lines += [
        "",
        "## Per-row audit",
        "",
        "| ASIN | verdict | score | overall | stages BLOCK | optional gaps | title |",
        "|---|---|---|---|---|---|---|",
    ]
    for a in sorted(audits, key=lambda x: (x["overall"] != "FULL_DATA", -(x["score"] or 0))):
        n_block = sum(1 for s in a["stages"] if s["status"] == "BLOCK")
        block_summary = ", ".join(
            s["stage"].split(" ")[0] for s in a["stages"] if s["status"] == "BLOCK"
        ) or "-"
        lines.append(
            f"| {a['asin']} | {a['verdict']} | {a['score']} | "
            f"**{a['overall']}** | {block_summary} | "
            f"{a['n_optional_missing']} | {a['title']} |"
        )

    # Stage-level rollup: which fields are most commonly missing?
    # Dedupe per row — a field listed in multiple stages should only
    # count once per row, otherwise the percentages exceed 100%.
    field_misses_required: dict[str, int] = {}
    field_misses_optional: dict[str, int] = {}
    for a in audits:
        per_row_required: set[str] = set()
        per_row_optional: set[str] = set()
        for s in a["stages"]:
            per_row_required.update(s["missing_required"])
            per_row_optional.update(s["missing_optional"])
        # Don't double-count fields that are missing in BOTH required
        # and optional lists across stages — required wins.
        per_row_optional -= per_row_required
        for f in per_row_required:
            field_misses_required[f] = field_misses_required.get(f, 0) + 1
        for f in per_row_optional:
            field_misses_optional[f] = field_misses_optional.get(f, 0) + 1

    lines += [
        "",
        "## Field-coverage rollup",
        "",
        "Most-missing **required** fields (gates the verdict):",
        "",
    ]
    if field_misses_required:
        lines.append("| field | missing on N rows | % of total |")
        lines.append("|---|---|---|")
        for f, n in sorted(field_misses_required.items(), key=lambda x: -x[1]):
            lines.append(f"| `{f}` | {n} | {n / n_total * 100:.0f}% |")
    else:
        lines.append("_All required fields populated on every row._")

    lines += [
        "",
        "Most-missing **optional** fields (refines confidence):",
        "",
        "| field | missing on N rows | % of total |",
        "|---|---|---|",
    ]
    for f, n in sorted(field_misses_optional.items(), key=lambda x: -x[1])[:15]:
        lines.append(f"| `{f}` | {n} | {n / n_total * 100:.0f}% |")

    # Per-row detail for INSUFFICIENT or PROBE_ONLY rows.
    flagged = [
        a for a in audits if a["overall"] in ("INSUFFICIENT_DATA", "PROBE_ONLY")
    ]
    if flagged:
        lines += [
            "",
            "## Flagged rows — detail",
            "",
        ]
        for a in flagged:
            lines.append(f"### {a['asin']} — {a['title']}")
            lines.append(f"  - **{a['overall']}** | analyst verdict: {a['verdict']} | score: {a['score']}")
            for s in a["stages"]:
                if s["missing_required"] or len(s["missing_optional"]) >= 3:
                    pieces = []
                    if s["missing_required"]:
                        pieces.append(
                            f"missing required: `{', '.join(s['missing_required'])}`"
                        )
                    if s["missing_optional"]:
                        pieces.append(
                            f"missing optional: `{', '.join(s['missing_optional'])}`"
                        )
                    lines.append(f"  - **{s['stage']}** — " + "; ".join(pieces))
            lines.append("")

    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    run_dir = Path(argv[0])
    if not run_dir.is_dir():
        print(f"not a directory: {run_dir}", file=sys.stderr)
        return 2

    json_files = sorted(run_dir.glob("buyer_report_*.json"))
    if not json_files:
        print(f"no buyer_report_*.json found in {run_dir}", file=sys.stderr)
        return 2
    csv_files = sorted(run_dir.glob("shortlist_*.csv"))
    if not csv_files:
        print(f"no shortlist_*.csv found in {run_dir}", file=sys.stderr)
        return 2

    payload = json.loads(json_files[-1].read_text(encoding="utf-8"))
    csv_rows: dict[str, dict] = {}
    with csv_files[-1].open(encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            asin = r.get("asin")
            if asin:
                csv_rows[asin] = r

    audits = []
    for row in payload.get("rows", []):
        csv_row = csv_rows.get(row["asin"], {})
        audits.append(audit_row(row, csv_row))

    md = render_markdown(run_dir, audits)
    out_path = run_dir / "data_audit.md"
    out_path.write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    sys.stdout.write(f"\n\nWritten: {out_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
