"""KeepaClient — typed HTTP client for the Keepa API.

Wraps the rate limiter, cache, and token-usage log so callers get a
cache-aware, throttled, observability-instrumented `get_product` /
`get_products` / `get_seller` interface.

Phase 2.1 (this PR) adds:
  - `get_products(asins)`: batch product lookup. Keepa's /product endpoint
    accepts a comma-separated list; the client chunks per
    ``config.batching.product_batch_size``, dedupes against the cache,
    preserves caller-supplied ASIN order, and filters Keepa's `null`
    entries for unknown ASINs.
  - Stale-on-error: when the API fails after exhausting retries, the
    client serves expired cached entries rather than raising. The token
    log records this with ``cached=true, stale=true`` so operators can
    correlate degraded responses with upstream incidents.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable

import requests

from .cache import DiskCache
from .config import KeepaConfig
from .log import append_token_log
from .models import KeepaProduct, KeepaSeller
from .rate_limit import TokenBucket

logger = logging.getLogger(__name__)


class KeepaApiError(RuntimeError):
    """Keepa returned a non-200 response after exhausting retries."""


class KeepaClient:
    """Cache-aware, throttled Keepa API client."""

    def __init__(
        self,
        api_key: str,
        config: KeepaConfig,
        *,
        _sleep_for_tests: Callable[[float], None] | None = None,
    ) -> None:
        self._api_key = api_key
        self._config = config
        # The bucket and the retry path both want to sleep — share one
        # injection point so tests can spy on both.
        self._sleep = _sleep_for_tests if _sleep_for_tests is not None else time.sleep
        self._bucket = TokenBucket(
            tokens_per_minute=config.rate_limit.tokens_per_minute,
            burst=config.rate_limit.burst,
            sleep=self._sleep,
        )
        self._cache = DiskCache(root=config.cache.root)
        self._log_path = config.cache.root / "token_log.jsonl"

    # ──────────────────────────────────────────────────────────────────
    # Public API.
    # ──────────────────────────────────────────────────────────────────

    def get_seller(self, seller_id: str, *, storefront: bool = False) -> KeepaSeller:
        """Look up a seller. With `storefront=True`, returns the seller's full ASIN list."""
        cache_key = f"{seller_id}__storefront" if storefront else seller_id
        cached = self._cache.get("seller", cache_key)
        if cached is not None:
            append_token_log(
                self._log_path, endpoint="seller", tokens=0, cached=True,
                extra={"seller_id": seller_id, "storefront": storefront},
            )
            return KeepaSeller.model_validate(cached)

        params = {
            "key": self._api_key,
            "domain": self._config.api.marketplace,
            "seller": seller_id,
            "storefront": 1 if storefront else 0,
        }
        try:
            payload = self._request("/seller", params)
        except KeepaApiError:
            stale = self._cache.get_stale("seller", cache_key)
            if stale is not None:
                logger.warning(
                    "Keepa /seller failed for %s — using stale cache",
                    seller_id,
                )
                append_token_log(
                    self._log_path, endpoint="seller", tokens=0, cached=True,
                    extra={
                        "seller_id": seller_id,
                        "storefront": storefront,
                        "stale": True,
                    },
                )
                return KeepaSeller.model_validate(stale)
            raise
        tokens_used = int(payload.get("tokensConsumed", 0))
        append_token_log(
            self._log_path, endpoint="seller", tokens=tokens_used, cached=False,
            extra={"seller_id": seller_id, "storefront": storefront},
        )

        sellers_obj = payload.get("sellers") or {}
        seller_payload = sellers_obj.get(seller_id) or sellers_obj.get(
            seller_id.upper()
        )
        if seller_payload is None:
            raise KeepaApiError(
                f"Keepa /seller response did not include seller '{seller_id}': {sellers_obj!r}"
            )

        ttl = self._config.cache.ttl_seconds.get("seller", 7 * 24 * 3600)
        self._cache.set("seller", cache_key, seller_payload, ttl_seconds=ttl)
        return KeepaSeller.model_validate(seller_payload)

    def get_product(self, asin: str) -> KeepaProduct:
        """Look up a single product by ASIN. Cached per ASIN."""
        cached = self._cache.get("product", asin)
        if cached is not None:
            append_token_log(
                self._log_path, endpoint="product", tokens=0, cached=True,
                extra={"asin": asin},
            )
            return KeepaProduct.model_validate(cached)

        params = {
            "key": self._api_key,
            "domain": self._config.api.marketplace,
            "asin": asin,
        }
        try:
            payload = self._request("/product", params)
        except KeepaApiError:
            stale = self._cache.get_stale("product", asin)
            if stale is not None:
                logger.warning(
                    "Keepa /product failed for %s — using stale cache", asin,
                )
                append_token_log(
                    self._log_path, endpoint="product", tokens=0, cached=True,
                    extra={"asin": asin, "stale": True},
                )
                return KeepaProduct.model_validate(stale)
            raise
        tokens_used = int(payload.get("tokensConsumed", 0))
        append_token_log(
            self._log_path, endpoint="product", tokens=tokens_used, cached=False,
            extra={"asin": asin},
        )

        products = payload.get("products") or []
        if not products:
            raise KeepaApiError(
                f"Keepa /product response did not include any product for ASIN '{asin}'"
            )
        product_payload = products[0]

        ttl = self._config.cache.ttl_seconds.get("product", 24 * 3600)
        self._cache.set("product", asin, product_payload, ttl_seconds=ttl)
        return KeepaProduct.model_validate(product_payload)

    def get_products(self, asins: list[str]) -> list[KeepaProduct]:
        """Look up multiple products in one or more batched API calls.

        Behaviour:
          - Empty input returns ``[]`` without any HTTP call.
          - Already-cached ASINs are served from the cache and removed
            from the batch — only uncached ASINs hit the API.
          - The remaining ASINs are chunked at
            ``config.batching.product_batch_size`` and each chunk is
            sent as a single comma-separated /product call. Each
            returned product is cached individually so a subsequent
            ``get_product`` call hits without an HTTP round-trip.
          - Output preserves the order of the input ``asins`` list.
          - Keepa returns ``null`` entries for ASINs it couldn't
            resolve; those are filtered out (the result list may
            therefore be shorter than the input).
          - On API failure, ASINs with a stale cached entry fall back
            to the stale value; the rest are dropped (caller compares
            ``len(out)`` to ``len(asins)`` to detect partial loss).
        """
        if not asins:
            return []

        # Build the by-ASIN result map first; that lets us preserve
        # caller order regardless of how the cache + API split the work.
        by_asin: dict[str, dict] = {}

        # Cache pass — pull anything fresh, leave the rest for the API.
        uncached: list[str] = []
        for asin in asins:
            cached = self._cache.get("product", asin)
            if cached is not None:
                by_asin[asin] = cached
                append_token_log(
                    self._log_path, endpoint="product", tokens=0, cached=True,
                    extra={"asin": asin, "batch": True},
                )
            else:
                uncached.append(asin)

        # API pass — chunked. Dedupe via `dict.fromkeys` (preserves first-
        # seen order, drops duplicate ASINs in the input so we don't waste
        # tokens fetching the same ASIN twice in one call).
        to_fetch = list(dict.fromkeys(uncached))
        batch_size = self._config.batching.product_batch_size
        ttl = self._config.cache.ttl_seconds.get("product", 24 * 3600)

        for i in range(0, len(to_fetch), batch_size):
            chunk = to_fetch[i : i + batch_size]
            params = {
                "key": self._api_key,
                "domain": self._config.api.marketplace,
                "asin": ",".join(chunk),
            }
            try:
                payload = self._request("/product", params)
            except KeepaApiError:
                # Stale-on-error: serve any expired cache entries for
                # this chunk, log them as stale, drop the rest. Don't
                # propagate — partial data is better than no data for
                # batch callers (e.g. seller_storefront walking 1000
                # ASINs that should not all fail because Keepa is
                # transiently degraded).
                logger.warning(
                    "Keepa /product batch (%d ASINs) failed — serving stale "
                    "cache where available", len(chunk),
                )
                for asin in chunk:
                    stale = self._cache.get_stale("product", asin)
                    if stale is not None:
                        by_asin[asin] = stale
                        append_token_log(
                            self._log_path, endpoint="product",
                            tokens=0, cached=True,
                            extra={"asin": asin, "batch": True, "stale": True},
                        )
                # `continue` skips this chunk only; subsequent chunks
                # still attempt fresh fetches (one bad chunk doesn't
                # poison the rest of the batch).
                continue

            tokens_used = int(payload.get("tokensConsumed", 0))
            append_token_log(
                self._log_path, endpoint="product",
                tokens=tokens_used, cached=False,
                extra={"batch_size": len(chunk), "batch": True},
            )

            products = payload.get("products") or []
            # Keepa returns one entry per requested ASIN, possibly
            # `null` for ASINs it couldn't resolve. Filter nulls.
            for prod in products:
                if not prod:
                    continue
                asin = prod.get("asin")
                if not asin:
                    continue
                self._cache.set("product", asin, prod, ttl_seconds=ttl)
                by_asin[asin] = prod

        # Reassemble in input order, dropping any ASINs we couldn't
        # resolve at all (no cache, no API, no stale fallback). Order
        # is carried by `asins` (the input list) — `by_asin` is a dict
        # used only for O(1) lookup, so insertion order doesn't leak.
        # Extra ASINs that Keepa returned beyond the request are cached
        # under their own asin key but excluded from the output here
        # (they're not in `asins`, so the membership check filters them).
        return [
            KeepaProduct.model_validate(by_asin[a])
            for a in asins
            if a in by_asin
        ]

    # ──────────────────────────────────────────────────────────────────
    # Internal HTTP layer.
    # ──────────────────────────────────────────────────────────────────

    def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Issue a GET against the Keepa API with retry on transients.

        Token reconciliation: we acquire a conservative estimate from the
        bucket BEFORE the call (Keepa's actual `tokensConsumed` is only
        known after the response, so we have to guess high to avoid
        bursting through the quota), then refund the diff once we read
        the response. Over time the bucket converges on the true ledger.
        """
        estimate = self._estimate_for(path)
        self._bucket.acquire(estimate)

        url = f"{self._config.api.base_url}{path}"
        retry_cfg = self._config.rate_limit.retry_on_429

        # Retry on transient: 429 (rate limit) and gateway-class 5xx
        # (502/503/504). 500 is excluded — Keepa's 500s are often deterministic
        # (malformed param, unknown ASIN format) and retrying just adds latency.
        # Stale-on-error fallback for 5xx-after-retries is deferred to a
        # follow-up PR per docs/PRD-sourcing-strategies.md §13.
        retryable = {429, 502, 503, 504}

        for attempt in range(retry_cfg.max_retries + 1):
            response = requests.get(
                url,
                params=params,
                timeout=self._config.api.request_timeout_seconds,
            )
            if response.status_code == 200:
                payload = response.json()
                # Reconcile: refund the bucket if Keepa's actual
                # `tokensConsumed` was below our pre-call estimate.
                actual = int(payload.get("tokensConsumed", estimate))
                if estimate > actual:
                    self._bucket.refund(estimate - actual)
                return payload
            if response.status_code in retryable and attempt < retry_cfg.max_retries:
                jitter = random.uniform(0, retry_cfg.backoff_jitter_seconds)
                wait = retry_cfg.backoff_base_seconds * (2 ** attempt) + jitter
                self._sleep(wait)
                continue
            raise KeepaApiError(
                f"Keepa {path} returned HTTP {response.status_code}: {response.text[:200]}"
            )

        raise KeepaApiError(f"Keepa {path} exhausted retries")

    def _estimate_for(self, path: str) -> int:
        """Conservative pre-call token estimate per endpoint.

        Used by `_request` to drain the bucket BEFORE the API call (Keepa's
        actual `tokensConsumed` is only known after). Estimates are
        deliberately high to avoid bursting through the quota wall;
        `_request` reconciles back to the true ledger via `bucket.refund`.
        """
        return 50 if path.startswith("/seller") else 6
