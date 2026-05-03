"""Internal helpers shared by the buyer-report submodules.

Underscore-prefixed module name signals "internal — don't import from
outside the buy_plan_html package".
"""
from __future__ import annotations

from typing import Any, Optional


def _num(v: Any) -> Optional[float]:
    """Coerce to float. None / NaN / non-numeric → None."""
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n:   # NaN
        return None
    return n


def _safe_get(d: dict, *path: str, default: Any = None) -> Any:
    """Walk nested dict; return default on any missing key.

    Used to read `payload["analyst"]["verdict"]`-style nested lookups
    without a chain of `.get(...) or {}` boilerplate.
    """
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur
