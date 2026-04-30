"""Abstract OA-feed importer base + canonical candidate record.

`OaCandidate` is the shape every importer normalises to. Discovery step
(`fba_engine/steps/oa_csv.py`) turns these into a DataFrame to feed the
canonical engine's resolve/enrich/calculate/decide chain.

Per `docs/PRD-sourcing-strategies.md` §6.3.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable


@dataclass(frozen=True)
class OaCandidate:
    """Canonical shape every OA importer emits, regardless of source tool.

    Maps to the canonical engine's input schema (per PRD §6.4: retail_cost
    becomes `buy_cost` after the resolve step).
    """
    asin: str
    retail_url: str
    retail_cost_inc_vat: float
    retail_name: str
    feed: str   # e.g. "selleramp", "tactical_arbitrage"


@runtime_checkable
class OaFeedImporter(Protocol):
    """Protocol for OA-feed CSV importers."""

    feed_id: str

    def parse(self, csv_path: Path) -> Iterable[OaCandidate]:
        """Read the CSV at `csv_path` and yield `OaCandidate` records."""
        ...


# Module-level registry. Importers register themselves at import time
# in oa_importers/__init__.py. Lookup by `feed_id`:
#
#     from oa_importers import IMPORTERS
#     importer = IMPORTERS["selleramp"]
#     for candidate in importer.parse(Path("/path/to/export.csv")):
#         ...
IMPORTERS: dict[str, OaFeedImporter] = {}
