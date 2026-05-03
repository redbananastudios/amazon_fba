"""Trend signals — direction arrows + 1-line trend story.

Reads engine-computed trend signals (bsr_slope_*, joiners_90d,
bb_drop_pct_90) and produces:

- 3 directional arrows (↗ / → / ↘ / ?) — what a chart-reader sees
  at-a-glance for sales / sellers / price movement
- 1 synthesised story line — "Demand rising, supply steady —
  entrance window" etc.

Pure functions; no I/O. Consumed by `analyst.fallback_analyse`.
"""
from __future__ import annotations

from typing import Optional


def _direction_arrow(slope: Optional[float], threshold: float = 0.003) -> str:
    """↗ / → / ↘ from a normalised slope.

    Engine's slope is normalised (mean-fraction-per-day) so values
    well below 0.01 still represent meaningful trends. Threshold of
    0.003 corresponds to ~0.3% BSR change per day = ~9% per month —
    materially trending.
    """
    if slope is None:
        return "?"
    if slope < -threshold:
        return "↗"   # negative slope = improving (BSR going down = sales up)
    if slope > threshold:
        return "↘"   # positive slope = worsening (BSR going up = sales down)
    return "→"


def _seller_arrow(joiners: Optional[float]) -> str:
    """↗ / → / ↘ from net seller joiners over 90d."""
    if joiners is None:
        return "?"
    if joiners >= 2:
        return "↗"
    if joiners <= -2:
        return "↘"
    return "→"


def _price_arrow(drop_pct: Optional[float]) -> str:
    """↗ / → / ↘ from buy_box_drop_pct_90 (in raw percent).

    bb_drop_pct measures the magnitude of recent BB drops vs avg90.
    Higher = more recent dropping = price softening. Engine stores
    as a fraction; payload._bb_drop_pct converts to raw percent at
    the boundary so analyst thresholds (3, 10) read naturally.
    """
    if drop_pct is None:
        return "?"
    if drop_pct >= 10:
        return "↘"   # price falling
    if drop_pct >= 3:
        return "→"   # mild softening
    return "→"       # stable (no down-arrow for "going up" because
                     # bb_drop measures only downside moves)


def _build_trend_story(payload_row: dict) -> dict:
    """Return {sales_arrow, sellers_arrow, price_arrow, story_line}."""
    trends = payload_row.get("trends") or {}
    sales = _direction_arrow(trends.get("bsr_slope_90d"))
    sellers = _seller_arrow(trends.get("joiners_90d"))
    price = _price_arrow(trends.get("bb_drop_pct_90"))

    # Synthesis — combine the three arrows into a one-line read.
    if sales == "↗" and sellers != "↗" and price != "↘":
        story = "Demand rising, supply steady — entrance window."
    elif sales == "↗" and sellers == "↗":
        story = "Demand and competition both rising — race to share."
    elif sales == "↘" and sellers == "↗":
        story = "Demand falling and more sellers entering — race to bottom."
    elif sales == "↘" and price == "↘":
        story = "Sales softening and price eroding — declining listing."
    elif sales == "→" and sellers == "→" and price == "→":
        story = "Stable mature listing — no recent movement."
    elif sales == "↗":
        story = "Sales improving."
    elif sales == "↘":
        story = "Sales softening."
    elif sellers == "↘":
        story = "Sellers leaving — possibly less competition ahead."
    else:
        story = "Mixed signals; no clear trend."

    return {
        "sales_arrow": sales,
        "sellers_arrow": sellers,
        "price_arrow": price,
        "story_line": story,
    }
