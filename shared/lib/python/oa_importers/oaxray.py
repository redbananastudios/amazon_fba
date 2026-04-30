"""OAXray CSV importer (stub).

Per PRD §6.3 v1: OAXray's export shape differs from SellerAmp 2DSorter
and Tactical Arbitrage. This stub raises `NotImplementedError` until a
real OAXray export is available to verify the column map.

To implement: copy `selleramp.py`, replace `_COLUMN_CANDIDATES` with the
actual OAXray export columns, set `feed_id = "oaxray"`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .base import OaCandidate


class OaxrayImporter:
    feed_id: str = "oaxray"

    def parse(self, csv_path: Path) -> Iterable[OaCandidate]:
        raise NotImplementedError(
            "OAXray importer not yet implemented — column map needs "
            "verification against a real OAXray export. See "
            "shared/lib/python/oa_importers/selleramp.py for the reference "
            "implementation; copy + replace _COLUMN_CANDIDATES."
        )
