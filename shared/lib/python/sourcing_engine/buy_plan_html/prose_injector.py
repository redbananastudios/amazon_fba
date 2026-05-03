"""Prose marker injector — replaces ``<!-- prose:{asin} -->`` markers."""
from __future__ import annotations

import html as _html
import logging
import re

logger = logging.getLogger(__name__)

_MARKER_RE = re.compile(r"<!-- prose:([A-Z0-9]{10,14}) -->")
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_MAX_PROSE_CHARS = 480


def _sanitise(prose: str) -> str:
    stripped = _TAG_STRIP_RE.sub("", prose)
    collapsed = " ".join(stripped.split())
    if len(collapsed) > _MAX_PROSE_CHARS:
        collapsed = collapsed[:_MAX_PROSE_CHARS]
    return _html.escape(collapsed)


def inject_prose(html: str, prose_by_asin: dict[str, str]) -> str:
    """Walk the HTML and replace ``<!-- prose:{asin} -->`` markers.

    Idempotent: calling twice with the same input is a no-op.
    Missing prose for a marker leaves the marker in place + logs warn.
    Surplus prose entries (no matching marker) are logged + ignored.
    """
    found_asins: set[str] = set()

    def repl(match):
        asin = match.group(1)
        found_asins.add(asin)
        prose = prose_by_asin.get(asin)
        if prose is None or not prose.strip():
            logger.warning("inject_prose: no prose for ASIN %s; leaving marker", asin)
            return match.group(0)
        safe = _sanitise(prose)
        return f'<p class="prose-text">{safe}</p>'

    out = _MARKER_RE.sub(repl, html)

    extras = set(prose_by_asin.keys()) - found_asins
    for asin in extras:
        logger.warning(
            "inject_prose: prose supplied for ASIN %s but no marker found", asin,
        )

    return out
