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
                    "risk_flags", "decision_reason"]
    shortlisted = sdf[sdf["decision"] == "SHORTLIST"] if "decision" in sdf.columns else pd.DataFrame()
    review = sdf[sdf["decision"] == "REVIEW"] if "decision" in sdf.columns else pd.DataFrame()
    rejected = sdf[sdf["decision"] == "REJECT"] if "decision" in sdf.columns else pd.DataFrame()

    for pb in ["FBA", "FBM"]:
        for mt, label in [("UNIT", "Unit"), ("CASE", "Case/Multipack")]:
            subset = shortlisted
            if not subset.empty and "price_basis" in subset.columns:
                subset = subset[subset["price_basis"] == pb]
            if not subset.empty and "match_type" in subset.columns:
                subset = subset[subset["match_type"] == mt]
            lines.append(f"\n### Shortlist — {pb} {label} Matches\n")
            lines.append("_None_\n" if subset.empty else _make_table(subset, display_cols))

    lines.append("\n### Manual Review\n")
    lines.append("_None_\n" if review.empty else _make_table(review, display_cols))

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
        "restriction_reasons", "profit_conservative", "margin_conservative",
    ]
    lines = [
        "\n## \U0001F6AB Restriction notes\n",
        "These SHORTLIST items have a non-UNRESTRICTED status from SP-API. "
        "Decision logic is unchanged — these items are still profitable "
        "enough to shortlist; the engine flags them so you can decide "
        "whether to apply for ungating.\n",
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
