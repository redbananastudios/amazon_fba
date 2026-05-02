"""Final opportunity validation — operator-facing verdict per row.

HANDOFF: Add Final Opportunity Validation. Runs as the
``07_validate_opportunity`` step after candidate scoring + decide,
before output. Pure additive: never changes the SHORTLIST/REVIEW/
REJECT decision. Adds an answer to the question:

    "Is this product actually worth acting on now?"

Verdicts (first match wins, KILL → SOURCE_ONLY → BUY → NEGOTIATE → WATCH):

    KILL          Do not pursue. Hard rejects (REJECT, profit < 0,
                  FBA-ineligible, Amazon dominates BB, severe
                  instability or BSR decline). Gating alone is NOT
                  a KILL — gated rows route to WATCH / SOURCE_ONLY
                  with the ungate link surfaced.

    SOURCE_ONLY   Product looks worth selling but the cost is unknown
                  (Keepa-discovery rows without buy_cost). Find a
                  supplier, validate cost, rerun.

    BUY           Every BUY gate passes. Buy now / pursue immediately.

    NEGOTIATE     Strong demand + currently profitable but conservative
                  profit below threshold. Price the supplier down.

    WATCH         Default: not safe today but worth monitoring.

Each verdict carries:
  - ``opportunity_verdict`` — one of the labels above
  - ``opportunity_score`` — 0-100, independent of verdict
  - ``opportunity_confidence`` — HIGH / MEDIUM / LOW
  - ``opportunity_reasons`` — list[str] short contributors
  - ``opportunity_blockers`` — list[str] (KILL reasons or BUY blockers)
  - ``next_action`` — verbatim from the verdict map (operator playbook)

Logic kept here in the shared lib so both ``supplier_pricelist`` and
``keepa_niche`` strategies share the same rules. Wrapper at
``fba_engine/steps/validate_opportunity.py``.
"""
from __future__ import annotations

from typing import Any, Optional

from fba_config_loader import OpportunityValidation, get_opportunity_validation


# ────────────────────────────────────────────────────────────────────────
# Verdict / next-action map
# ────────────────────────────────────────────────────────────────────────


VERDICT_BUY = "BUY"
VERDICT_SOURCE_ONLY = "SOURCE_ONLY"
VERDICT_NEGOTIATE = "NEGOTIATE"
VERDICT_WATCH = "WATCH"
VERDICT_KILL = "KILL"

VERDICTS = (VERDICT_BUY, VERDICT_SOURCE_ONLY, VERDICT_NEGOTIATE, VERDICT_WATCH, VERDICT_KILL)

# Operator playbook — keyed by verdict. Stored here so the next_action
# text never drifts between strategies.
NEXT_ACTIONS: dict[str, str] = {
    VERDICT_BUY: "Check live price, confirm stock, place test order",
    VERDICT_SOURCE_ONLY: "Find supplier, validate buy cost, then rerun",
    VERDICT_NEGOTIATE: "Negotiate supplier price below max buy price",
    VERDICT_WATCH: "Monitor price, seller count, and Buy Box movement",
    VERDICT_KILL: "Do not pursue",
}

# Sort priority for output writers — BUY first, KILL last.
VERDICT_SORT_PRIORITY: dict[str, int] = {
    VERDICT_BUY: 0,
    VERDICT_SOURCE_ONLY: 1,
    VERDICT_NEGOTIATE: 2,
    VERDICT_WATCH: 3,
    VERDICT_KILL: 4,
}


# ────────────────────────────────────────────────────────────────────────
# Pure helpers
# ────────────────────────────────────────────────────────────────────────


def _is_present(value: Any) -> bool:
    """True when value is "really" set — not None, not NaN, not empty string."""
    if value is None:
        return False
    # NaN check without importing pandas — `value != value` catches floats.
    if isinstance(value, float) and value != value:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Coerce a value to float; return ``default`` for None / NaN / non-numeric."""
    if not _is_present(value):
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n:   # NaN survived
        return default
    return n


def _flags_set(row: dict) -> set[str]:
    """Return the row's risk_flags as a set, regardless of how it's stored."""
    raw = row.get("risk_flags")
    if isinstance(raw, list):
        return {str(f) for f in raw if f}
    if isinstance(raw, str):
        return {f.strip() for f in raw.replace(",", ";").split(";") if f.strip()}
    return set()


def _bool(value: Any) -> Optional[bool]:
    """Coerce a Y/N/true/false-style value to bool. None on missing/unclear."""
    if not _is_present(value):
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("y", "yes", "true", "1"):
        return True
    if s in ("n", "no", "false", "0"):
        return False
    return None


# ────────────────────────────────────────────────────────────────────────
# Confidence
# ────────────────────────────────────────────────────────────────────────


# Critical fields the validator needs to feel confident. Missing any of
# these drops opportunity_confidence; missing many drops it to LOW.
#
# Strategy availability:
#   supplier_pricelist  → all 7 populate (sales_estimate from Keepa
#                         CSV; profit/ROI from calculate; bb_pct +
#                         oos + volatility from history wiring)
#   keepa_niche         → similar coverage
#   legacy phase-only   → may lack history fields (older runs predate
#                         PRs #54-#55) → expect MEDIUM most of the time
_CONFIDENCE_CRITICAL_FIELDS = (
    "sales_estimate",
    "profit_conservative",
    "roi_conservative",
    "amazon_bb_pct_90",
    "buy_box_oos_pct_90",
    "price_volatility_90d",
    "fba_seller_count",
)


def _opportunity_confidence(row: dict) -> tuple[str, list[str]]:
    """Compute opportunity_confidence + reasons for missing inputs.

    HIGH:   all critical fields present + data_confidence is HIGH (when set)
    MEDIUM: 1-2 critical fields missing OR data_confidence is MEDIUM
    LOW:    3+ critical fields missing OR data_confidence is LOW
    """
    missing = [f for f in _CONFIDENCE_CRITICAL_FIELDS if not _is_present(row.get(f))]
    upstream_conf = str(row.get("data_confidence") or "").upper().strip()

    reasons: list[str] = []
    if missing:
        reasons.append(f"missing: {','.join(missing)}")
    if upstream_conf and upstream_conf != "HIGH":
        reasons.append(f"data_confidence={upstream_conf}")

    if len(missing) >= 3 or upstream_conf == "LOW":
        return "LOW", reasons
    if len(missing) >= 1 or upstream_conf == "MEDIUM":
        return "MEDIUM", reasons
    return "HIGH", reasons


# ────────────────────────────────────────────────────────────────────────
# Opportunity score (0-100)
# ────────────────────────────────────────────────────────────────────────


def _score_demand_reliability(row: dict) -> tuple[int, str]:
    sales = _num(row.get("sales_estimate"), 0) or 0
    if sales >= 200:
        return 25, f"sales={int(sales)}/mo→25"
    if sales >= 100:
        return 20, f"sales={int(sales)}/mo→20"
    if sales >= 50:
        return 10, f"sales={int(sales)}/mo→10"
    if sales >= 20:
        return 5, f"sales={int(sales)}/mo→5"
    return 0, f"sales={int(sales)}/mo→0"


def _score_profit_quality(row: dict, cfg: OpportunityValidation) -> tuple[int, str]:
    roi = _num(row.get("roi_conservative"))
    profit = _num(row.get("profit_conservative"))
    profit_current = _num(row.get("profit_current"))

    if roi is not None and profit is not None:
        if roi >= 0.50 and profit >= 8.0:
            return 25, f"ROI={roi:.0%}+£{profit:.2f}→25"
        if roi >= 0.30 and profit >= 4.0:
            return 20, f"ROI={roi:.0%}+£{profit:.2f}→20"
        if roi >= cfg.min_roi_buy and profit >= cfg.min_profit_absolute_buy:
            return 15, f"ROI={roi:.0%}+£{profit:.2f}→15"
    # No conservative data, but current profit is real → partial credit.
    if profit_current is not None and profit_current > 0:
        return 8, f"profit_current=£{profit_current:.2f}→8"
    return 0, "no profit signal→0"


def _score_competition_safety(row: dict, cfg: OpportunityValidation) -> tuple[int, str]:
    bb_share = _num(row.get("amazon_bb_pct_90"))
    fba = _num(row.get("fba_seller_count"))
    sales = _num(row.get("sales_estimate"), 0) or 0

    if bb_share is None:
        return 0, "amazon_bb_pct_90 missing→0"

    healthy_seller_count = _is_seller_count_healthy(fba, sales, cfg)
    if bb_share < cfg.max_amazon_bb_share_buy and healthy_seller_count:
        return 20, f"AMZ BB={bb_share:.0%}+sellers ok→20"
    if bb_share < cfg.max_amazon_bb_share_watch:
        return 10, f"AMZ BB={bb_share:.0%}→10"
    return 0, f"AMZ BB={bb_share:.0%}→0"


def _score_price_stability(row: dict) -> tuple[int, str]:
    vol = _num(row.get("price_volatility_90d"))
    oos = _num(row.get("buy_box_oos_pct_90"))
    if vol is None and oos is None:
        return 0, "no stability data→0"
    vol_v = vol if vol is not None else 1.0
    oos_v = oos if oos is not None else 1.0
    if vol_v < 0.10 and oos_v < 0.05:
        return 15, "stable→15"
    if vol_v < 0.20 and oos_v < 0.15:
        return 10, "moderate→10"
    if vol_v < 0.35 and oos_v < 0.30:
        return 5, "wobbly→5"
    return 0, "unstable→0"


def _score_operational_safety(row: dict) -> tuple[int, str]:
    restriction = str(row.get("restriction_status") or "").upper().strip()
    fba_eligible = _bool(row.get("fba_eligible"))
    gated = str(row.get("gated") or "").upper().strip()
    flags = _flags_set(row)
    major_flags = {
        "PRICE_FLOOR_HIT", "PRICE_UNSTABLE", "BSR_DECLINING",
        "VAT_UNCLEAR", "VAT_FIELD_MISMATCH",
    }
    has_major = bool(flags & major_flags)

    if fba_eligible is False:
        return 0, "FBA-ineligible→0"
    if (
        restriction in ("UNRESTRICTED", "")
        and (fba_eligible is None or fba_eligible is True)
        and not has_major
    ):
        return 15, "ungated+fba→15"
    # Gating (BRAND_GATED, RESTRICTED) reduces the operational score
    # but doesn't zero it out — operator may hold a brand letter or
    # be willing to apply for ungating.
    if (
        gated in ("Y", "BRAND_GATED")
        or restriction in ("BRAND_GATED", "RESTRICTED")
    ):
        return 7, "gated→7"
    return 5, "minor concerns→5"


def _calculate_opportunity_score(
    row: dict, cfg: OpportunityValidation,
) -> tuple[int, list[str]]:
    parts: list[tuple[int, str]] = [
        _score_demand_reliability(row),
        _score_profit_quality(row, cfg),
        _score_competition_safety(row, cfg),
        _score_price_stability(row),
        _score_operational_safety(row),
    ]
    total = sum(p[0] for p in parts)
    reasons = [p[1] for p in parts]
    return min(100, max(0, total)), reasons


# ────────────────────────────────────────────────────────────────────────
# Verdict gates
# ────────────────────────────────────────────────────────────────────────


def _is_seller_count_healthy(
    fba: Optional[float], sales: float, cfg: OpportunityValidation,
) -> bool:
    """FBA seller count vs sales scale. None fba treated as healthy
    (we can't disqualify on missing data — confidence handles that)."""
    if fba is None:
        return True
    if sales >= 200:
        return fba <= cfg.max_fba_sellers_200_sales
    if sales >= 100:
        return fba <= cfg.max_fba_sellers_100_sales
    return fba <= cfg.max_fba_sellers_low_sales


def _check_kill(
    row: dict, cfg: OpportunityValidation,
) -> tuple[bool, list[str]]:
    """Return (is_kill, reasons). KILL when ANY reason fires."""
    reasons: list[str] = []
    decision = str(row.get("decision") or "").upper().strip()
    if decision == "REJECT":
        reasons.append("decision=REJECT")
    sales = _num(row.get("sales_estimate"))
    if sales is not None and sales < cfg.kill_min_sales:
        reasons.append(f"sales={int(sales)} < kill_min_sales={cfg.kill_min_sales}")
    profit = _num(row.get("profit_conservative"))
    if profit is not None and profit < 0:
        reasons.append(f"profit_conservative={profit:.2f} < 0")
    roi = _num(row.get("roi_conservative"))
    if roi is not None and roi < cfg.kill_min_roi:
        reasons.append(f"roi_conservative={roi:.2%} < kill_min_roi={cfg.kill_min_roi:.0%}")

    flags = _flags_set(row)
    if "PRICE_FLOOR_HIT" in flags:
        reasons.append("PRICE_FLOOR_HIT flag")

    # Severe = beyond the BUY threshold (i.e. above kill_*) — flags
    # alone (which fire at the REVIEW threshold) are NOT enough.
    vol = _num(row.get("price_volatility_90d"))
    if vol is not None and vol >= cfg.kill_price_volatility:
        reasons.append(
            f"price_volatility={vol:.2f} ≥ kill={cfg.kill_price_volatility:.2f}"
        )
    bsr = _num(row.get("bsr_slope_90d"))
    if bsr is not None and bsr >= cfg.kill_bsr_decline_slope:
        reasons.append(
            f"bsr_slope={bsr:.3f} ≥ kill={cfg.kill_bsr_decline_slope:.3f}"
        )

    bb_share = _num(row.get("amazon_bb_pct_90"))
    if bb_share is not None and bb_share >= cfg.kill_amazon_bb_share:
        reasons.append(
            f"amazon_bb_share={bb_share:.0%} ≥ kill={cfg.kill_amazon_bb_share:.0%}"
        )

    # Gating (BRAND_GATED, RESTRICTED) is NOT a KILL — operator may
    # already hold a brand letter, may apply for ungating, or may
    # consider FBM. Gated rows route to WATCH / SOURCE_ONLY (still
    # surfaced) and BUY remains blocked unless allow_*_buy is set.
    fba_elig = _bool(row.get("fba_eligible"))
    if fba_elig is False:
        # Hard exclusion: can't fulfil via FBA at all. Kill.
        reasons.append("fba_eligible=False")

    return bool(reasons), reasons


def _is_demand_strong_for_source_only(
    row: dict, cfg: OpportunityValidation,
) -> bool:
    sales = _num(row.get("sales_estimate"), 0) or 0
    if sales < cfg.source_only_min_sales:
        return False
    cscore = _num(row.get("candidate_score"), 0) or 0
    if cscore < cfg.source_only_min_candidate_score:
        return False
    cconf = str(row.get("data_confidence") or "").upper().strip()
    if cconf == "LOW":
        return False
    bb_share = _num(row.get("amazon_bb_pct_90"))
    if bb_share is not None and bb_share >= cfg.source_only_max_amazon_bb_share:
        return False
    vol = _num(row.get("price_volatility_90d"))
    if vol is not None and vol > cfg.source_only_max_volatility:
        return False
    return True


def _has_buy_cost(row: dict) -> bool:
    """A buy_cost is "present" when it's >0. The wholesale flow uses
    0.0 as the load-bearing 'no supplier yet' sentinel — see
    CLAUDE.md note on ``buy_cost = 0.0`` convention."""
    bc = _num(row.get("buy_cost"))
    return bc is not None and bc > 0


def _check_buy(
    row: dict, cfg: OpportunityValidation,
) -> tuple[bool, list[str]]:
    """Return (is_buy, blockers). BUY when zero blockers."""
    blockers: list[str] = []
    decision = str(row.get("decision") or "").upper().strip()
    if decision != "SHORTLIST":
        blockers.append(f"decision={decision} (need SHORTLIST)")

    cscore = _num(row.get("candidate_score"))
    if cscore is None or cscore < cfg.min_candidate_score_buy:
        blockers.append(
            f"candidate_score={cscore} < {cfg.min_candidate_score_buy}"
        )

    cconf = str(row.get("data_confidence") or "").upper().strip()
    if cfg.min_data_confidence_buy == "HIGH" and cconf != "HIGH":
        blockers.append(f"data_confidence={cconf or 'unknown'} (need HIGH)")

    sales = _num(row.get("sales_estimate"))
    if sales is None or sales < cfg.target_monthly_sales:
        blockers.append(
            f"sales_estimate={sales} < {cfg.target_monthly_sales}"
        )

    profit = _num(row.get("profit_conservative"))
    if profit is None or profit < cfg.min_profit_absolute_buy:
        blockers.append(
            f"profit_conservative={profit} < £{cfg.min_profit_absolute_buy}"
        )

    roi = _num(row.get("roi_conservative"))
    if roi is None or roi < cfg.min_roi_buy:
        blockers.append(f"roi_conservative={roi} < {cfg.min_roi_buy:.0%}")

    bb_share = _num(row.get("amazon_bb_pct_90"))
    if bb_share is not None and bb_share > cfg.max_amazon_bb_share_buy:
        blockers.append(
            f"amazon_bb_share={bb_share:.0%} > {cfg.max_amazon_bb_share_buy:.0%}"
        )

    vol = _num(row.get("price_volatility_90d"))
    if vol is not None and vol > cfg.max_price_volatility_buy:
        blockers.append(
            f"price_volatility={vol:.2f} > {cfg.max_price_volatility_buy:.2f}"
        )

    oos = _num(row.get("buy_box_oos_pct_90"))
    if oos is not None and oos > cfg.max_buy_box_oos_buy:
        blockers.append(
            f"buy_box_oos={oos:.1%} > {cfg.max_buy_box_oos_buy:.0%}"
        )

    joiners = _num(row.get("fba_offer_count_90d_joiners"))
    if joiners is not None and joiners > cfg.max_competition_joiners_buy:
        blockers.append(
            f"joiners={int(joiners)} > {cfg.max_competition_joiners_buy}"
        )

    fba = _num(row.get("fba_seller_count"))
    if not _is_seller_count_healthy(fba, sales or 0, cfg):
        blockers.append(f"fba_seller_count={fba} above ceiling for sales scale")

    restriction = str(row.get("restriction_status") or "").upper().strip()
    if (
        restriction not in ("", "UNRESTRICTED")
        and not cfg.allow_restricted_buy
    ):
        blockers.append(f"restriction_status={restriction}")

    fba_elig = _bool(row.get("fba_eligible"))
    if fba_elig is False:
        blockers.append("fba_eligible=False")

    gated = str(row.get("gated") or "").upper().strip()
    if gated == "Y" and not cfg.allow_gated_buy:
        blockers.append("gated=Y (allow_gated_buy=false)")

    if not _has_buy_cost(row):
        blockers.append("buy_cost missing — try SOURCE_ONLY first")

    return (not blockers), blockers


def _check_negotiate(
    row: dict, cfg: OpportunityValidation,
) -> bool:
    """NEGOTIATE = strong demand + currently profitable but conservative
    profit below threshold."""
    sales = _num(row.get("sales_estimate"), 0) or 0
    if sales < cfg.negotiate_min_sales:
        return False
    cscore = _num(row.get("candidate_score"), 0) or 0
    if cscore < cfg.negotiate_min_candidate_score:
        return False
    profit_current = _num(row.get("profit_current"))
    if profit_current is None or profit_current <= 0:
        return False
    profit_cons = _num(row.get("profit_conservative"))
    if profit_cons is None or profit_cons >= cfg.min_profit_absolute_buy:
        return False
    return True


# ────────────────────────────────────────────────────────────────────────
# Required-buy-cost helper (used for NEGOTIATE next-action context)
# ────────────────────────────────────────────────────────────────────────


def predict_seller_velocity(row: dict) -> Optional[dict[str, int]]:
    """Predict units/month a new entrant would sell on this listing.

    Returns ``{"low": int, "mid": int, "high": int}`` or None when
    inputs are insufficient.

    Model:
        non_amazon_sales = sales × (1 - amazon_bb_pct_90)
        equal_share     = non_amazon_sales / fba_seller_count
        low             = equal_share × 0.30   (worst-priced / OOS often)
        mid             = equal_share × 1.00   (equal BB rotation)
        high            = equal_share × 1.50   (best-priced / always stocked)

    The "sales" input is `min(sales_estimate, bsr_drops_30d × 1.5)`
    when both are present and `bsr_drops_30d` is materially below
    `sales_estimate`. The 1.5 multiplier on BSR drops covers the
    fact that some sales don't move the rank visibly; using the raw
    BSR-drop count alone slightly under-counts. Real-world: niche
    listings where Keepa's `monthlySold` over-estimates by 3× —
    this caps the prediction at the conservative number.

    Adjustment factors:
        joiners >= 3 in 90d           : multiplier × 0.7 (share dilutes)
        oos_pct_90 > 0.10             : multiplier × 1.15 (latent demand)
        bsr_slope_90d < -flat_thresh  : multiplier × 1.10 (improving)
        bsr_slope_90d > +flat_thresh  : multiplier × 0.85 (declining)

    Returns None when:
        - sales signal absent (both sales_estimate and bsr_drops_30d None)
        - fba_seller_count missing or zero
    """
    sales_est = _num(row.get("sales_estimate"))
    bsr_drops = _num(row.get("bsr_drops_30d"))

    # Pick the conservative sales signal. BSR drops × 1.5 covers the
    # under-counting for sales that don't move the rank visibly.
    bsr_proxy = bsr_drops * 1.5 if bsr_drops is not None else None
    if sales_est is None and bsr_proxy is None:
        return None
    if sales_est is None:
        sales = bsr_proxy
    elif bsr_proxy is None:
        sales = sales_est
    else:
        # Use min — the operator-observed pattern (B0B636ZKZQ
        # calibration: Keepa monthlySold=70 vs BSR drops=25) shows
        # Keepa's model over-estimates niche listings. Conservative
        # is right for sourcing decisions: better to under-promise
        # capacity than over-buy inventory.
        sales = min(sales_est, bsr_proxy)

    if sales is None or sales <= 0:
        return None

    fba = _num(row.get("fba_seller_count"))
    if fba is None or fba <= 0:
        return None

    bb_share = _num(row.get("amazon_bb_pct_90"))
    non_amazon_share = sales * (1.0 - (bb_share if bb_share is not None else 0.0))

    # PR G — share-aware path. When Keepa's per-seller buyBoxStats is
    # populated, the new entrant's expected share is the median of the
    # existing FBA sellers' percentageWon (a defensible "you'll perform
    # like a typical competitor here" assumption). Without it, fall
    # back to equal-split (1 / fba_seller_count).
    seller_stats = row.get("buy_box_seller_stats")
    entrant_share = None
    share_source = "equal-split"
    if isinstance(seller_stats, dict) and seller_stats:
        # Filter to non-Amazon FBA sellers' percentageWon — those are
        # the entrant's competitors. Amazon's percentageWon is
        # captured separately by amazon_bb_pct_90.
        fba_sellers_pct: list[float] = []
        for sid, stats in seller_stats.items():
            if not isinstance(stats, dict):
                continue
            if not stats.get("isFBA"):
                continue
            if str(sid).upper().startswith("AMAZON") or sid in ("AMZN",):
                continue
            pct = stats.get("percentageWon")
            try:
                pct_f = float(pct) / 100.0 if pct is not None else None
            except (TypeError, ValueError):
                continue
            if pct_f is not None and pct_f > 0:
                fba_sellers_pct.append(pct_f)
        if fba_sellers_pct:
            # Median of the existing competitors' shares is the
            # entrant's "expected" performance. Smaller than the max
            # (top performer is hard to dethrone), larger than the
            # tail (operator typically prices to compete, not to lose).
            sorted_pct = sorted(fba_sellers_pct)
            n = len(sorted_pct)
            entrant_share = (
                sorted_pct[n // 2] if n % 2 == 1
                else (sorted_pct[n // 2 - 1] + sorted_pct[n // 2]) / 2
            )
            share_source = f"median-of-{n}-sellers"

    if entrant_share is not None:
        equal_share = sales * entrant_share
    else:
        equal_share = non_amazon_share / fba

    multiplier = 1.0
    joiners = _num(row.get("fba_offer_count_90d_joiners"))
    if joiners is not None and joiners >= 3:
        multiplier *= 0.7
    oos = _num(row.get("buy_box_oos_pct_90"))
    if oos is not None and oos > 0.10:
        multiplier *= 1.15
    bsr_slope = _num(row.get("bsr_slope_90d"))
    if bsr_slope is not None:
        if bsr_slope < -0.05:
            multiplier *= 1.10
        elif bsr_slope > 0.05:
            multiplier *= 0.85

    mid = equal_share * multiplier
    low = mid * 0.30
    high = mid * 1.50

    # Cap high at the total non-Amazon sales — a single seller can't
    # take more than 100% of the BB rotation.
    high = min(high, non_amazon_share)

    return {
        "low": max(0, int(round(low))),
        "mid": max(0, int(round(mid))),
        "high": max(0, int(round(high))),
        "share_source": share_source,
    }


def _required_buy_cost(row: dict, cfg: OpportunityValidation) -> Optional[float]:
    """Maximum buy_cost that would clear both ROI and profit gates.

    Uses fees_conservative + raw_conservative_price when available so
    the operator sees the supplier-negotiation ceiling. None when we
    can't compute it (missing inputs)."""
    sell = _num(row.get("raw_conservative_price"))
    if sell is None or sell <= 0:
        sell = _num(row.get("market_price"))
    if sell is None or sell <= 0:
        return None
    fees = _num(row.get("fees_conservative"))
    if fees is None:
        fees = _num(row.get("fees_current"))
    if fees is None:
        return None
    breakeven = sell - fees
    # Cap by both min profit and min ROI:
    # roi = profit / buy_cost ≥ min_roi  ⇒  buy_cost ≤ (sell - fees) / (1 + min_roi)
    roi_cap = breakeven / (1 + cfg.min_roi_buy)
    profit_cap = breakeven - cfg.min_profit_absolute_buy
    cap = min(roi_cap, profit_cap)
    return max(0.0, round(cap, 2))


# ────────────────────────────────────────────────────────────────────────
# Public entry point
# ────────────────────────────────────────────────────────────────────────


def validate_opportunity(
    row: dict, *, config: OpportunityValidation | None = None,
) -> dict:
    """Compute the opportunity verdict + score for one row.

    Pure function. Reads from `row` via `.get()` only — never mutates.
    Returns a dict with the 6 new columns; caller merges into the row.
    Missing fields never raise; they degrade confidence and route to
    WATCH unless they were the cause of a hard KILL.
    """
    cfg = config or get_opportunity_validation()
    score, score_reasons = _calculate_opportunity_score(row, cfg)
    confidence, confidence_reasons = _opportunity_confidence(row)
    velocity = predict_seller_velocity(row)
    velocity_low = velocity["low"] if velocity else None
    velocity_mid = velocity["mid"] if velocity else None
    velocity_high = velocity["high"] if velocity else None
    velocity_share_source = (
        velocity.get("share_source") if velocity else None
    )

    velocity_fields = {
        "predicted_velocity_low": velocity_low,
        "predicted_velocity_mid": velocity_mid,
        "predicted_velocity_high": velocity_high,
        # PR G — "equal-split" or "median-of-N-sellers". Surfaces
        # whether the prediction used real per-seller BB share data
        # (from Keepa stats.buyBoxStats) or fell back to the equal
        # split. Operator can spot which assumption was used.
        "predicted_velocity_share_source": velocity_share_source,
    }

    # 1. KILL — always wins.
    is_kill, kill_reasons = _check_kill(row, cfg)
    if is_kill:
        return {
            "opportunity_verdict": VERDICT_KILL,
            "opportunity_score": score,
            "opportunity_confidence": confidence,
            "opportunity_reasons": score_reasons,
            "opportunity_blockers": kill_reasons,
            "next_action": NEXT_ACTIONS[VERDICT_KILL],
            **velocity_fields,
        }

    # 2. SOURCE_ONLY — strong demand, no buy_cost yet. Check BEFORE BUY
    #    because BUY requires buy_cost and we don't want a missing-cost
    #    row to fall through to WATCH when SOURCE_ONLY fits.
    if not _has_buy_cost(row) and _is_demand_strong_for_source_only(row, cfg):
        reasons = score_reasons + ["buy_cost missing — find supplier"]
        return {
            "opportunity_verdict": VERDICT_SOURCE_ONLY,
            "opportunity_score": score,
            "opportunity_confidence": confidence,
            "opportunity_reasons": reasons,
            "opportunity_blockers": [],
            "next_action": NEXT_ACTIONS[VERDICT_SOURCE_ONLY],
            **velocity_fields,
        }

    # 3. BUY — every gate passes.
    is_buy, buy_blockers = _check_buy(row, cfg)
    if is_buy:
        return {
            "opportunity_verdict": VERDICT_BUY,
            "opportunity_score": score,
            "opportunity_confidence": confidence,
            "opportunity_reasons": score_reasons,
            "opportunity_blockers": [],
            "next_action": NEXT_ACTIONS[VERDICT_BUY],
            **velocity_fields,
        }

    # 4. NEGOTIATE — strong demand + current profit + weak conservative.
    if _check_negotiate(row, cfg):
        ceiling = _required_buy_cost(row, cfg)
        reasons = list(score_reasons)
        if ceiling is not None:
            reasons.append(f"max_buy_cost=£{ceiling:.2f}")
        return {
            "opportunity_verdict": VERDICT_NEGOTIATE,
            "opportunity_score": score,
            "opportunity_confidence": confidence,
            "opportunity_reasons": reasons,
            "opportunity_blockers": buy_blockers,
            "next_action": NEXT_ACTIONS[VERDICT_NEGOTIATE],
            **velocity_fields,
        }

    # 5. WATCH — default.
    return {
        "opportunity_verdict": VERDICT_WATCH,
        "opportunity_score": score,
        "opportunity_confidence": confidence,
        "opportunity_reasons": score_reasons,
        "opportunity_blockers": buy_blockers,
        "next_action": NEXT_ACTIONS[VERDICT_WATCH],
        **velocity_fields,
    }
