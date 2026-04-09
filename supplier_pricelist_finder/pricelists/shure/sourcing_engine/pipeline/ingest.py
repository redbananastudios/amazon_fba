"""Supplier price list ingestion — supports PDF (Abgee) and CSV formats.

Supported formats:
  - Abgee PDFs: Part Code | Description | Pack Size | Trade Price | Retail Price | Carton Size | Barcode | [Comments]
  - CSV files: auto-detected column mapping based on header names
"""
import logging
import os
import re

import pandas as pd

logger = logging.getLogger(__name__)

_ABGEE_COLUMNS = [
    "part_code", "description", "pack_size", "trade_price",
    "retail_price", "carton_size", "barcode", "comments",
]

# Known CSV column mappings: csv_header_lower -> internal_name
_CSV_COLUMN_MAP = {
    "sku": "part_code",
    "product name": "description",
    "product_name": "description",
    "name": "description",
    "description": "description",
    "brand": "brand",
    "barcode": "barcode",
    "ean": "barcode",
    "price": "trade_price",
    "trade price": "trade_price",
    "trade_price": "trade_price",
    "cost": "trade_price",
    "wholesale": "trade_price",
    "retail price": "retail_price",
    "retail_price": "retail_price",
    "rrp": "retail_price",
    "pack size": "pack_size",
    "pack_size": "pack_size",
    "availability": "comments",
    "stock": "comments",
    "url": "url",
}


def ingest_file(file_path: str) -> pd.DataFrame:
    """Extract product rows from a supplier file (PDF or CSV/XLSX).
    Returns a DataFrame with normalised column names and source_file set.
    Returns an empty DataFrame on failure (never crashes).
    """
    if not os.path.isfile(file_path):
        logger.error("File not found: %s", file_path)
        return pd.DataFrame()

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".pdf":
            return _ingest_pdf(file_path)
        elif ext in (".csv", ".tsv"):
            return _ingest_csv(file_path)
        elif ext in (".xlsx", ".xls"):
            return _ingest_excel(file_path)
        else:
            logger.warning("Unsupported file type: %s", ext)
            return pd.DataFrame()
    except Exception:
        logger.exception("Failed to ingest %s", file_path)
        return pd.DataFrame()


def ingest_directory(directory: str, limit: int | None = None) -> pd.DataFrame:
    """Ingest all supported files in a directory. Returns combined DataFrame."""
    if not os.path.isdir(directory):
        logger.error("Directory not found: %s", directory)
        return pd.DataFrame()

    supported = (".pdf", ".csv", ".tsv", ".xlsx", ".xls")
    files = sorted(
        f for f in os.listdir(directory)
        if os.path.splitext(f)[1].lower() in supported
        and not f.startswith("keepa_")  # skip Keepa market data files
    )
    if limit:
        files = files[:limit]

    frames = []
    for fname in files:
        fpath = os.path.join(directory, fname)
        logger.info("Ingesting %s", fname)
        df = ingest_file(fpath)
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# --- CSV ingestion ---

def _ingest_csv(file_path: str) -> pd.DataFrame:
    """Ingest a CSV price list with auto-detected column mapping."""
    df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
    return _map_columns(df, file_path)


def _ingest_excel(file_path: str) -> pd.DataFrame:
    """Ingest an Excel price list with auto-detected column mapping."""
    df = pd.read_excel(file_path, dtype=str, keep_default_na=False)
    return _map_columns(df, file_path)


def _map_columns(df: pd.DataFrame, file_path: str) -> pd.DataFrame:
    """Map CSV/Excel columns to internal names using fuzzy header matching."""
    if df.empty:
        return pd.DataFrame()

    # Build mapping from actual columns to internal names
    col_map = {}
    for col in df.columns:
        key = col.strip().lower()
        if key in _CSV_COLUMN_MAP:
            col_map[col] = _CSV_COLUMN_MAP[key]

    if "trade_price" not in col_map.values():
        logger.warning("No price column found in %s — skipping", file_path)
        return pd.DataFrame()

    df = df.rename(columns=col_map)

    # Clean barcode field — strip "EAN " prefix, spaces, etc.
    if "barcode" in df.columns:
        df["barcode"] = df["barcode"].apply(_clean_barcode)

    # Derive supplier name from filename
    basename = os.path.splitext(os.path.basename(file_path))[0]
    supplier = basename.replace("_pricelist", "").replace("_price_list", "").replace("_", " ").title()
    df["supplier"] = supplier
    df["source_file"] = os.path.basename(file_path)

    return df


def _clean_barcode(val):
    """Clean barcode: strip 'EAN ' prefix, spaces, leading/trailing chars."""
    if not val or not str(val).strip():
        return ""
    s = str(val).strip()
    # Strip common prefixes
    s = re.sub(r"^(EAN|UPC|GTIN)\s*", "", s, flags=re.IGNORECASE)
    # Strip non-digit chars
    s = re.sub(r"[^\d]", "", s)
    return s


# --- PDF ingestion (Abgee format) ---

def _ingest_pdf(file_path: str) -> pd.DataFrame:
    """Extract product rows from an Abgee-format PDF."""
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed — cannot ingest PDFs")
        return pd.DataFrame()

    rows = _extract_rows_from_pdf(file_path)
    if not rows:
        logger.warning("No product rows extracted from %s", file_path)
        return pd.DataFrame()

    ncols = len(rows[0])
    col_names = _ABGEE_COLUMNS[:ncols]
    df = pd.DataFrame(rows, columns=col_names)
    df["source_file"] = os.path.basename(file_path)
    supplier = os.path.basename(file_path).split("_Spring")[0].replace("_", " ")
    df["supplier"] = supplier
    return df


def _extract_rows_from_pdf(file_path: str) -> list[list[str]]:
    import pdfplumber
    product_rows = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if _is_product_row(row):
                        cleaned = _clean_row(row)
                        product_rows.append(cleaned)
    return product_rows


def _is_product_row(row: list) -> bool:
    if not row or len(row) < 7:
        return False
    trade_price = row[3]
    if not trade_price or not str(trade_price).strip():
        return False
    if not any(c.isdigit() for c in str(trade_price)):
        return False
    if str(row[0]).strip().lower() == "part code":
        return False
    return True


def _clean_row(row: list) -> list[str]:
    cleaned = []
    for i, cell in enumerate(row):
        if cell is None:
            cleaned.append(None)
        else:
            val = str(cell).strip()
            if i in (3, 4) and val:
                val = re.sub(r"[^\d.]", "", val) if val else None
            cleaned.append(val if val else None)
    return cleaned
