"""Token usage log for Keepa API calls.

Every API call (and every cache hit) appends a single JSON line to a
JSONL file at the cache root. Operators tail this file to see live token
consumption; the engine summarises it at run end.

Schema (per PRD §7.1):

    {"ts":"2026-04-29T10:15:23Z","endpoint":"product","tokens":6,"cached":false,"asin":"B0XXXX"}

`extra` is merged into the entry for any context fields the caller wants
to surface (asin, seller_id, batch_size, etc.). Standard fields (ts,
endpoint, tokens, cached) cannot be overridden by `extra`.

**Concurrency**: this writer is NOT safe across processes. Two concurrent
runs writing to the same log file may interleave bytes within a line.
Single-process runs only — the engine's strategy runner is single-threaded,
so this is fine in practice.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def append_token_log(
    log_path: Path,
    *,
    endpoint: str,
    tokens: int,
    cached: bool,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one entry to the token usage JSONL log."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # ISO 8601 with explicit Z suffix (UTC). Python's `isoformat()` emits
    # `+00:00` which is technically equivalent but harder to grep.
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entry: dict[str, Any] = {
        "ts": ts,
        "endpoint": endpoint,
        "tokens": tokens,
        "cached": cached,
    }
    if extra:
        for k, v in extra.items():
            if k in entry:
                continue  # don't let extra overwrite the standard fields
            entry[k] = v

    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
