"""Discovery step: build a 1-row DataFrame from a single ASIN.

The minimal entry point for "should I buy this ASIN?" — operator passes
an ASIN (and optionally a buy_cost), the engine fetches market data via
``keepa_enrich``, runs SP-API gating + economics + decision, and emits
a verdict.

When ``buy_cost`` is omitted (or zero), the wholesale flow kicks in —
``calculate.calculate_profit`` emits ``max_buy_price`` as the
supplier-negotiation ceiling instead of a literal ROI. Operator pass
``--buy-cost <X>`` when they have a real cost (e.g. retail-arb or a
quoted wholesale rate) to get back current/conservative ROI numbers.

Pairs with ``fba_engine/strategies/single_asin.yaml``. ASIN validation
is intentionally light — Amazon ASINs are 10 chars, but the API will
return "not found" (and downstream None-fill) on a malformed input
anyway, so we don't duplicate the check beyond ensuring the value
exists.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Amazon ASIN format — 10 alphanumeric characters, leading "B" for most
# physical-product ASINs (with rare exceptions for legacy ISBN-style
# codes). We accept both for now; the downstream API call surfaces
# "not found" for nonsense values.
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


def discover_single_asin(asin: str, buy_cost: float = 0.0) -> pd.DataFrame:
    """Build a 1-row DataFrame seeded with the canonical-engine fields a
    discovery step would normally populate, leaving market-data columns
    to be filled by the downstream ``keepa_enrich`` step.

    Args:
        asin: Amazon ASIN (10 chars). Uppercased before insertion.
        buy_cost: Operator's buy cost in GBP. Default 0.0 triggers the
            wholesale flow (engine emits ``max_buy_price`` as the
            supplier-negotiation ceiling instead of literal ROI).

    Returns:
        Single-row DataFrame with the canonical engine schema fields
        seeded — ASIN, source/discovery_strategy tags, amazon_url,
        buy_cost, moq. Market data columns (buy_box_price, sales_estimate,
        fba_seller_count, etc.) are left absent so keepa_enrich can fill
        them without a clobber check.
    """
    if not asin:
        raise ValueError("single_asin: asin is required")
    asin = str(asin).strip().upper()
    if not _ASIN_RE.fullmatch(asin):
        raise ValueError(
            f"single_asin: {asin!r} doesn't look like an Amazon ASIN "
            f"(expected 10 alphanumeric chars)"
        )

    return pd.DataFrame([{
        "asin": asin,
        "source": "single_asin",
        "discovery_strategy": "single_asin",
        "amazon_url": f"https://www.amazon.co.uk/dp/{asin}",
        # Wholesale-flow defaults match seller_storefront / keepa_finder.
        # buy_cost=0 is the load-bearing convention that tells calculate
        # to emit max_buy_price; non-zero passes through unchanged.
        "buy_cost": float(buy_cost),
        "moq": 1,
    }])


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Runner-compatible discovery wrapper.

    Discovery steps create the DataFrame; the ``df`` arg is ignored.

    Required ``config`` keys:
      - ``asin``: Amazon ASIN.

    Optional ``config`` keys:
      - ``buy_cost``: GBP cost (string or float — strategy YAML
        interpolation produces strings, direct callers can pass floats).
        Empty string / "None" / missing → 0.0 (wholesale flow).
    """
    asin = config.get("asin")
    if not asin:
        raise ValueError("single_asin step requires config['asin']")

    raw_cost = config.get("buy_cost")
    # YAML interpolation produces "" when context lacks the key, "None"
    # when the value was Python None — both must collapse to 0.0 to
    # trigger the wholesale flow rather than crash the float() cast.
    if raw_cost in (None, "", "None"):
        buy_cost = 0.0
    else:
        try:
            buy_cost = float(raw_cost)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"single_asin: buy_cost {raw_cost!r} is not a number"
            ) from e
    if buy_cost < 0:
        raise ValueError(
            f"single_asin: buy_cost {buy_cost} cannot be negative"
        )
    return discover_single_asin(asin=asin, buy_cost=buy_cost)
