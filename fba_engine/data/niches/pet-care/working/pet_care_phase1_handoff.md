# Phase 1 Handoff -- pet-care

Status: COMPLETE
Products exported: 246 (245 data rows)
File: data\pet-care\pet_care_phase1_raw.csv

## Columns in this file
ASIN, Locale, Image, Title, Bought in past month, Buy Box: Current (GBP),
Amazon: Current (GBP), Buy Box: 90 days avg (GBP), Buy Box: 90 days drop %,
New Offer Count: Current, Sales Rank: 90 days avg, Sales Rank: 90 days drop %,
Sales Rank: Drops last 90 days, Sales Rank: Reference, Sales Rank: Display Group,
Sales Rank: Subcategory Sales Ranks, Sales Rank: Current,
90 days change % monthly sold, Return Rate, Reviews: Rating,
Reviews: Rating Count, Reviews: Rating Count - 90 days drop %,
Reviews: Review Count - Format Specific, Last Price Change,
Buy Box: Stock, Buy Box: 90 days OOS, Buy Box: Buy Box Seller,
Buy Box: Shipping Country, Buy Box: Strikethrough Price,
Buy Box: % Amazon 90 days, Buy Box: % Top Seller 90 days,
Buy Box: Winner Count 90 days, Buy Box: Standard Deviation 90 days,
Buy Box: Flipability 90 days, Buy Box: Is FBA, Buy Box: Unqualified,
Buy Box: Prime Eligible, Buy Box: Subscribe & Save,
Amazon: 90 days avg, Amazon: 90 days drop %, Amazon: Stock,
Amazon: 90 days OOS, New: Current, New: 90 days avg, New: 90 days drop %,
New: 90 days OOS, New 3rd Party FBA: Current/avg/drop/Stock,
FBA Pick&Pack Fee, Referral Fee %, Referral Fee based on current Buy Box price,
New 3rd Party FBM: Current/avg/drop/Stock, Categories: Root/Sub/Tree,
Product Codes: UPC/EAN/GTIN/PartNumber, Parent ASIN,
Manufacturer, Brand, Brand Store Name, Variation Attributes,
Color, Size, Unit Details, Scent, Item Form, Pattern, Style, Material,
Item Type, Target Audience, Batteries Required/Included,
Is HazMat, Is heat sensitive, Adult Product, Is Merch on Demand,
Deal Type, Coupons, Business Discount, and many more (160+ columns total)

## NOT in this file (added by later skills)
FBA fee estimate (detailed)      -- Skill 2
ROI %                            -- Skill 2 + Skill 3
Gating status                    -- Skill 2
Price flag (chart shape)         -- Skill 3 (shortlist only)
Supplier / trade price           -- Skill 4

## Filter adjustments from niche config
- BSR range widened from 10000-60000 to 1-100000 (UK Pet Supplies is smaller)
- Velocity lowered from 100 to 50 bought/month
- All other filters applied per config: FBA sellers 2-20, Buy Box £20-£70,
  HazMat excluded, heat sensitive excluded, physical products only

## Next step
Run Skill 2 -- SellerAmp Enrichment
Input: data\pet-care\pet_care_phase1_raw.csv
