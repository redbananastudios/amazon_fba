# CLAUDE.md — Amazon Supplier Shortlist Engine

This file is read by Claude Code at the start of every session.
Read it fully before touching any code.

> **Step 1 update (2026-04-28):** Thresholds now live in `shared/config/`
> (single source of truth across the whole repo). The decision engine uses
> ROI as the SHORTLIST gate, not margin. `config.py` is now a shim importing
> from `shared/lib/python/fba_config_loader.py`. See "Domain Rules → Decision
> Engine" below.

---

## What This Project Is

A Python pipeline that processes supplier price lists and finds profitable products to sell on Amazon via FBA or FBM. It is used with **real money**. Accuracy is not negotiable. Conservative assumptions always win over optimistic ones.

The legacy specification documents in the project root —
`PRD_Amazon_FBA_Sourcing_Engine_v5.md` and `BUILD_PROMPT_Sourcing_Engine_v5.md` —
were never fully implemented. They are kept for historical context but are
**not authoritative**. The actual code is the source of truth. Where docs
disagree with code, the code wins.

---

## Project Structure

```
sourcing_engine/
├── config.py                  # SHIM — re-exports from shared/lib/python/fba_config_loader
├── main.py                    # Entry point
├── pipeline/
│   ├── ingest.py              # File reading (CSV / XLSX / PDF / HTML)
│   ├── normalise.py           # Schema normalisation + VAT resolution
│   ├── case_detection.py      # Unit vs case price splitting
│   ├── match.py               # EAN → ASIN matching (up to 2 attempts per row)
│   ├── market_data.py         # Keepa data extraction
│   ├── fees.py                # Fee calculation — FBA path and FBM path are separate
│   ├── conservative_price.py  # 15th percentile historical pricing
│   ├── profit.py              # Profit + margin + ROI engine
│   └── decision.py            # SHORTLIST / REVIEW / REJECT logic — uses ROI gate
├── output/
│   ├── csv_writer.py
│   ├── excel_writer.py        # Green = SHORTLIST, amber = REVIEW, red = REJECT
│   └── markdown_report.py
├── utils/
│   ├── ean_validator.py       # EAN-8, EAN-13, UPC-A checksum validation
│   └── flags.py               # Risk flag constants
└── tests/
    ├── test_case_detection.py
    ├── test_ingest.py
    ├── test_normalise.py
    ├── test_profit.py
    ├── test_decision.py
    └── fixtures/
```

---

## How to Run

```bash
# Install dependencies (PyYAML added in step 1 for config loader)
pip install pandas openpyxl pdfplumber beautifulsoup4 pyyaml pytest

# Run pipeline against a supplier file or directory
python -m sourcing_engine.main --input ./raw/ --output ./results/ \
    --market-data ./raw/keepa_<supplier>.csv

# Run tests
pytest sourcing_engine/tests/ -v
```

---

## Domain Rules — Read These Before Every Code Change

These are the rules that protect real money. Do not deviate from them.

### Pricing

- **Sell price = Buy Box price.** Never use `lowest_fba_price` as the sell price. It is a distressed floor.
- **Sell price is gross revenue.** The seller is not VAT registered. Do not strip VAT from the Amazon sell price.
- **Buy cost = `supplier_cost_ex_vat × 1.20`.** VAT is always 20%. No other rate exists in this system.
- **VAT resolution has four states** — see `normalise.py` → `resolve_buy_cost()`. Never assume a VAT field is correct without checking for conflicts.

### Conservative Price

- There are **two conservative price values**: `raw_conservative_price` and `floored_conservative_price`.
- **The decision engine uses `raw_conservative_price` only.**
- `floored_conservative_price` is output/display only. It must never enter a profit or margin calculation.
- `raw_conservative_price` = 15th percentile of FBA prices over the last 90 days, excluding periods where `fba_seller_count = 0`.
- Minimum 30 days of qualifying data required. If less: `conservative_price = market_price`, flag `INSUFFICIENT_HISTORY`.

### Fee Engine

- **FBA and FBM are separate fee paths.** Never mix them.
- FBA path: referral fee + FBA fulfilment fee + storage fee estimate.
- FBM path: referral fee + `FBM_SHIPPING_ESTIMATE` + `FBM_PACKAGING_ESTIMATE`. No FBA fee. No storage fee.
- Fees are **calculated at each price point independently** — `fees_current` uses `market_price`, `fees_conservative` uses `raw_conservative_price`.
- If `size_tier` is unknown: use `FBA_FEE_CONSERVATIVE_FALLBACK` (£4.50 default) and flag `SIZE_TIER_UNKNOWN`. Do not null the fee. Do not skip the row.

### Case / Unit Pricing

- A single supplier row can produce **up to two output rows**: a unit match and a case match.
- `supplier_price_basis` must be explicitly detected — never assumed. Detection priority: (1) column header keywords, (2) implied unit price heuristic, (3) RRP comparison, (4) `AMBIGUOUS`.
- If `AMBIGUOUS`: flag `PRICE_BASIS_AMBIGUOUS`, attempt unit match only, route to REVIEW.
- If `case_qty == 1`: unit and case are identical. Produce only one output row.
- If `case_qty` is null: flag `CASE_QTY_UNKNOWN`, skip case match.

### Decision Engine

**SHORTLIST gate is ROI-based**, not margin-based. The previous `MIN_MARGIN`
threshold was replaced because ROI (`profit/buy_cost`) is the truer measure
of capital efficiency for a reseller. Margin is still computed and shown in
output for human reference but does not gate decisions.

SHORTLIST requires **all** of:
```
profit_conservative >= MIN_PROFIT_ABSOLUTE     (£2.50 default)
roi_conservative    >= TARGET_ROI              (30% default)
sales_estimate      >= MIN_SALES_SHORTLIST     (20/month)
"PRICE_FLOOR_HIT"      not in risk_flags
"VAT_FIELD_MISMATCH"   not in risk_flags
"VAT_UNCLEAR"          not in risk_flags
```

The ROI gate logic lives in `shared/lib/python/fba_roi_gate.py`. The decision
engine calls `passes_decision_gates()` which returns `passes/reason/roi`.

**Gated products are NOT rejected.** They flow through to SHORTLIST/REVIEW
with a gating indicator in the `decision_reason` field:
- `gated == "Y"` → shortlisted with "GATED — requires ungating"
- `gated == "UNKNOWN"` → shortlisted with "Gated status unknown — check before buying"

FBM listings **can** reach SHORTLIST — do not check `price_basis` in the SHORTLIST gate.
`SIZE_TIER_UNKNOWN` does **not** block SHORTLIST — fallback fee applied, flag visible.
`INSUFFICIENT_HISTORY` does **not** block SHORTLIST — visible flag, human decides.

REVIEW flags (any one routes to REVIEW if it fails SHORTLIST thresholds):
```
HIGH_MOQ, SIZE_TIER_RISK, SIZE_TIER_UNKNOWN, SINGLE_FBA_SELLER,
AMAZON_ON_LISTING, AMAZON_STATUS_UNKNOWN, PRICE_FLOOR_HIT,
MULTI_ASIN_MATCH, STORAGE_FEE_RISK, VAT_FIELD_MISMATCH, VAT_UNCLEAR,
PRICE_BASIS_AMBIGUOUS, CASE_MATCH_SKIPPED, CASE_QTY_UNKNOWN
```

REJECT if:
```
Both profit_current AND profit_conservative < MIN_PROFIT_ABSOLUTE
EAN invalid or no Amazon match
VAT_UNCLEAR with no valid buy_cost
sales_estimate < MIN_SALES_REVIEW (10/month)
```

`decision_reason` must be populated for **every** output row — SHORTLIST, REVIEW, and REJECT.

---

## Configuration

All thresholds live in `shared/config/decision_thresholds.yaml`. The single
tunable knob is `target_roi`. Other values are absolute floors or pipeline
mechanics. Cross-pipeline business rules (VAT, marketplace, price range)
live in `shared/config/business_rules.yaml`.

`sourcing_engine/config.py` is a backward-compat shim that re-exports the
legacy constant names from `shared/lib/python/fba_config_loader.py`.

**Never hardcode a threshold in pipeline logic.** Add to YAML and import
through the shim.

| Constant (legacy name) | YAML key | Default | Meaning |
|---|---|---|---|
| `MIN_PROFIT` / `MIN_PROFIT_ABSOLUTE` | `min_profit_absolute` | £2.50 | Minimum profit at conservative price |
| `TARGET_ROI` | `target_roi` | 30% | ROI gate for SHORTLIST |
| `MIN_SALES_SHORTLIST` | `min_sales_shortlist` | 20/month | Auto-shortlist sales threshold |
| `MIN_SALES_REVIEW` | `min_sales_review` | 10/month | Minimum sales to enter REVIEW |
| `VAT_RATE` | `vat_rate` (business_rules) | 0.20 | Fixed — never changes |
| `FBM_SHIPPING_ESTIMATE` | `fbm_shipping_estimate` | £3.50 | User must set to their real cost |
| `FBM_PACKAGING_ESTIMATE` | `fbm_packaging_estimate` | £0.50 | User must set to their real cost |
| `FBA_FEE_CONSERVATIVE_FALLBACK` | `fba_fee_conservative_fallback` | £4.50 | Used when size_tier is UNKNOWN |
| `LOWER_BAND_PERCENTILE` | `lower_band_percentile` | 15 | Percentile used for conservative price |
| `HISTORY_WINDOW_DAYS` | `history_window_days` | 90 | Lookback window for price history |
| `HISTORY_MINIMUM_DAYS` | `history_minimum_days` | 30 | Minimum qualifying days required |
| `MIN_PLAUSIBLE_UNIT_PRICE` | `min_plausible_unit_price` | £0.50 | Heuristic floor for case detection |
| `CAPITAL_EXPOSURE_LIMIT` | `capital_exposure_limit` | £200 | MOQ × buy_cost above this → HIGH_MOQ |

`MIN_MARGIN` was removed. Decisions now use `TARGET_ROI` via `fba_roi_gate`.

---

## Error Handling Policy

- **Never crash on a single bad row.** Catch, log, mark as REVIEW with `decision_reason = <exception message>`, continue.
- **Never crash on a file parse failure.** Mark all rows from that file as `MANUAL_REVIEW`, log the error, continue to the next file.
- **Always produce output**, even partial. A run with 50% failures still outputs valid results for the other 50%.
- Log format: `[TIMESTAMP] [SUPPLIER] [ROW_N] [EAN] — <message>`
- At pipeline end, print: suppliers processed, rows processed, matched, shortlisted, in review, rejected, errors.

---

## Testing

Test counts vary by supplier (per-supplier ingest/normalise tests). Baseline
as of 2026-04-28 step-1 verification:

| Supplier | Tests | Pass | Fail (pre-existing) |
|---|---|---|---|
| abgee | 35 | 35 | 0 |
| connect-beauty | 38 | 38 | 0 |
| shure | 35 | 32 | 3 |
| zappies | 35 | 32 | 3 |

The pre-existing shure/zappies failures are in `test_ingest.py` files copied
from abgee but never adapted to those suppliers' file formats. They will be
addressed in step 2 of the reorganisation (engine deduplication).

```bash
cd supplier_pricelist_finder/pricelists/<supplier>
pytest sourcing_engine/tests/ -v --tb=short
```

The shared library has its own tests:
```bash
cd shared/lib/python
pytest tests/ -v
```

Critical tests — if any of these fail, stop and fix before continuing:

```
test_profit_uses_raw_conservative_not_floored   # floored price must never enter profit calc
test_price_floor_hit_blocks_shortlist           # PRICE_FLOOR_HIT is a hard block
test_fbm_can_shortlist                          # FBM is a valid shortlist path
test_fbm_fee_path_no_fba_fee                    # FBM rows must not have FBA fee deducted
test_fba_fee_path_no_shipping_cost              # FBA rows must not have shipping deducted
test_case_qty_1_no_duplicate_row                # case_qty=1 must not produce two rows
test_single_supplier_row_produces_two_output_rows_when_both_match
test_vat_unclear_blocks_shortlist
test_gated_y_shortlists_with_indicator          # gated == Y goes to SHORTLIST with indicator
```

---

## What Not To Do

Do not do any of the following. These are the most common ways to introduce silent profit calculation errors:

- ❌ Use `lowest_fba_price` as the sell price
- ❌ Strip VAT from the Amazon sell price
- ❌ Apply FBA fees to an FBM row
- ❌ Apply FBM shipping/packaging to an FBA row
- ❌ Use `floored_conservative_price` in any profit, margin, or ROI calculation
- ❌ Produce a second output row when `case_qty == 1`
- ❌ Assume `price_basis` when it is ambiguous — flag it
- ❌ Hardcode any threshold value — add to `shared/config/*.yaml` instead
- ❌ Silently swallow an exception — log everything
- ❌ Default a missing `size_tier` to small parcel — use `FBA_FEE_CONSERVATIVE_FALLBACK` and flag
- ❌ Add a margin gate back into the decision engine — ROI replaced margin deliberately

---

## Output Files

Each pipeline run produces three files in `./results/<run_timestamp>/`:

| File | Purpose |
|---|---|
| `shortlist_<timestamp>.csv` | All rows, all decisions, full schema (includes REJECT for audit) |
| `shortlist_<timestamp>.xlsx` | Styled workbook — SHORTLIST + REVIEW only (no REJECT rows) |
| `report_<timestamp>.md` | Human-readable report with per-supplier tables |

The CSV schema includes `roi_current` and `roi_conservative` alongside the existing `margin_current` and `margin_conservative` columns (added in step 1).

**Excel format:** Styled with title bar, frozen panes, auto-filter. SHORTLIST rows green, REVIEW rows amber. No REJECT rows — those are in the CSV only. Key columns first: Product Name, Amazon URL (clickable), ASIN, Supplier, Decision, Gated, Cost inc VAT, Buy Box, Profit, Margin, ROI, FBA Sellers, Amazon On Listing, Amazon Share %. All monetary values formatted as `£0.00`, margins/ROI as `0.0%`. Gated cells highlighted purple (Y) or orange (UNKNOWN). Enriched with Keepa data: rating, reviews, BSR, bought/month, 90d price avg.

Markdown report structure per supplier:
- Shortlist — FBA Unit Matches
- Shortlist — FBA Case/Multipack Matches
- Shortlist — FBM Unit Matches  *(always shows FBM_SHIPPING_ESTIMATED)*
- Shortlist — FBM Case/Multipack Matches  *(always shows FBM_SHIPPING_ESTIMATED)*
- Manual Review
- Rejected

---

## Key Concepts for New Contributors

**Two price values exist for every product:**
- `market_price` — current Buy Box price. What you expect to sell at today.
- `raw_conservative_price` — 15th percentile of 90-day FBA history. What the product has regularly traded at in the lower band. The profit calculation that matters.

**Two fee paths exist:**
- FBA: you ship to Amazon's warehouse, they fulfil. Fees = referral + FBA fulfilment + storage.
- FBM: you hold stock, you ship to customer. Fees = referral + your shipping + your packaging.

**One supplier row can produce two Amazon matches:**
- Unit match: sells one unit. Buy cost = unit price. Match against single-unit ASIN.
- Case match: sells the whole box. Buy cost = case price. Match against multipack ASIN.

**Three decision outcomes:**
- `SHORTLIST` — profitable at conservative price (ROI ≥ 30%, profit ≥ £2.50), act on this.
- `REVIEW` — potentially profitable but has a flag that needs human eyes before buying.
- `REJECT` — does not meet thresholds or has a hard block (invalid EAN, no match).

**Two profitability metrics, one decision metric:**
- `margin_conservative` — profit ÷ sell_price. Shown for reference. Does NOT gate decisions.
- `roi_conservative` — profit ÷ buy_cost. The decision gate. Truer measure of capital efficiency.
