"""Profit, margin, and ROI calculation engine.

CRITICAL: profit_conservative uses raw_conservative_price — never floored.

ROI columns added in step 1 of the reorganisation. ROI replaces margin as the
SHORTLIST gate (see pipeline/decision.py). Margin remains computed and visible
in output for human reference.
"""
from sourcing_engine.config import MIN_PROFIT


def calculate_profit(
    market_price: float, raw_conservative_price: float,
    fees_current: dict, fees_conservative: dict, buy_cost: float,
) -> dict:
    profit_current = market_price - fees_current["total"] - buy_cost
    profit_conservative = raw_conservative_price - fees_conservative["total"] - buy_cost

    margin_current = profit_current / market_price if market_price > 0 else 0.0
    margin_conservative = profit_conservative / raw_conservative_price if raw_conservative_price > 0 else 0.0

    # ROI = profit / buy_cost. Capital efficiency.
    # None when buy_cost is missing/zero rather than infinity, so output handles it cleanly.
    roi_current = (profit_current / buy_cost) if buy_cost and buy_cost > 0 else None
    roi_conservative = (profit_conservative / buy_cost) if buy_cost and buy_cost > 0 else None

    max_buy_price = market_price - fees_current["total"] - MIN_PROFIT

    return {
        "profit_current": profit_current,
        "profit_conservative": profit_conservative,
        "margin_current": margin_current,
        "margin_conservative": margin_conservative,
        "roi_current": roi_current,
        "roi_conservative": roi_conservative,
        "max_buy_price": max_buy_price,
    }
