import pytest
import pandas as pd
from sourcing_engine.pipeline.ingest import ingest_file, ingest_directory


def test_ingest_abgee_pdf_returns_dataframe():
    """A valid Abgee PDF returns a DataFrame with expected columns."""
    df = ingest_file("raw/Fubbles_Spring_Summer_2026_Price_List.pdf")
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert "part_code" in df.columns
    assert "description" in df.columns
    assert "pack_size" in df.columns
    assert "trade_price" in df.columns
    assert "retail_price" in df.columns
    assert "barcode" in df.columns
    assert "source_file" in df.columns


def test_ingest_skips_header_and_category_rows():
    """Category headers (no trade price) and the template row are excluded."""
    df = ingest_file("raw/Fubbles_Spring_Summer_2026_Price_List.pdf")
    assert df["trade_price"].notna().all()
    assert not (df["description"] == "Fubbles").any()


def test_ingest_preserves_barcode_as_string():
    """Barcodes must be strings to preserve leading zeros."""
    df = ingest_file("raw/Hasbro_Spring_Summer_2026_Price_List.pdf")
    assert pd.api.types.is_string_dtype(df["barcode"])


def test_ingest_bad_file_returns_empty_with_error():
    """A non-existent file returns empty DataFrame, not a crash."""
    df = ingest_file("raw/nonexistent.pdf")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0


def test_ingest_directory_returns_combined():
    """Ingesting a directory combines all PDFs with source_file set."""
    df = ingest_directory("raw/", limit=2)
    assert isinstance(df, pd.DataFrame)
    assert df["source_file"].nunique() >= 1
