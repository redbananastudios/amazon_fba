# FBA Sourcing Workflow - Improvement Plan

Date: 2026-03-20
Based on: Pet Care niche run (complete pipeline)

---

## Problems Found During the Run

### P1 - Phase 1: Keepa Product Finder was brittle
- The old Skill 1 relied on manual form-filling in the browser which broke repeatedly (screenshot rendering issues, input values not sticking, dropdown selection failures).
- The new `keepa-product-finder` skill uses a URL-first approach with encoded JSON - much more reliable.
- **Action:** Replace old Skill 1 SKILL.md with a wrapper that calls the new keepa-product-finder skill.

### P2 - Phase 4 final file dropped key metrics
- The Phase 4 suppliers file stripped out: Amazon URL, BSR, seller count, Amazon on listing, Buy Box share, price stability, breakeven, max cost, scoring breakdown, verdict reason.
- Users need all of this in one file to make sourcing decisions.
- **Action:** Update Skill 4 output spec to produce a comprehensive merged file.

### P3 - ROI estimation misleads on low-price items
- The 65% cost assumption produces negative ROI on items under ~GBP25 where FBA fees are proportionally high.
- Users see "negative ROI" and think the product is bad, when it may be profitable at real trade prices.
- **Action:** Add a "Fee Impact" warning column and clarify ROI is estimated. Add "Min Viable Cost" column showing the cost needed for breakeven.

### P4 - No consolidated "open in browser" links
- The Phase 3 shortlist had Amazon URLs but Phase 4 dropped them.
- **Action:** Always carry Amazon URL through to final file.

### P5 - SellerAmp enrichment was slow and manual
- Each ASIN lookup took 5-10 seconds in the browser.
- No batch processing capability.
- **Action:** Document the batch approach better. When SP-API is approved, replace browser lookups entirely.

### P6 - Supplier research was desk research, not live lookups
- Phase 4 didn't actually log into supplier sites or check real trade prices.
- Route codes were mostly UNKNOWN.
- **Action:** Make Skill 4 explicitly split into "desk research" (current) and "live account check" (future with supplier logins). Don't pretend to have data we don't.

### P7 - No final results file spec
- The pipeline produced 4 separate phase files but no single "here's your answer" file.
- **Action:** Add a new Skill 5 or modify Skill 4 to produce `{niche}_final_results.csv` as the merged deliverable.

---

## Skill Updates Required

### Update 1: Skill 1 - Reference new keepa-product-finder skill
- Skill 1 SKILL.md should reference the new `keepa-product-finder` skill as primary method.
- Keep the manual fallback instructions but mark them as FALLBACK.
- Add Pet Supplies category ID (7493411) to the reference file.

### Update 2: Skill 4 - Richer output file
- Change the output spec to produce `{niche}_final_results.csv` with ALL useful columns merged from Phase 3 + Phase 4.
- New column order (40 columns, grouped logically):
  1. **Identity** (5): ASIN, Product Name, Brand, Amazon URL, Category
  2. **Verdict & Scores** (7): Verdict, Verdict Reason, Composite, Demand, Stability, Competition, Margin
  3. **Pricing** (9): Current Price, Buy Box 90-day avg, Price Stability, FBA Fee, Est Cost, Est Profit, Est ROI %, Max Cost for 30% ROI, Breakeven Price
  4. **Demand** (3): BSR Current, BSR Drops 90d, Bought in past month
  5. **Competition** (4): FBA Seller Count, Amazon on Listing, Amazon Buy Box Share, Private Label Risk
  6. **Gating** (2): Gated, SellerAmp Flags
  7. **Supplier** (10): Route Code, Supplier Name, Website, Contact, MOQ, Trade Price Found, Trade Price, Real ROI %, Notes, Outreach Email

### Update 3: Skill 3 - Add Price Stability label
- Currently outputs raw "Price drop % 90-day" as a number.
- Add a human-readable label: STABLE / SLIGHT DIP / DROPPING / RISING / SURGING.

### Update 4: keepa-product-finder reference file
- Add Pet Supplies category ID: 7493411
- Add verified Pet Care subcategory IDs when discovered.

### Update 5: CLAUDE.md - Add final file to data folder structure
- Add `{niche}_final_results.csv` to the data folder listing.
- Note it as the primary deliverable.

---

## Priority Order

1. **Update Skill 4** output spec (biggest user impact) ... DONE via rebuild_final.js
2. **Update CLAUDE.md** data folder structure
3. **Update Skill 1** to reference keepa-product-finder
4. **Update keepa-product-finder** reference file with Pet Supplies ID
5. **Update Skill 3** to add Price Stability label
6. **Future:** SP-API integration for Phase 2 (when approved)
