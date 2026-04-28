import pytest
import pandas as pd
from ingest import ingest_file, ingest_directory


def test_ingest_connect_beauty_csv_returns_dataframe():
    """A valid Connect Beauty CSV returns a DataFrame with expected columns."""
    df = ingest_file("raw/price-list.csv")
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert "part_code" in df.columns
    assert "description" in df.columns
    assert "trade_price" in df.columns
    assert "barcode" in df.columns
    assert "brand" in df.columns
    assert "pack_size" in df.columns
    assert "case_price" in df.columns
    assert "source_file" in df.columns


def test_ingest_maps_manufacturer_to_brand():
    """The 'Manufacturer' column maps to 'brand' internal name."""
    df = ingest_file("raw/price-list.csv")
    assert "brand" in df.columns
    # First row should have a brand value (Barry M or Bourjois etc.)
    assert df["brand"].iloc[0].strip() != ""


def test_ingest_maps_unit_price_gbp_to_trade_price():
    """The 'Unit Price (GBP)' column maps to 'trade_price'."""
    df = ingest_file("raw/price-list.csv")
    assert "trade_price" in df.columns
    # Values should contain pound signs or numeric values
    assert df["trade_price"].notna().any()


def test_ingest_preserves_barcode_as_string():
    """Barcodes must be strings to preserve leading zeros."""
    df = ingest_file("raw/price-list.csv")
    assert pd.api.types.is_string_dtype(df["barcode"])


def test_ingest_bad_file_returns_empty_with_error():
    """A non-existent file returns empty DataFrame, not a crash."""
    df = ingest_file("raw/nonexistent.csv")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0


def test_ingest_directory_returns_combined():
    """Ingesting a directory combines all CSVs with source_file set."""
    df = ingest_directory("raw/", limit=2)
    assert isinstance(df, pd.DataFrame)
    assert df["source_file"].nunique() >= 1
