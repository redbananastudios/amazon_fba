# Phase 3 Handoff -- educational-toys

Status: COMPLETE
Input: educational_toys_phase2_enriched.csv (539 products)
Output: educational_toys_phase3_shortlist.csv (88 products)

## Scoring dimensions
- Demand (30%): BSR, BSR drops, bought/month, rating, reviews
- Stability (30%): 90d price drop %, recovery detection, price check flag
- Competition (20%): FBA seller count (dynamic ceiling), Amazon BB %, seller trend, Brand 1P
- Margin (20%): Est ROI %, est profit, weight flag

## Verdict breakdown
- YES: 87 (composite >= 8.5)
- MAYBE: 287 (composite 7.0-8.4)
- MAYBE-ROI: 21 (ROI below 30%, composite 5-6.9)
- BRAND APPROACH: 1 (low sellers + weak listing)
- BUY THE DIP: 0
- GATED: 0
- PRICE EROSION: 0 (removed in Phase 2)
- NO: 143

## Shortlist composition
88 products: 87 YES + 1 BRAND APPROACH
Sorted by verdict priority, then composite score descending

## Key observations
- Pre-filter in Phase 2 removed worst products (137 oversaturated, 23 erosion)
- BSR was widened to 5000-200000 in Phase 1 -- scoring naturally ranks tighter BSR higher
- Many products score well on stability (price erosion already filtered)
- YES threshold set at 8.5 composite to keep shortlist at target 50-100

## Files
- educational_toys_phase3_scored.csv (all 539 scored)
- educational_toys_phase3_shortlist.csv (88 shortlisted)
- educational_toys_phase3_shortlist.json
- educational_toys_phase3_stats.txt

## Next step
Run Skill 4 -- Supplier Research
Input: data\educational-toys\educational_toys_phase3_shortlist.csv
