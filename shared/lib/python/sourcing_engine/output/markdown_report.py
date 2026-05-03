"""Markdown report — per-supplier grouped tables."""
import logging
import pandas as pd

logger = logging.getLogger(__name__)


def write_report(df: pd.DataFrame, path: str):
    try:
        lines = ["# Supplier Shortlist Report\n"]
        lines.append(_summary_section(df))
        for supplier in sorted(df["supplier"].dropna().unique()):
            sdf = df[df["supplier"] == supplier]
            lines.append(f"\n## Supplier: {supplier}\n")
            lines.extend(_supplier_sections(sdf))
        # Cross-supplier restriction notes section (only when SP-API
        # preflight ran and surfaced restricted shortlist items).
        notes = _restriction_notes_section(df)
        if notes:
            lines.append(notes)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        logger.info("Report written: %s", path)
    except Exception:
        logger.exception("Failed to write report: %s", path)


def _summary_section(df):
    lines = ["\n## Summary\n"]
    lines.append(f"- Suppliers processed: {df['supplier'].nunique()}")
    lines.append(f"- Source rows processed: {len(df)}")
    shortlisted = df[df["decision"] == "SHORTLIST"] if "decision" in df.columns else pd.DataFrame()
    for pb in ["FBA", "FBM"]:
        for mt in ["UNIT", "CASE"]:
            subset = shortlisted
            if "price_basis" in subset.columns:
                subset = subset[subset["price_basis"] == pb]
            if "match_type" in subset.columns:
                subset = subset[subset["match_type"] == mt]
            lines.append(f"- Shortlisted {pb} ({mt.lower()}): {len(subset)}")
    lines.append(f"- Sent to review: {(df['decision'] == 'REVIEW').sum() if 'decision' in df.columns else 0}")
    lines.append(f"- Rejected: {(df['decision'] == 'REJECT').sum() if 'decision' in df.columns else 0}")
    return "\n".join(lines)


def _supplier_sections(sdf):
    lines = []
    display_cols = ["ean", "asin", "product_name", "buy_cost", "market_price",
                    "profit_conservative", "margin_conservative", "sales_estimate",
                    "opportunity_verdict", "opportunity_score", "next_action",
                    "candidate_band", "candidate_score", "data_confidence",
                    # 08_buy_plan — high-level columns; the per-row line
                    # below the table carries the full breakdown.
                    "order_qty_recommended", "capital_required",
                    "payback_days", "target_buy_cost_buy",
                    "buy_plan_status",
                    "risk_flags", "decision_reason"]
    shortlisted = sdf[sdf["decision"] == "SHORTLIST"] if "decision" in sdf.columns else pd.DataFrame()
    review = sdf[sdf["decision"] == "REVIEW"] if "decision" in sdf.columns else pd.DataFrame()
    rejected = sdf[sdf["decision"] == "REJECT"] if "decision" in sdf.columns else pd.DataFrame()

    # Sort within each band: candidate_score descending so the
    # strongest opportunities lead each section. Falls through
    # gracefully when candidate_score is absent (older runs).
    # `kind="stable"` so equal-score rows preserve insertion order
    # (default `quicksort` is not stable; documented contract from
    # the WS3.6 acceptance is that ties keep their original order).
    if not shortlisted.empty and "candidate_score" in shortlisted.columns:
        shortlisted = shortlisted.assign(
            __score=pd.to_numeric(
                shortlisted["candidate_score"], errors="coerce",
            ).fillna(0),
        ).sort_values(
            "__score", ascending=False, kind="stable",
        ).drop(columns="__score")
    if not review.empty and "candidate_score" in review.columns:
        review = review.assign(
            __score=pd.to_numeric(
                review["candidate_score"], errors="coerce",
            ).fillna(0),
        ).sort_values(
            "__score", ascending=False, kind="stable",
        ).drop(columns="__score")

    for pb in ["FBA", "FBM"]:
        for mt, label in [("UNIT", "Unit"), ("CASE", "Case/Multipack")]:
            subset = shortlisted
            if not subset.empty and "price_basis" in subset.columns:
                subset = subset[subset["price_basis"] == pb]
            if not subset.empty and "match_type" in subset.columns:
                subset = subset[subset["match_type"] == mt]
            lines.append(f"\n### Shortlist — {pb} {label} Matches\n")
            if subset.empty:
                lines.append("_None_\n")
            else:
                summary = _candidate_score_summary(subset)
                if summary:
                    lines.append(summary)
                buy_plan = _buy_plan_summary(subset)
                if buy_plan:
                    lines.append(buy_plan)
                lines.append(_make_table(subset, display_cols))

    lines.append("\n### Manual Review\n")
    if review.empty:
        lines.append("_None_\n")
    else:
        summary = _candidate_score_summary(review)
        if summary:
            lines.append(summary)
        buy_plan = _buy_plan_summary(review)
        if buy_plan:
            lines.append(buy_plan)
        lines.append(_make_table(review, display_cols))

    reject_cols = ["ean", "product_name", "match_type", "decision_reason"]
    lines.append("\n### Rejected\n")
    lines.append("_None_\n" if rejected.empty else _make_table(rejected, reject_cols))
    return lines


def _restriction_notes_section(df: pd.DataFrame) -> str:
    """Build the cross-supplier "Restriction notes" section listing
    SHORTLIST rows that the SP-API preflight flagged as gated/restricted.
    Returns "" when the preflight didn't run or no shortlisted items are
    restricted, so we don't add an empty section.
    """
    if "restriction_status" not in df.columns:
        return ""
    if "decision" not in df.columns:
        return ""
    gated = df[
        (df["decision"] == "SHORTLIST")
        & df["restriction_status"].notna()
        & (df["restriction_status"] != "UNRESTRICTED")
    ]
    if gated.empty:
        return ""
    cols = [
        "supplier", "ean", "asin", "product_name", "restriction_status",
        "restriction_reasons", "restriction_links",
        "profit_conservative", "margin_conservative",
    ]
    lines = [
        "\n## \U0001F6AB Restriction notes\n",
        "These SHORTLIST items have a non-UNRESTRICTED status from SP-API. "
        "Decision logic is unchanged — these items are still profitable "
        "enough to shortlist; the engine flags them so you can decide "
        "whether to apply for ungating.\n",
        "The **`restriction_links`** column carries the SP-API-provided "
        "Seller Central application URL per ASIN — click straight from "
        "here instead of looking up each restriction by hand.\n",
    ]
    lines.append(_make_table(gated, cols))
    return "\n".join(lines)


def _make_table(df, cols):
    available = [c for c in cols if c in df.columns]
    if not available:
        return "_No data_\n"
    subset = df[available].copy()
    if "risk_flags" in subset.columns:
        subset["risk_flags"] = subset["risk_flags"].apply(
            lambda x: ", ".join(x) if isinstance(x, list) else str(x))
    for col in ["buy_cost", "market_price", "profit_conservative"]:
        if col in subset.columns:
            subset[col] = subset[col].apply(lambda x: f"\u00a3{x:.2f}" if pd.notna(x) and isinstance(x, (int, float)) else "")
    if "margin_conservative" in subset.columns:
        subset["margin_conservative"] = subset["margin_conservative"].apply(
            lambda x: f"{x:.1%}" if pd.notna(x) and isinstance(x, (int, float)) else "")
    lines = []
    lines.append("| " + " | ".join(available) + " |")
    lines.append("| " + " | ".join(["---"] * len(available)) + " |")
    for _, row in subset.iterrows():
        vals = [str(row.get(c, "")) for c in available]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def _candidate_score_summary(df: pd.DataFrame) -> str:
    """Per-row leading lines summarising candidate score + confidence.

    HANDOFF WS3.6: "In the markdown report, add a leading line per
    row: **STRONG** (HIGH confidence) \u2014 score 82/100".

    Emitted as a fenced bullet list above each band's table for the
    SHORTLIST and REVIEW sections. Falls through silently when
    candidate_score isn't present (older runs).

    NaN handling: `pd.DataFrame.from_records` fills missing dict keys
    with NaN, which is truthy for floats \u2014 `value or ""` does NOT
    coerce a NaN cell to "". Every cell read here therefore goes
    through `_clean_str` to keep NaN out of the output.
    """
    if "candidate_band" not in df.columns or df.empty:
        return ""
    lines = []
    for _, row in df.iterrows():
        band = _clean_str(row.get("candidate_band"))
        score = row.get("candidate_score")
        conf = _clean_str(row.get("data_confidence"))
        asin = _clean_str(row.get("asin"))
        title = _clean_str(row.get("product_name"))
        # Truncate the title so the bullet stays scannable.
        title_short = (title[:60] + "\u2026") if len(title) > 60 else title
        try:
            score_int = (
                int(score)
                if score is not None and not pd.isna(score)
                else None
            )
        except (ValueError, TypeError):
            score_int = None
        if not band:
            # Row has the column but no usable band value \u2014 skip.
            continue
        if score_int is not None:
            lead = (
                f"- **{band}** ({conf} confidence) \u2014 score {score_int}/100  "
                f"`{asin}` {title_short}"
            )
        else:
            lead = f"- **{band}** ({conf} confidence)  `{asin}` {title_short}"
        lines.append(lead)
    return "\n".join(lines) + "\n" if lines else ""


def _clean_str(value) -> str:
    """Coerce a cell to a clean string. NaN / None \u2192 \"\"."""
    if value is None or pd.isna(value):
        return ""
    return str(value)


def _buy_plan_summary(df: pd.DataFrame) -> str:
    """Per-verdict buy-plan one-liners. Per PRD \u00a78.3.

    BUY:        order qty + capital + projected revenue/profit + payback
    SOURCE_ONLY: target buy cost + stretch + projected at-target revenue
    NEGOTIATE:  \u00a3/% supplier needs to come down
    WATCH/KILL: no line (verdict + blockers cover it)

    Falls through silently when buy_plan_status isn't present (older
    runs).
    """
    if "buy_plan_status" not in df.columns or df.empty:
        return ""
    lines: list[str] = []
    for _, row in df.iterrows():
        verdict = _clean_str(row.get("opportunity_verdict")).upper()
        asin = _clean_str(row.get("asin"))
        if not asin:
            continue
        if verdict == "BUY":
            qty = _num(row.get("order_qty_recommended"))
            cap = _num(row.get("capital_required"))
            rev = _num(row.get("projected_30d_revenue"))
            prof = _num(row.get("projected_30d_profit"))
            pb = _num(row.get("payback_days"))
            if qty is None or cap is None:
                continue
            parts = [
                f"**Order plan ({asin}):** {int(qty)} units \u00b7 "
                f"\u00a3{cap:.2f} capital",
            ]
            if rev is not None and prof is not None:
                parts.append(
                    f"projected 30d: \u00a3{rev:.2f} revenue, "
                    f"\u00a3{prof:.2f} profit"
                )
            if pb is not None:
                parts.append(f"payback {pb:.0f} days")
            lines.append("- " + " \u00b7 ".join(parts))
        elif verdict == "SOURCE_ONLY":
            target = _num(row.get("target_buy_cost_buy"))
            stretch = _num(row.get("target_buy_cost_stretch"))
            rev = _num(row.get("projected_30d_revenue"))
            if target is None:
                continue
            parts = [
                f"**Source target ({asin}):** "
                f"\u2264 \u00a3{target:.2f}/unit",
            ]
            if stretch is not None:
                parts.append(f"stretch \u00a3{stretch:.2f}")
            if rev is not None:
                parts.append(
                    f"projected at target: \u00a3{rev:.2f} 30d revenue"
                )
            lines.append("- " + " \u00b7 ".join(parts))
        elif verdict == "NEGOTIATE":
            gap = _num(row.get("gap_to_buy_gbp"))
            gap_pct = _num(row.get("gap_to_buy_pct"))
            buy_cost = _num(row.get("buy_cost"))
            target = _num(row.get("target_buy_cost_buy"))
            if gap is None or gap_pct is None or buy_cost is None or target is None:
                continue
            lines.append(
                f"- **Negotiation ask ({asin}):** down "
                f"\u00a3{gap:.2f}/unit ({gap_pct:.1%}) \u2014 currently "
                f"\u00a3{buy_cost:.2f}, needs \u2264 \u00a3{target:.2f}"
            )
    return "\n".join(lines) + "\n" if lines else ""


def _num(value):
    """Coerce a cell to float. None / NaN / unparseable \u2192 None."""
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:   # NaN
        return None
    return n
