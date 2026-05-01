"""Calculate step — fees, conservative price, profit, capital exposure.

Stage 04 of the canonical engine (per
`docs/PRD-sourcing-strategies.md` §4). Takes the resolve step's output
(match rows + REJECT rows for invalid/no-match inputs) and applies the
math layer:

  - Determines price_basis (FBA when fba_seller_count > 0, else FBM)
  - Picks the market_price (min of buy_box / fba_price for FBA basis)
  - Computes Amazon fees (current and conservative)
  - Computes raw + floored conservative price (90-day low or low-margin
    floor — see ``calculate_conservative_price``)
  - Computes profit metrics (margin, ROI, breakeven, etc.)
  - Computes capital_exposure (moq × buy_cost)
  - Accumulates risk flags (PRICE_MISMATCH_RRP, FBM_ONLY, AMAZON_*,
    SINGLE_FBA_SELLER, INSUFFICIENT_HISTORY, HIGH_MOQ)

Special-case: a match row with no usable market_price gets a REJECT
decision here (``"No valid market price"``), since downstream decide
can't operate without it.

REJECT rows from the resolve step pass through unchanged — only match
rows (rows without a pre-set decision) get the math layer applied.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from fba_engine.steps._helpers import is_missing
from sourcing_engine.config import CAPITAL_EXPOSURE_LIMIT
from sourcing_engine.pipeline.conservative_price import calculate_conservative_price
from sourcing_engine.pipeline.fees import calculate_fees_fba, calculate_fees_fbm
from sourcing_engine.pipeline.profit import calculate_profit
from sourcing_engine.utils.flags import (
    AMAZON_ON_LISTING,
    AMAZON_STATUS_UNKNOWN,
    FBM_ONLY,
    HIGH_MOQ,
    INSUFFICIENT_HISTORY,
    PRICE_MISMATCH_RRP,
    SINGLE_FBA_SELLER,
)

logger = logging.getLogger(__name__)


def calculate_economics(df: pd.DataFrame) -> pd.DataFrame:
    """Apply fees / cp / profit / capital exposure to match rows.

    Match rows are identified as rows without a pre-set ``decision``
    column value (REJECT rows from resolve already carry one). The
    function preserves row order and count: every input row produces
    exactly one output row.

    Returns a new DataFrame; the input is not mutated.
    """
    if df.empty:
        return df

    output_rows: list[dict] = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        if not is_missing(row_dict.get("decision")) and row_dict.get("decision"):
            # Pre-decided (REJECT from resolve). Pass through.
            # NaN-aware: pd.DataFrame fills missing dict keys with NaN,
            # which is truthy — bare `if row_dict.get("decision")` would
            # skip every match row that has no real decision yet.
            output_rows.append(row_dict)
            continue

        try:
            output_rows.append(_calculate_match(row_dict))
        except Exception:
            logger.exception(
                "[%s] [ROW_%s] [%s] calculate error",
                row_dict.get("supplier"), idx, row_dict.get("ean"),
            )
            row_dict["decision"] = "REVIEW"
            row_dict["decision_reason"] = (
                "Calculate error — manual review required"
            )
            output_rows.append(row_dict)

    return pd.DataFrame(output_rows)


def _calculate_match(match: dict) -> dict:
    """Per-match math layer. Mirrors the legacy `_process_match`
    function minus the final ``decide()`` call (decide is its own step).
    """
    risk_flags = list(match.get("risk_flags") or [])
    fba_seller_count = match.get("fba_seller_count", 0) or 0
    amazon_status = match.get("amazon_status")
    buy_box_price = match.get("buy_box_price")
    lowest_fba_price = match.get("new_fba_price")

    if fba_seller_count > 0:
        price_basis = "FBA"
        market_price = _pick_market_price(buy_box_price, lowest_fba_price)
        if amazon_status == "ON_LISTING":
            risk_flags.append(AMAZON_ON_LISTING)
        elif amazon_status == "UNKNOWN":
            risk_flags.append(AMAZON_STATUS_UNKNOWN)
        if fba_seller_count == 1:
            risk_flags.append(SINGLE_FBA_SELLER)
    else:
        price_basis = "FBM"
        market_price = buy_box_price
        risk_flags.append(FBM_ONLY)

    if market_price is None or market_price <= 0:
        match["decision"] = "REJECT"
        match["decision_reason"] = "No valid market price"
        match["risk_flags"] = risk_flags
        match["price_basis"] = price_basis
        return match

    rrp = match.get("rrp_inc_vat")
    if rrp and rrp > 0:
        ratio = market_price / rrp
        if ratio > 2.0 or ratio < 0.3:
            risk_flags.append(PRICE_MISMATCH_RRP)

    buy_cost = match["buy_cost"]
    size_tier = match.get("size_tier")
    sales_estimate = match.get("sales_estimate")
    keepa_fba_fee = match.get("fba_pick_pack_fee")
    keepa_referral_pct = match.get("referral_fee_pct")

    fees_current = _fees(
        price_basis, market_price, size_tier, sales_estimate,
        keepa_fba_fee, keepa_referral_pct,
    )

    price_history = match.get("price_history")
    if price_history and isinstance(price_history, list):
        raw_cp, _, _ = calculate_conservative_price(
            price_history, market_price, buy_cost, 0,
        )
        fees_conservative = _fees(
            price_basis, raw_cp, size_tier, sales_estimate,
            keepa_fba_fee, keepa_referral_pct,
        )
        raw_cp, floored_cp, cp_flag = calculate_conservative_price(
            price_history, market_price, buy_cost, fees_conservative["total"],
        )
    else:
        raw_cp = market_price
        floored_cp = market_price
        cp_flag = INSUFFICIENT_HISTORY
        fees_conservative = _fees(
            price_basis, raw_cp, size_tier, sales_estimate,
            keepa_fba_fee, keepa_referral_pct,
        )

    if cp_flag:
        risk_flags.append(cp_flag)

    risk_flags.extend(fees_current.get("flags", []))
    risk_flags.extend(fees_conservative.get("flags", []))

    profit = calculate_profit(
        market_price, raw_cp, fees_current, fees_conservative, buy_cost,
    )

    moq = match.get("moq", 1) or 1
    capital_exposure = moq * buy_cost
    if capital_exposure > CAPITAL_EXPOSURE_LIMIT:
        risk_flags.append(HIGH_MOQ)

    risk_flags = list(dict.fromkeys(risk_flags))

    match.update({
        "market_price": market_price,
        "raw_conservative_price": raw_cp,
        "floored_conservative_price": floored_cp,
        "price_basis": price_basis,
        "fees_current": fees_current["total"],
        "fees_conservative": fees_conservative["total"],
        **profit,
        "capital_exposure": capital_exposure,
        "risk_flags": risk_flags,
    })
    return match


def _pick_market_price(bb: float | None, fba: float | None) -> float | None:
    """Lower of buy-box and lowest 3rd-party FBA. Mirrors legacy."""
    candidates = [p for p in (bb, fba) if p is not None and p > 0]
    return min(candidates) if candidates else None


def _fees(
    price_basis: str,
    price: float,
    size_tier: str | None,
    sales_estimate: float | None,
    keepa_fba_fee: float | None,
    keepa_referral_fee_pct: float | None,
) -> dict:
    if price_basis == "FBA":
        return calculate_fees_fba(
            price, size_tier,
            sales_estimate=sales_estimate,
            keepa_fba_fee=keepa_fba_fee,
            keepa_referral_fee_pct=keepa_referral_fee_pct,
        )
    return calculate_fees_fbm(price)


def add_stability_score(df: pd.DataFrame) -> pd.DataFrame:
    """Append a 0.0–1.0 ``stability_score`` column derived from Buy Box deltas.

    Formula per ``docs/PRD-keepa-sourcing-strategies.md`` §8:

        stability_score = 1.0 - (abs(delta_30d_pct) + abs(delta_90d_pct)) / 200

    Range: 1.0 (rock-steady — zero movement on both windows) to 0.0
    (highly volatile — ±100% on both windows).

    Reads ``delta_buy_box_30d_pct`` and ``delta_buy_box_90d_pct`` from
    each row (populated by the keepa_finder_csv discovery step from
    Keepa's "Buy Box: 30/90 days drop %" columns). Missing or non-
    numeric deltas are treated as 0 → max stability — defensive choice
    that won't penalise rows lacking the data.

    Informational only — does NOT gate SHORTLIST/REVIEW/REJECT.
    Mutating the DataFrame in place would surprise callers; we return
    a new DataFrame.
    """
    if df.empty:
        out = df.copy()
        out["stability_score"] = pd.Series(dtype=float)
        return out

    def _to_pct(value: object) -> float:
        if is_missing(value):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    delta30 = df.get("delta_buy_box_30d_pct", pd.Series([0.0] * len(df), index=df.index))
    delta90 = df.get("delta_buy_box_90d_pct", pd.Series([0.0] * len(df), index=df.index))
    abs30 = delta30.map(_to_pct).abs()
    abs90 = delta90.map(_to_pct).abs()
    score = 1.0 - (abs30 + abs90) / 200.0
    # Clamp to [0, 1] — a row with > ±100% deltas would otherwise produce
    # a negative score, which conveys the same information as 0 (max
    # volatility) while being a cleaner contract for downstream consumers.
    score = score.clip(lower=0.0, upper=1.0)

    out = df.copy()
    out["stability_score"] = score
    return out


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible wrapper.

    Recognised ``config`` keys:

      - ``compute_stability_score``: when truthy, append a
        ``stability_score`` column (0.0–1.0) derived from Buy Box
        delta-30d / delta-90d. Used by the keepa_finder strategy
        family (amazon_oos_wholesale, stable_price_low_volatility).
        Default ``False`` — existing strategies keep the same output
        schema.
    """
    out = calculate_economics(df)
    if config.get("compute_stability_score"):
        out = add_stability_score(out)
    return out
