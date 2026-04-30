"""Tactical Arbitrage CSV importer (stub).

Per PRD §6.3 v1: Tactical Arbitrage's column layout differs from
SellerAmp 2DSorter and needs verification against a real export before
implementation. Until then, this importer is a clear-error stub —
`parse()` raises `NotImplementedError` with a message pointing the
caller at the column-mapping TODO.

To implement: copy `selleramp.py`, replace `_COLUMN_CANDIDATES` with the
actual TA export columns, set `feed_id = "tactical_arbitrage"`. Most of
SellerAmp's logic (header normalisation, row skipping, cost parsing) is
generic enough to copy verbatim.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .base import OaCandidate


class TacticalArbitrageImporter:
    feed_id: str = "tactical_arbitrage"

    def parse(self, csv_path: Path) -> Iterable[OaCandidate]:
        raise NotImplementedError(
            "Tactical Arbitrage importer not yet implemented — column map "
            "needs verification against a real TA export. See "
            "shared/lib/python/oa_importers/selleramp.py for the reference "
            "implementation; copy + replace _COLUMN_CANDIDATES."
        )
