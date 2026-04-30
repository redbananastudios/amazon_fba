"""IP Risk step (formerly Phase 4 / Skill 4 in the legacy Keepa pipeline).

Adds 9 IP-risk columns to a Phase-3 shortlist:

  Brand Seller Match   YES / PARTIAL / NO
  Fortress Listing     YES / NO
  Brand Type           ESTABLISHED / GENERIC / SYNTHETIC
  A+ Content Present   YES / NO
  Brand Store Present  LIKELY / UNLIKELY
  Category Risk Level  HIGH / MEDIUM / LOW
  IP Risk Score        0..10
  IP Risk Band         High / Medium / Low
  IP Reason            "Reason 1 | Reason 2 | ..." (audit trail)

Logic ported 1:1 from `fba_engine/_legacy_keepa/skills/skill-4-ip-risk/
phase4_ip_risk.js`. Cross-validated by parameterised tests over the original
brand/seller fixtures.

Decision contract (informational only):
- This step never removes rows. Brand 1P==Y rows that band as Low surface in
  stats as "false positives avoided" so the operator can sanity-check the
  scoring weights.
- Downstream phases read the `IP Risk Band` column to weight or flag rows;
  no downstream step uses `IP Risk Score` arithmetically.

Standalone CLI invocation (mirrors the legacy JS contract):

    python -m fba_engine.steps.ip_risk \
        --niche kids-toys \
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

from fba_engine.steps._helpers import (
    clamp,
    coerce_str as _coerce_str,
    parse_money as _coerce_num,
)

# ────────────────────────────────────────────────────────────────────────
# Constants — kept identical to the legacy JS so band counts match.
# ────────────────────────────────────────────────────────────────────────

IP_HEADERS = [
    "Brand Seller Match",
    "Fortress Listing",
    "Brand Type",
    "A+ Content Present",
    "Brand Store Present",
    "Category Risk Level",
    "IP Risk Score",
    "IP Risk Band",
    "IP Reason",
]

KNOWN_ESTABLISHED_BRANDS: frozenset[str] = frozenset(
    {
        "lego",
        "pokemon",
        "disney",
        "barbie",
        "fisher price",
        "fisherprice",
        "vtech",
        "wilson",
        "yonex",
        "titleist",
        "adidas",
        "head",
        "babolat",
        "dunlop",
        "carlton",
        "stiga",
        "play doh",
        "playdoh",
        "nerf",
        "hot wheels",
        "hotwheels",
    }
)

CATEGORY_RISK_BY_NICHE: dict[str, str] = {
    "educational-toys": "HIGH",
    "kids-toys": "HIGH",
    "afro-hair": "MEDIUM",
    "pet-care": "MEDIUM",
    "sports-goods": "MEDIUM",
    "stationery": "LOW",
}

# ────────────────────────────────────────────────────────────────────────
# Pure helpers — no I/O, all unit-tested.
# ────────────────────────────────────────────────────────────────────────


def normalize_name(value: object) -> str:
    """Normalise a brand or seller string for fuzzy comparison.

    Drops legal suffixes ("Ltd"), parenthesised qualifiers, non-alphanumerics,
    then lowercases and collapses whitespace. The first segment before "/"
    is used so multi-brand seller names like "Acme/SubBrand" pick the
    primary identity.
    """
    s = str(value or "").lower().split("/", 1)[0]
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\b(ltd|limited|inc|uk)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def levenshtein(a: str, b: str) -> int:
    """Edit distance between two strings (stdlib, O(len(a)*len(b)))."""
    s = a or ""
    t = b or ""
    if s == t:
        return 0
    if not s:
        return len(t)
    if not t:
        return len(s)
    # Two-row DP — sufficient and lighter than full matrix.
    prev = list(range(len(t) + 1))
    for i, sc in enumerate(s, start=1):
        curr = [i] + [0] * len(t)
        for j, tc in enumerate(t, start=1):
            cost = 0 if sc == tc else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def similarity(a: object, b: object) -> float:
    """Levenshtein similarity in [0, 1] over normalised forms of a and b."""
    left = normalize_name(a)
    right = normalize_name(b)
    if not left or not right:
        return 0.0
    distance = levenshtein(left, right)
    return 1 - (distance / max(len(left), len(right), 1))


def category_risk_level(niche: str) -> str:
    """Map a niche slug to its IP-risk category. Unknown niches default to MEDIUM."""
    return CATEGORY_RISK_BY_NICHE.get(niche, "MEDIUM")


def brand_type(raw_brand: object, review_count: float, rating: float) -> str:
    """Classify a brand string as ESTABLISHED, SYNTHETIC, or GENERIC.

    ESTABLISHED: in the well-known list, OR has > 500 reviews + > 3.5 stars.
    SYNTHETIC: 2+ uppercase letters only / 2+ digits / <= 3 chars after
               stripping non-alphanumerics — typical of low-effort drop-ship
               brands.
    GENERIC: everything else.
    """
    clean = normalize_name(raw_brand)
    compact = re.sub(r"[^A-Za-z0-9]", "", str(raw_brand or ""))
    if clean in KNOWN_ESTABLISHED_BRANDS or (review_count > 500 and rating > 3.5):
        return "ESTABLISHED"
    if (
        re.fullmatch(r"[A-Z]{2,}", compact)
        or re.search(r"\d{2,}", compact)
        or len(compact) <= 3
    ):
        return "SYNTHETIC"
    return "GENERIC"


def _yes_token(raw: object) -> bool:
    """A+ Content present: matches /^(y|yes)$/i."""
    return str(raw or "").strip().lower() in {"y", "yes"}


# ────────────────────────────────────────────────────────────────────────
# Per-row scoring (the heart of the step).
# ────────────────────────────────────────────────────────────────────────


def _score_row(
    brand: str,
    bb_seller: str,
    fba_seller_count: float,
    fba_seller_90d_avg: float,
    review_count: float,
    rating: float,
    has_aplus: object,
    niche: str,
) -> dict[str, object]:
    """Compute the 9 IP-risk fields for one row."""
    brand_norm = normalize_name(brand)
    seller_norm = normalize_name(bb_seller)

    if brand_norm and seller_norm and (
        brand_norm in seller_norm or seller_norm in brand_norm
    ):
        brand_seller_match = "YES"
    elif similarity(brand, bb_seller) > 0.7:
        brand_seller_match = "PARTIAL"
    else:
        brand_seller_match = "NO"

    fortress_listing = (
        "YES" if (fba_seller_count <= 1 and fba_seller_90d_avg <= 1.5) else "NO"
    )
    derived_brand_type = brand_type(brand, review_count, rating)
    aplus_present = "YES" if _yes_token(has_aplus) else "NO"
    brand_store_present = (
        "LIKELY"
        if (brand_seller_match == "YES" and aplus_present == "YES")
        else "UNLIKELY"
    )
    risk_level = category_risk_level(niche)

    score = 0.0
    reasons: list[str] = []

    if brand_seller_match == "YES":
        score += 3
        reasons.append("Brand=Seller match (YES)")
    elif brand_seller_match == "PARTIAL":
        score += 1
        reasons.append("Brand=Seller match (PARTIAL)")

    if fortress_listing == "YES":
        score += 3
        reasons.append("Fortress listing")

    if derived_brand_type == "ESTABLISHED":
        score += 1
        reasons.append("Established brand")

    if aplus_present == "YES":
        score += 1
        reasons.append("A+ content")

    if brand_store_present == "LIKELY":
        score += 1
        reasons.append("Likely brand store")

    if risk_level == "HIGH":
        score += 1
        reasons.append("Category HIGH risk")
    elif risk_level == "MEDIUM":
        score += 0.5
        reasons.append("Category MEDIUM risk")

    # Commercial rounding (JS Math.round semantics) — Python's built-in
    # round() uses banker's rounding (half-to-even), so 6.5 → 6 instead of
    # 7. The legacy JS used Math.round, which rounds half-toward-+infinity.
    # Score is always non-negative so floor(x + 0.5) is sufficient.
    final_score = int(clamp(math.floor(score + 0.5), 0, 10))
    band = "High" if final_score >= 7 else "Medium" if final_score >= 4 else "Low"
    ip_reason = " | ".join(reasons)

    return {
        "Brand Seller Match": brand_seller_match,
        "Fortress Listing": fortress_listing,
        "Brand Type": derived_brand_type,
        "A+ Content Present": aplus_present,
        "Brand Store Present": brand_store_present,
        "Category Risk Level": risk_level,
        "IP Risk Score": final_score,
        "IP Risk Band": band,
        "IP Reason": ip_reason,
    }


# ────────────────────────────────────────────────────────────────────────
# DataFrame entry point.
# ────────────────────────────────────────────────────────────────────────


# Columns the scoring genuinely depends on. Missing any of these means the
# row's score is built from defaults (0 / empty) and will be misleading —
# warn loudly so a misshapen upstream isn't silent.
_REQUIRED_INPUT_COLUMNS = (
    "Brand",
    "BB Seller",
    "FBA Seller Count",
    "FBA Seller 90d Avg",
)


def compute_ip_risk(df: pd.DataFrame, niche: str) -> pd.DataFrame:
    """Append the 9 IP-risk columns to a Phase-3 shortlist DataFrame.

    Pure: does not read or write disk, does not mutate the input.
    Missing source columns are tolerated (coerce to 0/empty) but emit a
    warning so the operator knows scoring relied on defaults.
    """
    if df.empty:
        return df.assign(**{header: pd.Series(dtype=object) for header in IP_HEADERS})

    missing = [c for c in _REQUIRED_INPUT_COLUMNS if c not in df.columns]
    if missing:
        warnings.warn(
            f"compute_ip_risk: missing input columns {missing}; rows will be "
            f"scored using defaults (0/empty), which can produce misleading "
            f"fortress / brand-match signals.",
            stacklevel=2,
        )

    out = df.copy()
    rows = []
    for _, row in out.iterrows():
        rows.append(
            _score_row(
                brand=_coerce_str(row.get("Brand", "")),
                bb_seller=_coerce_str(row.get("BB Seller", "")),
                fba_seller_count=_coerce_num(row.get("FBA Seller Count", 0)),
                fba_seller_90d_avg=_coerce_num(row.get("FBA Seller 90d Avg", 0)),
                review_count=_coerce_num(row.get("Review Count", 0)),
                rating=_coerce_num(row.get("Star Rating", 0)),
                has_aplus=row.get("Has A+ Content", ""),
                niche=niche,
            )
        )
    enriched = pd.DataFrame(rows, index=out.index)
    return pd.concat([out, enriched], axis=1)


# ────────────────────────────────────────────────────────────────────────
# Stats + handoff text — match the legacy format byte-for-byte where it
# wouldn't add gratuitous churn to downstream reports.
# ────────────────────────────────────────────────────────────────────────


def build_stats(df: pd.DataFrame, niche: str) -> str:
    total = max(len(df), 1)
    band_counts = {
        "High": int((df["IP Risk Band"] == "High").sum()),
        "Medium": int((df["IP Risk Band"] == "Medium").sum()),
        "Low": int((df["IP Risk Band"] == "Low").sum()),
    }
    seller_match_counts = {
        "YES": int((df["Brand Seller Match"] == "YES").sum()),
        "PARTIAL": int((df["Brand Seller Match"] == "PARTIAL").sum()),
        "NO": int((df["Brand Seller Match"] == "NO").sum()),
    }
    fortress_count = int((df["Fortress Listing"] == "YES").sum())
    brand_type_counts = {
        "ESTABLISHED": int((df["Brand Type"] == "ESTABLISHED").sum()),
        "GENERIC": int((df["Brand Type"] == "GENERIC").sum()),
        "SYNTHETIC": int((df["Brand Type"] == "SYNTHETIC").sum()),
    }

    # Coerce numeric sort columns so non-numeric strings don't lex-sort
    # ("9" > "10"). ASIN is left as string for ascending tie-break.
    sort_frame = df.copy()
    sort_frame["__ip_score_sort"] = pd.to_numeric(
        sort_frame["IP Risk Score"], errors="coerce"
    ).fillna(0)
    sort_cols = ["__ip_score_sort"]
    sort_asc = [False]
    if "Monthly Gross Profit" in sort_frame.columns:
        sort_frame["__mgp_sort"] = pd.to_numeric(
            sort_frame["Monthly Gross Profit"], errors="coerce"
        ).fillna(0)
        sort_cols.append("__mgp_sort")
        sort_asc.append(False)
    if "ASIN" in sort_frame.columns:
        sort_cols.append("ASIN")
        sort_asc.append(True)
    top10 = sort_frame.sort_values(sort_cols, ascending=sort_asc).head(10)

    top10_lines = []
    for i, (_, row) in enumerate(top10.iterrows(), start=1):
        reason = row.get("IP Reason", "") or "No contributing factors"
        top10_lines.append(
            f"  {i}. {row.get('ASIN', '')} - {row.get('Brand', '')} - "
            f"{row.get('IP Risk Score', '')} - {row.get('IP Risk Band', '')} - {reason}"
        )

    fp_lines: list[str] = []
    if "Brand 1P" in df.columns:
        fp = df[(df["Brand 1P"] == "Y") & (df["IP Risk Band"] == "Low")]
        for _, row in fp.iterrows():
            fp_lines.append(
                f"  {row.get('ASIN', '')} - {row.get('Brand', '')} - Low - Brand 1P=Y"
            )

    return (
        f"Niche: {niche}\n"
        f"Date: {date.today().isoformat()}\n"
        f"Input: {len(df)} products from Phase 3 shortlist\n\n"
        f"IP Risk Band distribution:\n"
        f"  High:   {band_counts['High']} ({round(band_counts['High'] / total * 100)}%)\n"
        f"  Medium: {band_counts['Medium']} ({round(band_counts['Medium'] / total * 100)}%)\n"
        f"  Low:    {band_counts['Low']} ({round(band_counts['Low'] / total * 100)}%)\n\n"
        f"Brand Seller Match:\n"
        f"  YES:     {seller_match_counts['YES']}\n"
        f"  PARTIAL: {seller_match_counts['PARTIAL']}\n"
        f"  NO:      {seller_match_counts['NO']}\n\n"
        f"Fortress Listings: {fortress_count}\n\n"
        f"Brand Type:\n"
        f"  ESTABLISHED: {brand_type_counts['ESTABLISHED']}\n"
        f"  GENERIC:     {brand_type_counts['GENERIC']}\n"
        f"  SYNTHETIC:   {brand_type_counts['SYNTHETIC']}\n\n"
        f"Top 10 highest IP Risk:\n"
        + ("\n".join(top10_lines) if top10_lines else "  None")
        + "\n\nFalse positives avoided (Low risk with Brand 1P = Y):\n"
        + ("\n".join(fp_lines) if fp_lines else "  None")
        + "\n"
    )


def build_handoff(df: pd.DataFrame, niche: str, output_filename: str) -> str:
    band_counts = {
        "High": int((df["IP Risk Band"] == "High").sum()),
        "Medium": int((df["IP Risk Band"] == "Medium").sum()),
        "Low": int((df["IP Risk Band"] == "Low").sum()),
    }
    fortress_count = int((df["Fortress Listing"] == "YES").sum())
    return (
        f"# Phase 4 Handoff -- {niche}\n\n"
        f"Status: COMPLETE\n"
        f"Input products: {len(df)}\n\n"
        f"## Files\n"
        f"- {output_filename}\n"
        f"- {Path(output_filename).stem.replace('_phase4_ip_risk', '_phase4_stats')}.txt\n"
        f"- {Path(output_filename).stem.replace('_phase4_ip_risk', '_phase4_handoff')}.md\n\n"
        f"## Summary\n"
        f"- High IP risk: {band_counts['High']}\n"
        f"- Medium IP risk: {band_counts['Medium']}\n"
        f"- Low IP risk: {band_counts['Low']}\n"
        f"- Fortress listings: {fortress_count}\n\n"
        f"## Next step\n"
        f"Run Phase 5 build using {output_filename} as the preferred input.\n"
    )


# ────────────────────────────────────────────────────────────────────────
# Step contract — uniform call shape for the step 5 runner.
# ────────────────────────────────────────────────────────────────────────


def run_step(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Step-runner-compatible wrapper.

    All composable steps accept (df, config) so the step 5 YAML runner can
    invoke any step uniformly. `config` is a strategy/phase-level config
    dict — for IP risk, only the niche key is read.

    Required keys:
      niche: str — niche slug (kids-toys, sports-goods, etc.)
    """
    niche = config.get("niche")
    if not niche:
        raise ValueError("ip_risk step requires config['niche']")
    return compute_ip_risk(df, niche)


# ────────────────────────────────────────────────────────────────────────
# CLI — mirrors legacy phase4_ip_risk.js paths so existing data folders
# pick up the new step without restructuring.
# ────────────────────────────────────────────────────────────────────────


def run(niche: str, base: Path) -> None:
    """End-to-end: read phase 3 shortlist, write phase 4 CSV + stats + handoff."""
    working = base / "working"
    niche_snake = niche.replace("-", "_")
    input_path = working / f"{niche_snake}_phase3_shortlist.csv"
    output_path = working / f"{niche_snake}_phase4_ip_risk.csv"
    stats_path = working / f"{niche_snake}_phase4_stats.txt"
    handoff_path = working / f"{niche_snake}_phase4_handoff.md"

    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # utf-8-sig strips a leading BOM if present (the legacy JS did the same
    # via `.replace(/^﻿/, "")`); without it the first column header
    # would be `﻿ASIN` and every row.get("ASIN") would return "".
    df = pd.read_csv(input_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    enriched = compute_ip_risk(df, niche)
    enriched.to_csv(output_path, index=False)
    stats_path.write_text(build_stats(enriched, niche), encoding="utf-8")
    handoff_path.write_text(
        build_handoff(enriched, niche, output_path.name), encoding="utf-8"
    )
    print(f"Saved: {output_path}")
    print(f"Saved: {stats_path}")
    print(f"Saved: {handoff_path}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IP Risk step (Phase 4) — annotates Phase 3 shortlist with brand/listing IP-risk signals."
    )
    parser.add_argument(
        "--niche", required=True, help="Niche slug (e.g. kids-toys, sports-goods)"
    )
    parser.add_argument(
        "--base",
        required=True,
        type=Path,
        help="Base directory containing working/ subfolder for this niche.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    run(niche=args.niche, base=args.base)
