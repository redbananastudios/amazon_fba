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
                lines.append(_make_table(subset, display_cols))

    lines.append("\n### Manual Review\n")
    if review.empty:
        lines.append("_None_\n")
    else:
        summary = _candidate_score_summary(review)
        if summary:
            lines.append(summary)
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
