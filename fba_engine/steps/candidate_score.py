"""Candidate scoring step — data-driven product strength + data confidence.

HANDOFF_candidate_validation.md WS3. Runs **after** ``calculate`` and
**before** ``decide``. Pure additive — does NOT alter the
SHORTLIST/REVIEW/REJECT decision. Adds five output columns:

    candidate_score          int 0-100
    candidate_band           STRONG | OK | WEAK | FAIL
    candidate_reasons        list[str] — short contributors
    data_confidence          HIGH | MEDIUM | LOW
    data_confidence_reasons  list[str] — missing inputs

The two scores are independent on purpose:

    STRONG / HIGH   → operator can act with confidence
    STRONG / LOW    → score might be right, but trust it less
    WEAK / HIGH     → score is right and the product is weak
    WEAK / LOW      → not enough data to say either way

Why a separate step?
- ``scoring.py`` exists already but is keepa_niche-specific (lane
  verdicts, niche-tied weights). This step runs across all
  strategies and consumes the canonical history fields added in
  PRs #54 + #55.
- All numeric thresholds live in
  ``shared/config/decision_thresholds.yaml::candidate_scoring`` —
  every band edge can be tuned without touching code.

Inputs read from each row (none required — every absence becomes
``data_confidence_reasons`` text and a 0 contribution to the
relevant dimension):

    sales_estimate                — Demand
    bsr_slope_90d                 — Demand
    review_velocity_90d           — Demand
    buy_box_oos_pct_90            — Stability
    price_volatility_90d          — Stability
    listing_age_days              — Stability
    fba_seller_count              — Competition
    fba_offer_count_90d_joiners   — Competition
    amazon_bb_pct_90              — Competition
    roi_conservative              — Margin
    profit_conservative           — Margin

Plus for data_confidence:
    rating, review_count, history_days
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from fba_config_loader import _find_config_dir, _load_yaml

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CandidateScoringConfig:
    """All candidate-score thresholds. Loaded from
    ``decision_thresholds.yaml::candidate_scoring`` via
    ``load_candidate_scoring_config``."""

    band_strong: int
    band_ok: int
    band_weak: int

    sales_tier_thresholds: tuple[int, ...]
    sales_tier_points: tuple[int, ...]
    bsr_flat_threshold: float
    bsr_improving_points: int
    bsr_flat_points: int
    bsr_declining_points: int
    review_rising_points: int
    review_flat_points: int
    review_falling_points: int

    oos_pct_thresholds: tuple[float, ...]
    oos_points: tuple[int, ...]
    volatility_thresholds: tuple[float, ...]
    volatility_points: tuple[int, ...]
    age_thresholds: tuple[int, ...]
    age_points: tuple[int, ...]

    seller_ceiling_table: tuple[tuple[int, int], ...]   # (min_sales, ceiling)
    seller_ceiling_below_points: int
    seller_ceiling_at_points: int
    seller_ceiling_over_points: int
    joiner_thresholds: tuple[int, ...]
    joiner_points: tuple[int, ...]
    bb_share_warn_pct: float
    bb_share_block_pct: float
    bb_share_lt_warn_points: int
    bb_share_lt_block_points: int
    bb_share_lt_total_points: int
    bb_share_at_max_points: int

    roi_thresholds: tuple[float, ...]
    roi_points: tuple[int, ...]
    profit_thresholds: tuple[float, ...]
    profit_points: tuple[int, ...]

    confidence_high_history_days: int
    confidence_medium_history_days: int
    confidence_required_fields: tuple[str, ...]


_DEFAULT_CONFIG: Optional[CandidateScoringConfig] = None


def load_candidate_scoring_config(
    config_dir: Any | None = None,
) -> CandidateScoringConfig:
    """Load candidate_scoring + relevant data_signals from canonical YAML.

    Default-path loads are cached in a module-level slot (subsequent
    no-arg calls reuse). Passing ``config_dir`` always re-reads from
    that path and does NOT touch the cache — useful for tests and
    multi-config callers. Use ``reset_config_cache()`` to force a
    re-read of the canonical path.
    """
    global _DEFAULT_CONFIG
    if config_dir is None and _DEFAULT_CONFIG is not None:
        return _DEFAULT_CONFIG
    cd = _find_config_dir(config_dir)
    raw = _load_yaml(cd / "decision_thresholds.yaml")
    cs = raw.get("candidate_scoring") or {}
    ds = raw.get("data_signals") or {}

    demand = cs.get("demand") or {}
    stability = cs.get("stability") or {}
    competition = cs.get("competition") or {}
    margin = cs.get("margin") or {}
    bands = cs.get("bands") or {}
    confidence = cs.get("data_confidence") or {}

    # Seller-ceiling table: list of {min_sales, ceiling} → tuple of pairs
    # sorted by min_sales descending (so we walk from highest threshold
    # down and pick the first that the row's sales meets).
    seller_table_raw = competition.get("seller_ceiling_table") or []
    seller_table = tuple(
        sorted(
            ((int(e["min_sales"]), int(e["ceiling"])) for e in seller_table_raw),
            key=lambda p: -p[0],
        )
    )

    cfg = CandidateScoringConfig(
        band_strong=int(bands.get("strong", 75)),
        band_ok=int(bands.get("ok", 50)),
        band_weak=int(bands.get("weak", 25)),

        sales_tier_thresholds=tuple(
            int(x) for x in (demand.get("sales_tier_thresholds") or [200, 100, 50, 20])
        ),
        sales_tier_points=tuple(
            int(x) for x in (demand.get("sales_tier_points") or [10, 7, 4, 2, 0])
        ),
        bsr_flat_threshold=float(demand.get("bsr_flat_threshold", 0.05)),
        bsr_improving_points=int(demand.get("bsr_improving_points", 10)),
        bsr_flat_points=int(demand.get("bsr_flat_points", 7)),
        bsr_declining_points=int(demand.get("bsr_declining_points", 0)),
        review_rising_points=int(demand.get("review_rising_points", 5)),
        review_flat_points=int(demand.get("review_flat_points", 2)),
        review_falling_points=int(demand.get("review_falling_points", 0)),

        oos_pct_thresholds=tuple(
            float(x) for x in (stability.get("oos_pct_thresholds") or [0.05, 0.15, 0.30])
        ),
        oos_points=tuple(
            int(x) for x in (stability.get("oos_points") or [10, 6, 2, 0])
        ),
        volatility_thresholds=tuple(
            float(x) for x in (stability.get("volatility_thresholds") or [0.10, 0.20, 0.35])
        ),
        volatility_points=tuple(
            int(x) for x in (stability.get("volatility_points") or [10, 6, 2, 0])
        ),
        age_thresholds=tuple(
            int(x) for x in (stability.get("age_thresholds") or [730, 365, 180])
        ),
        age_points=tuple(
            int(x) for x in (stability.get("age_points") or [5, 3, 1, 0])
        ),

        seller_ceiling_table=seller_table or (
            (500, 20), (200, 12), (100, 8), (50, 5), (0, 3),
        ),
        seller_ceiling_below_points=int(competition.get("seller_ceiling_below_points", 10)),
        seller_ceiling_at_points=int(competition.get("seller_ceiling_at_points", 5)),
        seller_ceiling_over_points=int(competition.get("seller_ceiling_over_points", 0)),
        joiner_thresholds=tuple(
            int(x) for x in (competition.get("joiner_thresholds") or [2, 5, 10])
        ),
        joiner_points=tuple(
            int(x) for x in (competition.get("joiner_points") or [10, 6, 2, 0])
        ),
        bb_share_warn_pct=float(ds.get("amazon_bb_share_warn_pct", 0.30)),
        bb_share_block_pct=float(ds.get("amazon_bb_share_block_pct", 0.70)),
        bb_share_lt_warn_points=int(competition.get("bb_share_lt_warn_points", 5)),
        bb_share_lt_block_points=int(competition.get("bb_share_lt_block_points", 3)),
        bb_share_lt_total_points=int(competition.get("bb_share_lt_total_points", 1)),
        bb_share_at_max_points=int(competition.get("bb_share_at_max_points", 0)),

        roi_thresholds=tuple(
            float(x) for x in (margin.get("roi_thresholds") or [0.50, 0.30, 0.20])
        ),
        roi_points=tuple(
            int(x) for x in (margin.get("roi_points") or [15, 10, 5, 0])
        ),
        profit_thresholds=tuple(
            float(x) for x in (margin.get("profit_thresholds") or [8.0, 4.0, 2.50])
        ),
        profit_points=tuple(
            int(x) for x in (margin.get("profit_points") or [10, 6, 3, 0])
        ),

        confidence_high_history_days=int(confidence.get("high_history_days", 90)),
        confidence_medium_history_days=int(confidence.get("medium_history_days", 30)),
        confidence_required_fields=tuple(
            confidence.get("required_fields") or [
                "rating", "review_count", "fba_seller_count",
                "sales_estimate", "buy_box_oos_pct_90",
            ]
        ),
    )
    _validate_tier_arrays(cfg)
    if config_dir is None:
        _DEFAULT_CONFIG = cfg
    return cfg


def _validate_tier_arrays(cfg: CandidateScoringConfig) -> None:
    """Tier arrays must have ``len(points) == len(thresholds) + 1``.

    The trailing point is the below-all-thresholds default. A missing
    trailing entry made the worst-row fall through to a non-zero
    value (caught in initial test run; pinned here so future config
    edits can't reintroduce the same bug).
    """
    pairs = [
        ("sales_tier", cfg.sales_tier_thresholds, cfg.sales_tier_points),
        ("oos_pct", cfg.oos_pct_thresholds, cfg.oos_points),
        ("volatility", cfg.volatility_thresholds, cfg.volatility_points),
        ("age", cfg.age_thresholds, cfg.age_points),
        ("joiner", cfg.joiner_thresholds, cfg.joiner_points),
        ("roi", cfg.roi_thresholds, cfg.roi_points),
        ("profit", cfg.profit_thresholds, cfg.profit_points),
    ]
    for label, thr, pts in pairs:
        if len(pts) != len(thr) + 1:
            raise ValueError(
                f"candidate_scoring.{label}: len(points)={len(pts)} but "
                f"len(thresholds)={len(thr)}; expected len(points) == "
                f"len(thresholds) + 1 (trailing entry is the "
                f"below-all-thresholds default)."
            )


def reset_config_cache() -> None:
    """Clear the cached config. Useful in tests that mutate config files."""
    global _DEFAULT_CONFIG
    _DEFAULT_CONFIG = None


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────


def _pick_tier(
    value: float,
    thresholds: tuple[float, ...],
    points: tuple[int, ...],
    *,
    descending: bool = True,
) -> int:
    """Map a value to its tier's points.

    `descending=True` (default): bigger value = better. Walks
    `thresholds` from largest to smallest, returning `points[i]` at the
    first threshold the value clears. Falls through to last `points`.

    `descending=False`: smaller value = better. Walks `thresholds`
    smallest to largest; returns `points[i]` at the first threshold
    the value is BELOW. Falls through to last `points`.

    Both modes assume `len(points) == len(thresholds) + 1`. The
    handoff's tier specs are written this way.
    """
    if descending:
        for i, thr in enumerate(thresholds):
            if value >= thr:
                return points[i]
        return points[-1]
    for i, thr in enumerate(thresholds):
        if value < thr:
            return points[i]
    return points[-1]


def _seller_ceiling_for(
    sales_estimate: Optional[float],
    table: tuple[tuple[int, int], ...],
) -> int:
    """Look up the FBA seller ceiling for the given sales estimate."""
    sales = sales_estimate if sales_estimate is not None else 0
    for min_sales, ceiling in table:
        if sales >= min_sales:
            return ceiling
    return table[-1][1] if table else 3


# ────────────────────────────────────────────────────────────────────────
# Dimension scorers — each returns (points, contributors, missing_reasons)
# ────────────────────────────────────────────────────────────────────────


def _score_demand(
    row: dict, cfg: CandidateScoringConfig,
) -> tuple[int, list[str], list[str]]:
    contrib: list[str] = []
    missing: list[str] = []
    points = 0

    sales = row.get("sales_estimate")
    if sales is None:
        missing.append("sales_estimate")
    else:
        s = _pick_tier(
            sales, cfg.sales_tier_thresholds, cfg.sales_tier_points,
            descending=True,
        )
        contrib.append(f"sales={int(sales)}/mo→{s}")
        points += s

    bsr = row.get("bsr_slope_90d")
    if bsr is None:
        missing.append("bsr_slope_90d")
    else:
        if bsr < -cfg.bsr_flat_threshold:
            s = cfg.bsr_improving_points
            contrib.append(f"BSR improving→{s}")
        elif abs(bsr) <= cfg.bsr_flat_threshold:
            s = cfg.bsr_flat_points
            contrib.append(f"BSR flat→{s}")
        else:
            s = cfg.bsr_declining_points
            contrib.append(f"BSR declining→{s}")
        points += s

    rv = row.get("review_velocity_90d")
    if rv is None:
        missing.append("review_velocity_90d")
    else:
        if rv > 0:
            s = cfg.review_rising_points
            contrib.append(f"reviews rising (+{rv})→{s}")
        elif rv == 0:
            s = cfg.review_flat_points
            contrib.append(f"reviews flat→{s}")
        else:
            s = cfg.review_falling_points
            contrib.append(f"reviews falling ({rv})→{s}")
        points += s

    return points, contrib, missing


def _score_stability(
    row: dict, cfg: CandidateScoringConfig,
) -> tuple[int, list[str], list[str]]:
    contrib: list[str] = []
    missing: list[str] = []
    points = 0

    oos = row.get("buy_box_oos_pct_90")
    if oos is None:
        missing.append("buy_box_oos_pct_90")
    else:
        s = _pick_tier(
            oos, cfg.oos_pct_thresholds, cfg.oos_points,
            descending=False,
        )
        contrib.append(f"OOS={oos:.1%}→{s}")
        points += s

    vol = row.get("price_volatility_90d")
    if vol is None:
        missing.append("price_volatility_90d")
    else:
        s = _pick_tier(
            vol, cfg.volatility_thresholds, cfg.volatility_points,
            descending=False,
        )
        contrib.append(f"vol={vol:.2f}→{s}")
        points += s

    age = row.get("listing_age_days")
    if age is None:
        missing.append("listing_age_days")
    else:
        s = _pick_tier(
            age, cfg.age_thresholds, cfg.age_points,
            descending=True,
        )
        contrib.append(f"age={age}d→{s}")
        points += s

    return points, contrib, missing


def _score_competition(
    row: dict, cfg: CandidateScoringConfig,
) -> tuple[int, list[str], list[str]]:
    contrib: list[str] = []
    missing: list[str] = []
    points = 0

    fba = row.get("fba_seller_count")
    sales = row.get("sales_estimate")
    if fba is None:
        missing.append("fba_seller_count")
    else:
        ceiling = _seller_ceiling_for(sales, cfg.seller_ceiling_table)
        if fba < ceiling:
            s = cfg.seller_ceiling_below_points
            contrib.append(f"sellers={fba}<ceiling={ceiling}→{s}")
        elif fba == ceiling:
            s = cfg.seller_ceiling_at_points
            contrib.append(f"sellers={fba}=ceiling→{s}")
        else:
            s = cfg.seller_ceiling_over_points
            contrib.append(f"sellers={fba}>ceiling={ceiling}→{s}")
        points += s

    joiners = row.get("fba_offer_count_90d_joiners")
    if joiners is None:
        missing.append("fba_offer_count_90d_joiners")
    else:
        s = _pick_tier(
            joiners, cfg.joiner_thresholds, cfg.joiner_points,
            descending=False,  # smaller = better
        )
        contrib.append(f"joiners={joiners}→{s}")
        points += s

    bb_share = row.get("amazon_bb_pct_90")
    if bb_share is None:
        missing.append("amazon_bb_pct_90")
    else:
        if bb_share >= 1.0:
            s = cfg.bb_share_at_max_points
        elif bb_share < cfg.bb_share_warn_pct:
            s = cfg.bb_share_lt_warn_points
        elif bb_share < cfg.bb_share_block_pct:
            s = cfg.bb_share_lt_block_points
        else:
            s = cfg.bb_share_lt_total_points
        contrib.append(f"AMZ BB share={bb_share:.0%}→{s}")
        points += s

    return points, contrib, missing


def _score_margin(
    row: dict, cfg: CandidateScoringConfig,
) -> tuple[int, list[str], list[str]]:
    contrib: list[str] = []
    missing: list[str] = []
    points = 0

    roi = row.get("roi_conservative")
    if roi is None:
        missing.append("roi_conservative")
    else:
        s = _pick_tier(
            roi, cfg.roi_thresholds, cfg.roi_points,
            descending=True,
        )
        contrib.append(f"ROI={roi:.0%}→{s}")
        points += s

    profit = row.get("profit_conservative")
    if profit is None:
        missing.append("profit_conservative")
    else:
        s = _pick_tier(
            profit, cfg.profit_thresholds, cfg.profit_points,
            descending=True,
        )
        contrib.append(f"profit=£{profit:.2f}→{s}")
        points += s

    return points, contrib, missing


def _data_confidence(
    row: dict, cfg: CandidateScoringConfig,
) -> tuple[str, list[str]]:
    """HIGH/MEDIUM/LOW + reasons."""
    history_days = row.get("history_days")
    required = cfg.confidence_required_fields
    present = [f for f in required if row.get(f) is not None]
    missing = [f for f in required if row.get(f) is None]
    reasons: list[str] = []

    if (
        history_days is not None
        and history_days >= cfg.confidence_high_history_days
        and len(present) == len(required)
    ):
        return "HIGH", []

    if (
        history_days is not None
        and history_days >= cfg.confidence_medium_history_days
        and len(present) >= 3
    ):
        if missing:
            reasons.append(f"missing: {','.join(missing)}")
        return "MEDIUM", reasons

    # LOW
    if history_days is None:
        reasons.append("history_days unknown")
    elif history_days < cfg.confidence_medium_history_days:
        reasons.append(f"history_days={history_days} < {cfg.confidence_medium_history_days}")
    if missing:
        reasons.append(f"missing: {','.join(missing)}")
    return "LOW", reasons


# ────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────


def score_candidate(
    row: dict, *, config: CandidateScoringConfig | None = None,
) -> dict:
    """Score one row. Returns dict with the 5 candidate-score columns.

    Pure function — does not mutate the input row. The caller (a
    pipeline step or test) merges the result back into the row.
    """
    cfg = config or load_candidate_scoring_config()

    demand_pts, demand_contrib, demand_missing = _score_demand(row, cfg)
    stability_pts, stability_contrib, stability_missing = _score_stability(row, cfg)
    comp_pts, comp_contrib, comp_missing = _score_competition(row, cfg)
    margin_pts, margin_contrib, margin_missing = _score_margin(row, cfg)

    score = demand_pts + stability_pts + comp_pts + margin_pts
    if score >= cfg.band_strong:
        band = "STRONG"
    elif score >= cfg.band_ok:
        band = "OK"
    elif score >= cfg.band_weak:
        band = "WEAK"
    else:
        band = "FAIL"

    reasons = (
        demand_contrib + stability_contrib + comp_contrib + margin_contrib
    )

    confidence, confidence_reasons = _data_confidence(row, cfg)

    # Surface input-missing reasons in confidence_reasons too — the
    # operator wants to see WHY data is incomplete in one place.
    all_missing = list(dict.fromkeys(
        demand_missing + stability_missing + comp_missing + margin_missing
    ))
    if all_missing and not any("missing:" in r for r in confidence_reasons):
        confidence_reasons.append(f"score-input gaps: {','.join(all_missing)}")

    return {
        "candidate_score": int(score),
        "candidate_band": band,
        "candidate_reasons": reasons,
        "data_confidence": confidence,
        "data_confidence_reasons": confidence_reasons,
    }


def add_candidate_score(
    df: pd.DataFrame, *, config: CandidateScoringConfig | None = None,
) -> pd.DataFrame:
    """Append the 5 candidate-score columns to every row of `df`.

    REJECT rows are scored too — the score is informational and may
    help the operator understand why a row was rejected (e.g. a
    profitable-but-no-EAN row gets STRONG / LOW visibility).
    """
    if df.empty:
        out = df.copy()
        for col in (
            "candidate_score", "candidate_band", "candidate_reasons",
            "data_confidence", "data_confidence_reasons",
        ):
            out[col] = pd.Series(dtype=object)
        return out

    cfg = config or load_candidate_scoring_config()
    rows = []
    for _, row in df.iterrows():
        d = row.to_dict()
        try:
            d.update(score_candidate(d, config=cfg))
        except Exception:
            logger.exception(
                "candidate_score: failed on row asin=%s — defaulting to FAIL/LOW",
                d.get("asin"),
            )
            d.update({
                "candidate_score": 0,
                "candidate_band": "FAIL",
                "candidate_reasons": ["scoring error — see logs"],
                "data_confidence": "LOW",
                "data_confidence_reasons": ["scoring error"],
            })
        rows.append(d)
    return pd.DataFrame(rows)


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper.

    No config keys consumed — thresholds live in
    ``decision_thresholds.yaml::candidate_scoring``. Keeps the
    signature consistent with other steps in the canonical engine.
    """
    return add_candidate_score(df)
