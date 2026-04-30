"""Shared utilities for fba_engine.steps.* modules.

Each step file used to carry its own copy of `coerce_str`, `parse_money`,
`clamp`, `round_half_up`, etc. The duplication was flagged by reviewers
across step 4a / 4b / 4c.1 / 4c.2 — this module consolidates them so a
behaviour change lands in one place.

These helpers are deliberately lightweight: no state, no side effects
beyond `atomic_write`, no module-private aliases. Step files import the
canonical name; if a step keeps its own underscore-prefixed wrapper for
backwards compatibility (e.g. `decision_engine.parse_money` is part of
that module's public API tested by `test_decision_engine.py`), the
wrapper just re-exports the helper.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Callable

import pandas as pd

# ────────────────────────────────────────────────────────────────────────
# Missing-value detection.
# ────────────────────────────────────────────────────────────────────────


def is_missing(raw: object) -> bool:
    """True iff `raw` is a missing-value sentinel.

    Catches all the shapes pipeline cells legitimately arrive in:
      None, float NaN, np.nan, pandas NA / NaT.

    `pd.isna(pd.NA)` returns the array-aware boolean True — but
    `bool(pd.NA)` raises. We call `pd.isna` (which doesn't go through
    `__bool__`) and fall through if the type doesn't support pd.isna at all.
    """
    if raw is None:
        return True
    try:
        if pd.isna(raw):
            return True
    except (TypeError, ValueError):
        # Some custom objects can fail pd.isna; fall through to the
        # explicit float-NaN check below.
        pass
    if isinstance(raw, float) and math.isnan(raw):
        return True
    return False


# ────────────────────────────────────────────────────────────────────────
# String + numeric coercion.
# ────────────────────────────────────────────────────────────────────────


def coerce_str(raw: object) -> str:
    """Coerce a cell value to a clean string. Missing values -> ``""``.

    Critical: pandas NaN is a truthy float, so the naive `str(raw or "")`
    pattern returns ``"nan"`` instead of ``""``. We route through
    `is_missing` first to short-circuit on every nullable shape.
    """
    if is_missing(raw):
        return ""
    return str(raw).strip()


_GBP_RE = re.compile(r"GBP", re.IGNORECASE)
_NUMERIC_STRIP_RE = re.compile(r"[^0-9.\-]")


def parse_money(raw: object) -> float:
    """Mirror the JS `parseMoney`: strip GBP/symbols, parse float; bad input -> 0.

    Used for currency-like cells that may carry "GBP10.50", "£5", or "10.5"
    depending on the upstream phase. Missing values -> 0.0; unparseable
    -> 0.0 (the legacy JS behaviour via `parseFloat() || 0`).
    """
    if is_missing(raw):
        return 0.0
    s = str(raw)
    s = _GBP_RE.sub("", s)
    s = _NUMERIC_STRIP_RE.sub("", s).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


# ────────────────────────────────────────────────────────────────────────
# Numeric utilities.
# ────────────────────────────────────────────────────────────────────────


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp `value` to ``[lo, hi]``."""
    return max(lo, min(hi, value))


def round_half_up(value: float) -> int:
    """JS `Math.round` equivalent (half-toward-+infinity).

    Python's built-in `round()` uses banker's rounding (half-to-even), so
    `round(0.5)` is 0 and `round(2.5)` is 2 — both surprising for ports of
    JavaScript code where `Math.round(0.5)` is 1 and `Math.round(2.5)` is 3.
    `floor(value + 0.5)` matches JS for both positive and negative inputs:
    `Math.round(-0.5)` is 0 (toward +infinity) and `floor(-0.5 + 0.5)` is 0.
    """
    return int(math.floor(value + 0.5))


# ────────────────────────────────────────────────────────────────────────
# I/O.
# ────────────────────────────────────────────────────────────────────────


def atomic_write(path: Path, write_fn: Callable[[Path], None]) -> None:
    """Write to a `<path>.tmp` sibling then atomically rename.

    Prevents consumers from seeing a partial file if the run crashes
    mid-write — particularly important for the CSV/text outputs that
    downstream steps (Phase 6 decision engine, XLSX builder) consume.

    `write_fn` receives the temporary path and is responsible for the
    actual write call (e.g. `df.to_csv(tmp, ...)` or
    `tmp.write_text(content, encoding="utf-8-sig")`).
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        write_fn(tmp)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    tmp.replace(path)
