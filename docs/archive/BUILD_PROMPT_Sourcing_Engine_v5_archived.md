# Build Prompt: Amazon Supplier Shortlist Engine v5
# For use with Claude Code / Codex

---

## ROLE

You are an expert Python developer building a production Amazon FBA/FBM sourcing engine.
This will be used with real money. Accuracy and conservative assumptions are mandatory.
Do not take shortcuts. Do not make assumptions where the PRD specifies behaviour — implement it exactly.

---

## WHAT TO BUILD

A Python application that:

1. Reads supplier price list files (CSV, XLSX, PDF, HTML)
2. Normalises them into structured data
3. Detects unit vs case pricing and derives both price points
4. Matches products to Amazon ASINs via EAN lookup (Keepa data)
5. Calculates profitability for FBA and FBM fulfilment, at current and conservative prices
6. Outputs a shortlist of profitable products with full decision audit trail

---

## LANGUAGE AND STACK

- Language: Python 3.11+
- Package manager: pip with requirements.txt
- Config: All thresholds in `config.py` — never hardcoded in logic
- Output: CSV, Excel (.xlsx), Markdown report
- Logging: structured logs per supplier run with timestamps

---

## PROJECT STRUCTURE

```
sourcing_engine/
├── config.py                  # All constants — editable by user
├── main.py                    # Entry point — orchestrates full pipeline
├── pipeline/
│   ├── ingest.py              # File reading (CSV/XLSX/PDF/HTML)
│   ├── normalise.py           # Schema normalisation + VAT resolution
│   ├── case_detection.py      # Unit/case price splitting logic
│   ├── match.py               # EAN → ASIN matching (unit + case attempts)
│   ├── market_data.py         # Keepa data extraction
│   ├── fees.py                # Fee calculation (FBA path + FBM path)
│   ├── conservative_price.py  # Historical lower band calculation
│   ├── profit.py              # Profit + margin engine
│   └── decision.py            # SHORTLIST / REVIEW / REJECT logic
├── output/
│   ├── csv_writer.py
│   ├── excel_writer.py
│   └── markdown_report.py
├── utils/
│   ├── ean_validator.py       # EAN checksum validation
│   └── flags.py               # Risk flag constants + helpers
├── tests/
│   ├── test_case_detection.py
│   ├── test_profit.py
│   ├── test_decision.py
│   └── fixtures/              # Sample supplier files for testing
└── requirements.txt
```

---

## config.py — IMPLEMENT EXACTLY

```python
# Profit thresholds
MIN_PROFIT = 3.00           # £
MIN_MARGIN = 0.15           # 15%
MIN_SALES_SHORTLIST = 20    # units/month — auto-shortlist
MIN_SALES_REVIEW = 10       # units/month — minimum for REVIEW

# Capital exposure
CAPITAL_EXPOSURE_LIMIT = 200.00  # £

# History
HISTORY_MINIMUM_DAYS = 30
HISTORY_WINDOW_DAYS = 90
LOWER_BAND_PERCENTILE = 15   # 15th percentile

# Size tier
SIZE_TIER_BOUNDARY_PCT = 0.10   # 10% of next tier boundary
FBA_FEE_CONSERVATIVE_FALLBACK = 4.50  # £ — used when size_tier UNKNOWN

# Storage
STORAGE_RISK_THRESHOLD = 20  # sales/month below which storage fee risk flagged

# FBM fulfilment estimates — SET THESE TO YOUR REAL COSTS
FBM_SHIPPING_ESTIMATE = 3.50   # £
FBM_PACKAGING_ESTIMATE = 0.50  # £

# VAT
VAT_RATE = 0.20
VAT_MISMATCH_TOLERANCE = 0.02  # £ — rounding tolerance

# Case/unit detection
MIN_PLAUSIBLE_UNIT_PRICE = 0.50  # £ — implied unit below this → assume price is per case
```

---

## PIPELINE IMPLEMENTATION RULES

### ingest.py

- Support: `.csv`, `.xlsx`, `.xls`, `.pdf`, `.html`
- CSV/XLSX: use pandas. Preserve leading zeros on EAN columns (read as str).
- PDF: use pdfplumber for structured extraction. If confidence low → status = MANUAL_REVIEW, reason = "PDF extraction failed". Do not crash.
- HTML: use BeautifulSoup, extract first `<table>` with product-like structure.
- Return: `pd.DataFrame` with raw columns preserved + `source_file` field added.
- On any read error: log, mark all rows as MANUAL_REVIEW, continue to next file.

---

### normalise.py

Map raw supplier columns to standard schema. Column name matching must be case-insensitive and handle common variants:

```
EAN variants:        "ean", "barcode", "gtin", "ean13", "product code"
SKU variants:        "sku", "supplier sku", "product sku", "item code", "ref"
Name variants:       "product name", "description", "title", "product description"
Brand variants:      "brand", "manufacturer", "mfr"
Ex-VAT variants:     "price ex vat", "cost ex vat", "net price", "trade price", "ex vat"
Inc-VAT variants:    "price inc vat", "cost inc vat", "gross price", "inc vat"
RRP variants:        "rrp", "recommended retail", "retail price", "msrp"
Case qty variants:   "case qty", "case size", "box qty", "units per case", "pack size", "qty per box"
MOQ variants:        "moq", "minimum order", "min order qty", "minimum quantity"
Stock variants:      "stock", "availability", "in stock", "stock status"
```

**EAN validation:**
```python
def validate_ean(ean_str: str) -> bool:
    # Validate EAN-13 checksum
    # Also accept EAN-8 and UPC-A (12 digit)
    # Return False if invalid — row gets REJECT + reason "Invalid EAN"
```

**VAT resolution (implement exactly):**
```python
def resolve_buy_cost(cost_ex_vat, cost_inc_vat, vat_rate=0.20, tolerance=0.02):
    """
    Returns (buy_cost, flag_or_none)

    If only inc_vat provided:   buy_cost = cost_inc_vat, flag = None
    If only ex_vat provided:    buy_cost = ex_vat * 1.20, flag = None
    If both provided and match: buy_cost = cost_inc_vat, flag = None
    If both provided and conflict > tolerance: buy_cost = cost_inc_vat, flag = "VAT_FIELD_MISMATCH"
    If neither provided:        buy_cost = None, flag = "VAT_UNCLEAR"
    """
```

---

### case_detection.py

This is the most complex normalisation step. Implement the full detection logic:

```python
def detect_price_basis(row) -> tuple[str, float | None, float | None]:
    """
    Returns: (supplier_price_basis, unit_cost_ex_vat, case_cost_ex_vat)
    
    supplier_price_basis: "UNIT" | "CASE" | "AMBIGUOUS"
    
    Detection priority:
    1. Explicit column header (case-insensitive keyword match)
    2. Heuristic from implied unit price vs MIN_PLAUSIBLE_UNIT_PRICE
    3. Heuristic from RRP comparison
    4. Fall back to AMBIGUOUS
    
    If AMBIGUOUS: return (AMBIGUOUS, None, None)
    If case_qty is null or 0: treat as 1, basis = UNIT
    """
```

**Edge cases to handle explicitly:**
- `case_qty = 0` → treat as 1 (data error)
- `case_qty = null` → set flag `CASE_QTY_UNKNOWN`, skip case match
- `case_qty = 1` → unit and case price are identical; do not create duplicate case match row
- `supplier_price = 0` or `null` → flag `VAT_UNCLEAR`, skip row
- Decimal case_qty (e.g. "6.0") → cast to int

---

### match.py

```python
def match_product(row, keepa_data) -> list[dict]:
    """
    Returns a list of match dicts — 0, 1, or 2 entries.
    
    Attempt 1 — Unit match:
        Search keepa_data by row['ean']
        If found: create match with match_type="UNIT", buy_cost=unit_cost_inc_vat
    
    Attempt 2 — Case match:
        Only if case_qty > 1 AND price_basis != "AMBIGUOUS"
        Search keepa_data by row['case_ean'] if different from ean
        OR search by ean + filter for multipack listings (title contains f"{case_qty} x" or "pack of {case_qty}")
        If found: create match with match_type="CASE", buy_cost=case_cost_inc_vat
    
    If PRICE_BASIS_AMBIGUOUS: attempt unit match only, set flag CASE_MATCH_SKIPPED
    If CASE_QTY_UNKNOWN: attempt unit match only, set flag CASE_MATCH_SKIPPED
    
    MULTI_ASIN_MATCH: if single EAN returns >1 ASIN, flag and route to REVIEW — do not auto-select.
    """
```

---

### conservative_price.py

```python
def calculate_conservative_price(price_history, buy_cost, fees_conservative, config):
    """
    price_history: list of (timestamp, price, fba_seller_count) tuples
    
    1. Filter to last HISTORY_WINDOW_DAYS days
    2. Exclude all data points where fba_seller_count == 0
    3. Check if >= HISTORY_MINIMUM_DAYS of qualifying data remain
    4. If not: return (market_price, market_price, "INSUFFICIENT_HISTORY")
    5. Calculate Nth percentile (LOWER_BAND_PERCENTILE) of qualifying prices
    6. raw_conservative_price = min(market_price, nth_percentile)
    7. price_floor = buy_cost + fees_conservative + MIN_PROFIT
    8. floored_conservative_price = max(raw_conservative_price, price_floor)
    9. flag = "PRICE_FLOOR_HIT" if raw < price_floor else None
    
    Returns: (raw_conservative_price, floored_conservative_price, flag_or_none)
    
    CRITICAL: Decision engine must use raw_conservative_price only.
    floored_conservative_price is for display/output only.
    """
```

---

### fees.py

```python
def calculate_fees_fba(sell_price, size_tier, product_volume_cbft, sales_estimate, config):
    """
    Returns dict: {referral_fee, fba_fee, storage_fee, total}
    If size_tier == "UNKNOWN": use FBA_FEE_CONSERVATIVE_FALLBACK, set SIZE_TIER_UNKNOWN flag
    """

def calculate_fees_fbm(sell_price, config):
    """
    Returns dict: {referral_fee, shipping, packaging, total}
    NO fba_fee. NO storage_fee.
    Always sets FBM_SHIPPING_ESTIMATED flag.
    """
```

Referral fee rates by category — use UK Amazon referral fee schedule. If category unknown, use 15%.

---

### profit.py

```python
def calculate_profit(market_price, raw_conservative_price, fees_current, fees_conservative, buy_cost):
    """
    profit_current        = market_price - fees_current['total'] - buy_cost
    profit_conservative   = raw_conservative_price - fees_conservative['total'] - buy_cost
    margin_current        = profit_current / market_price
    margin_conservative   = profit_conservative / raw_conservative_price
    max_buy_price         = market_price - fees_current['total'] - MIN_PROFIT
    
    CRITICAL: profit_conservative uses raw_conservative_price — never floored version.
    """
```

---

### decision.py

Implement SHORTLIST / REVIEW / REJECT exactly as specified in PRD section 3.10.

**SHORTLIST requires ALL of:**
```python
profit_conservative >= MIN_PROFIT
margin_conservative >= MIN_MARGIN
sales_estimate >= MIN_SALES_SHORTLIST
gated != "Y"
"PRICE_FLOOR_HIT" not in risk_flags
"VAT_FIELD_MISMATCH" not in risk_flags
"VAT_UNCLEAR" not in risk_flags
# Note: FBM can shortlist — do NOT check price_basis
# Note: SIZE_TIER_UNKNOWN can shortlist — fallback fee applied
# Note: INSUFFICIENT_HISTORY does NOT block shortlist — visible flag only
```

**REVIEW triggers (any one sufficient):**
```python
REVIEW_FLAGS = {
    "HIGH_MOQ", "SIZE_TIER_RISK", "SIZE_TIER_UNKNOWN",
    "SINGLE_FBA_SELLER", "AMAZON_ON_LISTING", "AMAZON_STATUS_UNKNOWN",
    "PRICE_FLOOR_HIT", "MULTI_ASIN_MATCH", "STORAGE_FEE_RISK",
    "VAT_FIELD_MISMATCH", "VAT_UNCLEAR", "PRICE_BASIS_AMBIGUOUS",
    "CASE_MATCH_SKIPPED", "CASE_QTY_UNKNOWN",
}
```

Also REVIEW if:
- Passes current but fails conservative thresholds
- `MIN_SALES_REVIEW <= sales_estimate < MIN_SALES_SHORTLIST`
- `gated == "UNKNOWN"`

**REJECT if:**
- Both profit_current AND profit_conservative < MIN_PROFIT
- `gated == "Y"`
- EAN invalid or no match
- `VAT_UNCLEAR` with no buy_cost
- `sales_estimate < MIN_SALES_REVIEW`

**decision_reason:** Populate for ALL decisions — SHORTLIST, REVIEW, and REJECT. For REVIEW, list the specific flags and thresholds that triggered review. For SHORTLIST, write "Passes all thresholds at conservative price".

---

## OUTPUT REQUIREMENTS

### CSV + Excel
- One row per match attempt (a supplier row can produce 2 output rows)
- Include all fields from PRD section 5 output schema
- `match_type` column: UNIT | CASE
- Excel: conditional formatting — green rows SHORTLIST, amber REVIEW, red REJECT

### Markdown Report
Structure per PRD section 6:
- Summary statistics
- Per supplier: 4 shortlist tables (FBA unit, FBA case, FBM unit, FBM case) + review + rejected
- FBM shortlist tables: always show FBM_SHIPPING_ESTIMATED flag prominently

---

## ERROR HANDLING RULES

- Never crash on a single bad row — catch, log, mark REVIEW with reason
- Never crash on a supplier file parse failure — mark all rows MANUAL_REVIEW, continue
- Log all exceptions with: supplier name, row number, ean, exception message
- Partial output is always better than no output
- At end of run: print summary of rows processed, matched, shortlisted, errors

---

## TESTING REQUIREMENTS

Write tests for these exact scenarios before considering the build complete:

```
test_case_detection.py:
- test_explicit_case_price_column_detected()
- test_explicit_unit_price_column_detected()
- test_implied_price_below_threshold_flagged_as_case()
- test_ambiguous_routes_to_review()
- test_case_qty_null_treated_as_unit()
- test_case_qty_zero_treated_as_one()
- test_case_qty_1_no_duplicate_row()

test_profit.py:
- test_profit_uses_raw_conservative_not_floored()
- test_price_floor_hit_flag_set_correctly()
- test_fbm_fee_path_no_fba_fee()
- test_fba_fee_path_no_shipping_cost()
- test_case_match_uses_case_cost()
- test_unit_match_uses_unit_cost()

test_decision.py:
- test_fbm_can_shortlist()
- test_price_floor_hit_blocks_shortlist()
- test_vat_unclear_blocks_shortlist()
- test_insufficient_history_does_not_block_shortlist()
- test_gated_y_rejects()
- test_gated_unknown_routes_review()
- test_low_sales_10_19_routes_review()
- test_sales_below_10_rejects()
- test_single_supplier_row_produces_two_output_rows_when_both_match()
```

---

## WHAT NOT TO DO

- Do NOT hardcode any threshold — everything goes through config.py
- Do NOT apply FBA fees to FBM rows
- Do NOT apply FBM shipping cost to FBA rows
- Do NOT use lowest_fba_price as sell price — use buy_box_price
- Do NOT strip VAT from the Amazon sell price
- Do NOT use floored_conservative_price for profit or margin calculations
- Do NOT create a case match row when case_qty == 1
- Do NOT guess price_basis — if ambiguous, flag and route to REVIEW
- Do NOT default size_tier to a value — use FBA_FEE_CONSERVATIVE_FALLBACK and flag SIZE_TIER_UNKNOWN
- Do NOT silently swallow exceptions — log everything

---

## DELIVERABLES CHECKLIST

Before marking complete, verify:

- [ ] All config.py constants are used by the code (no orphan constants, no magic numbers)
- [ ] All 22 tests pass
- [ ] A supplier row with case_qty > 1 and valid unit+case EANs produces 2 output rows
- [ ] An FBM row shortlists correctly with FBM fee path applied
- [ ] A row with PRICE_FLOOR_HIT does not appear in SHORTLIST
- [ ] decision_reason is populated for every output row
- [ ] Running against an empty/corrupt file does not crash the pipeline
- [ ] Output CSV, Excel, and Markdown are all produced even on partial failures

---

## REFERENCE

Full PRD: `PRD_Amazon_FBA_Sourcing_Engine_v5.md`
This document specifies all business logic in detail.
When in doubt about behaviour, the PRD is the source of truth.
This build document specifies the implementation structure.
