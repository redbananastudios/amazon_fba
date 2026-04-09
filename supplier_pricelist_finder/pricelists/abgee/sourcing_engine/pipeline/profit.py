"""Profit and margin calculation engine.
CRITICAL: profit_conservative uses raw_conservative_price — never floored.
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
    max_buy_price = market_price - fees_current["total"] - MIN_PROFIT
    return {
        "profit_current": profit_current, "profit_conservative": profit_conservative,
        "margin_current": margin_current, "margin_conservative": margin_conservative,
        "max_buy_price": max_buy_price,
    }
