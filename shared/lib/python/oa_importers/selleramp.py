"""SellerAmp 2DSorter CSV importer.

The 2DSorter is SellerAmp's pre-filtered OA candidate export. As of v1
the column map below is a best-guess based on public documentation
screenshots — it MUST be verified against a real 2DSorter export before
production use, per PRD §15 Q2.

Tolerates header variations: matches on lowercased + whitespace-stripped
column names, so `"ASIN"` / `"asin"` / `"Asin"` all resolve. Missing
required columns (asin, retail_cost) raise; missing optional ones
(retail_url, retail_name) get sensible defaults.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable

from .base import OaCandidate

# Best-guess column-name candidates. Each tuple is (canonical_field,
# list_of_acceptable_csv_headers_in_priority_order). The importer tries
# each header in order; the first match wins.
#
# TODO(PRD §15 Q2): verify against a real SellerAmp 2DSorter export and
# tighten this map. The legacy keepa pipeline's references at
# fba_engine/_legacy_keepa/skills/skill-2-selleramp/ may have hints.
_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "asin": ("asin",),
    "retail_cost_inc_vat": (
        "buy_cost", "cost", "retail_cost", "buy_price", "retail_price",
        "cost_inc_vat", "purchase_price",
    ),
    "retail_url": (
        "retail_url", "source_url", "url", "buy_link", "retail_link",
    ),
    "retail_name": (
        "product_name", "name", "title", "retail_name",
    ),
}


def _normalise_header(h: str) -> str:
    """Lowercase + strip whitespace + collapse repeated spaces/underscores.

    `"Buy Cost"` → `"buy cost"` → matches against `"buy_cost"` candidate
    by also normalising candidates the same way at lookup time.
    """
    h = h.strip().lower()
    h = re.sub(r"[\s_-]+", "_", h)
    return h


def _resolve_columns(headers: list[str]) -> dict[str, str | None]:
    """Map canonical field name → actual CSV header to read from.

    Returns None for missing optional columns.
    """
    norm_to_actual = {_normalise_header(h): h for h in headers}
    resolved: dict[str, str | None] = {}
    for canonical, candidates in _COLUMN_CANDIDATES.items():
        actual = None
        for cand in candidates:
            cand_norm = _normalise_header(cand)
            if cand_norm in norm_to_actual:
                actual = norm_to_actual[cand_norm]
                break
        resolved[canonical] = actual
    return resolved


def _parse_money(value: str) -> float:
    """Strip £/GBP/$/€ and any non-numeric chars; parse float; bad input → 0.0.

    Mirrors the keepa-side `parse_money` but lives here so this importer
    has no dependency on `fba_engine.steps._helpers` (kept layered).
    """
    if value is None:
        return 0.0
    s = str(value).strip()
    if not s:
        return 0.0
    s = re.sub(r"[£$€]|GBP|USD|EUR", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


class SellerAmp2DSorterImporter:
    """Importer for SellerAmp 2DSorter CSV exports."""

    feed_id: str = "selleramp"

    def parse(self, csv_path: Path) -> Iterable[OaCandidate]:
        csv_path = Path(csv_path)
        # utf-8-sig handles the BOM SellerAmp's exports carry.
        with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            headers = reader.fieldnames or []
            cols = _resolve_columns(headers)

            if cols["asin"] is None:
                raise ValueError(
                    f"{csv_path}: SellerAmp 2DSorter CSV missing required "
                    f"column 'ASIN'. Got headers: {headers!r}"
                )
            if cols["retail_cost_inc_vat"] is None:
                raise ValueError(
                    f"{csv_path}: SellerAmp 2DSorter CSV missing a "
                    f"recognisable cost column "
                    f"(tried: {_COLUMN_CANDIDATES['retail_cost_inc_vat']}). "
                    f"Got headers: {headers!r}"
                )

            for row in reader:
                asin = (row.get(cols["asin"]) or "").strip()
                if not asin:
                    # Skip rows with no ASIN — common for trailing summary
                    # rows in some exports.
                    continue
                cost = _parse_money(row.get(cols["retail_cost_inc_vat"]) or "")
                retail_url = (
                    (row.get(cols["retail_url"]) or "").strip()
                    if cols["retail_url"] else ""
                )
                retail_name = (
                    (row.get(cols["retail_name"]) or "").strip()
                    if cols["retail_name"] else ""
                )
                yield OaCandidate(
                    asin=asin,
                    retail_url=retail_url,
                    retail_cost_inc_vat=cost,
                    retail_name=retail_name,
                    feed=self.feed_id,
                )
