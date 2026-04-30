"""Typed configuration for the Keepa client.

Loads `shared/config/keepa_client.yaml` (or any caller-provided path)
into a frozen `KeepaConfig` dataclass tree. The YAML schema is documented
in `docs/PRD-sourcing-strategies.md` §7.2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ApiConfig:
    base_url: str
    marketplace: int        # Keepa domain code: 2 = UK
    request_timeout_seconds: int


@dataclass
class RetryConfig:
    max_retries: int
    backoff_base_seconds: float
    backoff_jitter_seconds: float


@dataclass
class RateLimitConfig:
    tokens_per_minute: int
    burst: int
    retry_on_429: RetryConfig


@dataclass
class CacheConfig:
    root: Path
    ttl_seconds: dict[str, int] = field(default_factory=dict)


@dataclass
class BatchingConfig:
    product_batch_size: int


@dataclass
class KeepaConfig:
    api: ApiConfig
    rate_limit: RateLimitConfig
    cache: CacheConfig
    batching: BatchingConfig


def load_keepa_config(path: Path | str) -> KeepaConfig:
    """Read a YAML file at `path` and return a typed `KeepaConfig`."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    api_block = data.get("api", {})
    rate_block = data.get("rate_limit", {})
    retry_block = rate_block.get("retry_on_429", {})
    cache_block = data.get("cache", {})
    batching_block = data.get("batching", {})

    cache_root = Path(cache_block.get("root", ".cache/keepa"))
    # Resolve cache root relative to the repo root, not the cwd, so the
    # cache lives in a stable place regardless of where the caller
    # invoked the engine from.
    if not cache_root.is_absolute():
        cache_root = path.resolve().parents[2] / cache_root

    return KeepaConfig(
        api=ApiConfig(
            base_url=api_block.get("base_url", "https://api.keepa.com"),
            marketplace=int(api_block.get("marketplace", 2)),
            request_timeout_seconds=int(api_block.get("request_timeout_seconds", 30)),
        ),
        rate_limit=RateLimitConfig(
            tokens_per_minute=int(rate_block.get("tokens_per_minute", 20)),
            burst=int(rate_block.get("burst", 100)),
            retry_on_429=RetryConfig(
                max_retries=int(retry_block.get("max_retries", 3)),
                backoff_base_seconds=float(retry_block.get("backoff_base_seconds", 5)),
                backoff_jitter_seconds=float(retry_block.get("backoff_jitter_seconds", 2)),
            ),
        ),
        cache=CacheConfig(
            root=cache_root,
            ttl_seconds={
                k: int(v) for k, v in (cache_block.get("ttl_seconds") or {}).items()
            },
        ),
        batching=BatchingConfig(
            product_batch_size=int(batching_block.get("product_batch_size", 100)),
        ),
    )
