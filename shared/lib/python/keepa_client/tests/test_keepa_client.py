"""Tests for keepa_client.

Covers: pydantic model round-trip, TokenBucket rate-limit behaviour,
DiskCache TTL + persistence, token-usage log append, KeepaConfig loading
from YAML, and KeepaClient end-to-end with HTTP mocked.

Per `docs/PRD-sourcing-strategies.md` §12: target ~25 tests for keepa_client.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from keepa_client import (
    DiskCache,
    KeepaApiError,
    KeepaClient,
    KeepaConfig,
    KeepaProduct,
    KeepaSeller,
    TokenBucket,
    load_keepa_config,
)
from keepa_client.log import append_token_log


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestKeepaProductModel:
    def test_round_trip_minimal_payload(self):
        # Keepa /product responses include 30+ fields; only model the subset
        # the engine actually consumes (asin, title, brand, csv arrays).
        payload = {
            "asin": "B01EXAMPLE",
            "title": "Example Product",
            "brand": "Acme",
            "categoryTree": [{"catId": 123, "name": "Toys"}],
            "csv": [None, [100, 999], None],  # placeholder arrays
        }
        product = KeepaProduct.model_validate(payload)
        assert product.asin == "B01EXAMPLE"
        assert product.title == "Example Product"
        assert product.brand == "Acme"

    def test_missing_optional_fields_default_none(self):
        # Keepa often returns title/brand as null for unmatched ASINs.
        payload = {"asin": "B0NOTFOUND"}
        product = KeepaProduct.model_validate(payload)
        assert product.asin == "B0NOTFOUND"
        assert product.title is None
        assert product.brand is None

    def test_missing_asin_raises(self):
        with pytest.raises(Exception):  # pydantic.ValidationError
            KeepaProduct.model_validate({"title": "no asin"})


class TestKeepaSellerModel:
    def test_seller_with_asin_list(self):
        payload = {
            "sellerId": "A1B2C3D4E5",
            "sellerName": "Acme Storefront",
            "asinList": ["B001", "B002", "B003"],
        }
        seller = KeepaSeller.model_validate(payload)
        assert seller.seller_id == "A1B2C3D4E5"
        assert seller.seller_name == "Acme Storefront"
        assert seller.asin_list == ["B001", "B002", "B003"]

    def test_empty_asin_list(self):
        payload = {"sellerId": "A0", "sellerName": "Empty", "asinList": []}
        seller = KeepaSeller.model_validate(payload)
        assert seller.asin_list == []

    def test_missing_asin_list_defaults_to_empty(self):
        # Some Keepa responses omit asinList for sellers with no inventory.
        seller = KeepaSeller.model_validate({"sellerId": "A0", "sellerName": "X"})
        assert seller.asin_list == []

    def test_round_trip_dump_uses_field_names_not_aliases(self):
        # Pydantic v2 default: model_dump() emits field names, not aliases.
        # Use by_alias=True if you need to send the dict back to Keepa.
        # Test pins the contract so a future "let's switch defaults" doesn't
        # silently break upstream compatibility.
        seller = KeepaSeller.model_validate({"sellerId": "A1", "asinList": ["B0"]})
        assert seller.model_dump()["seller_id"] == "A1"
        assert seller.model_dump(by_alias=True)["sellerId"] == "A1"


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


class TestTokenBucket:
    def test_acquire_within_burst_returns_immediately(self):
        # 100 burst allows 50 tokens to be drawn instantly.
        bucket = TokenBucket(tokens_per_minute=20, burst=100, sleep=lambda _: None)
        start = time.monotonic()
        bucket.acquire(50)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_acquire_above_burst_blocks_for_refill(self):
        # 20 tokens/min = 1 token per 3 seconds. After draining the 100-burst
        # and asking for 1 more, the bucket should sleep (mocked) for ~3s
        # worth of refill time.
        sleeps: list[float] = []
        bucket = TokenBucket(
            tokens_per_minute=20, burst=100, sleep=lambda s: sleeps.append(s)
        )
        bucket.acquire(100)
        bucket.acquire(1)
        # Should have slept at least once for refill.
        assert len(sleeps) >= 1
        assert sum(sleeps) > 0

    def test_acquire_zero_is_no_op(self):
        sleeps: list[float] = []
        bucket = TokenBucket(
            tokens_per_minute=20, burst=100, sleep=lambda s: sleeps.append(s)
        )
        bucket.acquire(0)
        assert sleeps == []

    def test_request_above_burst_capacity_raises(self):
        # Acquiring more than the bucket capacity is a programming error.
        bucket = TokenBucket(tokens_per_minute=20, burst=100, sleep=lambda _: None)
        with pytest.raises(ValueError, match="exceeds bucket capacity"):
            bucket.acquire(200)

    def test_refund_returns_tokens_to_bucket(self):
        # Reviewer M2: post-response reconciliation needs a refund path.
        # Acquire 50, then refund 30 — bucket should regain 30 tokens'
        # worth of capacity for the next acquire without sleeping.
        sleeps: list[float] = []
        bucket = TokenBucket(
            tokens_per_minute=10,  # slow refill so any sleep is observable
            burst=50,
            sleep=lambda s: sleeps.append(s),
        )
        bucket.acquire(50)
        bucket.refund(30)
        # Now acquire 30 — should NOT block, since refund put 30 back.
        bucket.acquire(30)
        assert sleeps == []

    def test_refund_caps_at_burst_capacity(self):
        bucket = TokenBucket(tokens_per_minute=20, burst=100, sleep=lambda _: None)
        bucket.acquire(10)
        bucket.refund(1000)  # way more than capacity
        # Acquiring full capacity should still work without sleeping.
        sleeps: list[float] = []
        bucket = TokenBucket(
            tokens_per_minute=20, burst=100, sleep=lambda s: sleeps.append(s)
        )
        bucket.refund(1000)
        bucket.acquire(100)
        assert sleeps == []

    def test_refund_zero_or_negative_is_noop(self):
        bucket = TokenBucket(tokens_per_minute=20, burst=100, sleep=lambda _: None)
        bucket.refund(0)
        bucket.refund(-50)  # silently ignored


# ---------------------------------------------------------------------------
# DiskCache
# ---------------------------------------------------------------------------


class TestDiskCache:
    def test_set_and_get_round_trips(self, tmp_path: Path):
        cache = DiskCache(root=tmp_path)
        cache.set("product", "B0SAMPLE", {"asin": "B0SAMPLE", "title": "T"}, ttl_seconds=3600)
        result = cache.get("product", "B0SAMPLE")
        assert result is not None
        assert result["asin"] == "B0SAMPLE"

    def test_get_returns_none_for_unknown_key(self, tmp_path: Path):
        cache = DiskCache(root=tmp_path)
        assert cache.get("product", "B0MISSING") is None

    def test_expired_entry_returns_none(self, tmp_path: Path):
        cache = DiskCache(root=tmp_path)
        cache.set("product", "B0EXPIRED", {"x": 1}, ttl_seconds=0)
        # TTL=0 means already expired — sleep a hair to ensure the wallclock
        # check fires.
        time.sleep(0.01)
        assert cache.get("product", "B0EXPIRED") is None

    def test_separate_namespaces_dont_collide(self, tmp_path: Path):
        cache = DiskCache(root=tmp_path)
        cache.set("product", "ID", {"kind": "product"}, ttl_seconds=3600)
        cache.set("seller", "ID", {"kind": "seller"}, ttl_seconds=3600)
        assert cache.get("product", "ID")["kind"] == "product"
        assert cache.get("seller", "ID")["kind"] == "seller"

    def test_cache_persists_across_instances(self, tmp_path: Path):
        # Same root dir, different cache objects — second instance reads
        # what the first wrote.
        DiskCache(root=tmp_path).set(
            "seller", "A1", {"id": "A1"}, ttl_seconds=3600
        )
        assert DiskCache(root=tmp_path).get("seller", "A1")["id"] == "A1"


# ---------------------------------------------------------------------------
# Token usage log
# ---------------------------------------------------------------------------


class TestTokenLog:
    def test_appends_entry_with_iso_timestamp(self, tmp_path: Path):
        log_path = tmp_path / "token_log.jsonl"
        append_token_log(
            log_path,
            endpoint="product",
            tokens=6,
            cached=False,
            extra={"asin": "B0XXXX"},
        )
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["endpoint"] == "product"
        assert entry["tokens"] == 6
        assert entry["cached"] is False
        assert entry["asin"] == "B0XXXX"
        # ISO 8601 with Z suffix.
        assert entry["ts"].endswith("Z")

    def test_multiple_appends_accumulate(self, tmp_path: Path):
        log_path = tmp_path / "log.jsonl"
        append_token_log(log_path, endpoint="seller", tokens=50, cached=False)
        append_token_log(log_path, endpoint="product", tokens=0, cached=True)
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2

    def test_creates_parent_directory(self, tmp_path: Path):
        nested = tmp_path / "deep" / "nested" / "token_log.jsonl"
        append_token_log(nested, endpoint="product", tokens=1, cached=False)
        assert nested.exists()


# ---------------------------------------------------------------------------
# KeepaConfig
# ---------------------------------------------------------------------------


class TestKeepaConfig:
    def test_load_from_yaml(self, tmp_path: Path):
        body = """\
api:
  base_url: https://api.keepa.com
  marketplace: 2
  request_timeout_seconds: 30
rate_limit:
  tokens_per_minute: 20
  burst: 100
  retry_on_429:
    max_retries: 3
    backoff_base_seconds: 5
    backoff_jitter_seconds: 2
cache:
  root: .cache/keepa
  ttl_seconds:
    product: 86400
    seller: 604800
    category: 2592000
batching:
  product_batch_size: 100
"""
        path = tmp_path / "keepa_client.yaml"
        path.write_text(body, encoding="utf-8")
        cfg = load_keepa_config(path)
        assert cfg.api.base_url == "https://api.keepa.com"
        assert cfg.api.marketplace == 2
        assert cfg.rate_limit.tokens_per_minute == 20
        assert cfg.rate_limit.burst == 100
        assert cfg.cache.ttl_seconds["product"] == 86400

    def test_load_from_canonical_path(self):
        # The canonical config at shared/config/keepa_client.yaml MUST
        # parse cleanly — pin it so a typo gets caught at PR time.
        repo_root = Path(__file__).resolve().parents[5]
        canonical = repo_root / "shared" / "config" / "keepa_client.yaml"
        if not canonical.exists():
            pytest.skip(f"canonical config not found: {canonical}")
        cfg = load_keepa_config(canonical)
        assert cfg.api.marketplace == 2  # UK

    def test_canonical_config_resolves_cache_root_to_repo(self):
        # Reviewer LOW (verified): the relative cache root in the canonical
        # YAML resolves to <repo>/.cache/keepa via parents[2] math in
        # config.py. Pin it with a real test so the math is locked, not
        # just hoped.
        repo_root = Path(__file__).resolve().parents[5]
        canonical = repo_root / "shared" / "config" / "keepa_client.yaml"
        if not canonical.exists():
            pytest.skip(f"canonical config not found: {canonical}")
        cfg = load_keepa_config(canonical)
        assert cfg.cache.root == repo_root / ".cache" / "keepa"

    def test_load_with_empty_yaml_uses_defaults(self, tmp_path: Path):
        # Reviewer LOW: an empty YAML should yield default-everything,
        # not crash. Defends against `pii:` typos that would otherwise
        # silently use the api block defaults.
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        cfg = load_keepa_config(path)
        assert cfg.api.marketplace == 2
        assert cfg.rate_limit.tokens_per_minute == 20


# ---------------------------------------------------------------------------
# KeepaClient (HTTP layer mocked)
# ---------------------------------------------------------------------------


def _config_for_test(tmp_path: Path) -> KeepaConfig:
    """Build a KeepaConfig that points all on-disk artefacts at tmp_path."""
    from keepa_client.config import (
        ApiConfig,
        BatchingConfig,
        CacheConfig,
        KeepaConfig,
        RateLimitConfig,
        RetryConfig,
    )

    return KeepaConfig(
        api=ApiConfig(
            base_url="https://api.keepa.test",
            marketplace=2,
            request_timeout_seconds=5,
        ),
        rate_limit=RateLimitConfig(
            tokens_per_minute=1000,  # high so tests don't sleep
            burst=10000,
            retry_on_429=RetryConfig(
                max_retries=1, backoff_base_seconds=0, backoff_jitter_seconds=0
            ),
        ),
        cache=CacheConfig(
            root=tmp_path / "keepa_cache",
            ttl_seconds={"product": 3600, "seller": 3600, "category": 3600},
        ),
        batching=BatchingConfig(product_batch_size=100),
    )


class TestKeepaClientGetSeller:
    @patch("keepa_client.client.requests.get")
    def test_calls_seller_endpoint_with_storefront_param(
        self, get_mock, tmp_path: Path
    ):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 50,
                "sellers": {
                    "A1B2C3D4E5": {
                        "sellerId": "A1B2C3D4E5",
                        "sellerName": "Acme",
                        "asinList": ["B001", "B002"],
                    }
                },
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        seller = client.get_seller("A1B2C3D4E5", storefront=True)
        assert seller.seller_id == "A1B2C3D4E5"
        assert seller.asin_list == ["B001", "B002"]

        # Verify URL parameters.
        call_args = get_mock.call_args
        params = call_args.kwargs["params"]
        assert params["seller"] == "A1B2C3D4E5"
        assert params["storefront"] == 1
        assert params["domain"] == 2

    @patch("keepa_client.client.requests.get")
    def test_caches_seller_response(self, get_mock, tmp_path: Path):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 50,
                "sellers": {
                    "A1": {
                        "sellerId": "A1", "sellerName": "X", "asinList": ["B0"]
                    }
                },
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))

        # First call hits the API.
        client.get_seller("A1", storefront=True)
        # Second call should hit cache.
        client.get_seller("A1", storefront=True)

        # API was called only once.
        assert get_mock.call_count == 1

    @patch("keepa_client.client.requests.get")
    def test_logs_token_usage(self, get_mock, tmp_path: Path):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 50,
                "sellers": {
                    "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []}
                },
            },
        )
        cfg = _config_for_test(tmp_path)
        client = KeepaClient(api_key="fake", config=cfg)
        client.get_seller("A1", storefront=True)

        log_path = cfg.cache.root / "token_log.jsonl"
        assert log_path.exists()
        entries = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines()]
        assert any(
            e["endpoint"] == "seller" and e["tokens"] == 50 and e["cached"] is False
            for e in entries
        )

    @patch("keepa_client.client.requests.get")
    def test_cached_call_logs_zero_tokens(self, get_mock, tmp_path: Path):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 50,
                "sellers": {
                    "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []}
                },
            },
        )
        cfg = _config_for_test(tmp_path)
        client = KeepaClient(api_key="fake", config=cfg)
        client.get_seller("A1", storefront=True)
        client.get_seller("A1", storefront=True)  # cached

        log_path = cfg.cache.root / "token_log.jsonl"
        entries = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines()]
        cached_entries = [e for e in entries if e["cached"] is True]
        assert len(cached_entries) == 1
        assert cached_entries[0]["tokens"] == 0

    @patch("keepa_client.client.requests.get")
    def test_500_raises_immediately_without_retry(self, get_mock, tmp_path: Path):
        # 500 is intentionally excluded from the retryable set — Keepa's
        # 500s are often deterministic (malformed param, unknown ASIN
        # format) and retrying just adds latency. 502/503/504 ARE retried;
        # see test_503_retries_then_succeeds below.
        get_mock.return_value = MagicMock(status_code=500, text="server error")
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        with pytest.raises(KeepaApiError, match="500"):
            client.get_seller("A1", storefront=True)
        # Single call, no retry on 500.
        assert get_mock.call_count == 1

    @patch("keepa_client.client.requests.get")
    def test_429_then_200_succeeds_after_retry(self, get_mock, tmp_path: Path):
        # Reviewer M3: core retry feature was previously untested.
        # 429 -> retry -> 200. With retry_on_429.max_retries=1 in
        # _config_for_test, we expect exactly 2 total HTTP calls.
        get_mock.side_effect = [
            MagicMock(status_code=429, text="rate limited"),
            MagicMock(
                status_code=200,
                json=lambda: {
                    "tokensConsumed": 50,
                    "sellers": {
                        "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []},
                    },
                },
            ),
        ]
        sleeps: list[float] = []
        client = KeepaClient(
            api_key="fake",
            config=_config_for_test(tmp_path),
            _sleep_for_tests=lambda s: sleeps.append(s),
        )
        seller = client.get_seller("A1", storefront=True)
        assert seller.seller_id == "A1"
        assert get_mock.call_count == 2

    @patch("keepa_client.client.requests.get")
    def test_persistent_429_raises_after_max_retries(
        self, get_mock, tmp_path: Path
    ):
        # 429 every attempt -> raises. With max_retries=1, expect 2 total
        # HTTP calls (initial + 1 retry).
        get_mock.return_value = MagicMock(status_code=429, text="rate limited")
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        with pytest.raises(KeepaApiError, match="429"):
            client.get_seller("A1", storefront=True)
        assert get_mock.call_count == 2

    @patch("keepa_client.client.requests.get")
    def test_503_retries_then_succeeds(self, get_mock, tmp_path: Path):
        # Reviewer M1: gateway-class 5xx (502/503/504) is now retryable.
        get_mock.side_effect = [
            MagicMock(status_code=503, text="unavailable"),
            MagicMock(
                status_code=200,
                json=lambda: {
                    "tokensConsumed": 50,
                    "sellers": {
                        "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []},
                    },
                },
            ),
        ]
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        seller = client.get_seller("A1", storefront=True)
        assert seller.seller_id == "A1"
        assert get_mock.call_count == 2

    @patch("keepa_client.client.requests.get")
    def test_token_estimate_reconciled_to_actual(self, get_mock, tmp_path: Path):
        # Reviewer M2: bucket should refund the diff between the pre-call
        # estimate (50 for /seller) and the actual tokensConsumed.
        # Two back-to-back /seller calls each consuming 10 tokens (vs
        # estimate 50): without reconciliation the bucket drifts by 80
        # over two calls; with it, drift is zero.
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 10,  # below estimate
                "sellers": {
                    "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []},
                    "A2": {"sellerId": "A2", "sellerName": "Y", "asinList": []},
                },
            },
        )
        from keepa_client.config import (
            ApiConfig, BatchingConfig, CacheConfig, KeepaConfig,
            RateLimitConfig, RetryConfig,
        )

        cfg = KeepaConfig(
            api=ApiConfig(
                base_url="https://api.keepa.test",
                marketplace=2,
                request_timeout_seconds=5,
            ),
            rate_limit=RateLimitConfig(
                tokens_per_minute=1,  # very slow refill — any sleep is observable
                burst=60,             # exactly enough for one 50-token estimate + one 10-token reconciled
                retry_on_429=RetryConfig(
                    max_retries=0, backoff_base_seconds=0, backoff_jitter_seconds=0
                ),
            ),
            cache=CacheConfig(
                root=tmp_path / "c",
                ttl_seconds={"product": 60, "seller": 60, "category": 60},
            ),
            batching=BatchingConfig(product_batch_size=100),
        )
        sleeps: list[float] = []
        client = KeepaClient(
            api_key="fake", config=cfg,
            _sleep_for_tests=lambda s: sleeps.append(s),
        )
        # First call: acquires 50 from a 60-token bucket (10 left), refunds
        # 40 → bucket back to 50.
        client.get_seller("A1", storefront=True)
        # Second call (different seller, fresh cache miss): acquires 50
        # from 50-available — no sleep needed thanks to reconciliation.
        client.get_seller("A2", storefront=True)
        assert sleeps == [], (
            "bucket should have reconciled after the first call so the "
            f"second acquire didn't block; sleeps={sleeps}"
        )


class TestKeepaClientGetProduct:
    @patch("keepa_client.client.requests.get")
    def test_get_product_returns_typed_model(self, get_mock, tmp_path: Path):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 6,
                "products": [{
                    "asin": "B0SAMPLE",
                    "title": "Sample",
                    "brand": "Acme",
                    "categoryTree": [{"catId": 1, "name": "Toys"}],
                    "csv": [],
                }],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        product = client.get_product("B0SAMPLE")
        assert isinstance(product, KeepaProduct)
        assert product.asin == "B0SAMPLE"
        assert product.title == "Sample"

    @patch("keepa_client.client.requests.get")
    def test_get_product_caches_per_asin(self, get_mock, tmp_path: Path):
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 6,
                "products": [{"asin": "B0CACHE", "title": "T", "brand": "B"}],
            },
        )
        client = KeepaClient(api_key="fake", config=_config_for_test(tmp_path))
        client.get_product("B0CACHE")
        client.get_product("B0CACHE")
        assert get_mock.call_count == 1


# ---------------------------------------------------------------------------
# End-to-end: token bucket actually engages
# ---------------------------------------------------------------------------


class TestTokenBucketEngagement:
    @patch("keepa_client.client.requests.get")
    def test_token_bucket_acquires_before_request(
        self, get_mock, tmp_path: Path
    ):
        from keepa_client.config import (
            ApiConfig, BatchingConfig, CacheConfig, KeepaConfig,
            RateLimitConfig, RetryConfig,
        )

        # Each .get() call returns the seller payload keyed to whichever
        # seller_id was requested. We can't read the seller_id from the
        # patched call here, so just return both keys in every response —
        # the client will pick the right one out by id.
        get_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokensConsumed": 50,
                "sellers": {
                    "A1": {"sellerId": "A1", "sellerName": "X", "asinList": []},
                    "A2": {"sellerId": "A2", "sellerName": "Y", "asinList": []},
                },
            },
        )

        sleeps: list[float] = []
        cfg = KeepaConfig(
            api=ApiConfig(
                base_url="https://api.keepa.test",
                marketplace=2,
                request_timeout_seconds=5,
            ),
            rate_limit=RateLimitConfig(
                tokens_per_minute=600,  # 10/sec
                burst=50,                # tight enough that 2x50-token call triggers refill
                retry_on_429=RetryConfig(
                    max_retries=0, backoff_base_seconds=0, backoff_jitter_seconds=0
                ),
            ),
            cache=CacheConfig(
                root=tmp_path / "c",
                ttl_seconds={"product": 60, "seller": 60, "category": 60},
            ),
            batching=BatchingConfig(product_batch_size=100),
        )
        # Inject our sleep spy so we can verify the bucket waited.
        client = KeepaClient(
            api_key="fake", config=cfg, _sleep_for_tests=lambda s: sleeps.append(s)
        )

        client.get_seller("A1", storefront=True)
        client.get_seller("A2", storefront=True)
        # Second call drains burst → should sleep for refill.
        assert len(sleeps) >= 1
