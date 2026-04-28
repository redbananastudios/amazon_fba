---
name: skill-1-keepa-finder
description: >
  FBA sourcing Phase 1. Use whenever the task is to discover Amazon FBA
  product leads for a niche using Keepa Product Finder. Triggers on:
  "run phase 1", "find products for [niche]", "start sourcing",
  "discover products", "build product list". Uses the keepa-product-finder
  skill (URL-first approach) as primary method. Falls back to manual
  browser form-filling only if URL method fails.
  Always run this skill before any validation or scoring work.
---

# Skill 1 -- Keepa Product Finder (Phase 1)

Uses the keepa-product-finder skill to build a Keepa Product Finder URL
from niche-specific filters, then exports the full Phase 1 dataset as CSV.

PRIMARY METHOD: Use keepa-product-finder skill (URL-encoded JSON approach).
FALLBACK: Manual browser form-filling (see Step 2-ALT below).

---

## Before You Start

1. Read CLAUDE.md -- confirm Keepa credentials and output base path
2. Identify niche from the task:
   afro-hair | kids-toys | educational-toys | stationery | sports-goods | pet-care
3. Read config/niche-configs\{niche}.md for niche-specific overrides
4. Create output folder if it does not exist:
   ./data/{niche}\
5. Read skills/keepa-product-finder\references\keepa-finder-values.md
   for verified category IDs and filter shapes
6. Navigate to: https://keepa.com/#!finder
7. Confirm logged in -- username visible top right
   If not: use credentials from CLAUDE.md to log in

---

## Step 1 -- Clear Any Previous Filters

Click "CLEAR ENTIRE FORM" at the top of the page.
Wait for all fields to reset before proceeding.

---

## Step 2 -- Apply Filters

Apply in this order. Values marked [NICHE] come from the niche config file.

### BLOCK A -- Sales Rank

  Sales Rank # Current:
    From: [NICHE.bsr_min]
    To:   [NICHE.bsr_max]

  Sales Rank # 90 days avg:
    To: [NICHE.bsr_max + 20000]
    (allows natural fluctuation -- product may dip above ceiling briefly)

  Drops last 90 days:
    From: 3
    (must have sold at least 3 times -- removes dead stock)

### BLOCK B -- Price (Buy Box)

  Buy Box current:
    From: 20
    To:   70

  Buy Box 90 days avg:
    From: 18
    (protects against products in a sustained dip -- avg must be near range)

### BLOCK C -- Sellers

  New FBA Offer Count -- Current:
    From: 2   (min 2 -- single seller = almost always private label)
    To:   20  (max 20 -- beyond this = saturated)

  New FBA Offer Count -- 90 days avg:
    To: 20
    (filters products that recently attracted a flood of new sellers)

### BLOCK D -- Demand

  Bought in past month:
    From: [NICHE.velocity_min]
    (floor set per niche -- see config)

### BLOCK E -- Buy Box Seller

  Leave set to ALL (include Amazon).
  Do NOT exclude Amazon here.
  Amazon buy box % is handled as a flag in Skill 2, not a hard filter here.

### BLOCK F -- Safety Filters

  Is HazMat: No
  Physical Product: Yes only (uncheck Digital and eBooks)
  Variations: Show only one variation per product

### BLOCK G -- Category and Title

  Root category:        [NICHE.root_category]
  Include subcategory:  [NICHE.include_subcategory] (if set)
  Exclude subcategory:  [NICHE.exclude_subcategory] (if set)
  Title keyword:        [NICHE.title_keyword] (if set -- uses ### for OR)
  Item Form:            [NICHE.exclude_item_form] (if set)
  Is Heat Sensitive:    [NICHE.heat_sensitive] (if set)
  Batteries Required:   [NICHE.batteries] (if set)
  Batteries Included:   [NICHE.batteries_included] (if set)

---

## Step 3 -- Set Rows Per Page

Before running the search set rows per page to 5000.
Keepa export is page-based, so use the largest supported page size to
minimize the number of exports needed.

---

## Step 4 -- Run Search

Click FIND PRODUCTS.
Wait for the results grid to fully load.
Confirm the total result count is shown in the toolbar.

If result count is under 500:
  Increase BSR To by 20,000 and re-run.
  Note the adjusted range in the stats file.

If result count is under 200:
  Flag this in the handoff. Do not pad with out-of-scope products.

---

## Step 5 -- Export

Click the Export button in the results toolbar (top left of grid).
Keepa exports the current results page.
Export every page needed to capture the full result set. Do not
artificially cap at 1000.

If the result set spans multiple pages:
  Export each page in order.
  Merge the page CSVs into one final CSV for Phase 1 handoff.
  Save the merged file using the standard raw path below.

Save the file to:
  ./data/{niche}\{niche}_phase1_raw.csv

---

## Step 5A -- Exclusion Filter (Reruns Only)

If this is a rerun of an existing niche, filter out previously rejected ASINs.

Run:
  node skills/shared\manage_exclusions.js --filter --niche {niche} --input data/{niche}\{niche}_phase1_raw.csv --output data/{niche}\{niche}_phase1_filtered.csv

If exclusions were applied:
  Use {niche}_phase1_filtered.csv as the handoff file for Phase 2 (not _raw.csv).
  Log the exclusion count in the stats file.

If no exclusions exist (first run):
  The script copies raw to filtered unchanged. Use _filtered.csv anyway for consistency.

---

## Step 5B -- Weight and Dimension Flags

Keepa export includes package weight and dimensions. Scan the exported CSV:

  Weight > 5kg     → add flag HEAVY to a new "Weight Flag" column
  Largest dimension > 45cm → add flag OVERSIZE to "Weight Flag" column
  Both conditions   → HEAVY+OVERSIZE
  Neither          → OK

This flag is informational. Do not remove products here.
Phase 2 will watch FBA fees closely on HEAVY/OVERSIZE items.
Phase 3 uses it as a scoring modifier.

---

## Step 6 -- Save Output Files

### Stats file
Path: ./data/{niche}\{niche}_phase1_stats.txt

Content:
  Niche: {niche}
  Date: {today}
  BSR range used: {from} - {to}
  Velocity floor: {value}
  Raw export count: {count}
  Exclusions applied: {count} (or 0 if first run)
  Weight flags: {heavy_count} HEAVY, {oversize_count} OVERSIZE
  Output: {niche}_phase1_filtered.csv
  Notes: {anything unusual}

### Handoff file
Path: ./data/{niche}\{niche}_phase1_handoff.md

Content:
  # Phase 1 Handoff -- {niche}

  Status: COMPLETE
  Products exported: {count}
  File: data/{niche}\{niche}_phase1_filtered.csv

  ## Columns in this file
  ASIN, Title, Bought in past month, Buy Box current (GBP),
  Buy Box 90-day avg (GBP), Buy Box 90-day drop %,
  Amazon on listing (flag), New FBA Offer Count current,
  New FBA Offer Count 90-day avg, BSR current,
  BSR drops last 90 days, Category, Subcategory

  ## NOT in this file (added by later skills)
  Buy Box % split (Amazon vs 3P)  -- Skill 2
  Fulfilment Fee                   -- Skill 2
  ROI %                            -- Skill 2 + Skill 3
  Gating status                    -- Skill 2
  Price flag (chart shape)         -- Skill 3 (shortlist only)
  Supplier / trade price           -- Skill 5

  ## Next step
  Run Skill 2 -- SellerAmp Enrichment
  Input: data/{niche}\{niche}_phase1_filtered.csv

---

## Quality Check

Before finishing confirm:
  [ ] CSV exists at correct path
  [ ] Row count is at least 500 (warn if under, flag if under 200)
  [ ] Spot check 10 rows: ASINs are 10 chars, prices are in GBP
  [ ] No obvious private label in first 20 rows
  [ ] Stats file and handoff file saved
  [ ] Exclusion filter applied (if rerun)
  [ ] Weight Flag column present with HEAVY/OVERSIZE/OK values

---

## Common Issues

Keepa session expired:
  Log out and back in using credentials from CLAUDE.md. Retry.

Result count under 200:
  BSR range too tight. Broaden To by 30,000 and retry.
  Note the wider range in stats file.

Export button not visible:
  Wait for the results grid to fully populate (count shown in toolbar).
  Export appears top-left of results area after grid loads.
