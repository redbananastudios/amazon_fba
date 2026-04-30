---
name: skill-2-selleramp
description: >
  DEPRECATED legacy skill. The non-Buy-Box-% checks (gating, FBA
  eligibility, hazmat, catalog brand) are now provided by the SP-API
  MCP via fba_engine/steps/enrich.py with `include: leads`. Buy Box %
  remains the only SellerAmp-unique field; Keepa stats expose enough
  signal (`buy_box_avg90`) for most cases. Keep this file for
  reference only — do not invoke from new pipelines.
---

# Skill 2 -- SellerAmp Enrichment (Phase 2) — DEPRECATED

> **Deprecated 2026-04-30.** Replaced by the canonical engine's
> `enrich` step in leads mode (PR #34). The MCP at
> `services/amazon-fba-fees-mcp/` returns gating, FBA eligibility,
> hazmat, and catalog brand directly via SP-API — no SellerAmp login
> or paid subscription required. Strategies that need this data
> chain `enrich` with `include: leads` (alias for
> `[restrictions, fba, catalog]`).
>
> See `docs/strategies/seller_storefront.md` for the canonical
> wholesale-leads workflow. The historical SellerAmp content below
> is kept for archival reference only.

---

Reads phase1_filtered.csv (output of Phase 1 exclusion filter),
pre-filters using Keepa data, then visits sas.selleramp.com per ASIN
to collect the fields Keepa cannot provide.
Produces phase2_enriched.csv plus gated/hazmat side files.

---

## Before You Start

1. Confirm phase1_filtered.csv exists:
   ./data/{niche}\{niche}_phase1_filtered.csv
   (output of Phase 1 exclusion filter -- falls back to phase1_raw.csv if no exclusions exist)
2. Read CLAUDE.md -- confirm SellerAmp credentials
3. Navigate to sas.selleramp.com and log in using credentials from CLAUDE.md
4. Confirm the SellerAmp dashboard loads correctly before processing

---

## Step 1 -- Pre-Filter Using Keepa Data (No Browser Needed)

Read phase1_filtered.csv. Apply the following rules using columns already
in the file. No browser needed for this step.

### Remove entirely (do not process in SellerAmp):

  New FBA Offer Count current > 20
    Slipped through Keepa snapshot -- oversaturated, skip

  Buy Box 90-day drop % < -20%
    Strong price erosion signal -- not worth pursuing
    Log as: PRICE EROSION

  Bought in past month < [NICHE.velocity_min]
    Failed velocity floor -- dead stock
    Log as: LOW VELOCITY

### Flag but keep (still process in SellerAmp):

  Buy Box 90-day drop % between -10% and -20%
    Possible dip or slow erosion -- needs chart confirmation
    Flag as: PRICE CHECK

  Amazon on listing = Yes (from Keepa flag column)
    May have high Amazon Buy Box % -- confirm in SellerAmp
    Flag as: AMAZON CHECK

  New FBA Offer Count = 2
    Could be private label with one ghost seller -- check brand quality
    Flag as: LOW SELLER CHECK

  Current price outside GBP20-70 (price may have shifted since export)
    Flag as: PRICE DRIFT

### Expected result after pre-filter:
  Typically 200-400 products survive from 1000.
  Save filtered list as: {niche}_phase2_prefiltered.csv

---

## Step 2 -- SellerAmp Enrichment

For each product in the pre-filtered list:

  Navigate to:
  https://sas.selleramp.com/sas/lookup?search={ASIN}

  Wait for the results page to fully load (SellerAmp calculates fees
  and retrieves listing data -- allow 3-5 seconds per product).

  Read and record the following fields:

  FIELD               COLUMN         NOTES
  Fulfilment Fee (GBP) fba_fee        The fulfillment fee Amazon charges
  Est Profit (GBP)    est_profit     SellerAmp calculation at current price
  ROI %               roi_pct        SellerAmp ROI at 65% assumed cost
  Buy Box %           buybox_pct     Amazon vs 3P split -- key filter
  Amazon on listing   amazon_listed  Y/N confirmation
  Gating status       gated          Y = restricted, N = open
  Hazmat status       hazmat         Y = hazmat confirmed, N = clear
  Sales velocity      velocity_sa    SellerAmp monthly estimate (cross-check)

  Additional fields to collect:

  FIELD               COLUMN           NOTES
  Listing Quality     listing_quality  Derived from indicators below
  Image Count         image_count      Number of listing images (main + gallery)
  Has A+ Content      has_aplus        Y/N -- enhanced brand content on listing
  Bullet Point Count  bullet_count     Number of bullet points in listing
  Star Rating         star_rating      Average customer rating (1.0-5.0)
  Review Count        review_count     Total number of customer reviews
  Brand 1P            brand_1p         Y/N -- brand sells direct via Amazon Vendor/1P

  Listing Quality derivation:
    STRONG: 6+ images AND A+ content AND 5+ bullet points
    AVERAGE: 4+ images OR A+ content OR 4+ bullet points (at least one)
    WEAK: under 4 images AND no A+ AND under 4 bullet points

  Brand 1P detection:
    Check if the Buy Box seller name matches the brand name (or a known
    brand trading name). Also check if seller ID is Amazon (A3P5ROKL5A1OLE
    or AZH2GF8Z5J95G for .co.uk). If brand IS the seller: Brand 1P = Y.

  If SellerAmp cannot find the ASIN or returns an error:
    Log as: SA ERROR
    Move on -- do not block the run

---

## Step 3 -- Apply Trim Rules After Enrichment

After all SellerAmp data is collected, apply these rules:

### Flag (not remove):

  ROI % < 20%
    Flag as: MAYBE-ROI
    Note: Real supplier price in Skill 5 may improve this.
    Do NOT remove -- a 28% estimated ROI may become 35%+ on trade price.

  Amazon Buy Box % > 70%
    Flag as: AMAZON DOMINANT
    Not removed here -- scored down heavily in Skill 3

### Move to separate list:

  Gated = Y
    Move to: {niche}_phase2_gated.csv
    Keep in main file with verdict: GATED
    Note: most products may show as gated initially -- do not panic.
    Gating can be applied for and resolved -- this is not a hard reject.

### Remove entirely:

  Hazmat = Y (confirmed by SellerAmp, not just Keepa filter)
    Remove from main file entirely
    Log in: {niche}_phase2_hazmat.csv

  FBA Offer Count now > 20 (SellerAmp confirms saturation)
    Remove. Log as: OVERSATURATED

---

## Step 4 -- Calculate Estimated Profit and ROI

For any product where SellerAmp profit/ROI is missing or unclear,
calculate manually:

  Est Cost  = Current Price x 0.65
  Est Profit = Current Price - Est Cost - Fulfilment Fee
  ROI %      = (Est Profit / Est Cost) x 100

Use SellerAmp figures where available. Use calculation as fallback.
Note the source in the data_source column: SA or CALC.

---

## Step 5 -- Save Output Files

### Main enriched file (all pre-filtered products + SellerAmp data)
Path: ./data/{niche}\{niche}_phase2_enriched.csv

Columns:
  ASIN, Title, Brand, Amazon URL, Category,
  Current Price (GBP), Buy Box 90-day avg (GBP), Price drop % 90-day,
  BSR Current, BSR Drops last 90 days, Bought in past month,
  New FBA Offer Count, Amazon on listing,
  Fulfilment Fee (GBP), Est Profit (GBP), ROI %, Buy Box %,
  Gated (Y/N), Hazmat (Y/N), Sales velocity (SA),
  Listing Quality, Image Count, Has A+ Content, Bullet Point Count,
  Star Rating, Review Count, Brand 1P, Weight Flag (carried from Phase 1),
  Pre-filter flags, SellerAmp flags, Data source

### Gated products (separate)
Path: ./data/{niche}\{niche}_phase2_gated.csv
Same columns. These products stay visible for ungating decisions.

### Hazmat products (separate, for awareness)
Path: ./data/{niche}\{niche}_phase2_hazmat.csv

### Stats file
Path: ./data/{niche}\{niche}_phase2_stats.txt

Content:
  Phase 1 input count: {count}
  After pre-filter: {count}
  Removed at pre-filter:
    Price erosion: {count}
    Low velocity: {count}
    Oversaturated: {count}
  SellerAmp processed: {count}
  SellerAmp errors: {count}
  After enrichment trim:
    Hazmat removed: {count}
    Gated (flagged): {count}
    MAYBE-ROI flagged: {count}
    Amazon dominant flagged: {count}
  Final enriched count: {count}
  Listing quality breakdown:
    STRONG: {count}
    AVERAGE: {count}
    WEAK: {count}
  Brand 1P detected: {count}
  Reviews > 500: {count}
  Reviews < 20: {count}

### Handoff file
Path: ./data/{niche}\{niche}_phase2_handoff.md

Content:
  # Phase 2 Handoff -- {niche}

  Status: COMPLETE
  Input: {niche}_phase1_filtered.csv ({count} products)
  Output: {niche}_phase2_enriched.csv ({count} products)
  Gated list: {niche}_phase2_gated.csv ({count} products)

  ## Key flags in the enriched file
  PRICE CHECK: may be dipping -- confirm chart in Skill 3
  AMAZON CHECK: Amazon on listing -- Buy Box % confirms dominance
  LOW SELLER CHECK: only 2 sellers -- check brand quality
  MAYBE-ROI: below 20% estimated -- may improve with real trade price
  AMAZON DOMINANT: Amazon Buy Box >70% -- scored down in Skill 3

  ## Next step
  Run Skill 3 -- Scoring and Shortlist
  Input: data/{niche}\{niche}_phase2_enriched.csv

---

## Step 5A -- Update Exclusion List

Append confirmed HAZMAT products to the exclusion list so they are
skipped on future reruns:

  node skills/shared\manage_exclusions.js --add --niche {niche} --csv data/{niche}\{niche}_phase2_hazmat.csv --verdicts HAZMAT --phase "Phase 2"

---

## Quality Check

  [ ] phase2_enriched.csv exists with correct column count
  [ ] SellerAmp errors are under 10% of processed products
  [ ] Gated and hazmat files exist (even if empty)
  [ ] Stats and handoff files saved
  [ ] Spot check 5 rows: Fulfilment Fees look realistic (GBP2-8 range typical)
  [ ] Listing Quality column populated (STRONG/AVERAGE/WEAK)
  [ ] Star Rating and Review Count present
  [ ] Brand 1P flagged where detected
  [ ] HAZMAT ASINs appended to exclusions.csv

