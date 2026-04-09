# PRD: Amazon Supplier Shortlist Engine (v5)

> Revised from v4.
> Changes in this version marked **[v5]**. Unmarked sections unchanged from v4.
> This is the build-ready version. Do not proceed to implementation from v4.

---

## 1. Objective

Build a system that:
- Logs into supplier portals
- Downloads price lists (PDF/XLSX/CSV/HTML)
- Converts them into structured data
- Matches products to Amazon using strict EAN
- Pulls market + fee data (Keepa browser + SellerAmp)
- Calculates real profitability for both FBA and FBM fulfilment models (non-VAT registered)
- Outputs a shortlist of profitable products grouped by supplier

---

## 2. Core Principles

### 2.1 Pricing Truth
- Do NOT use supplier RRP for profitability
- Use real Amazon market pricing only

### 2.2 Sell Price Selection **[v4]**

```
IF fba_seller_count > 0:
    price_basis = "FBA"

    IF amazon_status == "ON_LISTING":
        market_price = amazon_price
        risk_flags += "AMAZON_ON_LISTING"

    ELSE IF amazon_status == "UNKNOWN":
        market_price = buy_box_price
        risk_flags += "AMAZON_STATUS_UNKNOWN"

    ELSE IF fba_seller_count == 1:
        market_price = buy_box_price
        risk_flags += "SINGLE_FBA_SELLER"

    ELSE:
        market_price = buy_box_price

ELSE:
    price_basis = "FBM"
    market_price = buy_box_price
    risk_flags += "FBM_ONLY"
    # FBM listings are valid sourcing opportunities.
    # Fee model switches to FBM path — see 3.8a.
    # Do NOT apply FBA fulfilment or storage fees to FBM products.
```

### 2.3 Dual Price Reality

System calculates three price values: **[v3]**

- `market_price` — current realistic sell price (Buy Box, per 2.2)
- `raw_conservative_price` — 15th percentile of 90-day FBA history. No floor applied. This is the truth.
- `floored_conservative_price` — `raw_conservative_price` with a break-even floor applied. Used for display only.

**Decision engine uses `raw_conservative_price` exclusively.**
`floored_conservative_price` is shown in output for context but never used to qualify a product for SHORTLIST.

### 2.4 VAT Model **[v3]**

User is NOT VAT registered. VAT rate is fixed at 20%. No exceptions.

```
# VAT field resolution — applied during normalisation:

IF supplier provides cost_inc_vat only:
    buy_cost = cost_inc_vat
    cost_ex_vat = cost_inc_vat / 1.20   # back-derived for schema completeness

ELSE IF supplier provides cost_ex_vat only:
    buy_cost = cost_ex_vat × 1.20
    cost_inc_vat = buy_cost

ELSE IF supplier provides both:
    expected_inc = cost_ex_vat × 1.20
    IF abs(cost_inc_vat - expected_inc) > £0.02:    # tolerance for rounding
        status = REVIEW
        flag = "VAT_FIELD_MISMATCH"
        buy_cost = cost_inc_vat    # trust the inc-vat figure as source of truth
    ELSE:
        buy_cost = cost_inc_vat    # consistent — use directly

ELSE:
    status = REVIEW
    flag = "VAT_UNCLEAR"
    # do not proceed to profit calculation

# Sell price on Amazon is gross revenue.
# Do NOT strip VAT from sell price — non-VAT registered sellers receive the full Amazon price.
```

---

## 3. Functional Requirements

### 3.1 Supplier Data Acquisition
- All suppliers require login
- Use saved browser session first
- Fallback to automated login if needed
- Download latest price list

Supported formats:
- CSV
- XLSX / XLS
- PDF
- HTML tables

---

### 3.2 Data Normalisation **[v5]**

Standard schema:

```
supplier
source_file
supplier_sku
ean                       # unit-level EAN where known; case EAN in case_ean if different
case_ean                  # [v5] EAN of the case/multipack if different from unit EAN; null if same
product_name
brand
supplier_price_ex_vat     # [v5] the price exactly as quoted by the supplier — do not alter
supplier_price_inc_vat    # [v5] supplier_price_ex_vat × 1.20
supplier_price_basis      # [v5] UNIT | CASE — what the supplier price refers to (see 3.2b)
case_qty                  # [v5] units per case/box; 1 if sold individually, null if unknown
unit_cost_ex_vat          # [v5] derived — see 3.2b
unit_cost_inc_vat         # [v5] unit_cost_ex_vat × 1.20
case_cost_ex_vat          # [v5] derived — see 3.2b
case_cost_inc_vat         # [v5] case_cost_ex_vat × 1.20
rrp_inc_vat
moq                       # in cases if supplier sells by case; in units if sold individually
stock_status
category
capital_exposure          # moq × case_cost_inc_vat (or unit_cost_inc_vat if sold individually)
gated                     # Y / N / UNKNOWN — see 3.2a
```

Rules:
- EAN must be valid (standard checksum) or reject
- Preserve leading zeros on EANs
- Do not guess missing fields
- `supplier_price_basis` must be explicitly determined — never assumed (see 3.2b)
- If `case_qty` cannot be determined: set to null, flag `CASE_QTY_UNKNOWN`
- Unit and case costs are always derived from supplier_price and case_qty — never taken independently

#### 3.2b Case Quantity Detection and Price Splitting **[v5]**

Supplier price lists quote prices in one of two ways. The system must detect which before deriving costs.

**Detection logic — in priority order:**

```
1. Explicit column header
   IF supplier file has a column named or containing:
       "case price", "box price", "price per case", "per box"
   THEN supplier_price_basis = "CASE"

   IF supplier file has a column named or containing:
       "unit price", "price per unit", "each", "per unit"
   THEN supplier_price_basis = "UNIT"

2. Case qty column present
   IF case_qty column exists AND case_qty > 1:
       # Price basis is ambiguous — apply heuristic:
       implied_unit_price = supplier_price / case_qty
       IF implied_unit_price < £0.50:
           # Unit price this low is implausible for most products — price is likely per CASE
           supplier_price_basis = "CASE"
       ELSE IF supplier_price > (rrp_inc_vat × 0.90) AND rrp_inc_vat is known:
           # Price close to or above RRP — almost certainly per UNIT not per case
           supplier_price_basis = "UNIT"
       ELSE:
           supplier_price_basis = "AMBIGUOUS"
           flag = "PRICE_BASIS_AMBIGUOUS"
           # Route to MANUAL REVIEW — do not derive costs

3. No case qty column
   case_qty = 1
   supplier_price_basis = "UNIT"
   # Treat as single unit
```

**Once basis is determined — derive both prices:**

```
IF supplier_price_basis == "UNIT":
    unit_cost_ex_vat  = supplier_price_ex_vat
    unit_cost_inc_vat = supplier_price_ex_vat × 1.20
    case_cost_ex_vat  = supplier_price_ex_vat × case_qty
    case_cost_inc_vat = case_cost_ex_vat × 1.20

IF supplier_price_basis == "CASE":
    case_cost_ex_vat  = supplier_price_ex_vat
    case_cost_inc_vat = supplier_price_ex_vat × 1.20
    unit_cost_ex_vat  = supplier_price_ex_vat / case_qty
    unit_cost_inc_vat = unit_cost_ex_vat × 1.20

IF supplier_price_basis == "AMBIGUOUS":
    # Do not derive. Route to MANUAL REVIEW.
    unit_cost_ex_vat  = null
    case_cost_ex_vat  = null
```

#### 3.2a Gating Data Source **[v3]**

`gated` is populated as follows, in priority order:

1. **SellerAmp** — if SellerAmp returns a restriction status for the ASIN, use it
2. **Keepa** — some brand/category restriction data is surfaced in Keepa product data
3. **Default** — if neither source returns a definitive answer: `gated = UNKNOWN`

**Important:** In v1, the majority of products will return `gated = UNKNOWN` because SP-API (which provides authoritative restriction data) is out of scope. This is expected behaviour. `UNKNOWN` routes to REVIEW — the user performs a manual Seller Central check before purchasing.

Do NOT attempt to infer gating status from brand name or category alone — this produces false positives on open brands.

---

### 3.3 PDF Handling
- Attempt structured extraction
- If unreliable:
  ```
  status = MANUAL_REVIEW
  reason = "PDF extraction failed"
  ```

---

### 3.4 Product Matching (STRICT) **[v5]**

Each supplier row with a valid case_qty generates **up to two match attempts**:

```
MATCH ATTEMPT 1 — Unit ASIN
    Search Keepa by: ean (unit EAN)
    buy_cost = unit_cost_inc_vat
    match_type = "UNIT"

MATCH ATTEMPT 2 — Case/Multipack ASIN
    IF case_qty > 1:
        Search Keepa by: case_ean (if different from ean)
        OR search Keepa by: ean + filter for listings where title contains
            case_qty as a quantity (e.g. "Pack of 12", "Box of 12", "12 x")
        buy_cost = case_cost_inc_vat
        match_type = "CASE"
```

Rules:
- Match ONLY by EAN — no fuzzy matching on title or brand
- Reject if no exact EAN match for either attempt
- Each successful match produces an **independent product row** in the output
- A single supplier row can produce 0, 1, or 2 output rows (unit match, case match, or both)
- If one EAN matches multiple ASINs: flag `MULTI_ASIN_MATCH`, route to MANUAL REVIEW — do not auto-select
- If `case_qty` is null or `PRICE_BASIS_AMBIGUOUS` is set: attempt unit match only; flag `CASE_MATCH_SKIPPED`

---

### 3.5 Amazon Data Extraction (Keepa browser)

Pull:
```
asin
title
brand
buy_box_price
amazon_price              # price when Amazon.co.uk is a seller; null if not present
amazon_status             # [v3] "ON_LISTING" | "NOT_ON_LISTING" | "UNKNOWN"
fba_seller_count
monthly_sales_estimate
price_history             # raw FBA price series with timestamps
history_days              # number of days of FBA price history available
size_tier                 # small_parcel | large_parcel | oversize | UNKNOWN — see 3.5a
```

#### 3.5a Size Tier Derivation **[v3]**

Size tier is required to calculate FBA fees accurately.

Source priority:
1. SellerAmp — returns size tier directly for most standard products
2. Keepa — product dimensions where available
3. Manual lookup — if neither source provides dimensions

```
IF size_tier cannot be determined from any source:
    size_tier = "UNKNOWN"
    risk_flags += "SIZE_TIER_UNKNOWN"
    fba_fee = null
    # product CANNOT be auto-shortlisted — route to REVIEW
    # developer must NOT substitute an average fee or default fee value
```

**This is a hard rule.** An unknown size tier means an unknown fee. An unknown fee means profitability cannot be calculated. Do not shortlist.

---

### 3.6 Price Basis Engine

```
IF fba_seller_count > 0:
    price_basis = "FBA"

    IF amazon_status == "ON_LISTING":
        market_price = amazon_price
        risk_flags += "AMAZON_ON_LISTING"

    ELSE IF amazon_status == "UNKNOWN":
        market_price = buy_box_price
        risk_flags += "AMAZON_STATUS_UNKNOWN"

    ELSE IF fba_seller_count == 1:
        market_price = buy_box_price
        risk_flags += "SINGLE_FBA_SELLER"

    ELSE:
        market_price = buy_box_price

ELSE:
    price_basis = "FBM"
    market_price = buy_box_price
    risk_flags += "FBM_ONLY"
```

---

### 3.7 Conservative Pricing **[v3]**

Two values are calculated. Only `raw_conservative_price` is used for decisions.

`historical_lower_band` definition:
- **Window:** last 90 days of Keepa FBA price history
- **Metric:** 15th percentile of FBA prices within that window
- **Exclusions:** exclude periods where `fba_seller_count = 0`
- **Minimum data:** 30 days qualifying FBA history

```
IF history_days < 30:
    raw_conservative_price = market_price       # no adjustment possible
    floored_conservative_price = market_price
    risk_flags += "INSUFFICIENT_HISTORY"
    # Note: profit figures at INSUFFICIENT_HISTORY are based on current market_price.
    # If market_price is elevated (seasonal spike, competitor OOS), these figures
    # will be optimistic. Human reviewer must assess price history manually.

ELSE:
    historical_lower_band = 15th_percentile(
        fba_prices_last_90_days,
        excluding periods where fba_seller_count = 0
    )

    raw_conservative_price = min(market_price, historical_lower_band)

    price_floor = buy_cost + fees_conservative + MIN_PROFIT
    floored_conservative_price = max(raw_conservative_price, price_floor)

    IF raw_conservative_price < price_floor:
        risk_flags += "PRICE_FLOOR_HIT"
        # This product has historically traded below your break-even.
        # It will NOT reach SHORTLIST regardless of floored figure.
```

**Decision engine uses `raw_conservative_price` only.**
`floored_conservative_price` appears in output for reference but has no role in SHORTLIST/REVIEW/REJECT logic.

---

### 3.8 Fee Engine (SellerAmp) **[v4]**

Fee calculation branches on `price_basis`. Do not cross-apply FBA fees to FBM products or vice versa.

#### 3.8a FBA Fee Path

```
fees_current = calculate_fees_fba(market_price)
    referral_fee_current          # % of market_price
    fba_fee                       # fixed by size_tier — see 3.8c if UNKNOWN
    storage_fee_estimate

fees_conservative = calculate_fees_fba(raw_conservative_price)
    referral_fee_conservative     # % of raw_conservative_price
    fba_fee                       # same — size tier unchanged
    storage_fee_estimate
```

**Storage fee estimate (FBA only):**
```
storage_fee_estimate = (product_volume_cbft × monthly_storage_rate) / sales_estimate
```
- Flag `STORAGE_FEE_RISK` if `sales_estimate < STORAGE_RISK_THRESHOLD` (default 20/month)
- Q4 surcharge (Oct–Dec) materially higher — note in output if run during Q4

#### 3.8b FBM Fee Path **[v4]**

For FBM listings, FBA fulfilment and storage fees do not apply. Shipping cost is substituted.

```
fees_current = calculate_fees_fbm(market_price)
    referral_fee_current          # % of market_price — same rate as FBA
    shipping_cost                 # FBM_SHIPPING_ESTIMATE constant
    packaging_cost                # FBM_PACKAGING_ESTIMATE constant
    # NO fba_fee
    # NO storage_fee

fees_conservative = calculate_fees_fbm(raw_conservative_price)
    referral_fee_conservative     # % of raw_conservative_price
    shipping_cost                 # same
    packaging_cost                # same
```

Shipping and packaging are estimates. Real cost varies by weight and carrier.
All FBM shortlist items must carry `FBM_SHIPPING_ESTIMATED` flag — user verifies actual fulfilment cost before purchasing.
Default estimates are deliberately conservative (overstate cost rather than understate).

#### 3.8c Size Tier — FBA only **[v4]**

Size tier is irrelevant for FBM. Do not flag `SIZE_TIER_UNKNOWN` on FBM products.

```
IF price_basis == "FBM":
    size_tier = "N/A"

IF price_basis == "FBA" AND size_tier cannot be determined:
    size_tier = "UNKNOWN"
    risk_flags += "SIZE_TIER_UNKNOWN"
    fba_fee = FBA_FEE_CONSERVATIVE_FALLBACK    # default £4.50 — large parcel estimate
    # Profit calculation proceeds with fallback fee
    # Product routes to REVIEW — user verifies actual tier before purchasing
```

Using a conservative fallback instead of null avoids blocking all unknown-tier FBA products from REVIEW entirely. If profit passes at £4.50 FBA fee, the product is worth a human check.

**Size tier boundary (FBA only):**
```
IF size_tier is known AND product within SIZE_TIER_BOUNDARY_PCT of next tier:
    risk_flags += "SIZE_TIER_RISK"
```

---

### 3.9 Profit Engine **[v5]**

```
# buy_cost is determined by match_type — set during matching (3.4)
IF match_type == "UNIT":
    buy_cost = unit_cost_inc_vat

IF match_type == "CASE":
    buy_cost = case_cost_inc_vat

profit_current        = market_price - fees_current - buy_cost
profit_conservative   = raw_conservative_price - fees_conservative - buy_cost

margin_current        = profit_current / market_price
margin_conservative   = profit_conservative / raw_conservative_price

max_buy_price = market_price - fees_current - MIN_PROFIT

# MOQ capital exposure
# MOQ is in cases for case-sold products, units for unit-sold products
IF match_type == "CASE":
    capital_exposure = moq × case_cost_inc_vat
ELSE:
    capital_exposure = moq × unit_cost_inc_vat

IF capital_exposure > CAPITAL_EXPOSURE_LIMIT:
    risk_flags += "HIGH_MOQ"
```

---

### 3.10 Decision Engine **[v4]**

Thresholds (configurable — see Section 10):

```
SHORTLIST (all must be true):
    profit_conservative ≥ MIN_PROFIT            # uses raw_conservative_price
    AND margin_conservative ≥ MIN_MARGIN        # uses raw_conservative_price
    AND sales_estimate ≥ MIN_SALES_SHORTLIST    # default 20/month
    AND gated != Y
    AND "PRICE_FLOOR_HIT" NOT IN risk_flags     # product has historically traded below break-even
    AND "VAT_FIELD_MISMATCH" NOT IN risk_flags
    AND "VAT_UNCLEAR" NOT IN risk_flags

    # FBM listings CAN reach SHORTLIST [v4]
    # If price_basis == "FBM", fees_current uses FBM fee path (3.8b)
    # FBM_SHIPPING_ESTIMATED flag will be present — user confirms shipping cost before buying

    # SIZE_TIER_UNKNOWN uses conservative fallback fee [v4]
    # Product can reach SHORTLIST but SIZE_TIER_UNKNOWN flag will be present

REVIEW (any of the following):
    passes thresholds at market_price only (fails raw conservative)
    OR passes conservative thresholds but has any of:
        HIGH_MOQ
        SIZE_TIER_RISK
        SIZE_TIER_UNKNOWN
        SINGLE_FBA_SELLER
        AMAZON_ON_LISTING
        AMAZON_STATUS_UNKNOWN
        PRICE_FLOOR_HIT
        MULTI_ASIN_MATCH
        STORAGE_FEE_RISK
        VAT_FIELD_MISMATCH
        VAT_UNCLEAR
        FBM_ONLY (when profit passes at current but not conservative)
    OR sales_estimate >= MIN_SALES_REVIEW AND < MIN_SALES_SHORTLIST
    OR gated = UNKNOWN

    # INSUFFICIENT_HISTORY no longer forces REVIEW [v4]
    # It is a visible flag in output — human reviewer assesses at their discretion
    # Products with INSUFFICIENT_HISTORY can reach SHORTLIST if all other criteria pass

REJECT:
    profit_current < MIN_PROFIT AND profit_conservative < MIN_PROFIT
    OR gated = Y
    OR EAN invalid / no Amazon match
    OR VAT_UNCLEAR with no valid buy_cost
    OR sales_estimate < MIN_SALES_REVIEW
```

---

### 3.11 Risk Flags **[v4]**

All flags surface in output. Flags marked (→ REVIEW) force REVIEW even if profit thresholds pass.
Flags marked (blocks SHORTLIST) prevent auto-shortlisting regardless of profit.

```
AMAZON_ON_LISTING           # Amazon is a seller — Buy Box win probability low (→ REVIEW)
AMAZON_STATUS_UNKNOWN       # Cannot confirm whether Amazon is on listing (→ REVIEW)
SINGLE_FBA_SELLER           # One FBA seller controls listing — visible flag, does not force REVIEW
FBM_ONLY                    # No FBA sellers — FBM fee path applied, FBM_SHIPPING_ESTIMATED also set
FBM_SHIPPING_ESTIMATED      # [v4] FBM fulfilment cost is an estimate — verify before purchasing
PRICE_UNSTABLE              # High variance in recent price history
POSSIBLE_PRIVATE_LABEL      # See detection rules below (→ REVIEW)
INSUFFICIENT_HISTORY        # <30 days qualifying Keepa FBA history — visible flag only, user decides [v4]
PRICE_FLOOR_HIT             # Raw conservative price below break-even (→ REVIEW, blocks SHORTLIST)
SIZE_TIER_RISK              # Near size tier boundary — FBA fee may increase at remeasure
SIZE_TIER_UNKNOWN           # FBA fee unknown — conservative fallback applied (→ REVIEW) [v4]
STORAGE_FEE_RISK            # Low velocity — storage fees material to margin (→ REVIEW)
HIGH_MOQ                    # Capital exposure exceeds threshold (→ REVIEW)
MULTI_ASIN_MATCH            # EAN matched multiple ASINs (→ REVIEW)
VAT_FIELD_MISMATCH          # Supplier inc/ex VAT fields conflict (→ REVIEW, blocks SHORTLIST)
VAT_UNCLEAR                 # Cannot determine buy cost from supplier data (→ REVIEW, blocks SHORTLIST)
PRICE_BASIS_AMBIGUOUS       # [v5] Cannot determine if supplier price is per unit or per case (→ REVIEW, blocks SHORTLIST)
CASE_QTY_UNKNOWN            # [v5] Case quantity not found in supplier data — case match skipped
CASE_MATCH_SKIPPED          # [v5] Case ASIN match not attempted due to missing or ambiguous qty data
```

#### Private Label Detection Rules **[v3]**

Flag `POSSIBLE_PRIVATE_LABEL` when ANY TWO of the following are true:

1. `fba_seller_count == 1` AND that seller has held the Buy Box for >80% of the last 90 days
2. Brand name does not appear in any other ASIN in Keepa (unique brand with single listing)
3. Listing has fewer than 3 FBA sellers across its entire price history

Route to REVIEW only. Do not auto-reject.

---

## 4. Output Requirements

Generate:
1. CSV file
2. Excel file
3. Markdown report

---

## 5. Output Schema **[v5]**

Each supplier row can produce multiple output rows (one per successful ASIN match).
`match_type` identifies whether this row is a unit or case match.

```
supplier
ean                           # unit EAN used for this match
case_ean                      # case EAN if match_type == CASE and EAN differs
asin
product_name
match_type                    # [v5] UNIT | CASE
supplier_price_basis          # [v5] UNIT | CASE | AMBIGUOUS — as quoted by supplier
case_qty                      # [v5] units per case; 1 if unit match
unit_cost_ex_vat              # [v5]
unit_cost_inc_vat             # [v5]
case_cost_ex_vat              # [v5] null if case_qty == 1
case_cost_inc_vat             # [v5] null if case_qty == 1
buy_cost                      # [v5] the cost used in profit calc — unit or case depending on match_type
market_price
raw_conservative_price
floored_conservative_price    # display only
price_basis                   # FBA | FBM
fees_current
fees_conservative
profit_current
profit_conservative
margin_current
margin_conservative
sales_estimate
max_buy_price
capital_exposure
size_tier
history_days
gated                         # Y / N / UNKNOWN
decision                      # SHORTLIST | REVIEW | REJECT
decision_reason
risk_flags
```

---

## 6. Markdown Report Format **[v5]**

```
# Supplier Shortlist Report

## Summary
- Suppliers processed
- Source rows processed
- Unit ASINs matched
- Case/multipack ASINs matched
- Shortlisted FBA (unit)
- Shortlisted FBA (case)
- Shortlisted FBM (unit)
- Shortlisted FBM (case)
- Sent to review
- Rejected

## Supplier: <n>

### Shortlist — FBA Unit Matches
(table — full schema, match_type = UNIT)

### Shortlist — FBA Case/Multipack Matches
(table — full schema, match_type = CASE)

### Shortlist — FBM Unit Matches
(table — full schema, FBM_SHIPPING_ESTIMATED always visible)

### Shortlist — FBM Case/Multipack Matches
(table — full schema, FBM_SHIPPING_ESTIMATED always visible)

### Manual Review
(table — full schema + decision_reason, all match types)

### Rejected
(table — ean, product_name, match_type, decision_reason)
```

---

## 7. Non-Functional Requirements **[v5]**

- Must handle messy supplier data without crashing
- Must output partial results on partial failure
- Must populate `decision_reason` for every row regardless of decision state
- Must be conservative — no optimistic assumptions
- All thresholds must be configurable constants — not hardcoded
- FBM products must use FBM fee path (3.8b) — never apply FBA fees to FBM rows
- SIZE_TIER_UNKNOWN uses conservative fallback fee — do not null out the fee or skip the row

---

## 8. Future Enhancements (Not v1)

- SP-API integration (will resolve gating to Y/N definitively)
- SellerAmp API integration
- Price trend scoring
- Supplier scoring
- Multipack optimisation
- Reverse sourcing engine

---

## 9. Success Criteria

- Shortlist contains genuinely profitable products at raw conservative pricing
- False positives minimised — a shortlisted product survives normal price drops without the floor masking history
- Gated products never appear in shortlist
- FBM-only products never appear in shortlist
- User can act immediately on shortlist output with confidence
- REVIEW items provide enough context for a 60-second human decision

---

## 10. Key Constants (Configurable)

```
MIN_PROFIT                  = £3.00
MIN_MARGIN                  = 15%
MIN_SALES_SHORTLIST         = 20 / month     # auto-shortlist threshold
MIN_SALES_REVIEW            = 10 / month     # minimum to appear in REVIEW
CAPITAL_EXPOSURE_LIMIT      = £200
HISTORY_MINIMUM_DAYS        = 30
HISTORY_WINDOW_DAYS         = 90
LOWER_BAND_PERCENTILE       = 15
SIZE_TIER_BOUNDARY_PCT      = 10%
STORAGE_RISK_THRESHOLD      = 20 / month
VAT_RATE                    = 0.20           # fixed — UK standard rate, non-VAT registered seller
VAT_MISMATCH_TOLERANCE      = £0.02          # rounding tolerance for VAT field validation

# FBM fulfilment estimates [v4] — set these to your real average costs
FBM_SHIPPING_ESTIMATE       = £3.50          # default — Royal Mail 2nd class up to 1kg; adjust per product weight
FBM_PACKAGING_ESTIMATE      = £0.50          # default — poly bag / small box
FBA_FEE_CONSERVATIVE_FALLBACK = £4.50        # used when size_tier is UNKNOWN — large parcel estimate

# Case/unit detection [v5]
MIN_PLAUSIBLE_UNIT_PRICE    = £0.50          # implied unit price below this → assume price is per CASE
```

---

## Appendix A: What Changed from v2

| Section | Change | Reason |
|---|---|---|
| 2.2 | Added `AMAZON_STATUS_UNKNOWN` state | Detection of Amazon on listing can be ambiguous — needs explicit fallback |
| 2.3 | Split `conservative_price` into `raw_conservative_price` and `floored_conservative_price` | Floor was rescuing below-break-even products to SHORTLIST — false positive |
| 2.4 | VAT field resolution logic for three supplier states | Many suppliers provide inc-VAT only; conflicting fields were silently ignored |
| 3.2a | Added gating data source definition | `gated` field had no implementation instruction |
| 3.5a | Added size tier derivation rules and `SIZE_TIER_UNKNOWN` state | Size tier had no defined source; unknown tier means unknown fee; fee cannot be assumed |
| 3.7 | `raw_conservative_price` separated from floored version | Decision engine must not use floored price for SHORTLIST |
| 3.7 | Added note on INSUFFICIENT_HISTORY + elevated price risk | Profit figures at insufficient history are based on potentially spiked market_price |
| 3.8 | `fees_conservative` now uses `raw_conservative_price` | Was using floored version — overstated conservative profit |
| 3.9 | Profit and margin calculations use `raw_conservative_price` | Consistency with decision engine |
| 3.10 | `FBM_ONLY` explicitly blocks SHORTLIST | Was a mandatory flag but not in SHORTLIST exclusions — FBM listings could auto-shortlist |
| 3.10 | `SIZE_TIER_UNKNOWN`, `VAT_FIELD_MISMATCH`, `VAT_UNCLEAR` block SHORTLIST | Unknown inputs cannot produce reliable profit calculations |
| 3.10 | Sales threshold split: `MIN_SALES_SHORTLIST = 20`, `MIN_SALES_REVIEW = 10` | 10/month too low for auto-shortlist; storage fees and BSR estimate variance too high |
| 3.11 | `POSSIBLE_PRIVATE_LABEL` now has three deterministic detection rules | Was vague — no implementable logic existed |
| 3.11 | Added `VAT_FIELD_MISMATCH`, `VAT_UNCLEAR`, `AMAZON_STATUS_UNKNOWN`, `SIZE_TIER_UNKNOWN` | New flags from issue resolution |
| 5 | `fail_reason` → `decision_reason` | Semantically wrong for REVIEW items; now populated for all three decision states |
| 5 | `conservative_price` → `raw_conservative_price` + `floored_conservative_price` | Schema must match engine |
| 10 | Added `MIN_SALES_SHORTLIST`, `MIN_SALES_REVIEW`, `VAT_MISMATCH_TOLERANCE` | New constants from fixes |

---

## Appendix B: False Positive Defence — Scenario Tests

The following scenarios were explicitly tested against this PRD:

| Scenario | How v3 handles it |
|---|---|
| Price temporarily high, regularly drops 10–20% | 15th percentile over 90 days captures the drop. If drop is severe, `PRICE_FLOOR_HIT` blocks SHORTLIST. |
| Amazon re-enters listing | `AMAZON_ON_LISTING` → REVIEW. Market price modelled at Amazon's price (worst case). |
| Single FBA seller controlling Buy Box | `SINGLE_FBA_SELLER` → REVIEW. Cannot auto-shortlist. |
| Liquidation event skewing 15th percentile | `PRICE_FLOOR_HIT` fires. `raw_conservative_price` is below floor. Routes to REVIEW. Does NOT shortlist via floored price. |
| Low sales hiding storage cost impact | `STORAGE_FEE_RISK` at <20/month. Sales 10–19 → REVIEW. Sales <10 → fails `MIN_SALES_REVIEW`, REJECT. |
| Insufficient history on spiked price | `INSUFFICIENT_HISTORY` → REVIEW. Note in output that figures are based on elevated current price. |
| FBM listing modelled with FBA fees | Blocked. `FBM_ONLY` explicitly excluded from SHORTLIST. |
| Unknown size tier → wrong fee → wrong profit | `SIZE_TIER_UNKNOWN` → REVIEW, blocks SHORTLIST. No default fee substitution permitted. |
