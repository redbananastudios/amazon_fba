# Abgee Sourcing Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pipeline that ingests Abgee PDF price lists, normalises them, detects unit/case pricing, and outputs structured data ready for Keepa matching and profit calculation.

**Architecture:** PDF extraction via pdfplumber → pandas DataFrame normalisation → case/unit price detection → EAN validation → output rows ready for the match/fees/profit/decision stages (Tasks 8-12). The Abgee PDFs all share a uniform format: `[Part Code, Description, Pack Size, Trade Price, Retail Price, Carton Size, Barcode, ?Comments]`. Trade Price is ex-VAT. Pack Size is `EA` (unit) or `PK<n>` (case of n).

**Tech Stack:** Python 3.11+, pdfplumber, pandas, openpyxl, pytest

**Key data facts discovered during research:**
- 38 PDF files, ~5,720 product rows total
- 836 rows have blank barcodes → will be REJECT (no EAN)
- All PDFs have identical column structure (7 or 8 cols — some lack Comments)
- Pack Size values: `EA` (4,757 rows), `PK<n>` (963 rows where n = 3..48)
- Trade Price is always ex-VAT (Abgee is a wholesaler quoting trade/net prices)
- Retail Price = RRP inc VAT
- Barcode lengths: mostly 13 (EAN-13) and 12 (UPC-A), some short/long outliers
- No MOQ column in Abgee lists — MOQ defaults to 1

---

## File Structure

```
sourcing_engine/
├── config.py                     # EXISTS — all thresholds (done)
├── main.py                       # Entry point — orchestrates pipeline
├── pipeline/
│   ├── ingest.py                 # PDF table extraction → raw DataFrame
│   ├── normalise.py              # Column mapping + VAT resolution
│   ├── case_detection.py         # Pack Size parsing → unit/case cost derivation
│   ├── match.py                  # EAN → ASIN matching (Keepa stub)
│   ├── market_data.py            # Keepa data extraction (stub)
│   ├── fees.py                   # FBA + FBM fee calculation
│   ├── conservative_price.py     # 15th percentile historical pricing
│   ├── profit.py                 # Profit + margin engine
│   └── decision.py               # SHORTLIST / REVIEW / REJECT
├── output/
│   ├── csv_writer.py             # Full schema CSV
│   ├── excel_writer.py           # Colour-coded Excel
│   └── markdown_report.py        # Per-supplier markdown report
├── utils/
│   ├── ean_validator.py          # EXISTS — EAN checksum (done)
│   └── flags.py                  # EXISTS — risk flag constants (done)
├── tests/
│   ├── test_ingest.py            # PDF extraction tests
│   ├── test_normalise.py         # VAT resolution tests
│   ├── test_case_detection.py    # Unit/case detection tests (PRD-specified)
│   ├── test_profit.py            # Profit calc tests (PRD-specified)
│   ├── test_decision.py          # Decision engine tests (PRD-specified)
│   └── fixtures/
│       └── sample_abgee.pdf      # Small test PDF (generated)
└── requirements.txt              # EXISTS (done)
```

---

## Task 1: PDF Ingest (`pipeline/ingest.py`)

**Files:**
- Create: `sourcing_engine/pipeline/ingest.py`
- Create: `sourcing_engine/tests/test_ingest.py`
- Create: `sourcing_engine/tests/fixtures/sample_abgee.pdf` (use a real PDF from raw/)

- [ ] **Step 1: Write the failing test for single-file PDF extraction**

```python
# sourcing_engine/tests/test_ingest.py
import pytest
import pandas as pd
from sourcing_engine.pipeline.ingest import ingest_file


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
    # No row should have a blank trade_price
    assert df["trade_price"].notna().all()
    # No row should have description == "Fubbles" (category header)
    assert not (df["description"] == "Fubbles").any()


def test_ingest_preserves_barcode_as_string():
    """Barcodes must be strings to preserve leading zeros."""
    df = ingest_file("raw/Hasbro_Spring_Summer_2026_Price_List.pdf")
    assert df["barcode"].dtype == object  # pandas string type


def test_ingest_bad_file_returns_empty_with_error():
    """A non-existent file returns empty DataFrame, not a crash."""
    df = ingest_file("raw/nonexistent.pdf")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0


def test_ingest_directory_returns_combined():
    """Ingesting a directory combines all PDFs with source_file set."""
    from sourcing_engine.pipeline.ingest import ingest_directory
    df = ingest_directory("raw/", limit=2)
    assert isinstance(df, pd.DataFrame)
    assert df["source_file"].nunique() >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/test_ingest.py -v`
Expected: FAIL — `ingest_file` not defined

- [ ] **Step 3: Implement `ingest.py`**

```python
# sourcing_engine/pipeline/ingest.py
"""Abgee PDF price list extraction.

All Abgee PDFs share a uniform table format:
  Part Code | Description | Pack Size | Trade Price | Retail Price | Carton Size | Barcode | [Comments]

Trade Price is ex-VAT. Retail Price is RRP inc-VAT.
"""
import logging
import os
import re

import pandas as pd
import pdfplumber

logger = logging.getLogger(__name__)

# Standard Abgee column mapping (positional — columns are always in this order)
_ABGEE_COLUMNS = [
    "part_code",
    "description",
    "pack_size",
    "trade_price",
    "retail_price",
    "carton_size",
    "barcode",
    "comments",
]


def ingest_file(file_path: str) -> pd.DataFrame:
    """Extract product rows from a single Abgee PDF price list.

    Returns a DataFrame with normalised column names and source_file set.
    Returns an empty DataFrame on failure (never crashes).
    """
    if not os.path.isfile(file_path):
        logger.error("File not found: %s", file_path)
        return pd.DataFrame()

    try:
        rows = _extract_rows_from_pdf(file_path)
    except Exception:
        logger.exception("Failed to extract tables from %s", file_path)
        return pd.DataFrame()

    if not rows:
        logger.warning("No product rows extracted from %s", file_path)
        return pd.DataFrame()

    # Build DataFrame — use only as many column names as we have data columns
    ncols = len(rows[0])
    col_names = _ABGEE_COLUMNS[:ncols]
    df = pd.DataFrame(rows, columns=col_names)
    df["source_file"] = os.path.basename(file_path)

    # Derive supplier name from filename
    supplier = os.path.basename(file_path).split("_Spring")[0].replace("_", " ")
    df["supplier"] = supplier

    return df


def ingest_directory(directory: str, limit: int | None = None) -> pd.DataFrame:
    """Ingest all PDF files in a directory. Returns combined DataFrame.

    Args:
        directory: Path to directory containing PDF files.
        limit: Max number of files to process (for testing). None = all.
    """
    if not os.path.isdir(directory):
        logger.error("Directory not found: %s", directory)
        return pd.DataFrame()

    pdf_files = sorted(
        f for f in os.listdir(directory) if f.lower().endswith(".pdf")
    )
    if limit:
        pdf_files = pdf_files[:limit]

    frames = []
    for fname in pdf_files:
        fpath = os.path.join(directory, fname)
        logger.info("Ingesting %s", fname)
        df = ingest_file(fpath)
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _extract_rows_from_pdf(file_path: str) -> list[list[str]]:
    """Extract all product rows from all pages of an Abgee PDF."""
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
    """Return True if this row contains product data (not a header or category)."""
    if not row or len(row) < 7:
        return False
    # Must have a trade price (column index 3)
    trade_price = row[3]
    if not trade_price or not str(trade_price).strip():
        return False
    # Trade price must look like a price (contains a digit)
    if not any(c.isdigit() for c in str(trade_price)):
        return False
    # Skip the literal header row
    if str(row[0]).strip().lower() == "part code":
        return False
    return True


def _clean_row(row: list) -> list[str]:
    """Clean a single row: strip whitespace, normalise price strings."""
    cleaned = []
    for i, cell in enumerate(row):
        if cell is None:
            cleaned.append(None)
        else:
            val = str(cell).strip()
            # Clean price columns (indices 3, 4) — remove £ sign and whitespace
            if i in (3, 4) and val:
                val = val.replace("£", "").replace("¬£", "").replace("\u00a3", "").strip()
                # Handle the Â£ mojibake from PDF extraction
                val = re.sub(r"[^\d.]", "", val) if val else None
            cleaned.append(val if val else None)
    return cleaned
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/test_ingest.py -v`
Expected: All PASS

- [ ] **Step 5: Smoke-test against all 38 PDFs**

Run: `cd O:/fba/pricelists/abgee && python -c "from sourcing_engine.pipeline.ingest import ingest_directory; df = ingest_directory('raw/'); print(f'Rows: {len(df)}, Suppliers: {df[\"supplier\"].nunique()}, Columns: {list(df.columns)}')" `
Expected: ~5,700 rows, 38 suppliers

- [ ] **Step 6: Commit**

```bash
git add sourcing_engine/pipeline/ingest.py sourcing_engine/tests/test_ingest.py
git commit -m "feat: add PDF ingest for Abgee price lists"
```

---

## Task 2: Normalisation (`pipeline/normalise.py`)

**Files:**
- Create: `sourcing_engine/pipeline/normalise.py`
- Create: `sourcing_engine/tests/test_normalise.py`

- [ ] **Step 1: Write failing tests for VAT resolution and schema normalisation**

```python
# sourcing_engine/tests/test_normalise.py
import pytest
from sourcing_engine.pipeline.normalise import resolve_buy_cost, normalise
from sourcing_engine.config import VAT_RATE, VAT_MISMATCH_TOLERANCE


def test_resolve_ex_vat_only():
    """Ex-VAT only: buy_cost = ex * 1.20, no flag."""
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=10.00, cost_inc_vat=None)
    assert buy_cost == pytest.approx(12.00)
    assert flag is None


def test_resolve_inc_vat_only():
    """Inc-VAT only: buy_cost = inc, no flag."""
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=None, cost_inc_vat=12.00)
    assert buy_cost == pytest.approx(12.00)
    assert flag is None


def test_resolve_both_consistent():
    """Both provided, consistent: buy_cost = inc, no flag."""
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=10.00, cost_inc_vat=12.00)
    assert buy_cost == pytest.approx(12.00)
    assert flag is None


def test_resolve_both_within_tolerance():
    """Both provided, within rounding tolerance: no flag."""
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=10.00, cost_inc_vat=12.01)
    assert buy_cost == pytest.approx(12.01)
    assert flag is None


def test_resolve_both_conflict():
    """Both provided, conflict > tolerance: buy_cost = inc, flag VAT_FIELD_MISMATCH."""
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=10.00, cost_inc_vat=13.00)
    assert buy_cost == pytest.approx(13.00)
    assert flag == "VAT_FIELD_MISMATCH"


def test_resolve_neither():
    """Neither provided: buy_cost = None, flag VAT_UNCLEAR."""
    buy_cost, flag = resolve_buy_cost(cost_ex_vat=None, cost_inc_vat=None)
    assert buy_cost is None
    assert flag == "VAT_UNCLEAR"


def test_normalise_abgee_row():
    """Abgee row normalisation: trade_price is ex-VAT, pack_size parsed."""
    import pandas as pd
    raw = pd.DataFrame([{
        "part_code": "285 E7876",
        "description": "Avengers Titan Hero Black Panther",
        "pack_size": "EA",
        "trade_price": "6.24",
        "retail_price": "9.99",
        "carton_size": "4",
        "barcode": "5010996214669",
        "source_file": "Hasbro.pdf",
        "supplier": "Hasbro",
    }])
    result = normalise(raw)
    row = result.iloc[0]
    assert row["ean"] == "5010996214669"
    assert row["supplier_price_ex_vat"] == pytest.approx(6.24)
    assert row["supplier_price_inc_vat"] == pytest.approx(6.24 * 1.20)
    assert row["rrp_inc_vat"] == pytest.approx(9.99)
    assert row["supplier_price_basis"] == "UNIT"  # EA = unit
    assert row["case_qty"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/test_normalise.py -v`

- [ ] **Step 3: Implement `normalise.py`**

```python
# sourcing_engine/pipeline/normalise.py
"""Schema normalisation and VAT resolution for Abgee price lists.

Abgee-specific: Trade Price is always ex-VAT. There is no inc-VAT column.
VAT resolution simplifies to: buy_cost = trade_price * 1.20.
"""
import logging
import re

import pandas as pd

from sourcing_engine.config import VAT_RATE, VAT_MISMATCH_TOLERANCE
from sourcing_engine.utils.ean_validator import validate_ean
from sourcing_engine.utils.flags import VAT_FIELD_MISMATCH, VAT_UNCLEAR

logger = logging.getLogger(__name__)


def resolve_buy_cost(
    cost_ex_vat: float | None,
    cost_inc_vat: float | None,
    vat_rate: float = VAT_RATE,
    tolerance: float = VAT_MISMATCH_TOLERANCE,
) -> tuple[float | None, str | None]:
    """Resolve buy cost from supplier VAT fields.

    Returns (buy_cost, flag_or_none).
    See PRD section 2.4 for the four states.
    """
    has_ex = cost_ex_vat is not None
    has_inc = cost_inc_vat is not None

    if has_ex and has_inc:
        expected_inc = cost_ex_vat * (1 + vat_rate)
        if abs(cost_inc_vat - expected_inc) > tolerance:
            return cost_inc_vat, VAT_FIELD_MISMATCH
        return cost_inc_vat, None

    if has_inc and not has_ex:
        return cost_inc_vat, None

    if has_ex and not has_inc:
        return cost_ex_vat * (1 + vat_rate), None

    # Neither provided
    return None, VAT_UNCLEAR


def normalise(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Normalise an Abgee raw DataFrame into the standard schema.

    Abgee columns: part_code, description, pack_size, trade_price,
    retail_price, carton_size, barcode, comments, source_file, supplier.

    Trade Price = ex-VAT. No inc-VAT column exists.
    Pack Size = 'EA' (unit) or 'PK<n>' (case of n).
    """
    if raw_df.empty:
        return pd.DataFrame()

    rows = []
    for idx, raw in raw_df.iterrows():
        try:
            row = _normalise_row(raw)
            if row is not None:
                rows.append(row)
        except Exception:
            logger.exception(
                "[%s] [ROW_%s] [%s] — normalisation error",
                raw.get("supplier", "UNKNOWN"),
                idx,
                raw.get("barcode", "NO_EAN"),
            )
            continue

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


def _normalise_row(raw: pd.Series) -> dict | None:
    """Normalise a single raw row into the standard schema."""
    # Parse trade price (ex-VAT)
    trade_price = _parse_price(raw.get("trade_price"))
    if trade_price is None or trade_price <= 0:
        return None  # skip rows with no valid price

    # Parse retail price (RRP inc-VAT)
    rrp = _parse_price(raw.get("retail_price"))

    # Parse pack size → case_qty and price basis
    pack_size_raw = str(raw.get("pack_size", "")).strip().upper()
    case_qty, price_basis = _parse_pack_size(pack_size_raw)

    # Barcode → EAN
    barcode = str(raw.get("barcode", "")).strip() if raw.get("barcode") else ""

    # VAT resolution — Abgee provides ex-VAT only
    supplier_price_ex_vat = trade_price
    buy_cost, vat_flag = resolve_buy_cost(
        cost_ex_vat=supplier_price_ex_vat, cost_inc_vat=None
    )
    supplier_price_inc_vat = buy_cost  # ex * 1.20

    # EAN validation
    ean_valid = validate_ean(barcode) if barcode else False

    # Risk flags
    risk_flags = []
    if vat_flag:
        risk_flags.append(vat_flag)

    return {
        "supplier": raw.get("supplier", ""),
        "source_file": raw.get("source_file", ""),
        "supplier_sku": str(raw.get("part_code", "")).strip(),
        "ean": barcode if barcode else None,
        "ean_valid": ean_valid,
        "product_name": str(raw.get("description", "")).strip(),
        "brand": "",  # not in Abgee data — derived from supplier/description later
        "supplier_price_ex_vat": supplier_price_ex_vat,
        "supplier_price_inc_vat": supplier_price_inc_vat,
        "supplier_price_basis": price_basis,
        "case_qty": case_qty,
        "rrp_inc_vat": rrp,
        "moq": 1,  # Abgee does not specify MOQ in price lists
        "stock_status": str(raw.get("comments", "")).strip() if raw.get("comments") else None,
        "carton_size": _parse_int(raw.get("carton_size")),
        "risk_flags": risk_flags,
    }


def _parse_pack_size(pack_size: str) -> tuple[int, str]:
    """Parse Abgee Pack Size into (case_qty, price_basis).

    EA → (1, "UNIT")
    PK<n> → (n, "CASE")   — trade price is per case of n units
    """
    if not pack_size or pack_size == "EA":
        return 1, "UNIT"

    match = re.match(r"PK(\d+)", pack_size)
    if match:
        qty = int(match.group(1))
        if qty <= 0:
            return 1, "UNIT"
        if qty == 1:
            return 1, "UNIT"
        return qty, "CASE"

    # Unknown format — treat as unit, flag ambiguous
    return 1, "UNIT"


def _parse_price(val) -> float | None:
    """Parse a price value, handling currency symbols and whitespace."""
    if val is None:
        return None
    s = str(val).strip()
    # Remove currency symbols and any non-numeric chars except dot
    s = re.sub(r"[^\d.]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(val) -> int | None:
    """Parse an integer, returning None on failure."""
    if val is None:
        return None
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/test_normalise.py -v`

- [ ] **Step 5: Commit**

```bash
git add sourcing_engine/pipeline/normalise.py sourcing_engine/tests/test_normalise.py
git commit -m "feat: add normalisation and VAT resolution"
```

---

## Task 3: Case Detection (`pipeline/case_detection.py`)

**Files:**
- Create: `sourcing_engine/pipeline/case_detection.py`
- Create: `sourcing_engine/tests/test_case_detection.py`

- [ ] **Step 1: Write the PRD-specified failing tests**

```python
# sourcing_engine/tests/test_case_detection.py
import pytest
from sourcing_engine.pipeline.case_detection import derive_costs
from sourcing_engine.utils.flags import (
    PRICE_BASIS_AMBIGUOUS, CASE_QTY_UNKNOWN, CASE_MATCH_SKIPPED,
)


def test_explicit_case_price_column_detected():
    """When pack_size is PK6 and basis is CASE, case_cost = supplier_price."""
    result = derive_costs(
        supplier_price_ex_vat=24.00,
        supplier_price_basis="CASE",
        case_qty=6,
        rrp_inc_vat=9.99,
    )
    assert result["case_cost_ex_vat"] == pytest.approx(24.00)
    assert result["unit_cost_ex_vat"] == pytest.approx(4.00)
    assert result["case_cost_inc_vat"] == pytest.approx(24.00 * 1.20)
    assert result["unit_cost_inc_vat"] == pytest.approx(4.00 * 1.20)


def test_explicit_unit_price_column_detected():
    """When pack_size is EA and basis is UNIT, unit_cost = supplier_price."""
    result = derive_costs(
        supplier_price_ex_vat=6.24,
        supplier_price_basis="UNIT",
        case_qty=1,
        rrp_inc_vat=9.99,
    )
    assert result["unit_cost_ex_vat"] == pytest.approx(6.24)
    assert result["unit_cost_inc_vat"] == pytest.approx(6.24 * 1.20)
    assert result["case_cost_ex_vat"] is None  # case_qty == 1 → no case cost
    assert result["case_cost_inc_vat"] is None


def test_implied_price_below_threshold_flagged_as_case():
    """If implied unit price < MIN_PLAUSIBLE_UNIT_PRICE, basis should be CASE."""
    # supplier_price = 2.00, case_qty = 12 → implied unit = 0.17 → below £0.50
    from sourcing_engine.pipeline.case_detection import detect_price_basis
    from sourcing_engine.config import MIN_PLAUSIBLE_UNIT_PRICE
    basis = detect_price_basis(
        supplier_price_ex_vat=2.00,
        case_qty=12,
        rrp_inc_vat=None,
        column_hint=None,
    )
    assert basis == "CASE"


def test_ambiguous_routes_to_review():
    """When price basis can't be determined, derive_costs returns AMBIGUOUS with null costs."""
    result = derive_costs(
        supplier_price_ex_vat=5.00,
        supplier_price_basis="AMBIGUOUS",
        case_qty=6,
        rrp_inc_vat=None,
    )
    assert result["unit_cost_ex_vat"] is None
    assert result["case_cost_ex_vat"] is None
    assert PRICE_BASIS_AMBIGUOUS in result["flags"]


def test_case_qty_null_treated_as_unit():
    """case_qty=None → treat as unit, flag CASE_QTY_UNKNOWN."""
    result = derive_costs(
        supplier_price_ex_vat=5.00,
        supplier_price_basis="UNIT",
        case_qty=None,
        rrp_inc_vat=None,
    )
    assert result["unit_cost_ex_vat"] == pytest.approx(5.00)
    assert result["case_cost_ex_vat"] is None
    assert CASE_QTY_UNKNOWN in result["flags"]


def test_case_qty_zero_treated_as_one():
    """case_qty=0 is a data error → treat as 1."""
    result = derive_costs(
        supplier_price_ex_vat=5.00,
        supplier_price_basis="UNIT",
        case_qty=0,
        rrp_inc_vat=None,
    )
    assert result["unit_cost_ex_vat"] == pytest.approx(5.00)
    assert result["case_cost_ex_vat"] is None  # qty=1 → no case variant


def test_case_qty_1_no_duplicate_row():
    """case_qty=1 → unit and case are identical. case_cost must be None."""
    result = derive_costs(
        supplier_price_ex_vat=5.00,
        supplier_price_basis="UNIT",
        case_qty=1,
        rrp_inc_vat=None,
    )
    assert result["unit_cost_ex_vat"] == pytest.approx(5.00)
    assert result["case_cost_ex_vat"] is None
    assert result["case_cost_inc_vat"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/test_case_detection.py -v`

- [ ] **Step 3: Implement `case_detection.py`**

```python
# sourcing_engine/pipeline/case_detection.py
"""Unit vs case price detection and cost derivation.

For Abgee specifically:
- Pack Size = 'EA' → UNIT (trade price is per unit)
- Pack Size = 'PK<n>' → CASE (trade price is per case of n units)

The general detection logic from the PRD (section 3.2b) is also implemented
for future use with other suppliers where the basis is not explicit.
"""
from sourcing_engine.config import VAT_RATE, MIN_PLAUSIBLE_UNIT_PRICE
from sourcing_engine.utils.flags import (
    PRICE_BASIS_AMBIGUOUS,
    CASE_QTY_UNKNOWN,
    CASE_MATCH_SKIPPED,
)


def detect_price_basis(
    supplier_price_ex_vat: float,
    case_qty: int | None,
    rrp_inc_vat: float | None,
    column_hint: str | None,
) -> str:
    """Detect whether the supplier price is per UNIT or per CASE.

    Detection priority per PRD 3.2b:
    1. Explicit column header hint
    2. Implied unit price heuristic
    3. RRP comparison
    4. AMBIGUOUS
    """
    # Priority 1: explicit column hint (already resolved for Abgee in normalise)
    if column_hint:
        hint = column_hint.upper()
        if hint in ("UNIT", "CASE"):
            return hint

    # If no case_qty or case_qty <= 1, it's a unit price
    if case_qty is None or case_qty <= 1:
        return "UNIT"

    # Priority 2: implied unit price heuristic
    implied_unit_price = supplier_price_ex_vat / case_qty
    if implied_unit_price < MIN_PLAUSIBLE_UNIT_PRICE:
        return "CASE"

    # Priority 3: RRP comparison
    if rrp_inc_vat is not None and rrp_inc_vat > 0:
        if supplier_price_ex_vat > (rrp_inc_vat * 0.90):
            return "UNIT"

    # Priority 4: cannot determine
    return "AMBIGUOUS"


def derive_costs(
    supplier_price_ex_vat: float,
    supplier_price_basis: str,
    case_qty: int | None,
    rrp_inc_vat: float | None,
    vat_rate: float = VAT_RATE,
) -> dict:
    """Derive unit and case costs from supplier price and basis.

    Returns dict with:
        unit_cost_ex_vat, unit_cost_inc_vat,
        case_cost_ex_vat, case_cost_inc_vat,
        flags: list of any flags raised
    """
    flags = []

    # Handle null/zero case_qty
    if case_qty is None:
        flags.append(CASE_QTY_UNKNOWN)
        case_qty = 1  # treat as unit for cost derivation
    elif case_qty <= 0:
        case_qty = 1  # data error → treat as 1

    # AMBIGUOUS — do not derive costs
    if supplier_price_basis == "AMBIGUOUS":
        flags.append(PRICE_BASIS_AMBIGUOUS)
        flags.append(CASE_MATCH_SKIPPED)
        return {
            "unit_cost_ex_vat": None,
            "unit_cost_inc_vat": None,
            "case_cost_ex_vat": None,
            "case_cost_inc_vat": None,
            "case_qty": case_qty,
            "flags": flags,
        }

    # Derive costs based on basis
    if supplier_price_basis == "UNIT":
        unit_ex = supplier_price_ex_vat
        unit_inc = unit_ex * (1 + vat_rate)
        if case_qty > 1:
            case_ex = unit_ex * case_qty
            case_inc = case_ex * (1 + vat_rate)
        else:
            case_ex = None
            case_inc = None

    elif supplier_price_basis == "CASE":
        case_ex = supplier_price_ex_vat
        case_inc = case_ex * (1 + vat_rate)
        unit_ex = supplier_price_ex_vat / case_qty
        unit_inc = unit_ex * (1 + vat_rate)
        if case_qty == 1:
            # case_qty=1 means unit and case are identical — don't duplicate
            case_ex = None
            case_inc = None
    else:
        # Unknown basis — treat as ambiguous
        flags.append(PRICE_BASIS_AMBIGUOUS)
        return {
            "unit_cost_ex_vat": None,
            "unit_cost_inc_vat": None,
            "case_cost_ex_vat": None,
            "case_cost_inc_vat": None,
            "case_qty": case_qty,
            "flags": flags,
        }

    return {
        "unit_cost_ex_vat": unit_ex,
        "unit_cost_inc_vat": unit_inc,
        "case_cost_ex_vat": case_ex,
        "case_cost_inc_vat": case_inc,
        "case_qty": case_qty,
        "flags": flags,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/test_case_detection.py -v`

- [ ] **Step 5: Commit**

```bash
git add sourcing_engine/pipeline/case_detection.py sourcing_engine/tests/test_case_detection.py
git commit -m "feat: add case detection and cost derivation"
```

---

## Task 4: Fee Engine (`pipeline/fees.py`)

**Files:**
- Create: `sourcing_engine/pipeline/fees.py`
- Tests inline in `test_profit.py` (Task 5)

- [ ] **Step 1: Implement `fees.py`**

```python
# sourcing_engine/pipeline/fees.py
"""Fee calculation — FBA and FBM paths are strictly separate.

FBA: referral_fee + fba_fulfilment_fee + storage_fee
FBM: referral_fee + shipping + packaging  (NO fba_fee, NO storage_fee)

Fees are calculated at each price point independently.
"""
from sourcing_engine.config import (
    DEFAULT_REFERRAL_FEE_PCT,
    FBA_FEE_CONSERVATIVE_FALLBACK,
    FBM_SHIPPING_ESTIMATE,
    FBM_PACKAGING_ESTIMATE,
    STORAGE_RISK_THRESHOLD,
)
from sourcing_engine.utils.flags import (
    SIZE_TIER_UNKNOWN,
    FBM_SHIPPING_ESTIMATED,
    STORAGE_FEE_RISK,
)

# UK Amazon FBA fulfilment fees by size tier (approximate — 2024/2025 rates)
_FBA_FEES_BY_TIER = {
    "small_envelope": 1.78,
    "standard_envelope": 2.10,
    "large_envelope": 2.45,
    "small_parcel": 3.07,
    "standard_parcel": 3.68,
    "small_oversize": 5.90,
    "standard_oversize": 7.31,
    "large_oversize": 13.84,
}

# Monthly storage rate per cubic foot (standard, non-Q4)
_STORAGE_RATE_PER_CBFT = 0.75  # approximate UK rate


def calculate_fees_fba(
    sell_price: float,
    size_tier: str | None,
    product_volume_cbft: float | None = None,
    sales_estimate: float | None = None,
    referral_fee_pct: float = DEFAULT_REFERRAL_FEE_PCT,
) -> dict:
    """Calculate FBA fees for a given sell price.

    Returns: {referral_fee, fba_fee, storage_fee, total, flags}
    """
    flags = []

    referral_fee = sell_price * referral_fee_pct

    # FBA fulfilment fee — by size tier, or fallback
    if size_tier and size_tier.lower() != "unknown":
        fba_fee = _FBA_FEES_BY_TIER.get(size_tier.lower(), FBA_FEE_CONSERVATIVE_FALLBACK)
    else:
        fba_fee = FBA_FEE_CONSERVATIVE_FALLBACK
        flags.append(SIZE_TIER_UNKNOWN)

    # Storage fee estimate
    storage_fee = 0.0
    if product_volume_cbft and sales_estimate and sales_estimate > 0:
        storage_fee = (product_volume_cbft * _STORAGE_RATE_PER_CBFT) / sales_estimate
    if sales_estimate is not None and sales_estimate < STORAGE_RISK_THRESHOLD:
        flags.append(STORAGE_FEE_RISK)

    total = referral_fee + fba_fee + storage_fee

    return {
        "referral_fee": referral_fee,
        "fba_fee": fba_fee,
        "storage_fee": storage_fee,
        "total": total,
        "flags": flags,
    }


def calculate_fees_fbm(
    sell_price: float,
    referral_fee_pct: float = DEFAULT_REFERRAL_FEE_PCT,
    shipping: float = FBM_SHIPPING_ESTIMATE,
    packaging: float = FBM_PACKAGING_ESTIMATE,
) -> dict:
    """Calculate FBM fees for a given sell price.

    NO fba_fee. NO storage_fee.
    Returns: {referral_fee, shipping, packaging, total, flags}
    """
    referral_fee = sell_price * referral_fee_pct

    total = referral_fee + shipping + packaging

    return {
        "referral_fee": referral_fee,
        "shipping": shipping,
        "packaging": packaging,
        "fba_fee": 0.0,
        "storage_fee": 0.0,
        "total": total,
        "flags": [FBM_SHIPPING_ESTIMATED],
    }
```

- [ ] **Step 2: Commit**

```bash
git add sourcing_engine/pipeline/fees.py
git commit -m "feat: add FBA and FBM fee engines"
```

---

## Task 5: Profit Engine + Fee Tests (`pipeline/profit.py`, `test_profit.py`)

**Files:**
- Create: `sourcing_engine/pipeline/profit.py`
- Create: `sourcing_engine/tests/test_profit.py`

- [ ] **Step 1: Write the PRD-specified failing tests**

```python
# sourcing_engine/tests/test_profit.py
import pytest
from sourcing_engine.pipeline.profit import calculate_profit
from sourcing_engine.pipeline.fees import calculate_fees_fba, calculate_fees_fbm
from sourcing_engine.config import MIN_PROFIT


def test_profit_uses_raw_conservative_not_floored():
    """profit_conservative must use raw_conservative_price, never floored."""
    market_price = 20.00
    raw_conservative = 12.00
    floored_conservative = 15.00  # floored higher — must NOT be used
    buy_cost = 8.00
    fees_current = calculate_fees_fba(market_price, "small_parcel")
    fees_conservative = calculate_fees_fba(raw_conservative, "small_parcel")

    result = calculate_profit(
        market_price=market_price,
        raw_conservative_price=raw_conservative,
        fees_current=fees_current,
        fees_conservative=fees_conservative,
        buy_cost=buy_cost,
    )
    # profit_conservative = raw_conservative - fees_conservative['total'] - buy_cost
    expected = raw_conservative - fees_conservative["total"] - buy_cost
    assert result["profit_conservative"] == pytest.approx(expected)
    # Ensure it does NOT equal the floored version
    wrong = floored_conservative - fees_conservative["total"] - buy_cost
    if abs(expected - wrong) > 0.01:
        assert result["profit_conservative"] != pytest.approx(wrong)


def test_price_floor_hit_flag_set_correctly():
    """PRICE_FLOOR_HIT flag is set when raw_conservative < break-even floor."""
    from sourcing_engine.pipeline.conservative_price import calculate_conservative_price
    # Create price history where 15th percentile is very low
    # 90 days of data, all at £5.00
    history = [(i, 5.00, 2) for i in range(90)]
    buy_cost = 8.00
    fees_conservative = calculate_fees_fba(5.00, "small_parcel")

    raw, floored, flag = calculate_conservative_price(
        price_history=history,
        market_price=10.00,
        buy_cost=buy_cost,
        fees_conservative_total=fees_conservative["total"],
    )
    # raw should be 5.00 (min of market 10.00 and percentile 5.00)
    # floor = buy_cost + fees + MIN_PROFIT = 8.00 + fees + 3.00
    # Since 5.00 < floor, PRICE_FLOOR_HIT should be set
    assert flag == "PRICE_FLOOR_HIT"
    assert raw == pytest.approx(5.00)
    assert floored > raw  # floored is raised to break-even


def test_fbm_fee_path_no_fba_fee():
    """FBM fee path must not include FBA fulfilment or storage fees."""
    fees = calculate_fees_fbm(20.00)
    assert fees["fba_fee"] == 0.0
    assert fees["storage_fee"] == 0.0
    assert fees["shipping"] > 0
    assert fees["packaging"] > 0


def test_fba_fee_path_no_shipping_cost():
    """FBA fee path must not include shipping or packaging costs."""
    fees = calculate_fees_fba(20.00, "small_parcel")
    assert "shipping" not in fees or fees.get("shipping", 0) == 0
    assert "packaging" not in fees or fees.get("packaging", 0) == 0
    assert fees["fba_fee"] > 0


def test_case_match_uses_case_cost():
    """When match_type is CASE, buy_cost = case_cost_inc_vat."""
    case_cost_inc_vat = 24.00
    unit_cost_inc_vat = 4.00
    market_price = 30.00
    fees = calculate_fees_fba(market_price, "small_parcel")
    fees_cons = calculate_fees_fba(25.00, "small_parcel")

    result = calculate_profit(
        market_price=market_price,
        raw_conservative_price=25.00,
        fees_current=fees,
        fees_conservative=fees_cons,
        buy_cost=case_cost_inc_vat,  # CASE match → case cost
    )
    assert result["profit_current"] == pytest.approx(
        market_price - fees["total"] - case_cost_inc_vat
    )


def test_unit_match_uses_unit_cost():
    """When match_type is UNIT, buy_cost = unit_cost_inc_vat."""
    unit_cost_inc_vat = 7.49
    market_price = 20.00
    fees = calculate_fees_fba(market_price, "small_parcel")
    fees_cons = calculate_fees_fba(15.00, "small_parcel")

    result = calculate_profit(
        market_price=market_price,
        raw_conservative_price=15.00,
        fees_current=fees,
        fees_conservative=fees_cons,
        buy_cost=unit_cost_inc_vat,
    )
    assert result["profit_current"] == pytest.approx(
        market_price - fees["total"] - unit_cost_inc_vat
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/test_profit.py -v`

- [ ] **Step 3: Implement `profit.py`**

```python
# sourcing_engine/pipeline/profit.py
"""Profit and margin calculation engine.

CRITICAL: profit_conservative uses raw_conservative_price — never floored.
"""
from sourcing_engine.config import MIN_PROFIT


def calculate_profit(
    market_price: float,
    raw_conservative_price: float,
    fees_current: dict,
    fees_conservative: dict,
    buy_cost: float,
) -> dict:
    """Calculate profit and margin at current and conservative prices.

    Args:
        market_price: Current Buy Box price (gross revenue).
        raw_conservative_price: 15th percentile historical — NEVER floored.
        fees_current: Fee dict calculated at market_price.
        fees_conservative: Fee dict calculated at raw_conservative_price.
        buy_cost: unit_cost_inc_vat or case_cost_inc_vat depending on match_type.

    Returns dict with profit_current, profit_conservative, margin_current,
    margin_conservative, max_buy_price.
    """
    profit_current = market_price - fees_current["total"] - buy_cost
    profit_conservative = raw_conservative_price - fees_conservative["total"] - buy_cost

    margin_current = profit_current / market_price if market_price > 0 else 0.0
    margin_conservative = (
        profit_conservative / raw_conservative_price
        if raw_conservative_price > 0
        else 0.0
    )

    max_buy_price = market_price - fees_current["total"] - MIN_PROFIT

    return {
        "profit_current": profit_current,
        "profit_conservative": profit_conservative,
        "margin_current": margin_current,
        "margin_conservative": margin_conservative,
        "max_buy_price": max_buy_price,
    }
```

- [ ] **Step 4: Implement `conservative_price.py`** (needed for test_price_floor_hit)

```python
# sourcing_engine/pipeline/conservative_price.py
"""Conservative price calculation — 15th percentile of 90-day FBA history.

Two values calculated:
- raw_conservative_price: used by decision engine (NEVER floored)
- floored_conservative_price: display only (break-even floor applied)
"""
import numpy as np

from sourcing_engine.config import (
    HISTORY_MINIMUM_DAYS,
    HISTORY_WINDOW_DAYS,
    LOWER_BAND_PERCENTILE,
    MIN_PROFIT,
)
from sourcing_engine.utils.flags import INSUFFICIENT_HISTORY, PRICE_FLOOR_HIT


def calculate_conservative_price(
    price_history: list[tuple],
    market_price: float,
    buy_cost: float,
    fees_conservative_total: float,
) -> tuple[float, float, str | None]:
    """Calculate raw and floored conservative prices.

    Args:
        price_history: list of (day_index, price, fba_seller_count) tuples.
            Covers last HISTORY_WINDOW_DAYS days.
        market_price: Current Buy Box price.
        buy_cost: Total buy cost inc VAT.
        fees_conservative_total: Total fees at conservative price.

    Returns: (raw_conservative_price, floored_conservative_price, flag_or_none)
    """
    if not price_history:
        return market_price, market_price, INSUFFICIENT_HISTORY

    # Filter: exclude periods where fba_seller_count == 0
    qualifying = [
        (day, price, sellers)
        for day, price, sellers in price_history
        if sellers and sellers > 0
    ]

    # Check minimum data requirement
    qualifying_days = len(set(day for day, _, _ in qualifying))
    if qualifying_days < HISTORY_MINIMUM_DAYS:
        return market_price, market_price, INSUFFICIENT_HISTORY

    # Calculate Nth percentile of qualifying prices
    prices = [price for _, price, _ in qualifying]
    percentile_price = float(np.percentile(prices, LOWER_BAND_PERCENTILE))

    # raw = min(market_price, percentile)
    raw_conservative_price = min(market_price, percentile_price)

    # Floor = buy_cost + fees + MIN_PROFIT
    price_floor = buy_cost + fees_conservative_total + MIN_PROFIT
    floored_conservative_price = max(raw_conservative_price, price_floor)

    # Flag if raw is below floor
    flag = PRICE_FLOOR_HIT if raw_conservative_price < price_floor else None

    return raw_conservative_price, floored_conservative_price, flag
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/test_profit.py -v`

- [ ] **Step 6: Commit**

```bash
git add sourcing_engine/pipeline/profit.py sourcing_engine/pipeline/conservative_price.py sourcing_engine/tests/test_profit.py
git commit -m "feat: add profit engine and conservative price calculation"
```

---

## Task 6: Decision Engine (`pipeline/decision.py`, `test_decision.py`)

**Files:**
- Create: `sourcing_engine/pipeline/decision.py`
- Create: `sourcing_engine/tests/test_decision.py`

- [ ] **Step 1: Write the PRD-specified failing tests**

```python
# sourcing_engine/tests/test_decision.py
import pytest
from sourcing_engine.pipeline.decision import decide
from sourcing_engine.utils.flags import (
    PRICE_FLOOR_HIT, VAT_UNCLEAR, VAT_FIELD_MISMATCH,
    INSUFFICIENT_HISTORY, SIZE_TIER_UNKNOWN, FBM_ONLY,
    FBM_SHIPPING_ESTIMATED,
)
from sourcing_engine.config import MIN_PROFIT, MIN_MARGIN, MIN_SALES_SHORTLIST


def _make_row(**overrides):
    """Helper: create a row dict with sensible defaults that qualify for SHORTLIST."""
    defaults = {
        "profit_current": 5.00,
        "profit_conservative": 5.00,
        "margin_current": 0.25,
        "margin_conservative": 0.25,
        "sales_estimate": 30,
        "gated": "N",
        "risk_flags": [],
        "price_basis": "FBA",
        "buy_cost": 8.00,
    }
    defaults.update(overrides)
    return defaults


def test_fbm_can_shortlist():
    """FBM listings CAN reach SHORTLIST — do not check price_basis."""
    row = _make_row(
        price_basis="FBM",
        risk_flags=[FBM_ONLY, FBM_SHIPPING_ESTIMATED],
    )
    decision, reason = decide(row)
    assert decision == "SHORTLIST"


def test_price_floor_hit_blocks_shortlist():
    """PRICE_FLOOR_HIT blocks SHORTLIST even if profit thresholds pass."""
    row = _make_row(risk_flags=[PRICE_FLOOR_HIT])
    decision, reason = decide(row)
    assert decision != "SHORTLIST"
    assert "PRICE_FLOOR_HIT" in reason


def test_vat_unclear_blocks_shortlist():
    """VAT_UNCLEAR blocks SHORTLIST."""
    row = _make_row(risk_flags=[VAT_UNCLEAR], buy_cost=None)
    decision, reason = decide(row)
    assert decision != "SHORTLIST"


def test_insufficient_history_does_not_block_shortlist():
    """INSUFFICIENT_HISTORY is a visible flag only — does NOT block SHORTLIST."""
    row = _make_row(risk_flags=[INSUFFICIENT_HISTORY])
    decision, reason = decide(row)
    assert decision == "SHORTLIST"


def test_gated_y_rejects():
    """gated=Y always rejects."""
    row = _make_row(gated="Y")
    decision, reason = decide(row)
    assert decision == "REJECT"
    assert "gated" in reason.lower()


def test_gated_unknown_routes_review():
    """gated=UNKNOWN routes to REVIEW."""
    row = _make_row(gated="UNKNOWN")
    decision, reason = decide(row)
    assert decision == "REVIEW"


def test_low_sales_10_19_routes_review():
    """Sales 10-19 routes to REVIEW (between MIN_SALES_REVIEW and MIN_SALES_SHORTLIST)."""
    row = _make_row(sales_estimate=15)
    decision, reason = decide(row)
    assert decision == "REVIEW"
    assert "sales" in reason.lower()


def test_sales_below_10_rejects():
    """Sales below MIN_SALES_REVIEW (10) rejects."""
    row = _make_row(sales_estimate=5)
    decision, reason = decide(row)
    assert decision == "REJECT"


def test_size_tier_unknown_does_not_block_shortlist():
    """SIZE_TIER_UNKNOWN does NOT block SHORTLIST — fallback fee applied, flag visible."""
    row = _make_row(risk_flags=[SIZE_TIER_UNKNOWN])
    decision, reason = decide(row)
    assert decision == "SHORTLIST"


def test_single_supplier_row_produces_two_output_rows_when_both_match():
    """A unit match and a case match both get independent decisions."""
    unit_row = _make_row(match_type="UNIT", buy_cost=4.80)
    case_row = _make_row(match_type="CASE", buy_cost=24.00)
    d1, r1 = decide(unit_row)
    d2, r2 = decide(case_row)
    # Both should get a decision (not crash, not skip)
    assert d1 in ("SHORTLIST", "REVIEW", "REJECT")
    assert d2 in ("SHORTLIST", "REVIEW", "REJECT")
    # Both should have a reason
    assert r1
    assert r2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/test_decision.py -v`

- [ ] **Step 3: Implement `decision.py`**

```python
# sourcing_engine/pipeline/decision.py
"""Decision engine — SHORTLIST / REVIEW / REJECT.

Implements PRD section 3.10 exactly.
"""
from sourcing_engine.config import (
    MIN_PROFIT,
    MIN_MARGIN,
    MIN_SALES_SHORTLIST,
    MIN_SALES_REVIEW,
)
from sourcing_engine.utils.flags import (
    SHORTLIST_BLOCKERS,
    REVIEW_FLAGS,
    has_any_flag,
    FBM_ONLY,
    FBM_SHIPPING_ESTIMATED,
    INSUFFICIENT_HISTORY,
)


def decide(row: dict) -> tuple[str, str]:
    """Make SHORTLIST / REVIEW / REJECT decision for a single row.

    Args:
        row: dict with keys: profit_current, profit_conservative,
            margin_current, margin_conservative, sales_estimate,
            gated, risk_flags, price_basis, buy_cost.

    Returns: (decision, decision_reason)
    """
    risk_flags = row.get("risk_flags", [])
    profit_current = row.get("profit_current", 0)
    profit_conservative = row.get("profit_conservative", 0)
    margin_current = row.get("margin_current", 0)
    margin_conservative = row.get("margin_conservative", 0)
    sales_estimate = row.get("sales_estimate", 0)
    gated = str(row.get("gated", "UNKNOWN")).upper()
    buy_cost = row.get("buy_cost")

    reasons = []

    # --- REJECT checks (hard blocks) ---

    # Gated = Y → always reject
    if gated == "Y":
        return "REJECT", "Gated product — cannot sell"

    # VAT_UNCLEAR with no valid buy_cost
    if "VAT_UNCLEAR" in risk_flags and buy_cost is None:
        return "REJECT", "VAT_UNCLEAR — no valid buy cost"

    # Sales below minimum review threshold
    if sales_estimate is not None and sales_estimate < MIN_SALES_REVIEW:
        return "REJECT", f"Sales estimate {sales_estimate}/month below minimum {MIN_SALES_REVIEW}"

    # Both profit figures below MIN_PROFIT
    if profit_current < MIN_PROFIT and profit_conservative < MIN_PROFIT:
        return "REJECT", (
            f"Unprofitable — current £{profit_current:.2f}, "
            f"conservative £{profit_conservative:.2f} (min £{MIN_PROFIT:.2f})"
        )

    # --- SHORTLIST checks (all must pass) ---

    can_shortlist = True

    if profit_conservative < MIN_PROFIT:
        can_shortlist = False
        reasons.append(
            f"Conservative profit £{profit_conservative:.2f} below £{MIN_PROFIT:.2f}"
        )

    if margin_conservative < MIN_MARGIN:
        can_shortlist = False
        reasons.append(
            f"Conservative margin {margin_conservative:.1%} below {MIN_MARGIN:.0%}"
        )

    if sales_estimate is not None and sales_estimate < MIN_SALES_SHORTLIST:
        can_shortlist = False
        reasons.append(
            f"Sales {sales_estimate}/month below shortlist threshold {MIN_SALES_SHORTLIST}"
        )

    if has_any_flag(risk_flags, SHORTLIST_BLOCKERS):
        can_shortlist = False
        blocking = [f for f in risk_flags if f in SHORTLIST_BLOCKERS]
        reasons.append(f"Blocked by: {', '.join(blocking)}")

    if gated == "UNKNOWN":
        can_shortlist = False
        reasons.append("Gated status unknown")

    if can_shortlist:
        return "SHORTLIST", "Passes all thresholds at conservative price"

    # --- REVIEW (not shortlisted, not rejected) ---

    # Check for review-forcing flags
    review_flag_hits = [f for f in risk_flags if f in REVIEW_FLAGS]
    if review_flag_hits:
        reasons.append(f"Review flags: {', '.join(review_flag_hits)}")

    reason_str = "; ".join(reasons) if reasons else "Does not meet shortlist criteria"
    return "REVIEW", reason_str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/test_decision.py -v`

- [ ] **Step 5: Commit**

```bash
git add sourcing_engine/pipeline/decision.py sourcing_engine/tests/test_decision.py
git commit -m "feat: add decision engine with SHORTLIST/REVIEW/REJECT logic"
```

---

## Task 7: Match Stub + Market Data Stub (`pipeline/match.py`, `pipeline/market_data.py`)

**Files:**
- Create: `sourcing_engine/pipeline/match.py`
- Create: `sourcing_engine/pipeline/market_data.py`

These are stubs — real Keepa/SellerAmp integration is out of scope for v1 local pipeline. The stubs define the interface that the rest of the pipeline uses, and accept mock/manual data.

- [ ] **Step 1: Implement `market_data.py` stub**

```python
# sourcing_engine/pipeline/market_data.py
"""Keepa data extraction — stub for v1.

In v1, market data is provided as a CSV/dict lookup keyed by EAN.
Real Keepa browser integration is a future enhancement.
"""
import logging
import pandas as pd

logger = logging.getLogger(__name__)


def load_market_data(source: str | pd.DataFrame | None = None) -> dict:
    """Load market data keyed by EAN.

    Args:
        source: path to CSV file, or DataFrame, or None (empty).

    Returns: dict[ean] -> {asin, title, buy_box_price, amazon_price,
        amazon_status, fba_seller_count, monthly_sales_estimate,
        price_history, history_days, size_tier, brand, gated}
    """
    if source is None:
        return {}

    if isinstance(source, str):
        try:
            df = pd.read_csv(source, dtype={"ean": str})
        except Exception:
            logger.exception("Failed to load market data from %s", source)
            return {}
    elif isinstance(source, pd.DataFrame):
        df = source
    else:
        return {}

    data = {}
    for _, row in df.iterrows():
        ean = str(row.get("ean", "")).strip()
        if ean:
            data[ean] = row.to_dict()
    return data


def lookup_ean(ean: str, market_data: dict) -> dict | None:
    """Look up a single EAN in market data. Returns None if not found."""
    return market_data.get(ean)
```

- [ ] **Step 2: Implement `match.py` stub**

```python
# sourcing_engine/pipeline/match.py
"""EAN → ASIN matching.

In v1 this is a lookup against pre-loaded market data (CSV or dict).
Each supplier row can produce up to 2 matches: unit and case.
"""
import logging

from sourcing_engine.utils.flags import (
    MULTI_ASIN_MATCH,
    CASE_MATCH_SKIPPED,
    PRICE_BASIS_AMBIGUOUS,
    CASE_QTY_UNKNOWN,
)

logger = logging.getLogger(__name__)


def match_product(row: dict, market_data: dict) -> list[dict]:
    """Match a normalised row against market data.

    Returns list of match dicts (0, 1, or 2 entries).
    Each match includes match_type ("UNIT" or "CASE") and the buy_cost to use.
    """
    matches = []
    ean = row.get("ean")
    risk_flags = list(row.get("risk_flags", []))

    if not ean:
        return []

    # --- Attempt 1: Unit match ---
    unit_data = market_data.get(ean)
    if unit_data:
        match = _build_match(row, unit_data, "UNIT")
        if match:
            matches.append(match)

    # --- Attempt 2: Case match ---
    case_qty = row.get("case_qty", 1)
    price_basis = row.get("supplier_price_basis", "UNIT")

    skip_case = False
    if price_basis == "AMBIGUOUS":
        skip_case = True
        risk_flags.append(CASE_MATCH_SKIPPED)
    if case_qty is None or case_qty <= 1:
        skip_case = True
    if CASE_QTY_UNKNOWN in risk_flags:
        skip_case = True
        if CASE_MATCH_SKIPPED not in risk_flags:
            risk_flags.append(CASE_MATCH_SKIPPED)

    if not skip_case:
        case_ean = row.get("case_ean") or ean
        case_data = market_data.get(f"{ean}_case") or market_data.get(case_ean)
        if case_data and case_data.get("asin") != (unit_data or {}).get("asin"):
            match = _build_match(row, case_data, "CASE")
            if match:
                matches.append(match)

    # Propagate flags
    for m in matches:
        m["risk_flags"] = list(set(m.get("risk_flags", []) + risk_flags))

    return matches


def _build_match(row: dict, market_row: dict, match_type: str) -> dict:
    """Build a match dict combining supplier row and market data."""
    if match_type == "UNIT":
        buy_cost = row.get("unit_cost_inc_vat")
    else:
        buy_cost = row.get("case_cost_inc_vat")

    if buy_cost is None:
        return None

    return {
        # Supplier data
        "supplier": row.get("supplier"),
        "source_file": row.get("source_file"),
        "supplier_sku": row.get("supplier_sku"),
        "ean": row.get("ean"),
        "case_ean": row.get("case_ean") if match_type == "CASE" else None,
        "product_name": market_row.get("title") or row.get("product_name"),
        "match_type": match_type,
        "supplier_price_basis": row.get("supplier_price_basis"),
        "case_qty": row.get("case_qty", 1),
        "unit_cost_ex_vat": row.get("unit_cost_ex_vat"),
        "unit_cost_inc_vat": row.get("unit_cost_inc_vat"),
        "case_cost_ex_vat": row.get("case_cost_ex_vat"),
        "case_cost_inc_vat": row.get("case_cost_inc_vat"),
        "buy_cost": buy_cost,
        "rrp_inc_vat": row.get("rrp_inc_vat"),
        "moq": row.get("moq", 1),
        # Market data
        "asin": market_row.get("asin"),
        "brand": market_row.get("brand") or row.get("brand"),
        "buy_box_price": market_row.get("buy_box_price"),
        "amazon_price": market_row.get("amazon_price"),
        "amazon_status": market_row.get("amazon_status"),
        "fba_seller_count": market_row.get("fba_seller_count"),
        "sales_estimate": market_row.get("monthly_sales_estimate"),
        "price_history": market_row.get("price_history"),
        "history_days": market_row.get("history_days"),
        "size_tier": market_row.get("size_tier"),
        "gated": market_row.get("gated", "UNKNOWN"),
        "risk_flags": list(row.get("risk_flags", [])),
    }
```

- [ ] **Step 3: Commit**

```bash
git add sourcing_engine/pipeline/match.py sourcing_engine/pipeline/market_data.py
git commit -m "feat: add match and market data stubs"
```

---

## Task 8: Main Pipeline Orchestrator (`main.py`)

**Files:**
- Create: `sourcing_engine/main.py`

- [ ] **Step 1: Implement `main.py`**

```python
# sourcing_engine/main.py
"""Entry point — orchestrates the full sourcing pipeline.

Usage:
    python -m sourcing_engine.main --input ./raw/ --output ./results/
    python -m sourcing_engine.main --input ./raw/ --output ./results/ --market-data ./keepa.csv
"""
import argparse
import logging
import os
import sys
from datetime import datetime

import pandas as pd

from sourcing_engine.pipeline.ingest import ingest_directory, ingest_file
from sourcing_engine.pipeline.normalise import normalise
from sourcing_engine.pipeline.case_detection import derive_costs
from sourcing_engine.pipeline.match import match_product
from sourcing_engine.pipeline.market_data import load_market_data, lookup_ean
from sourcing_engine.pipeline.fees import calculate_fees_fba, calculate_fees_fbm
from sourcing_engine.pipeline.conservative_price import calculate_conservative_price
from sourcing_engine.pipeline.profit import calculate_profit
from sourcing_engine.pipeline.decision import decide
from sourcing_engine.config import CAPITAL_EXPOSURE_LIMIT, MIN_PROFIT
from sourcing_engine.utils.flags import (
    AMAZON_ON_LISTING, AMAZON_STATUS_UNKNOWN, SINGLE_FBA_SELLER,
    FBM_ONLY, FBM_SHIPPING_ESTIMATED, HIGH_MOQ, SIZE_TIER_UNKNOWN,
)
from sourcing_engine.utils.ean_validator import validate_ean

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sourcing_engine")


def run_pipeline(input_path: str, output_dir: str, market_data_path: str | None = None):
    """Run the full sourcing pipeline."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(output_dir, timestamp)
    os.makedirs(run_dir, exist_ok=True)

    # --- Step 1: Ingest ---
    logger.info("Ingesting supplier files from %s", input_path)
    if os.path.isdir(input_path):
        raw_df = ingest_directory(input_path)
    else:
        raw_df = ingest_file(input_path)

    if raw_df.empty:
        logger.error("No data ingested — exiting")
        return

    logger.info("Ingested %d raw rows from %d files",
                len(raw_df), raw_df["source_file"].nunique())

    # --- Step 2: Normalise ---
    norm_df = normalise(raw_df)
    logger.info("Normalised %d rows", len(norm_df))

    # --- Step 3: Case detection + cost derivation ---
    for idx, row in norm_df.iterrows():
        costs = derive_costs(
            supplier_price_ex_vat=row["supplier_price_ex_vat"],
            supplier_price_basis=row["supplier_price_basis"],
            case_qty=row["case_qty"],
            rrp_inc_vat=row.get("rrp_inc_vat"),
        )
        for key in ("unit_cost_ex_vat", "unit_cost_inc_vat",
                     "case_cost_ex_vat", "case_cost_inc_vat", "case_qty"):
            norm_df.at[idx, key] = costs[key]
        # Merge flags
        existing = norm_df.at[idx, "risk_flags"]
        if not isinstance(existing, list):
            existing = []
        norm_df.at[idx, "risk_flags"] = existing + costs["flags"]

    # --- Step 4: EAN validation + matching ---
    market_data = load_market_data(market_data_path)
    output_rows = []
    stats = {"matched": 0, "no_match": 0, "invalid_ean": 0, "errors": 0}

    for idx, row in norm_df.iterrows():
        try:
            row_dict = row.to_dict()

            # Validate EAN
            if not row_dict.get("ean") or not validate_ean(row_dict["ean"]):
                output_rows.append({
                    **row_dict,
                    "decision": "REJECT",
                    "decision_reason": "Invalid or missing EAN",
                    "match_type": "UNIT",
                })
                stats["invalid_ean"] += 1
                continue

            # Match against market data
            matches = match_product(row_dict, market_data)

            if not matches:
                output_rows.append({
                    **row_dict,
                    "decision": "REJECT",
                    "decision_reason": "No Amazon match found",
                    "match_type": "UNIT",
                })
                stats["no_match"] += 1
                continue

            # --- Steps 5-8 for each match ---
            for match in matches:
                try:
                    processed = _process_match(match)
                    output_rows.append(processed)
                    stats["matched"] += 1
                except Exception:
                    logger.exception(
                        "[%s] [ROW_%s] [%s] — match processing error",
                        row_dict.get("supplier"), idx, row_dict.get("ean"),
                    )
                    match["decision"] = "REVIEW"
                    match["decision_reason"] = "Processing error — manual review required"
                    output_rows.append(match)
                    stats["errors"] += 1

        except Exception:
            logger.exception(
                "[%s] [ROW_%s] [%s] — pipeline error",
                row.get("supplier"), idx, row.get("ean"),
            )
            stats["errors"] += 1

    # --- Step 9: Output ---
    output_df = pd.DataFrame(output_rows)

    if not output_df.empty:
        from sourcing_engine.output.csv_writer import write_csv
        from sourcing_engine.output.excel_writer import write_excel
        from sourcing_engine.output.markdown_report import write_report

        csv_path = os.path.join(run_dir, f"shortlist_{timestamp}.csv")
        xlsx_path = os.path.join(run_dir, f"shortlist_{timestamp}.xlsx")
        md_path = os.path.join(run_dir, f"report_{timestamp}.md")

        write_csv(output_df, csv_path)
        write_excel(output_df, xlsx_path)
        write_report(output_df, md_path)

    # --- Summary ---
    _print_summary(output_df, stats, norm_df)


def _process_match(match: dict) -> dict:
    """Run price basis, fees, conservative price, profit, and decision for a single match."""
    risk_flags = list(match.get("risk_flags", []))

    # Price basis determination (FBA vs FBM)
    fba_seller_count = match.get("fba_seller_count", 0) or 0
    amazon_status = match.get("amazon_status")
    buy_box_price = match.get("buy_box_price")
    amazon_price = match.get("amazon_price")

    if fba_seller_count > 0:
        price_basis = "FBA"
        if amazon_status == "ON_LISTING":
            market_price = amazon_price or buy_box_price
            risk_flags.append(AMAZON_ON_LISTING)
        elif amazon_status == "UNKNOWN":
            market_price = buy_box_price
            risk_flags.append(AMAZON_STATUS_UNKNOWN)
        elif fba_seller_count == 1:
            market_price = buy_box_price
            risk_flags.append(SINGLE_FBA_SELLER)
        else:
            market_price = buy_box_price
    else:
        price_basis = "FBM"
        market_price = buy_box_price
        risk_flags.append(FBM_ONLY)

    if market_price is None or market_price <= 0:
        match["decision"] = "REJECT"
        match["decision_reason"] = "No valid market price"
        return match

    buy_cost = match["buy_cost"]

    # Fees at current price
    size_tier = match.get("size_tier")
    sales_estimate = match.get("sales_estimate")

    if price_basis == "FBA":
        fees_current = calculate_fees_fba(market_price, size_tier,
                                          sales_estimate=sales_estimate)
    else:
        fees_current = calculate_fees_fbm(market_price)

    # Conservative price — two-pass approach to solve bootstrapping:
    # 1. Calculate raw_conservative_price (percentile — no fees needed)
    # 2. Calculate fees at that price
    # 3. Compute the floor using those fees (for floored_conservative_price + PRICE_FLOOR_HIT)
    price_history = match.get("price_history")
    if price_history and isinstance(price_history, list):
        # Pass 1: get raw conservative price (floor args are placeholders — raw doesn't use them)
        raw_cp, _, _ = calculate_conservative_price(
            price_history, market_price, buy_cost, 0,  # fees placeholder — raw is independent of floor
        )
        # Pass 2: calculate fees at the raw conservative price
        if price_basis == "FBA":
            fees_conservative = calculate_fees_fba(raw_cp, size_tier,
                                                   sales_estimate=sales_estimate)
        else:
            fees_conservative = calculate_fees_fbm(raw_cp)
        # Pass 3: recalculate with correct fees for the floor + PRICE_FLOOR_HIT flag
        raw_cp, floored_cp, cp_flag = calculate_conservative_price(
            price_history, market_price, buy_cost, fees_conservative["total"],
        )
    else:
        raw_cp = market_price
        floored_cp = market_price
        cp_flag = "INSUFFICIENT_HISTORY"
        # Fees at conservative price (= market price when no history)
        if price_basis == "FBA":
            fees_conservative = calculate_fees_fba(raw_cp, size_tier,
                                                   sales_estimate=sales_estimate)
        else:
            fees_conservative = calculate_fees_fbm(raw_cp)

    if cp_flag:
        risk_flags.append(cp_flag)

    # Merge fee flags
    risk_flags.extend(fees_current.get("flags", []))
    risk_flags.extend(fees_conservative.get("flags", []))

    # Profit
    profit = calculate_profit(market_price, raw_cp, fees_current,
                              fees_conservative, buy_cost)

    # Capital exposure
    moq = match.get("moq", 1) or 1
    capital_exposure = moq * buy_cost
    if capital_exposure > CAPITAL_EXPOSURE_LIMIT:
        risk_flags.append(HIGH_MOQ)

    # Deduplicate flags
    risk_flags = list(dict.fromkeys(risk_flags))

    # Decision
    decision_input = {
        **profit,
        "sales_estimate": sales_estimate,
        "gated": match.get("gated", "UNKNOWN"),
        "risk_flags": risk_flags,
        "price_basis": price_basis,
        "buy_cost": buy_cost,
    }
    decision, reason = decide(decision_input)

    # Build output row
    match.update({
        "market_price": market_price,
        "raw_conservative_price": raw_cp,
        "floored_conservative_price": floored_cp,
        "price_basis": price_basis,
        "fees_current": fees_current["total"],
        "fees_conservative": fees_conservative["total"],
        **profit,
        "capital_exposure": capital_exposure,
        "decision": decision,
        "decision_reason": reason,
        "risk_flags": risk_flags,
    })
    return match


def _print_summary(output_df, stats, norm_df):
    """Print pipeline run summary."""
    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    logger.info("=" * 60)
    logger.info("Suppliers processed: %d", norm_df["supplier"].nunique() if not norm_df.empty else 0)
    logger.info("Source rows processed: %d", len(norm_df))
    logger.info("Matched: %d", stats["matched"])
    logger.info("Invalid EAN: %d", stats["invalid_ean"])
    logger.info("No match: %d", stats["no_match"])
    logger.info("Errors: %d", stats["errors"])
    if not output_df.empty and "decision" in output_df.columns:
        for d in ["SHORTLIST", "REVIEW", "REJECT"]:
            count = (output_df["decision"] == d).sum()
            logger.info("%s: %d", d, count)


def main():
    parser = argparse.ArgumentParser(description="Amazon Supplier Shortlist Engine")
    parser.add_argument("--input", required=True, help="Supplier file or directory")
    parser.add_argument("--output", default="./results", help="Output directory")
    parser.add_argument("--market-data", default=None, help="Market data CSV path")
    args = parser.parse_args()

    run_pipeline(args.input, args.output, args.market_data)


if __name__ == "__main__":
    main()
```

Note: The import of `price_basis` module is not needed — that logic is inline in `_process_match`. Remove the import line for `determine_price_basis_and_flags`.

- [ ] **Step 2: Commit**

```bash
git add sourcing_engine/main.py
git commit -m "feat: add main pipeline orchestrator"
```

---

## Task 9: Output Writers (`output/csv_writer.py`, `output/excel_writer.py`, `output/markdown_report.py`)

**Files:**
- Create: `sourcing_engine/output/csv_writer.py`
- Create: `sourcing_engine/output/excel_writer.py`
- Create: `sourcing_engine/output/markdown_report.py`

- [ ] **Step 1: Implement `csv_writer.py`**

```python
# sourcing_engine/output/csv_writer.py
"""CSV output — all rows, all decisions, full schema."""
import logging
import pandas as pd

logger = logging.getLogger(__name__)

# Output column order per PRD section 5
OUTPUT_COLUMNS = [
    "supplier", "ean", "case_ean", "asin", "product_name", "match_type",
    "supplier_price_basis", "case_qty", "unit_cost_ex_vat", "unit_cost_inc_vat",
    "case_cost_ex_vat", "case_cost_inc_vat", "buy_cost", "market_price",
    "raw_conservative_price", "floored_conservative_price", "price_basis",
    "fees_current", "fees_conservative", "profit_current", "profit_conservative",
    "margin_current", "margin_conservative", "sales_estimate", "max_buy_price",
    "capital_exposure", "size_tier", "history_days", "gated",
    "decision", "decision_reason", "risk_flags",
]


def write_csv(df: pd.DataFrame, path: str):
    """Write output DataFrame to CSV."""
    try:
        # Use only columns that exist
        cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
        # Convert risk_flags list to semicolon-separated string
        out = df[cols].copy()
        if "risk_flags" in out.columns:
            out["risk_flags"] = out["risk_flags"].apply(
                lambda x: "; ".join(x) if isinstance(x, list) else str(x)
            )
        out.to_csv(path, index=False)
        logger.info("CSV written: %s (%d rows)", path, len(out))
    except Exception:
        logger.exception("Failed to write CSV: %s", path)
```

- [ ] **Step 2: Implement `excel_writer.py`**

```python
# sourcing_engine/output/excel_writer.py
"""Excel output — colour-coded by decision (green/amber/red)."""
import logging
import pandas as pd

logger = logging.getLogger(__name__)


def write_excel(df: pd.DataFrame, path: str):
    """Write output DataFrame to Excel with conditional formatting."""
    try:
        from sourcing_engine.output.csv_writer import OUTPUT_COLUMNS
        cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
        out = df[cols].copy()
        if "risk_flags" in out.columns:
            out["risk_flags"] = out["risk_flags"].apply(
                lambda x: "; ".join(x) if isinstance(x, list) else str(x)
            )

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            out.to_excel(writer, index=False, sheet_name="Shortlist")
            _apply_formatting(writer, out)

        logger.info("Excel written: %s (%d rows)", path, len(out))
    except Exception:
        logger.exception("Failed to write Excel: %s", path)


def _apply_formatting(writer, df):
    """Apply green/amber/red row formatting based on decision."""
    try:
        from openpyxl.styles import PatternFill

        green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        amber = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
        red = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

        ws = writer.sheets["Shortlist"]
        decision_col = None
        for col_idx, col_name in enumerate(df.columns, 1):
            if col_name == "decision":
                decision_col = col_idx
                break

        if decision_col is None:
            return

        for row_idx in range(2, len(df) + 2):  # skip header
            cell_val = ws.cell(row=row_idx, column=decision_col).value
            if cell_val == "SHORTLIST":
                fill = green
            elif cell_val == "REVIEW":
                fill = amber
            else:
                fill = red
            for col_idx in range(1, len(df.columns) + 1):
                ws.cell(row=row_idx, column=col_idx).fill = fill
    except Exception:
        logger.exception("Failed to apply Excel formatting")
```

- [ ] **Step 3: Implement `markdown_report.py`**

```python
# sourcing_engine/output/markdown_report.py
"""Markdown report — per-supplier grouped tables."""
import logging
import pandas as pd

logger = logging.getLogger(__name__)


def write_report(df: pd.DataFrame, path: str):
    """Write markdown report grouped by supplier and decision."""
    try:
        lines = ["# Supplier Shortlist Report\n"]
        lines.append(_summary_section(df))

        for supplier in sorted(df["supplier"].dropna().unique()):
            sdf = df[df["supplier"] == supplier]
            lines.append(f"\n## Supplier: {supplier}\n")
            lines.extend(_supplier_sections(sdf))

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info("Report written: %s", path)
    except Exception:
        logger.exception("Failed to write report: %s", path)


def _summary_section(df: pd.DataFrame) -> str:
    """Generate summary statistics."""
    lines = ["\n## Summary\n"]
    lines.append(f"- Suppliers processed: {df['supplier'].nunique()}")
    lines.append(f"- Source rows processed: {len(df)}")

    shortlisted = df[df["decision"] == "SHORTLIST"]
    lines.append(f"- Shortlisted FBA (unit): {len(shortlisted[(shortlisted.get('price_basis', pd.Series()) == 'FBA') & (shortlisted.get('match_type', pd.Series()) == 'UNIT')])}")
    lines.append(f"- Shortlisted FBA (case): {len(shortlisted[(shortlisted.get('price_basis', pd.Series()) == 'FBA') & (shortlisted.get('match_type', pd.Series()) == 'CASE')])}")
    lines.append(f"- Shortlisted FBM (unit): {len(shortlisted[(shortlisted.get('price_basis', pd.Series()) == 'FBM') & (shortlisted.get('match_type', pd.Series()) == 'UNIT')])}")
    lines.append(f"- Shortlisted FBM (case): {len(shortlisted[(shortlisted.get('price_basis', pd.Series()) == 'FBM') & (shortlisted.get('match_type', pd.Series()) == 'CASE')])}")
    lines.append(f"- Sent to review: {(df['decision'] == 'REVIEW').sum()}")
    lines.append(f"- Rejected: {(df['decision'] == 'REJECT').sum()}")
    return "\n".join(lines)


def _supplier_sections(sdf: pd.DataFrame) -> list[str]:
    """Generate per-supplier shortlist/review/reject tables."""
    lines = []
    display_cols = ["ean", "asin", "product_name", "buy_cost", "market_price",
                    "profit_conservative", "margin_conservative", "sales_estimate",
                    "risk_flags", "decision_reason"]

    shortlisted = sdf[sdf["decision"] == "SHORTLIST"]
    review = sdf[sdf["decision"] == "REVIEW"]
    rejected = sdf[sdf["decision"] == "REJECT"]

    # Shortlist sections
    for pb in ["FBA", "FBM"]:
        for mt, label in [("UNIT", "Unit"), ("CASE", "Case/Multipack")]:
            subset = shortlisted
            if "price_basis" in subset.columns:
                subset = subset[subset["price_basis"] == pb]
            if "match_type" in subset.columns:
                subset = subset[subset["match_type"] == mt]
            lines.append(f"\n### Shortlist — {pb} {label} Matches\n")
            if subset.empty:
                lines.append("_None_\n")
            else:
                lines.append(_make_table(subset, display_cols))

    # Review
    lines.append("\n### Manual Review\n")
    if review.empty:
        lines.append("_None_\n")
    else:
        lines.append(_make_table(review, display_cols))

    # Rejected
    reject_cols = ["ean", "product_name", "match_type", "decision_reason"]
    lines.append("\n### Rejected\n")
    if rejected.empty:
        lines.append("_None_\n")
    else:
        lines.append(_make_table(rejected, reject_cols))

    return lines


def _make_table(df: pd.DataFrame, cols: list[str]) -> str:
    """Render a DataFrame subset as a markdown table."""
    available = [c for c in cols if c in df.columns]
    if not available:
        return "_No data_\n"

    subset = df[available].copy()
    # Format risk_flags
    if "risk_flags" in subset.columns:
        subset["risk_flags"] = subset["risk_flags"].apply(
            lambda x: ", ".join(x) if isinstance(x, list) else str(x)
        )
    # Format numeric columns
    for col in ["buy_cost", "market_price", "profit_conservative", "margin_conservative"]:
        if col in subset.columns:
            if col == "margin_conservative":
                subset[col] = subset[col].apply(
                    lambda x: f"{x:.1%}" if pd.notna(x) else ""
                )
            else:
                subset[col] = subset[col].apply(
                    lambda x: f"£{x:.2f}" if pd.notna(x) else ""
                )

    lines = []
    lines.append("| " + " | ".join(available) + " |")
    lines.append("| " + " | ".join(["---"] * len(available)) + " |")
    for _, row in subset.iterrows():
        vals = [str(row.get(c, "")) for c in available]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Commit**

```bash
git add sourcing_engine/output/csv_writer.py sourcing_engine/output/excel_writer.py sourcing_engine/output/markdown_report.py
git commit -m "feat: add CSV, Excel, and markdown output writers"
```

---

## Task 10: Integration Test — Full Pipeline Smoke Test

**Files:**
- Create: `sourcing_engine/tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# sourcing_engine/tests/test_integration.py
"""Integration test — run the full pipeline against real Abgee PDFs without market data.

Without market data, all valid-EAN rows should REJECT with 'No Amazon match found'.
This verifies the pipeline runs end-to-end without crashing.
"""
import os
import pytest
from sourcing_engine.main import run_pipeline


def test_pipeline_runs_without_crash():
    """Pipeline runs against real PDFs and produces output without crashing."""
    output_dir = "results/test_run"
    run_pipeline("raw/Fubbles_Spring_Summer_2026_Price_List.pdf", output_dir)

    # Should produce output files
    assert os.path.isdir(output_dir)
    subdirs = os.listdir(output_dir)
    assert len(subdirs) > 0

    # Find the latest run dir
    run_dir = os.path.join(output_dir, sorted(subdirs)[-1])
    files = os.listdir(run_dir)
    csv_files = [f for f in files if f.endswith(".csv")]
    assert len(csv_files) > 0


def test_pipeline_handles_all_suppliers():
    """Pipeline processes all 38 PDFs without crashing."""
    output_dir = "results/test_all"
    run_pipeline("raw/", output_dir)
    assert os.path.isdir(output_dir)
```

- [ ] **Step 2: Run integration test**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/test_integration.py -v --tb=short`

- [ ] **Step 3: Fix any issues found**

- [ ] **Step 4: Commit**

```bash
git add sourcing_engine/tests/test_integration.py
git commit -m "test: add integration smoke tests"
```

---

## Task 11: Run All Tests

- [ ] **Step 1: Run the full test suite**

Run: `cd O:/fba/pricelists/abgee && python -m pytest sourcing_engine/tests/ -v --tb=short`
Expected: All 22+ tests pass

- [ ] **Step 2: Fix any failures**

- [ ] **Step 3: Final commit**

```bash
git add -A sourcing_engine/
git commit -m "feat: complete Abgee sourcing pipeline v1"
```

---

## Notes for the Implementer

1. **Without Keepa data**, all rows will REJECT with "No Amazon match found". This is correct. The pipeline is ready for market data to be fed in via `--market-data` CSV.

2. **Abgee Pack Size = "PK<n>"** means the Trade Price is **per case** (the whole box), not per unit. This is confirmed by comparing Trade Price and Retail Price: e.g. "PK12" with Trade Price £59.88 and Retail £7.99 → £59.88/12 = £4.99 per unit, which makes sense as ~60% of £7.99 RRP.

3. **The `_summary_section` in markdown_report.py** uses a somewhat awkward pandas pattern for filtering. If it causes issues with missing columns, simplify to check `if col in df.columns` before filtering.
