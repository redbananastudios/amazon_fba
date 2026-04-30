"""Scoring step (formerly Phase 3 / Skill 3 in the legacy Keepa pipeline).

Scores each ASIN across 4 dimensions (Demand, Stability, Competition,
Margin), computes a 30/30/20/20 weighted composite, derives 3 lane
scores (Cash Flow, Profit, Balanced), classifies the row's
Opportunity Lane + Commercial Priority, applies a Price Compression
flag, and assigns a Verdict (YES / MAYBE / MAYBE-ROI / BRAND APPROACH /
BUY THE DIP / PRICE EROSION / GATED / HAZMAT / NO).

Logic ported from
``fba_engine/_legacy_keepa/skills/skill-3-scoring/SKILL.md``. Output
columns match the relevant subset of `build_output.FINAL_HEADERS` so
the chain `scoring -> ip_risk -> build_output -> decision_engine`
flows cleanly.

Decision contract:
- This step appends columns; it does NOT drop rows. Verdict-based
  filtering (e.g. excluding NO/PRICE EROSION/HAZMAT from the
  shortlist) is the caller's responsibility — typically by
  ``df[df["Verdict"].isin({"YES", "MAYBE", ...})]`` after this step.
- All scoring/lane logic is universal across niches today. Per-niche
  weight overrides can be added via ``shared/config/scoring/<niche>.yaml``
  when needed; not implemented here to avoid speculative complexity.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from fba_engine.steps._helpers import (
    clamp,
    coerce_str,
    parse_money,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Output schema (subset of build_output.FINAL_HEADERS).
# ────────────────────────────────────────────────────────────────────────

SCORING_COLUMNS: tuple[str, ...] = (
    "Demand Score",
    "Stability Score",
    "Competition Score",
    "Margin Score",
    "Composite Score",
    "Cash Flow Score",
    "Profit Score",
    "Balanced Score",
    "Opportunity Lane",
    "Commercial Priority",
    "Lane Reason",
    "Monthly Gross Profit",
    "Price Compression",
    "Verdict",
    "Verdict Reason",
)


# ────────────────────────────────────────────────────────────────────────
# Configurable thresholds. Universal today; future niche YAMLs would
# override via shared/config/scoring/<niche>.yaml.
# ────────────────────────────────────────────────────────────────────────

PRICE_RANGE_MIN: float = 20.0
PRICE_RANGE_MAX: float = 70.0
HARD_SELLER_CEILING: int = 20

YES_COMPOSITE_THRESHOLD: float = 8.5
MAYBE_COMPOSITE_THRESHOLD: float = 7.0


# ────────────────────────────────────────────────────────────────────────
# Cell coercion helpers.
# ────────────────────────────────────────────────────────────────────────


def _num(row: dict, key: str, default: float = 0.0) -> float:
    """Coerce a numeric cell, tolerating GBP prefix / % / commas / NaN."""
    raw = row.get(key)
    if raw is None or raw == "":
        return default
    try:
        return parse_money(raw) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError):
        return default


def _yn(row: dict, key: str) -> bool:
    """True iff cell is a Y/yes/true marker. Tolerates int 1, bool True."""
    raw = row.get(key)
    if raw is True or raw == 1:
        return True
    return coerce_str(raw).upper() in {"Y", "YES", "TRUE", "1"}


# ────────────────────────────────────────────────────────────────────────
# Per-dimension scoring functions.
# ────────────────────────────────────────────────────────────────────────


def score_demand(row: dict) -> int:
    """0..10 demand signal. Primary input: BSR Current."""
    bsr = _num(row, "BSR Current")
    if bsr <= 0:
        return 0
    if bsr < 10_000:
        score = 10
    elif bsr < 20_000:
        score = 9
    elif bsr < 30_000:
        score = 8
    elif bsr < 40_000:
        score = 7
    elif bsr < 50_000:
        score = 6
    elif bsr < 60_000:
        score = 5
    elif bsr < 80_000:
        score = 3
    else:
        score = 1

    drops = _num(row, "BSR Drops 90d")
    if drops >= 15:
        score += 1
    elif drops < 3:
        score -= 1

    bought = _num(row, "Bought per Month")
    # Match the legacy phase3_scoring.js thresholds (200/50). SKILL.md
    # says 300/100 but the actually-shipped scoring used 200/50 — the
    # operator-tuned values, kept here for parity.
    if bought >= 200:
        score += 1
    elif 0 < bought < 50:
        score -= 1

    rating = _num(row, "Star Rating")
    reviews = _num(row, "Review Count")
    if rating > 0 and rating < 3.5:
        score -= 1
    if reviews > 500 and rating > 4.0:
        score += 1

    return int(clamp(score, 0, 10))


def score_stability(row: dict) -> int:
    """0..10 price-stability signal. Primary input: Buy Box 90-day drop %.

    A 0 here triggers PRICE EROSION downstream — verdict logic checks
    this directly, so don't artificially floor at 1.
    """
    drop_pct = _num(row, "Price Drop % 90d")
    if drop_pct >= 0:
        score = 10
    elif drop_pct >= -5:
        score = 8
    elif drop_pct >= -10:
        score = 6
    elif drop_pct >= -15:
        score = 4
    elif drop_pct >= -20:
        score = 2
    else:
        score = 0

    if _yn(row, "PRICE CHECK"):
        score -= 1

    # Recovery / continued-erosion modifier — only when the 90-day
    # drop is large enough to matter (>10%). Legacy compares the
    # current price against the 90-day Buy Box average: above-avg with
    # a big historical drop signals a recovery (BUY THE DIP), at-or-
    # below-avg signals continued erosion. No 30-day-avg input needed.
    if drop_pct < -10:
        current = _num(row, "Current Price")
        avg90 = _num(row, "Buy Box 90d Avg")
        if current > 0 and avg90 > 0:
            if current > avg90:
                score += 2
            else:
                score -= 1

    return int(clamp(score, 0, 10))


def score_competition(row: dict) -> int:
    """0..10 competition signal. Primary input: FBA Seller Count.

    Applies a velocity-based dynamic ceiling first — over-saturated
    listings score 0 even when seller count is otherwise reasonable.
    """
    sellers = int(_num(row, "FBA Seller Count"))
    bought = _num(row, "Bought per Month")

    # Dynamic ceiling by velocity tier.
    if bought < 300:
        ceiling = 8
    elif bought < 600:
        ceiling = 12
    elif bought < 1000:
        ceiling = 15
    else:
        ceiling = 20

    if sellers > ceiling:
        return 0

    if sellers <= 2:
        score = 10
    elif sellers == 3:
        score = 9
    elif sellers <= 5:
        score = 7
    elif sellers <= 8:
        score = 5
    elif sellers <= 12:
        score = 3
    else:
        score = 1

    amazon_share = _num(row, "Buy Box Amazon %")
    if amazon_share > 70:
        score -= 3
    elif amazon_share >= 50:
        score -= 1

    seller_avg = _num(row, "FBA Seller 90d Avg")
    if seller_avg > 0:
        if sellers < seller_avg:
            score += 1  # sellers leaving — opportunity
        elif sellers > seller_avg * 1.5:
            score -= 1  # sellers piling in

    if _yn(row, "Brand 1P"):
        score -= 2

    reviews = _num(row, "Review Count")
    if 0 < reviews < 20:
        score -= 1

    return int(clamp(score, 0, 10))


def score_margin(row: dict) -> int:
    """0..10 margin signal. Primary input: Est ROI %.

    Tier boundaries use strict ``>`` to match the legacy
    `phase3_scoring.js` (e.g. ROI=35 falls in the 30+ tier, returns 7).
    """
    roi = _num(row, "Est ROI %")
    if roi > 40:
        score = 10
    elif roi > 35:
        score = 9
    elif roi > 30:
        score = 7
    elif roi > 25:
        score = 5
    elif roi > 20:
        score = 3
    else:
        score = 1

    profit = _num(row, "Est Profit")
    if profit > 8:
        score += 1
    elif profit < 3:
        score -= 1

    weight_flag = coerce_str(row.get("Weight Flag")).upper()
    if "HEAVY" in weight_flag and "OVERSIZE" in weight_flag:
        score -= 2
    elif weight_flag in {"HEAVY", "OVERSIZE"}:
        score -= 1

    return int(clamp(score, 0, 10))


# ────────────────────────────────────────────────────────────────────────
# Lane scores + classification.
# ────────────────────────────────────────────────────────────────────────


def _cash_flow_score(d: int, s: int, c: int, m: int, row: dict) -> float:
    base = d * 0.30 + s * 0.25 + c * 0.20 + m * 0.10
    bought = _num(row, "Bought per Month")
    profit = _num(row, "Est Profit")
    if bought >= 400:
        base += 1.5
    elif bought >= 200:
        base += 1.0
    elif bought >= 100:
        base += 0.5
    if profit < 1.5:
        base -= 2
    elif profit < 2.5:
        base -= 1
    return round(clamp(base, 0, 10), 1)


def _profit_score(d: int, s: int, c: int, m: int, row: dict) -> float:
    base = m * 0.30 + s * 0.25 + c * 0.20 + d * 0.10
    profit = _num(row, "Est Profit")
    roi = _num(row, "Est ROI %")
    if profit >= 12:
        base += 1.5
    elif profit >= 8:
        base += 1.0
    elif profit >= 5:
        base += 0.5
    if roi >= 35:
        base += 1.0
    elif roi >= 25:
        base += 0.5
    return round(clamp(base, 0, 10), 1)


def _balanced_score(d: int, s: int, c: int, m: int, row: dict) -> float:
    base = (d + s + c + m) * 0.25
    bought = _num(row, "Bought per Month")
    profit = _num(row, "Est Profit")
    if bought >= 150 and profit >= 4:
        base += 1.0
    elif bought >= 100 and profit >= 3:
        base += 0.5
    if bought < 50 or profit < 1.5:
        base -= 1
    return round(clamp(base, 0, 10), 1)


def _classify_lane(row: dict, verdict: str) -> tuple[str, int, str]:
    """Return (Opportunity Lane, Commercial Priority, Lane Reason).

    NO / PRICE EROSION / HAZMAT verdicts get no lane (priority 9).
    """
    if verdict in {"NO", "PRICE EROSION", "HAZMAT"}:
        return "", 9, ""

    bought = _num(row, "Bought per Month")
    roi = _num(row, "Est ROI %")
    profit = _num(row, "Est Profit")
    sellers = int(_num(row, "FBA Seller Count"))
    monthly_gross = bought * profit

    # Hard disqualifiers per the SKILL.md thresholds.
    if roi < 5 or profit < 1:
        return "", 9, ""

    # Lane rules — first match wins after BALANCED check.
    qualifies_profit = (
        profit >= 8
        or (roi >= 25 and profit >= 4)
        or (roi >= 30 and profit >= 3)
    )
    qualifies_cash_flow = (
        bought >= 200 and roi >= 10 and profit >= 1.5
    )
    qualifies_balanced = (
        bought >= 150 and roi >= 20 and profit >= 2.5 and sellers <= 10
    ) or (qualifies_profit and qualifies_cash_flow)

    reason = (
        f"{int(bought)}/mo | GBP{profit:.2f} profit | "
        f"ROI {int(round(roi))}% | GBP{monthly_gross:.0f}/mo gross"
    )

    if qualifies_balanced:
        return "BALANCED", 1, reason
    if qualifies_profit:
        return "PROFIT", 2, reason
    if qualifies_cash_flow:
        return "CASH FLOW", 3, reason
    return "", 9, reason


def _price_compression(row: dict) -> str:
    current = _num(row, "Current Price")
    avg90 = _num(row, "Buy Box 90d Avg")
    if current <= 0 or avg90 <= 0:
        return ""
    ratio = current / avg90
    if ratio < 0.8:
        return "COMPRESSED"
    if ratio < 0.9:
        return "SQUEEZED"
    return "OK"


# ────────────────────────────────────────────────────────────────────────
# Verdict assignment.
# ────────────────────────────────────────────────────────────────────────


def _verdict(
    row: dict, dem: int, stab: int, comp: int, marg: int, composite: float,
) -> tuple[str, str]:
    """Returns (Verdict, Verdict Reason). First match wins per SKILL.md
    ordering. Reason is a short pipe-separated metrics string.

    Hard rejects (price range / oversaturated / hazmat) take precedence
    over composite-based verdicts.
    """
    bsr = int(_num(row, "BSR Current"))
    bought = int(_num(row, "Bought per Month"))
    sellers = int(_num(row, "FBA Seller Count"))
    roi = _num(row, "Est ROI %")
    amazon_share = _num(row, "Buy Box Amazon %")
    drop_pct = _num(row, "Price Drop % 90d")
    current = _num(row, "Current Price")
    avg90 = _num(row, "Buy Box 90d Avg")

    # Hard disqualifiers first — the operator wants these surfaced
    # before any composite-based verdict can override.
    if _yn(row, "Hazmat"):
        return "HAZMAT", "Confirmed hazmat by SellerAmp"
    if current > 0 and (current < PRICE_RANGE_MIN or current > PRICE_RANGE_MAX):
        return (
            "NO",
            f"NO (Price Range) | GBP{current:.2f} outside "
            f"GBP{PRICE_RANGE_MIN:.0f}-GBP{PRICE_RANGE_MAX:.0f}",
        )
    if sellers > HARD_SELLER_CEILING:
        return (
            "NO",
            f"NO (Oversaturated) | {sellers} FBA sellers > "
            f"{HARD_SELLER_CEILING} cap",
        )

    # Stability score = 0 means PRICE EROSION (>20% 90-day drop).
    if stab == 0:
        return (
            "PRICE EROSION",
            f"90-day drop {drop_pct:.0f}% | no recovery signal",
        )

    # Brand 1P + Amazon dominant — can't compete.
    if _yn(row, "Brand 1P") and amazon_share > 60:
        return (
            "NO",
            f"Brand 1P detected | Amazon BB {amazon_share:.0f}% | "
            "cannot compete",
        )

    # Gated stays in file with GATED verdict.
    if _yn(row, "Gated"):
        return (
            "GATED",
            f"Score {composite:.1f} | BSR {bsr:,} | ROI {int(round(roi))}% | "
            "apply for access",
        )

    # YES — strong composite, no blocking flags.
    if composite >= YES_COMPOSITE_THRESHOLD:
        return (
            "YES",
            f"BSR {bsr:,} | {bought}/mo | {sellers} sellers | "
            f"ROI {int(round(roi))}% | score {composite:.1f}",
        )

    # BRAND APPROACH: ≤3 sellers + weak listing. Legacy uses ``<= 3``
    # (any with weak listing — including the rare 1-seller case which
    # the operator triages manually as PL-risk).
    listing_quality = coerce_str(row.get("Listing Quality")).upper()
    if sellers <= 3 and "WEAK" in listing_quality:
        return (
            "BRAND APPROACH",
            f"{sellers} sellers | listing WEAK | BSR {bsr:,} | contact brand",
        )

    # BUY THE DIP: large drop with recovery signal — current price is
    # above the 90-day average despite the steep historical decline.
    if drop_pct < -25 and current > 0 and avg90 > 0 and current > avg90:
        return (
            "BUY THE DIP",
            f"GBP{current:.2f} vs 90d avg GBP{avg90:.2f} "
            f"({drop_pct:.0f}%) | recovering",
        )

    # MAYBE — middle composite tier.
    if composite >= MAYBE_COMPOSITE_THRESHOLD:
        amazon_note = (
            f" | Amazon BB {amazon_share:.0f}% drags competition"
            if amazon_share >= 50 else ""
        )
        return (
            "MAYBE",
            f"BSR {bsr:,} | {bought}/mo | score {composite:.1f}{amazon_note}",
        )

    # MAYBE-ROI: low ROI but composite still ≥ 5 — flag for trade-
    # price improvement rather than reject outright. Sits BELOW the
    # MAYBE composite branch so a strong-composite-with-weak-ROI row
    # still wins the MAYBE label (matches legacy ordering).
    if 0 < roi < 20 and composite >= 5:
        return (
            "MAYBE-ROI",
            f"BSR {bsr:,} | est ROI {int(round(roi))}% — needs better trade price",
        )

    # Default: NO with reason citing the weakest dimension.
    weakest_label, weakest_score = min(
        [("demand", dem), ("stability", stab),
         ("competition", comp), ("margin", marg)],
        key=lambda kv: kv[1],
    )
    return (
        "NO",
        f"Composite {composite:.1f} below {MAYBE_COMPOSITE_THRESHOLD:.1f} | "
        f"weakest: {weakest_label} {weakest_score}",
    )


# ────────────────────────────────────────────────────────────────────────
# Public entry point.
# ────────────────────────────────────────────────────────────────────────


def compute_scoring(df: pd.DataFrame) -> pd.DataFrame:
    """Append all scoring columns to a copy of `df`.

    Empty input returns an empty frame with the canonical columns
    added, so chained downstream steps can index by name.
    """
    out = df.copy()
    if df.empty:
        for col in SCORING_COLUMNS:
            out[col] = pd.Series(dtype=object)
        return out

    rows: list[dict] = out.to_dict(orient="records")
    # Initialise scoring columns with object dtype so we can write
    # mixed types (int scores, float composite, str verdict) without
    # tripping pandas' strict-string inference.
    for col in SCORING_COLUMNS:
        out[col] = pd.Series([None] * len(out), dtype=object, index=out.index)

    for idx, row in enumerate(rows):
        dem = score_demand(row)
        stab = score_stability(row)
        comp = score_competition(row)
        marg = score_margin(row)
        composite = round(
            dem * 0.30 + stab * 0.30 + comp * 0.20 + marg * 0.20, 1,
        )
        verdict, reason = _verdict(row, dem, stab, comp, marg, composite)
        lane, priority, lane_reason = _classify_lane(row, verdict)
        bought = _num(row, "Bought per Month")
        profit = _num(row, "Est Profit")
        monthly_gross = round(bought * profit, 2)

        out.at[idx, "Demand Score"] = dem
        out.at[idx, "Stability Score"] = stab
        out.at[idx, "Competition Score"] = comp
        out.at[idx, "Margin Score"] = marg
        out.at[idx, "Composite Score"] = composite
        out.at[idx, "Cash Flow Score"] = _cash_flow_score(
            dem, stab, comp, marg, row,
        )
        out.at[idx, "Profit Score"] = _profit_score(
            dem, stab, comp, marg, row,
        )
        out.at[idx, "Balanced Score"] = _balanced_score(
            dem, stab, comp, marg, row,
        )
        out.at[idx, "Opportunity Lane"] = lane
        out.at[idx, "Commercial Priority"] = priority
        out.at[idx, "Lane Reason"] = lane_reason
        out.at[idx, "Monthly Gross Profit"] = monthly_gross
        out.at[idx, "Price Compression"] = _price_compression(row)
        out.at[idx, "Verdict"] = verdict
        out.at[idx, "Verdict Reason"] = reason

    return out


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper. No config keys today; reserved for
    future per-niche threshold/weight overrides.
    """
    return compute_scoring(df)
