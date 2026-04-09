# Phase 1 Handoff -- educational-toys

Status: COMPLETE
Products exported: 699
File: data\educational-toys\educational_toys_phase1_filtered.csv

## Filters Applied
- BSR: 5000 - 200000 (widened from config 10000-80000)
- BSR 90d avg: <= 250000
- BSR drops 90d: >= 3
- Buy Box: GBP 20 - 70
- Buy Box 90d avg: >= 18
- FBA sellers: 2 - 20
- FBA sellers 90d avg: <= 20
- Velocity (bought/month): >= 50 (lowered from config 100)
- Root category: Toys & Games
- isHazMat: No
- batteriesRequired: No
- batteriesIncluded: No
- Product type: Physical only
- Single variation: Yes

## Columns in this file
ASIN, Title, Bought in past month, Buy Box current (GBP),
Buy Box 90-day avg (GBP), Buy Box 90-day drop %,
Amazon on listing (flag), New FBA Offer Count current,
New FBA Offer Count 90-day avg, BSR current,
BSR drops last 90 days, Category, Subcategory,
Package Weight (g), Package Dimension (cm3), Weight Flag,
Reviews Rating, Reviews Count, Has A+ Content,
and 140+ additional Keepa columns

## NOT in this file (added by later skills)
Buy Box % split (Amazon vs 3P)  -- Skill 2
FBA fee                          -- Skill 2
ROI %                            -- Skill 2 + Skill 3
Gating status                    -- Skill 2
Listing Quality                  -- Skill 2
Brand 1P                         -- Skill 2
Price flag (chart shape)         -- Skill 3 (shortlist only)
Supplier / trade price           -- Skill 4

## Next step
Run Skill 2 -- SellerAmp Enrichment
Input: data\educational-toys\educational_toys_phase1_filtered.csv
