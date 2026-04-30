"""Tests for oa_importers — SellerAmp 2DSorter + stub registry.

Per `docs/PRD-sourcing-strategies.md` §12: target ~6 tests for the
oa_csv layer. We test the importer abstractions here; the discovery
step tests live alongside the step itself.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from oa_importers import IMPORTERS, OaCandidate, SellerAmp2DSorterImporter


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestImporterRegistry:
    def test_registry_includes_known_feeds(self):
        for feed_id in ("selleramp", "tactical_arbitrage", "oaxray"):
            assert feed_id in IMPORTERS

    def test_selleramp_importer_is_implemented(self):
        # The SellerAmp importer must be a real class with feed_id set.
        assert IMPORTERS["selleramp"].feed_id == "selleramp"
        # Calling parse() on an empty CSV should not raise NotImplementedError.
        # We verify with a concrete path test below; here we just check the
        # type contract.
        from oa_importers.selleramp import SellerAmp2DSorterImporter
        assert isinstance(IMPORTERS["selleramp"], SellerAmp2DSorterImporter)

    def test_tactical_arbitrage_stub_raises_not_implemented(self, tmp_path: Path):
        # Empty CSV is fine — the parse() call itself raises before reading.
        csv = tmp_path / "ta.csv"
        csv.write_text("ASIN\n", encoding="utf-8")
        with pytest.raises(NotImplementedError, match="Tactical Arbitrage"):
            list(IMPORTERS["tactical_arbitrage"].parse(csv))

    def test_oaxray_stub_raises_not_implemented(self, tmp_path: Path):
        csv = tmp_path / "ox.csv"
        csv.write_text("ASIN\n", encoding="utf-8")
        with pytest.raises(NotImplementedError, match="OAXray"):
            list(IMPORTERS["oaxray"].parse(csv))


# ---------------------------------------------------------------------------
# SellerAmp 2DSorter importer
# ---------------------------------------------------------------------------


class TestSellerAmpImporter:
    def _write_csv(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "selleramp.csv"
        path.write_text(body, encoding="utf-8")
        return path

    def test_parses_canonical_columns(self, tmp_path: Path):
        body = "ASIN,Buy Cost,Retail URL,Product Name\n"
        body += "B0CLEAN,12.99,https://example.com/widget,Widget Pro\n"
        path = self._write_csv(tmp_path, body)

        importer = SellerAmp2DSorterImporter()
        candidates = list(importer.parse(path))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.asin == "B0CLEAN"
        assert c.retail_cost_inc_vat == 12.99
        assert c.retail_url == "https://example.com/widget"
        assert c.retail_name == "Widget Pro"
        assert c.feed == "selleramp"

    def test_strips_currency_symbols_in_cost(self, tmp_path: Path):
        body = "ASIN,Buy Cost\nB0PRICED,£15.50\n"
        path = self._write_csv(tmp_path, body)
        candidates = list(SellerAmp2DSorterImporter().parse(path))
        assert candidates[0].retail_cost_inc_vat == 15.50

    def test_skips_rows_with_empty_asin(self, tmp_path: Path):
        body = "ASIN,Buy Cost\n,9.99\nB0VALID,12.00\n,5.50\n"
        path = self._write_csv(tmp_path, body)
        candidates = list(SellerAmp2DSorterImporter().parse(path))
        assert len(candidates) == 1
        assert candidates[0].asin == "B0VALID"

    def test_tolerates_header_case_and_whitespace(self, tmp_path: Path):
        # Real exports use Title Case with spaces.
        body = "asin , Buy_Cost , source url\nB0X,7.50,http://x\n"
        path = self._write_csv(tmp_path, body)
        candidates = list(SellerAmp2DSorterImporter().parse(path))
        assert len(candidates) == 1
        assert candidates[0].asin == "B0X"
        assert candidates[0].retail_cost_inc_vat == 7.50
        assert candidates[0].retail_url == "http://x"

    def test_missing_asin_column_raises(self, tmp_path: Path):
        body = "Title,Cost\nWidget,9.99\n"
        path = self._write_csv(tmp_path, body)
        with pytest.raises(ValueError, match="ASIN"):
            list(SellerAmp2DSorterImporter().parse(path))

    def test_missing_cost_column_raises(self, tmp_path: Path):
        body = "ASIN,Title\nB0NOPRICE,Widget\n"
        path = self._write_csv(tmp_path, body)
        with pytest.raises(ValueError, match="cost"):
            list(SellerAmp2DSorterImporter().parse(path))

    def test_handles_utf8_bom(self, tmp_path: Path):
        body = "ASIN,Buy Cost\nB0BOM,9.99\n"
        path = tmp_path / "bom.csv"
        # Write with BOM prefix.
        path.write_bytes(("﻿" + body).encode("utf-8"))
        candidates = list(SellerAmp2DSorterImporter().parse(path))
        assert len(candidates) == 1
        assert candidates[0].asin == "B0BOM"

    def test_yields_zero_for_unparseable_cost(self, tmp_path: Path):
        body = 'ASIN,Buy Cost\nB0BAD,"call us for price"\n'
        path = self._write_csv(tmp_path, body)
        candidates = list(SellerAmp2DSorterImporter().parse(path))
        assert candidates[0].retail_cost_inc_vat == 0.0


# ---------------------------------------------------------------------------
# OaCandidate dataclass
# ---------------------------------------------------------------------------


class TestOaCandidate:
    def test_immutable(self):
        c = OaCandidate(
            asin="B0", retail_url="u", retail_cost_inc_vat=1.0,
            retail_name="n", feed="selleramp",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            c.asin = "B1"  # type: ignore[misc]
