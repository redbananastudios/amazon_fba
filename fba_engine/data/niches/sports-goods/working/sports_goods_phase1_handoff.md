# Phase 1 Handoff -- sports-goods

Status: COMPLETE
Products exported: 853
File: data\sports-goods\sports_goods_phase1_filtered.csv

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
Input: data\sports-goods\sports_goods_phase1_filtered.csv
