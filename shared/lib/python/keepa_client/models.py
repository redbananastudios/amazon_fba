"""Pydantic models for Keepa API responses.

We model only the subset of fields the engine actually consumes. Keepa's
`/product` and `/seller` responses include 30+ fields; modelling all of
them would couple this library to upstream changes for no payoff. If a
caller needs a field not modelled here, add it as an `Optional` field on
the relevant class — pydantic ignores extras by default so backwards
compat is automatic.

Field aliases let the JSON-as-shipped (`sellerId`, `asinList`,
`categoryTree`, `tokensConsumed`) map to Pythonic names.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class KeepaProduct(BaseModel):
    """Subset of Keepa /product response fields actually used by the engine."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    asin: str
    title: Optional[str] = None
    brand: Optional[str] = None
    category_tree: list[dict[str, Any]] = Field(
        default_factory=list, alias="categoryTree"
    )
    # Keepa's `csv` is a list of 30+ parallel time-series. We surface it as
    # opaque list-of-lists; callers that need a specific series index by
    # Keepa's documented enum positions (kept out of this model to avoid
    # coupling to indices that may shift).
    csv: list[Any] = Field(default_factory=list)


class KeepaSeller(BaseModel):
    """Subset of Keepa /seller response fields used by store-stalking."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    seller_id: str = Field(alias="sellerId")
    seller_name: Optional[str] = Field(default=None, alias="sellerName")
    asin_list: list[str] = Field(default_factory=list, alias="asinList")
