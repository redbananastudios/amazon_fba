---
name: skill-3-scoring
description: >
  FBA sourcing Phase 3. Use after Skill 2 has produced phase2_enriched.csv.
  Triggers on: "run phase 3", "score products", "build shortlist",
  "assign verdicts", "shortlist products". Pure logic -- no browser needed.
  Reads enriched CSV, scores each product across 4 dimensions, assigns
  a verdict, produces a shortlist of 50-100 products for supplier research.
---

# Skill 3 -- Scoring and Shortlist (Phase 3)

Pure data processing. No browser. Reads phase2_enriched.csv, calculates
composite scores, assigns verdicts, outputs a clean shortlist.

---

## Before You Start

1. Confirm phase2_enriched.csv exists:
   ./data/{niche}\{niche}_phase2_enriched.csv
2. No browser or login needed for this skill

---

## Step 1 -- Hard Rejects (Apply First, Before Scoring)

Remove from the main scored file immediately. Log reason.

  Hazmat = Y               --> HAZMAT (already in separate file but confirm)
  Price outside GBP20-70   --> NO (Price Range)
  FBA sellers > 20         --> NO (Oversaturated)

Do NOT hard reject:
  Gated products           --> verdict = GATED, keep in file
  ROI below 20%            --> verdict = MAYBE-ROI, keep in file
  Amazon Buy Box > 70%     --> score down heavily, keep in file

---

## Step 2 -- Score Each Product (0-10 Per Dimension)

### DEMAND SCORE (30% weight)

Primary signal: BSR Current
  Under 10,000:   10
  10,000-20,000:  9
  20,000-30,000:  8
  30,000-40,000:  7
  40,000-50,000:  6
  50,000-60,000:  5
  60,000-80,000:  3
  Above 80,000:   1

Modifier: BSR Drops last 90 days
  15+ drops:  +1 (selling frequently)
  Under 3:    -1 (barely moving)

Modifier: Bought in past month
  300+:       +1
  Under 100:  -1

Modifier: Star Rating (from Phase 2)
  Rating < 3.5:   -1 (quality issues, returns risk)

Modifier: Review Count (from Phase 2)
  Reviews > 500 AND rating > 4.0:  +1 (proven, trusted product)

Cap demand score at 10.

### PRICE STABILITY SCORE (30% weight)

Primary signal: Buy Box 90-day drop %
  0% to +5% (flat or rising):          10
  -5% to 0%:                            8
  -10% to -5%:                          6
  -15% to -10%:                         4
  -20% to -15%:                         2
  Below -20%:                           0 (PRICE EROSION)

Modifier: PRICE CHECK flag (from Skill 2)
  If flagged PRICE CHECK:               -1

Modifier: Recovery Detection
  If 90-day drop > 10% BUT 30-day trend is rising (current > 30-day avg):
    +2 (recovering from dip -- BUY THE DIP candidate)
  If 90-day drop > 10% AND 30-day trend still falling:
    -1 (still declining -- stronger erosion signal)

Note: Products scoring 0 on stability get verdict PRICE EROSION.

### COMPETITION SCORE (20% weight)

Primary signal: New FBA Offer Count current

  Apply dynamic ceiling first.
  If seller count exceeds ceiling for velocity tier: score = 0.

  Dynamic ceiling by monthly velocity:
    Under 300/month:   max 8 sellers
    300-600/month:     max 12 sellers
    600-1000/month:    max 15 sellers
    Over 1000/month:   max 20 sellers

  If within ceiling:
    2 sellers:    10 (flag BRAND APPROACH if listing quality weak)
    3 sellers:    9
    4-5 sellers:  7
    6-8 sellers:  5
    9-12 sellers: 3
    13-20 sellers:1

Modifier: Amazon on listing + Buy Box %
  Amazon Buy Box % > 70%:  -3
  Amazon Buy Box % 50-70%: -1
  Amazon Buy Box % < 30%:  0 (buying fairly -- no penalty)

Modifier: Seller Count Trend
  Current sellers < 90-day avg sellers:                +1 (sellers leaving = opportunity)
  Current sellers > 90-day avg sellers by 50%+:        -1 (sellers piling in = rising competition)

Modifier: Brand 1P (from Phase 2)
  Brand 1P = Y:   -2 (brand sells direct, will match any price)

Modifier: Review Count (from Phase 2)
  Reviews < 20:   -1 (unproven product, risky to source)

### MARGIN SCORE (20% weight)

Primary signal: ROI %
  Above 40%:    10
  35-40%:       9
  30-35%:       7
  25-30%:       5
  20-25%:       3
  Below 20%:    1  (flag MAYBE-ROI -- real price may improve)

Note: ROI is estimated at 65% of current price. Real supplier price
in Skill 5 will update this. Do not hard reject on ROI here.

Modifier: Est Profit
  Above GBP8:   +1
  Under GBP3:   -1

Modifier: Weight Flag (from Phase 1)
  HEAVY or OVERSIZE:    -1 (FBA fees likely elevated, margin pressure)
  HEAVY+OVERSIZE:       -2 (significant fee impact)

Cap margin score at 10.

---

## Step 3 -- Calculate Composite Score

  Composite = (Demand x 0.30) + (Stability x 0.30) +
              (Competition x 0.20) + (Margin x 0.20)

  Round to 1 decimal place.

---

## Step 3A -- Calculate Lane Scores

Three lane-specific scores derived from the base dimensions plus real velocity/profit data:

### Cash Flow Score (0-10)
Base: Demand x 0.30 + Stability x 0.25 + Competition x 0.20 + Margin x 0.10
Bonuses:
  Bought >= 400/mo: +1.5
  Bought >= 200/mo: +1.0
  Bought >= 100/mo: +0.5
Penalties:
  Est Profit < GBP1.50: -2
  Est Profit < GBP2.50: -1

### Profit Score (0-10)
Base: Margin x 0.30 + Stability x 0.25 + Competition x 0.20 + Demand x 0.10
Bonuses:
  Est Profit >= GBP12: +1.5
  Est Profit >= GBP8: +1.0
  Est Profit >= GBP5: +0.5
  Est ROI >= 35%: +1.0
  Est ROI >= 25%: +0.5

### Balanced Score (0-10)
Base: Demand x 0.25 + Stability x 0.25 + Competition x 0.25 + Margin x 0.25
Bonuses (requires BOTH velocity and profit):
  Bought >= 150 AND Est Profit >= GBP4: +1.0
  Bought >= 100 AND Est Profit >= GBP3: +0.5
Penalties:
  Bought < 50 OR Est Profit < GBP1.50: -1

---

## Step 3B -- Price Compression Flag

Compares current price to 90-day average:
  Price < 80% of 90d avg: COMPRESSED (margin being crushed)
  Price 80-90% of 90d avg: SQUEEZED (margin under pressure)
  Price >= 90% of 90d avg: OK

---

## Step 3C -- Lane Classification

### Thresholds (configurable)
Hard disqualifiers (no lane assigned):
  Est ROI < 5%
  Est Profit < GBP1
  Verdict = NO, PRICE EROSION, or HAZMAT

Lane rules (first match wins after BALANCED check):

BALANCED (Commercial Priority = 1):
  Bought >= 150/mo AND Est ROI >= 20% AND Est Profit >= GBP2.50 AND Sellers <= 10
  OR qualifies as BOTH Profit AND Cash Flow

PROFIT (Commercial Priority = 2):
  Est Profit >= GBP8
  OR Est ROI >= 25% AND Est Profit >= GBP4
  OR Est ROI >= 30% AND Est Profit >= GBP3

CASH FLOW (Commercial Priority = 3):
  Bought >= 200/mo AND Est ROI >= 10% AND Est Profit >= GBP1.50

Unclassified (Commercial Priority = 9):
  Does not meet any lane criteria

### Lane Reason format
"[velocity]/mo | GBP[profit] profit | ROI [roi]% | GBP[monthly_gross]/mo gross"

### Monthly Gross Profit
Calculated as: Bought per Month x Est Profit

---

## Step 4 -- Assign Verdict

Apply in this order (first match wins):

  Hazmat = Y                                  --> HAZMAT
  Stability score = 0                         --> PRICE EROSION
  Brand 1P = Y AND Amazon BB % > 60%         --> NO (Brand 1P dominant)
  Gated = Y                                   --> GATED
  Composite >= 8.5, all flags clear            --> YES
  Composite >= 7                              --> MAYBE
  Seller count = 2-3 AND Listing Quality = WEAK --> BRAND APPROACH
  Price drop % < -25% with recovery signal   --> BUY THE DIP
    (drop is large but 90-day avg was much higher -- potential dip)
  Everything else                             --> NO (state reason)

### Verdict Reason (mandatory for every product)

Every product MUST have a brief, specific Verdict Reason explaining WHY
it got that verdict. Use actual numbers from the data. Examples:

  YES:            "BSR 5,200 | 300/mo | 4 sellers | ROI 38% | price stable"
  MAYBE:          "BSR 32,000 | 150/mo | score 5.8 | Amazon BB 55% drags competition"
  MAYBE-ROI:      "Strong demand BSR 8,000 but est ROI 22% -- needs trade price under GBP18"
  BRAND APPROACH: "Only 2 sellers | listing WEAK (3 images, no A+) | BSR 15,000 | contact brand"
  BUY THE DIP:    "Price GBP25 vs 90-day avg GBP38 (-34%) | 30-day trend recovering | 6 sellers"
  PRICE EROSION:  "90-day drop -28% | 30-day still falling | no recovery signal"
  GATED:          "Score 7.2 would be YES but gated | BSR 12,000 | ROI 35% | apply for access"
  NO:             "FBA sellers 18 + velocity only 120/mo = oversaturated for demand"
  NO:             "Brand 1P detected | brand holds BB 85% | cannot compete"
  HAZMAT:         "Confirmed hazmat by SellerAmp"

Format: pipe-separated key metrics, max ~80 chars. Must reference the
specific data points that drove the verdict decision.

---

## Step 5 -- Build Shortlist

Include in shortlist: YES + MAYBE + BRAND APPROACH + BUY THE DIP + GATED

Exclude from shortlist: NO + PRICE EROSION + HAZMAT

Target: 50-100 products on shortlist.

If shortlist > 100:
  Raise composite threshold to 6 and re-filter.

If shortlist < 30:
  Lower composite threshold to 4.5 and re-filter.
  Check if Keepa BSR range was too restrictive -- note in handoff.

Sort shortlist by:
  1. Commercial Priority ascending (BALANCED=1, PROFIT=2, CASH FLOW=3, unclassified=9)
  2. Verdict priority (YES first, then MAYBE, then BRAND APPROACH, BUY THE DIP, GATED)
  3. Monthly Gross Profit descending
  4. Composite Score descending

---

## Step 6 -- Save Output Files

### Full scored file (all products with scores and verdicts)
Path: ./data/{niche}\{niche}_phase3_scored.csv

Columns (all Phase 2 columns plus):
  Demand Score, Stability Score, Competition Score, Margin Score,
  Composite Score, Cash Flow Score, Profit Score, Balanced Score,
  Opportunity Lane, Commercial Priority, Monthly Gross Profit,
  Price Compression, Lane Reason, Verdict, Verdict Reason

### Shortlist (YES + MAYBE + BRAND APPROACH + BUY THE DIP + GATED only)
Path: ./data/{niche}\{niche}_phase3_shortlist.csv

### Shortlist JSON (for easy reading and future processing)
Path: ./data/{niche}\{niche}_phase3_shortlist.json

### Verdict breakdown summary
Path: ./data/{niche}\{niche}_phase3_stats.txt

Content:
  Niche: {niche}
  Date: {today}
  Input: {count} products from Phase 2
  Composite score threshold used: {value}

  Verdict breakdown:
    YES:            {count}
    MAYBE:          {count}
    BRAND APPROACH: {count}
    BUY THE DIP:    {count}
    GATED:          {count}
    PRICE EROSION:  {count}
    NO:             {count}
    HAZMAT:         {count}

  Lane breakdown:
    BALANCED:      {count}
    PROFIT:        {count}
    CASH FLOW:     {count}
    Unclassified:  {count}

  Price Compression:
    OK:            {count}
    SQUEEZED:      {count}
    COMPRESSED:    {count}

  Shortlist total: {count}
  Top 5 by composite score:
    1. {ASIN} - {title} - {score} - {verdict}
    2. ...

---

## Step 6A -- Update Exclusion List

Append rejected products to the exclusion list so they are skipped on
future reruns:

  node skills/shared\manage_exclusions.js --add --niche {niche} --csv data/{niche}\{niche}_phase3_scored.csv --verdicts "NO,PRICE EROSION" --phase "Phase 3"

This ensures NO and PRICE EROSION products from this run are excluded
from future Phase 1 exports for the same niche.

### Handoff file
Path: ./data/{niche}\{niche}_phase3_handoff.md

Content:
  # Phase 3 Handoff -- {niche}

  Status: COMPLETE
  Shortlist: {count} products
  Files:
    {niche}_phase3_scored.csv    -- all products with scores
    {niche}_phase3_shortlist.csv -- shortlist for supplier research
    {niche}_phase3_shortlist.json

  ## Priority actions from shortlist
  YES products ({count}): ready for supplier research immediately
  BRAND APPROACH ({count}): contact brand direct -- weak Amazon presence
  BUY THE DIP ({count}): check availability at dip price
  GATED ({count}): apply for approval before supplier research

  ## Next step
  Run Skill 5 -- Find Suppliers
  Input: data/{niche}\{niche}_phase3_shortlist.csv
  Also read: SUPPLIERS.CSV in project root

---

## Quality Check

  [ ] phase3_scored.csv has composite scores for all products
  [ ] Shortlist is between 30 and 100 products
  [ ] No YES products with ROI below 20% (something went wrong if so)
  [ ] Stats, handoff, and JSON files saved
  [ ] Shortlist is sorted by commercial priority, verdict, monthly gross profit, then composite
  [ ] Verdict Reason populated for every product with specific data points
  [ ] Seller count trend used in competition scoring
  [ ] Brand 1P products scored down or rejected
  [ ] Lane scores calculated for all products
  [ ] Price Compression flag populated (OK/SQUEEZED/COMPRESSED)
  [ ] Commercial Priority assigned (1/2/3/9)
  [ ] Sort order: commercial priority -> verdict -> monthly gross profit -> composite
  [ ] NO and PRICE EROSION ASINs appended to exclusions.csv


