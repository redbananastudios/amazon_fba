"""Build Output step (Phase 5 — formerly Skill 5 in the legacy Keepa pipeline).

Merges the Phase-3 shortlist + Phase-4 IP-risk output into the canonical
67-column `final_results.csv`, splits confirmed private-label rows out to a
reject CSV, and emits a placeholder supplier skeleton CSV plus stats and
handoff text.

Logic ported 1:1 from the per-niche `phase5_build.js` scripts (see
`fba_engine/data/niches/{niche}/working/phase5_build.js` — the four niche
copies are 99% identical, parameterised only by the niche slug). This port
is generic over the niche.

Decision contract (informational only):
- Confirmed private-label rows are removed from the main final CSV and sent
  to a reject CSV. Other PL-suspect rows stay (they're flagged in
  `Private Label Risk`, never auto-killed here).
- Supplier columns are placeholders. Skill 99 (parked) is meant to populate
  them later via outreach research.

Standalone CLI invocation:

    python -m fba_engine.steps.build_output \\
        --niche kids-toys \\
        --base fba_engine/data/niches/kids-toys
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import warnings
from datetime import date
from pathlib import Path

import pandas as pd

# ────────────────────────────────────────────────────────────────────────
# Constants — kept identical to the legacy JS so downstream column lookups
# (Phase 6 decision engine, Phase 5 XLSX builder) keep matching by name.
# ────────────────────────────────────────────────────────────────────────

FINAL_HEADERS: list[str] = [
    # 1-6: product
    "ASIN", "Product Name", "Brand", "Amazon URL", "Category", "Weight Flag",
    # 7-11: verdict
    "Verdict", "Verdict Reason", "Opportunity Lane", "Commercial Priority", "Lane Reason",
    # 12-19: scores
    "Composite Score", "Demand Score", "Stability Score", "Competition Score",
    "Margin Score", "Cash Flow Score", "Profit Score", "Balanced Score",
    # 20-22: monthly + quality
    "Monthly Gross Profit", "Price Compression", "Listing Quality",
    # 23-25: pricing
    "Current Price", "Buy Box 90d Avg", "Price Stability",
    # 26-30: fees + cost
    "Fulfilment Fee", "Amazon Fees", "Total Amazon Fees", "Est Cost 65%", "Est Profit",
    # 31-33: ROI + cost limits
    "Est ROI %", "Max Cost 20% ROI", "Breakeven Price",
    # 34-36: BSR + bought
    "BSR Current", "BSR Drops 90d", "Bought per Month",
    # 37-39: ratings + brand 1P
    "Star Rating", "Review Count", "Brand 1P",
    # 40-43: competition signals
    "FBA Seller Count", "Amazon on Listing", "Amazon Buy Box Share", "Private Label Risk",
    # 44-48: brand control signals (from Phase 4)
    "Brand Seller Match", "Fortress Listing", "Brand Type", "A+ Content Present",
    "Brand Store Present",
    # 49-52: IP risk (from Phase 4)
    "Category Risk Level", "IP Risk Score", "IP Risk Band", "IP Reason",
    # 53-54: misc
    "Gated", "SAS Flags",
    # 55-64: supplier placeholders
    "Route Code", "Supplier Name", "Supplier Website", "Supplier Contact",
    "MOQ", "Trade Price Found", "Trade Price", "Real ROI %",
    "Supplier Notes", "Outreach Email File",
    # 65-67: product codes (for supplier price matching)
    "EAN", "UPC", "GTIN",
]
assert len(FINAL_HEADERS) == 67, f"FINAL_HEADERS must be 67, got {len(FINAL_HEADERS)}"

SUPPLIER_HEADERS: list[str] = [
    "ASIN", "Product Name", "Brand", "Category", "Verdict", "Composite Score",
    "Current Price", "FBA Fee", "Est ROI %",
    "Existing Account Found", "Existing Account Name",
    "Trade Price Found", "Trade Price", "Real ROI %", "ROI Change",
    "Route Code", "Supplier Name", "Supplier Website", "Supplier Contact",
    "MOQ", "Notes", "Outreach Email File",
]
assert len(SUPPLIER_HEADERS) == 22

REJECT_HEADER_EXTRA: str = "Private Label Exclusion Reason"

# Verdict sort priority — lower number = sorted earlier.
VERDICT_ORDER: dict[str, int] = {
    "YES": 1,
    "MAYBE": 2,
    "BRAND APPROACH": 3,
    "BUY THE DIP": 4,
    "MAYBE-ROI": 5,
    "GATED": 6,
}

# Supplier-skeleton placeholder values, ordered to match SUPPLIER_HEADERS.
_SUPPLIER_PLACEHOLDER_TAIL = [
    "N", "", "N", "", "", "UNKNOWN",
    "UNCLEAR", "", "", "", "",
    "No supplier accounts configured", "",
]

# Final-row supplier placeholder block (cols 55-64).
_FINAL_SUPPLIER_PLACEHOLDERS = [
    "UNCLEAR", "", "", "", "", "N", "", "",
    "No supplier accounts configured", "",
]

# Required input columns. Missing any of these means the row scoring is
# built from defaults (0/empty) — warn so a misshapen upstream isn't silent.
_REQUIRED_INPUT_COLUMNS = (
    "ASIN",
    "Verdict",
    "Opportunity Lane",
    "Commercial Priority",
    "Current Price",
    "FBA Seller Count",
    "Brand 1P",
)

# ────────────────────────────────────────────────────────────────────────
# Coercion helpers — same shape as ip_risk.py / decision_engine.py.
# Duplicated for now; reviewer flagged in step 4b that a shared
# `fba_engine/steps/_helpers.py` should land once both ports are merged.
# ────────────────────────────────────────────────────────────────────────


def _is_missing(raw: object) -> bool:
    """True for any nullable sentinel: None, float NaN, pandas NA/NaT, np.nan."""
    if raw is None:
        return True
    try:
        # pd.isna handles pd.NA, pd.NaT, np.nan, float('nan'). It raises on
        # pd.NA only inside boolean context (`bool(pd.NA)`); the call itself
        # returns the array-aware result and is safe.
        if pd.isna(raw):
            return True
    except (TypeError, ValueError):
        # Some custom objects can fail pd.isna check; fall through.
        pass
    if isinstance(raw, float) and math.isnan(raw):
        return True
    return False


def _coerce_str(raw: object) -> str:
    if _is_missing(raw):
        return ""
    return str(raw).strip()


def _parse_money(raw: object) -> float:
    """Strip GBP and any non-[0-9.-] chars; parse float; NaN/garbage -> 0."""
    if _is_missing(raw):
        return 0.0
    s = str(raw)
    s = re.sub(r"GBP", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^0-9.\-]", "", s).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _round_half_up(value: float) -> int:
    """JS Math.round equivalent (half-toward-+infinity).

    Python's built-in round() is banker's (half-to-even), so 200.5 -> 200
    instead of 201. Score values are non-negative, so floor(x + 0.5) is
    sufficient.
    """
    return int(math.floor(value + 0.5))


# ────────────────────────────────────────────────────────────────────────
# Pure scoring helpers.
# ────────────────────────────────────────────────────────────────────────


def price_stability(drop_pct: float) -> str:
    """Bucket a 90-day price-drop percentage into a stability band.

    Bands match the legacy JS 1:1:
      [-2, 2]   -> STABLE
      (2, 10]   -> SLIGHT DIP (N%)
      (10, +∞)  -> DROPPING (N%)
      [-10, -2) -> RISING (N%)
      (-∞, -10) -> SURGING (N%)

    Note the JS uses `Math.abs(...).toFixed(0)` for RISING/SURGING and just
    `.toFixed(0)` for SLIGHT DIP/DROPPING. We replicate that.
    """
    n = float(drop_pct)
    if -2 <= n <= 2:
        return "STABLE"
    if 2 < n <= 10:
        return f"SLIGHT DIP ({_round_half_up(n)}%)"
    if n > 10:
        return f"DROPPING ({_round_half_up(n)}%)"
    if -10 <= n < -2:
        return f"RISING ({_round_half_up(abs(n))}%)"
    return f"SURGING ({_round_half_up(abs(n))}%)"


def pl_risk(fba_count: float, brand_1p: object) -> str:
    """Return Likely / Unlikely / "-" private-label risk classification."""
    if str(brand_1p or "").strip().upper() == "Y":
        return "Likely"
    if fba_count <= 2:
        return "Unlikely"
    return "-"


def confirmed_private_label_status(
    brand_1p: object,
    brand_seller_match: object,
    fortress_listing: object,
    brand_store_present: object,
    brand_type: object,
) -> tuple[bool, str]:
    """Multi-signal check for confirmed private-label exclusion.

    Returns (confirmed, reason) where reason is a pipe-joined list of the
    signals that fired (used as audit trail in the reject CSV).

    Confirmed if any of:
      - Brand 1P=Y (Amazon brand);
      - Fortress listing AND seller match in {YES, PARTIAL};
      - Seller match=YES AND Brand store=LIKELY;
      - 3+ signals AND any of (seller match=YES OR fortress OR store likely).
    """
    seller_match = str(brand_seller_match or "").upper()
    fortress = str(fortress_listing or "").upper() == "YES"
    store_likely = str(brand_store_present or "").upper() == "LIKELY"
    established = str(brand_type or "").upper() == "ESTABLISHED"
    amazon_brand = str(brand_1p or "").upper() == "Y"

    reasons: list[str] = []
    if amazon_brand:
        reasons.append("Brand 1P")
    if fortress:
        reasons.append("Fortress listing")
    if seller_match == "YES":
        reasons.append("Brand seller match")
    if store_likely:
        reasons.append("Brand store likely")
    if established:
        reasons.append("Established brand")

    strong_control = fortress and seller_match in {"YES", "PARTIAL"}
    brand_owned = seller_match == "YES" and store_likely
    multi_signal = len(reasons) >= 3 and (
        seller_match == "YES" or fortress or store_likely
    )

    confirmed = amazon_brand or strong_control or brand_owned or multi_signal
    return confirmed, " | ".join(reasons)


# ────────────────────────────────────────────────────────────────────────
# Per-row build (the heart of the step).
# ────────────────────────────────────────────────────────────────────────


def _build_final_row(row: dict) -> tuple[list[object], tuple[bool, str]]:
    """Build one final-output row + the PL-confirmation status for that row."""

    def s(name: str) -> str:
        return _coerce_str(row.get(name, ""))

    def n(name: str) -> float:
        return _parse_money(row.get(name, ""))

    asin = s("ASIN")
    title = s("Title")
    brand = s("Brand")
    amazon_url = s("Amazon URL")
    category = s("Category")
    weight_flag = s("Weight Flag")
    verdict = s("Verdict")
    verdict_reason = s("Verdict Reason")
    composite = n("Composite Score")
    demand = n("Demand Score")
    stability = n("Stability Score")
    competition = n("Competition Score")
    margin = n("Margin Score")
    listing_quality = s("Listing Quality")
    cash_flow_score = n("Cash Flow Score")
    profit_score = n("Profit Score")
    balanced_score = n("Balanced Score")
    lane = s("Opportunity Lane")
    commercial_priority = n("Commercial Priority")
    monthly_gross_profit = n("Monthly Gross Profit")
    lane_reason = s("Lane Reason")
    price_compression = s("Price Compression")
    price = n("Current Price")
    bb90_avg = n("Buy Box 90d Avg")
    price_drop = n("Price Drop % 90d")
    fba_fee = n("Fulfilment Fee")
    amazon_fees = n("Amazon Fees")
    total_fees = n("Total Amazon Fees")
    est_cost = n("Est Cost 65%")
    est_profit = n("Est Profit")
    est_roi = n("Est ROI %")
    max_cost = n("Max Cost 20% ROI")
    breakeven = n("Breakeven Price")
    bsr = n("BSR Current")
    bsr_drops = n("BSR Drops 90d")
    bought = n("Bought per Month")
    star_rating = n("Star Rating")
    review_count = n("Review Count")
    brand_1p = s("Brand 1P")
    fba_count = n("FBA Seller Count")
    amazon_on_listing = s("Amazon on Listing")
    bb_amazon_pct = s("Buy Box Amazon %")
    brand_seller_match = s("Brand Seller Match")
    fortress_listing = s("Fortress Listing")
    brand_type_v = s("Brand Type")
    aplus_present = s("A+ Content Present")
    brand_store_present = s("Brand Store Present")
    category_risk_level = s("Category Risk Level")
    ip_risk_score = s("IP Risk Score")
    ip_risk_band = s("IP Risk Band")
    ip_reason = s("IP Reason")
    gated = s("Gated")
    sas_flags = s("SAS Flags")

    price_stab = price_stability(price_drop)
    plr = pl_risk(fba_count, brand_1p)
    pl_status = confirmed_private_label_status(
        brand_1p, brand_seller_match, fortress_listing,
        brand_store_present, brand_type_v,
    )

    # Guard against NaN/inf — _parse_money normally clears NaN to 0.0 but
    # any propagation of inf via upstream arithmetic would crash int().
    bought_int = (
        int(bought)
        if math.isfinite(bought) and bought == int(bought)
        else bought
    )

    out_row: list[object] = [
        asin, title, brand, amazon_url, category, weight_flag,
        verdict, verdict_reason, lane, commercial_priority, lane_reason,
        f"{composite:.1f}", demand, stability, competition, margin,
        cash_flow_score, profit_score, balanced_score,
        f"GBP{_round_half_up(monthly_gross_profit)}", price_compression, listing_quality,
        f"GBP{price:.2f}", f"GBP{bb90_avg:.2f}", price_stab,
        f"GBP{fba_fee:.2f}", f"GBP{amazon_fees:.2f}", f"GBP{total_fees:.2f}",
        f"GBP{est_cost:.2f}", f"GBP{est_profit:.2f}",
        f"{est_roi:.1f}%",
        f"GBP{max_cost:.2f}", f"GBP{breakeven:.2f}",
        _round_half_up(bsr), bsr_drops, bought_int,
        f"{star_rating:.1f}", _round_half_up(review_count), brand_1p,
        fba_count, amazon_on_listing, bb_amazon_pct, plr,
        brand_seller_match, fortress_listing, brand_type_v, aplus_present, brand_store_present,
        category_risk_level, ip_risk_score, ip_risk_band, ip_reason,
        gated, sas_flags,
        *_FINAL_SUPPLIER_PLACEHOLDERS,
        s("EAN"), s("UPC"), s("GTIN"),
    ]
    assert len(out_row) == 67

    return out_row, pl_status


def _build_supplier_row(row: dict) -> list[object]:
    """Supplier skeleton row (22 cols) — mostly placeholders for Skill 99."""

    def s(name: str) -> str:
        return _coerce_str(row.get(name, ""))

    def n(name: str) -> float:
        return _parse_money(row.get(name, ""))

    asin = s("ASIN")
    title = s("Title")
    brand = s("Brand")
    category = s("Category")
    verdict = s("Verdict")
    composite = n("Composite Score")
    price = n("Current Price")
    fba_fee = n("Fulfilment Fee")
    est_roi = n("Est ROI %")

    return [
        asin, title, brand, category, verdict, f"{composite:.1f}",
        f"GBP{price:.2f}", f"GBP{fba_fee:.2f}", f"{est_roi:.1f}%",
        *_SUPPLIER_PLACEHOLDER_TAIL,
    ]


# ────────────────────────────────────────────────────────────────────────
# DataFrame entry point.
# ────────────────────────────────────────────────────────────────────────


def compute_phase5(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build Phase-5 outputs from a Phase-3 shortlist or Phase-4 IP-risk frame.

    Returns (final_df, supplier_skeleton_df, rejected_pl_df).

    Pure: does not read or write disk, does not mutate the input. Confirmed
    private-label rows are split out to the rejected_pl_df. Other rows go to
    final_df, sorted by Commercial Priority asc -> MGP desc -> Bought desc ->
    Profit desc -> Composite desc -> Verdict order.
    """
    if df.empty:
        return (
            pd.DataFrame(columns=FINAL_HEADERS),
            pd.DataFrame(columns=SUPPLIER_HEADERS),
            pd.DataFrame(columns=[*FINAL_HEADERS, REJECT_HEADER_EXTRA]),
        )

    missing = [c for c in _REQUIRED_INPUT_COLUMNS if c not in df.columns]
    if missing:
        warnings.warn(
            f"compute_phase5: missing input columns {missing}; rows will be "
            f"built using defaults (0/empty), which can produce misleading "
            f"output.",
            stacklevel=2,
        )

    final_rows: list[list[object]] = []
    supplier_rows: list[list[object]] = []
    rejected_rows: list[list[object]] = []

    for _, row in df.iterrows():
        rd = row.to_dict()
        out_row, (confirmed, reason) = _build_final_row(rd)
        if confirmed:
            rejected_rows.append([*out_row, reason or "Confirmed private label"])
            continue
        final_rows.append(out_row)
        supplier_rows.append(_build_supplier_row(rd))

    final_df = pd.DataFrame(final_rows, columns=FINAL_HEADERS)
    supplier_df = pd.DataFrame(supplier_rows, columns=SUPPLIER_HEADERS)
    rejected_df = pd.DataFrame(
        rejected_rows, columns=[*FINAL_HEADERS, REJECT_HEADER_EXTRA]
    )

    final_df = _sort_final_df(final_df)
    return final_df, supplier_df, rejected_df


def _sort_final_df(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the legacy multi-key sort, leaving the schema untouched."""
    if df.empty:
        return df

    out = df.copy()
    # Match JS `parseFloat(a[9]) || 99`: 0/NaN/empty all collapse to 99.
    # Python's `_parse_money(x) or 99` is the falsy-coalesce equivalent —
    # 0.0 is falsy so a literal "0" Commercial Priority sorts as missing,
    # which is what the JS upstream relies on.
    out["_sort_priority"] = out["Commercial Priority"].apply(
        lambda x: _parse_money(x) or 99
    )
    out["_sort_mgp"] = out["Monthly Gross Profit"].apply(_parse_money)
    out["_sort_bought"] = out["Bought per Month"].apply(_parse_money)
    out["_sort_profit"] = out["Est Profit"].apply(_parse_money)
    out["_sort_composite"] = out["Composite Score"].apply(_parse_money)
    # JS does case-sensitive lookup (`verdictOrder[a[6]] || 99`). Match that.
    out["_sort_verdict"] = out["Verdict"].apply(
        lambda v: VERDICT_ORDER.get(_coerce_str(v), 99)
    )

    out = out.sort_values(
        ["_sort_priority", "_sort_mgp", "_sort_bought", "_sort_profit",
         "_sort_composite", "_sort_verdict"],
        ascending=[True, False, False, False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)

    return out.drop(columns=[c for c in out.columns if c.startswith("_sort_")])


# ────────────────────────────────────────────────────────────────────────
# Stats + handoff text.
# ────────────────────────────────────────────────────────────────────────


def build_stats(
    final_df: pd.DataFrame,
    rejected_df: pd.DataFrame,
    niche: str,
    reject_csv_path: str,
) -> str:
    lane_counts: dict[str, int] = {}
    verdict_counts: dict[str, int] = {}
    for _, row in final_df.iterrows():
        lane = _coerce_str(row.get("Opportunity Lane")) or "Unclassified"
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
        verdict = _coerce_str(row.get("Verdict")) or "UNKNOWN"
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    lane_lines = [f"  {k}: {v}" for k, v in lane_counts.items()]
    verdict_lines = [f"  {k}: {v}" for k, v in verdict_counts.items()]

    return (
        f"Niche: {niche}\n"
        f"Date: {date.today().isoformat()}\n"
        f"Phase 5 Build Output\n"
        f"Products in final CSV: {len(final_df)}\n"
        f"Confirmed private label excluded: {len(rejected_df)}\n"
        f"Columns: 67\n\n"
        f"Lane breakdown:\n"
        + ("\n".join(lane_lines) if lane_lines else "  (none)")
        + "\n\nVerdict breakdown:\n"
        + ("\n".join(verdict_lines) if verdict_lines else "  (none)")
        + "\n\nSupplier columns: empty (run skill-99-find-suppliers to populate)\n"
        f"Rejected private label CSV: {reject_csv_path}\n"
    )


def build_handoff(final_df: pd.DataFrame, niche: str) -> str:
    niche_snake = niche.replace("-", "_")
    return (
        f"# Phase 5 Handoff -- {niche}\n\n"
        f"Status: BUILD COMPLETE\n"
        f"Products in final output: {len(final_df)}\n"
        f"Columns: 67\n\n"
        f"## What was built\n"
        f"- {niche_snake}_final_results.csv (67-column output, sorted by "
        f"Commercial Priority)\n"
        f"- {niche_snake}_phase5_suppliers.csv (skeleton -- supplier columns "
        f"empty)\n"
        f"- {niche_snake}_phase5_rejected_private_label.csv (confirmed PL "
        f"excluded from final file)\n"
        f"- {niche_snake}_phase5_stats.txt\n\n"
        f"## Next steps\n"
        f"1. Build XLSX (step 4c.2 — pending Python port)\n"
        f"2. Push to Google Sheets (step 4c.3 — pending Python port)\n"
        f"3. (Optional) Run skill-99-find-suppliers to populate supplier "
        f"columns, then rebuild\n"
    )


# ────────────────────────────────────────────────────────────────────────
# Step contract.
# ────────────────────────────────────────────────────────────────────────


def run_step(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Step-runner-compatible wrapper. Returns only the final_df.

    The supplier skeleton + rejected-PL frames are written to disk by the
    CLI but aren't part of the chained-step contract — downstream steps
    (Phase 6 decision engine) consume only the final_results frame.
    """
    final_df, _, _ = compute_phase5(df)
    return final_df


# ────────────────────────────────────────────────────────────────────────
# CLI — mirrors legacy phase5_build.js paths.
# ────────────────────────────────────────────────────────────────────────


def _atomic_write(path: Path, write_fn) -> None:
    """Write to a `<path>.tmp` sibling then atomically rename. Prevents
    consumers from seeing partial files if the run crashes mid-write."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        write_fn(tmp)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    tmp.replace(path)


def run(niche: str, base: Path) -> None:
    """End-to-end: read Phase-3/4 CSV, write all Phase-5 outputs."""
    base = Path(base)
    niche_snake = niche.replace("-", "_")

    # Prefer Phase 4 IP-risk output; fall back to Phase 3 shortlist.
    # Compute candidates BEFORE creating working/ so a typo'd niche
    # doesn't leave an empty working directory behind.
    working = base / "working"
    candidates = [
        working / f"{niche_snake}_phase4_ip_risk.csv",
        working / f"{niche_snake}_phase3_shortlist.csv",
        base / f"{niche_snake}_phase3_shortlist.csv",
    ]
    input_path = next((c for c in candidates if c.exists()), None)
    if input_path is None:
        print(
            f"No Phase 3 or Phase 4 input found for niche '{niche}'.\n"
            f"Looked in:\n  " + "\n  ".join(str(c) for c in candidates),
            file=sys.stderr,
        )
        sys.exit(1)

    working.mkdir(parents=True, exist_ok=True)

    final_csv_working = working / f"{niche_snake}_final_results.csv"
    final_csv_base = base / f"{niche_snake}_final_results.csv"
    supplier_csv = working / f"{niche_snake}_phase5_suppliers.csv"
    reject_csv = working / f"{niche_snake}_phase5_rejected_private_label.csv"
    stats_path = working / f"{niche_snake}_phase5_stats.txt"
    handoff_path = working / f"{niche_snake}_phase5_handoff.md"

    df = pd.read_csv(
        input_path, dtype=str, keep_default_na=False, encoding="utf-8-sig"
    )
    final_df, supplier_df, rejected_df = compute_phase5(df)

    # Atomic + utf-8-sig writes for round-trip parity with read_csv above.
    # Excel users opening the file directly need the BOM for non-ASCII
    # brand names; downstream pd.read_csv with utf-8-sig strips it cleanly.
    _atomic_write(
        final_csv_working,
        lambda p: final_df.to_csv(p, index=False, encoding="utf-8-sig"),
    )
    _atomic_write(
        final_csv_base,
        lambda p: final_df.to_csv(p, index=False, encoding="utf-8-sig"),
    )
    _atomic_write(
        supplier_csv,
        lambda p: supplier_df.to_csv(p, index=False, encoding="utf-8-sig"),
    )
    _atomic_write(
        reject_csv,
        lambda p: rejected_df.to_csv(p, index=False, encoding="utf-8-sig"),
    )
    _atomic_write(
        stats_path,
        lambda p: p.write_text(
            build_stats(final_df, rejected_df, niche, str(reject_csv)),
            encoding="utf-8",
        ),
    )
    _atomic_write(
        handoff_path,
        lambda p: p.write_text(build_handoff(final_df, niche), encoding="utf-8"),
    )

    # Lane + verdict counts at-a-glance (matches the legacy JS stdout).
    lane_counts: dict[str, int] = {}
    verdict_counts: dict[str, int] = {}
    for _, row in final_df.iterrows():
        lane = _coerce_str(row.get("Opportunity Lane")) or "Unclassified"
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
        verdict = _coerce_str(row.get("Verdict")) or "UNKNOWN"
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    print(f"Phase 5 build complete for {niche}:")
    print(f"  Final results CSV: {len(final_df)} products, 67 columns")
    print(f"  Supplier CSV: {len(supplier_df)} products (skeleton)")
    print(f"  Rejected PL CSV: {len(rejected_df)} rows")
    for k, v in lane_counts.items():
        print(f"  Lane {k}: {v}")
    for k, v in verdict_counts.items():
        print(f"  Verdict {k}: {v}")
    print(f"  Saved: {final_csv_base}")
    print(f"  Saved: {final_csv_working}")
    print(f"  Saved: {supplier_csv}")
    print(f"  Saved: {reject_csv}")
    print(f"  Saved: {stats_path}")
    print(f"  Saved: {handoff_path}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 5 Build Output step — merges Phase 3/4 inputs into the "
            "67-column final_results.csv plus supplier skeleton + reject CSVs."
        )
    )
    parser.add_argument(
        "--niche", required=True, help="Niche slug (e.g. kids-toys, sports-goods)"
    )
    parser.add_argument(
        "--base",
        required=True,
        type=Path,
        help="Base directory containing the niche's working/ subfolder.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    run(niche=args.niche, base=args.base)
