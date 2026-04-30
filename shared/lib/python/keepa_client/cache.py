"""Persistent on-disk cache for Keepa responses.

Layout (matches the SP-API MCP cache convention at `<repo>/.cache/fba-mcp/`):

    <root>/<namespace>/<key>.json

Each cached entry is a JSON file with a wrapper:

    {"expires_at": <unix_ts>, "value": <stored_value>}

Cache misses (file not found, file expired, malformed JSON) return None;
the caller is expected to re-fetch + `set()`. Stale-on-error fallback is
deferred to the next PR per the PRD §13.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class DiskCache:
    """File-system-backed key/value store with per-entry TTL."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def _path(self, namespace: str, key: str) -> Path:
        # Sanitise key so it's safe as a filesystem path. Keepa IDs (ASINs,
        # seller IDs) are alphanumeric so the strict allowlist is fine.
        safe_key = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        return self._root / namespace / f"{safe_key}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        """Return the cached value, or None if missing/expired/malformed."""
        path = self._path(namespace, key)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                wrapper = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None
        expires_at = wrapper.get("expires_at", 0)
        if time.time() >= expires_at:
            return None
        return wrapper.get("value")

    def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        *,
        ttl_seconds: int,
    ) -> None:
        """Write `value` to the cache with the given TTL in seconds."""
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        wrapper = {
            "expires_at": time.time() + ttl_seconds,
            "value": value,
        }
        # Atomic write via tmp-and-rename so a concurrent reader never sees
        # a half-written file.
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(wrapper, fh)
        tmp.replace(path)
