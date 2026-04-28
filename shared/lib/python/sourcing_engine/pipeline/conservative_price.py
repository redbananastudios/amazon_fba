"""Conservative price — 15th percentile of 90-day FBA history.
raw_conservative_price: used by decision engine (NEVER floored)
floored_conservative_price: display only
"""
import numpy as np
from sourcing_engine.config import HISTORY_MINIMUM_DAYS, LOWER_BAND_PERCENTILE, MIN_PROFIT
from sourcing_engine.utils.flags import INSUFFICIENT_HISTORY, PRICE_FLOOR_HIT


def calculate_conservative_price(
    price_history: list[tuple], market_price: float,
    buy_cost: float, fees_conservative_total: float,
) -> tuple[float, float, str | None]:
    """Returns: (raw_conservative_price, floored_conservative_price, flag_or_none)"""
    if not price_history:
        return market_price, market_price, INSUFFICIENT_HISTORY

    qualifying = [(day, price, sellers) for day, price, sellers in price_history if sellers and sellers > 0]
    qualifying_days = len(set(day for day, _, _ in qualifying))
    if qualifying_days < HISTORY_MINIMUM_DAYS:
        return market_price, market_price, INSUFFICIENT_HISTORY

    prices = [price for _, price, _ in qualifying]
    percentile_price = float(np.percentile(prices, LOWER_BAND_PERCENTILE))
    raw_conservative_price = min(market_price, percentile_price)

    price_floor = buy_cost + fees_conservative_total + MIN_PROFIT
    floored_conservative_price = max(raw_conservative_price, price_floor)
    flag = PRICE_FLOOR_HIT if raw_conservative_price < price_floor else None
    return raw_conservative_price, floored_conservative_price, flag
