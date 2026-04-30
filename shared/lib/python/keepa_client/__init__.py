"""Typed Keepa API client with token-bucket rate limiting + disk cache.

This module consolidates Keepa access that was previously ad-hoc `requests`
calls scattered through `_legacy_keepa/`. Exports:

  - `KeepaClient`        — the API client (single + batch product, seller)
  - `KeepaConfig`        — typed config loaded from `shared/config/keepa_client.yaml`
  - `load_keepa_config`  — reads + validates the YAML config file
  - `KeepaProduct`       — pydantic model for /product responses
  - `KeepaSeller`        — pydantic model for /seller responses
  - `TokenBucket`        — exposed for testing; not normally needed externally
  - `DiskCache`          — exposed for testing
  - `KeepaApiError`      — Keepa returned a non-200 response after retries

All three lookup methods (``get_product``, ``get_products``,
``get_seller``) fall back to expired cached data when the API fails
after retries — see ``client.py`` for the stale-on-error contract.

Per `docs/PRD-sourcing-strategies.md` §7.
"""
from .cache import DiskCache
from .client import KeepaApiError, KeepaClient
from .config import KeepaConfig, load_keepa_config
from .models import KeepaProduct, KeepaSeller
from .rate_limit import TokenBucket

__all__ = [
    "DiskCache",
    "KeepaApiError",
    "KeepaClient",
    "KeepaConfig",
    "KeepaProduct",
    "KeepaSeller",
    "TokenBucket",
    "load_keepa_config",
]
