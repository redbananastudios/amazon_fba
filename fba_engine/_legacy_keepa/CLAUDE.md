================================================================
FBA SOURCING SYSTEM -- PROJECT CONTEXT
================================================================

## Credentials

All credentials are centralized at: F:\My Drive\workspace\credentials.env
Run sync script after changes: powershell "F:\My Drive\workspace\sync-credentials.ps1"

## PATHS

All paths are relative to this project's root directory (the folder containing this CLAUDE.md).

Project root:     ./
Data output:      ./data/{niche}/
Skills:           ./skills/
Niche configs:    ./config/niche-configs/
Suppliers file:   ./SUPPLIERS.CSV
Exclusions file:  ./data/exclusions.csv

## SKILLS

Skill 1 -- Keepa Product Finder (Phase 1)
  Path: skills/skill-1-keepa-finder/SKILL.md
  Input: niche name + niche config file
  Output: {niche}_phase1_raw.csv (full Keepa export) -> {niche}_phase1_filtered.csv (after exclusions)
  Tool: keepa-product-finder skill (URL-first, primary) / browser fallback
  Skill: skills/keepa-product-finder/SKILL.md (URL-encoded JSON approach)
  Refs:  skills/keepa-product-finder/references/keepa-finder-values.md
  Note:   Uses URL-first approach -- builds encoded finder JSON URL.
          Falls back to manual browser form if URL method fails.
          Reads Keepa credentials from this CLAUDE.md file.

Skill 2 -- SellerAmp Enrichment (Phase 2)
  Path: skills/skill-2-selleramp/SKILL.md
  Input: {niche}_phase1_filtered.csv (fallback: {niche}_phase1_raw.csv if no filtered file exists)
  Output: {niche}_phase2_enriched.csv
  Tool: Browser (sas.selleramp.com)

Skill 3 -- Scoring and Shortlist (Phase 3)
  Path: skills/skill-3-scoring/SKILL.md
  Input: {niche}_phase2_enriched.csv
  Output: {niche}_phase3_shortlist.csv (50-100 products)
  Tool: Logic only, no browser

Skill 4 -- Listing & IP Risk Analysis (Phase 4)
  Path: skills/skill-4-ip-risk/SKILL.md
  Input: {niche}_phase3_shortlist.csv (scored shortlist from Phase 3)
  Output: {niche}_phase4_ip_risk.csv (shortlist + IP risk columns)
  Tool: Logic only, no browser
  Triggers: "run phase 4", "ip risk", "listing risk", "ip analysis"
  Note: Analyses brand control, seller structure, and listing signals
        to flag IP complaint risk. Adds IP Risk Score (0-10),
        IP Risk Band (Low/Medium/High), and supporting fields.
        Does NOT override commercial scoring or auto-kill products.
        Run AFTER Phase 3, BEFORE Phase 5 (build).

Skill 5 -- Build Final Output (Phase 5) [BUILD ONLY -- no supplier research]
  Path: skills/skill-5-build-output/SKILL.md
  Input: {niche}_phase4_ip_risk.csv (or phase3_shortlist.csv if Phase 4 not run)
  Output: {niche}_final_results.xlsx (merged deliverable)
  Tool: Logic + Node.js
  Build: node skills/skill-5-build-output/build_final_xlsx.js --niche {niche}
  Triggers: "run phase 5", "build final output", "create xlsx", "build xlsx"
  Note: Merges Phase 3 shortlist + Phase 4 IP risk data into the final
        styled Excel workbook. No supplier research. Handles cleanup
        (move working files) and Google Sheets upload.
  GSheets: node skills/skill-5-build-output/push_to_gsheets.js --niche {niche}

Skill 6 -- Decision Engine (Phase 6)
  Path: skills/skill-6-decision-engine/SKILL.md
  Input: {niche}_final_results.csv (fallback: working/{niche}_final_results.csv)
  Output: {niche}_phase6_decisions.csv + {niche}_phase6_shortlist.xlsx
  Tool: Logic + Node.js
  Build: node data/{niche}/working/phase6_decision.js
  Triggers: "run phase 6", "decision engine", "buy negotiate watch kill", "build shortlist"
  Note: Appends the final operator decision layer on top of the Phase 5
        output. Produces BUY / NEGOTIATE / WATCH / KILL decisions,
        decision scoring, buy-price targets, action notes, and a
        separate shortlist workbook.

Skill 99 -- Find Suppliers [NOT IMPLEMENTED]
  Path: skills/skill-99-find-suppliers/SKILL.md
  Input: {niche}_phase3_shortlist.csv + SUPPLIERS.CSV
  Output: {niche}_phase99_suppliers.csv + outreach emails
  Tool: Browser
  Triggers: "find suppliers", "research suppliers", "find trade prices", "source products"
  Note: All supplier research logic. Checks existing accounts first,
        then researches brand direct, distributors, trade platforms.
        Drafts outreach emails for BRAND APPROACH brands.
        NOT YET IMPLEMENTED -- parked as Skill 99.

Execution order:
  Phase 1 -> Phase 2 -> Phase 3 -> Phase 4 (IP Risk) -> Phase 5 (build) -> Phase 6 (decision)
  Skill 99 (suppliers) is optional and run on user request only.

Shared Utilities:
  Exclusions:     node skills/shared/manage_exclusions.js (--add | --filter | --stats)
  Google Sheets:  node skills/skill-5-build-output/push_to_gsheets.js --niche {niche}

## NICHES

  afro-hair        config\niche-configs\afro-hair.md
  kids-toys        config\niche-configs\kids-toys.md
  educational-toys config\niche-configs\educational-toys.md
  stationery       config\niche-configs\stationery.md
  sports-goods     config\niche-configs\sports-goods.md
  pet-care         config\niche-configs\pet-care.md

## DATA FOLDER STRUCTURE PER NICHE

  After pipeline completes, Skill 5 cleanup moves working files to working/.

  data/{niche}/
    {niche}_final_results.xlsx       FINAL DELIVERABLE -- open this first
    {niche}_phase6_shortlist.xlsx    Decision shortlist -- BUY + NEGOTIATE only
    {niche}_gsheet_id.txt            Google Sheet ID (for reruns)
    outreach/
      {brand_name}.txt              One email per BRAND APPROACH brand
    working/                        Audit trail -- all intermediate files
      {niche}_phase1_raw.csv        Skill 1 output -- full Keepa export
      {niche}_phase1_filtered.csv   Phase 1 after exclusion filter
      {niche}_phase1_stats.txt
      {niche}_phase1_handoff.md
      {niche}_phase2_enriched.csv   Skill 2 output -- enriched
      {niche}_phase2_gated.csv      Gated products (flagged not removed)
      {niche}_phase2_hazmat.csv     Hazmat confirmed (removed)
      {niche}_phase2_stats.txt
      {niche}_phase2_handoff.md
      {niche}_phase3_scored.csv     Skill 3 output -- all scored
      {niche}_phase3_shortlist.csv  Shortlist 50-100 products
      {niche}_phase3_shortlist.json
      {niche}_phase3_stats.txt
      {niche}_phase3_handoff.md
      {niche}_phase4_ip_risk.csv    Skill 4 output -- IP risk analysis
      {niche}_phase4_stats.txt
      {niche}_phase4_handoff.md
      {niche}_phase5_suppliers.csv  Skill 5 build output -- skeleton (generated by build)
      {niche}_phase5_rejected_private_label.csv  Confirmed private label rows removed from final file
      {niche}_phase5_stats.txt
      {niche}_phase5_handoff.md
      {niche}_phase6_decisions.csv  Skill 6 output -- final operator decisions
      {niche}_phase6_stats.txt
      {niche}_phase6_handoff.md
      *.js                          Build/scoring scripts
      sas_*                         SellerAmp raw data

  data/exclusions.csv                Global ASIN exclusion list (all niches)

## GLOBAL RULES

Price range:        GBP20 - GBP70
Min FBA sellers:    2 (single seller = private label risk)
Max FBA sellers:    20 (hard ceiling)
Velocity floor:     100/month (50 for sports-goods)
ROI floor:          20% target (flag below, never hard reject in scoring)
HazMat:             Excluded at Keepa filter level, confirmed by SellerAmp
Private label:      Min 2 sellers filters most out -- SellerAmp confirms
Amazon Buy Box:     Include in results, flag >70% in scoring
Gating:             Flag as GATED verdict, do not remove

## VERDICT VALUES

  YES            -- composite 8.5+, all filters pass, pursue
  MAYBE          -- composite 7-8.4, one concern, review needed
  MAYBE-ROI      -- ROI below 20% estimated, may improve with real price
  BRAND APPROACH -- 2-3 sellers, weak listing, contact brand direct
  BUY THE DIP    -- price 30%+ below 90-day avg, recovery pattern
  PRICE EROSION  -- consistent downward slope, reject
  GATED          -- restricted listing, flag for ungating decision
  HAZMAT         -- confirmed hazmat, excluded
  NO             -- fails filter, reason stated

## WHEN SP-API IS APPROVED

Add credentials here:
  SP_API_CLIENT_ID:
  SP_API_CLIENT_SECRET:
  SP_API_REFRESH_TOKEN:
  SP_API_SELLER_ID:
  SP_API_MARKETPLACE: A1F83G8C2ARO7P (amazon.co.uk)

Switch Skill 2 FBA fee source to getMyFeesEstimateForASIN
Switch Skill 2 gating source to /listings/2021-08-01/restrictions

## GOOGLE SHEETS

Service account key:  ./config/google-service-account.json
Shared folder ID:     1uFYiz7rYFm5ZJHgkXJh86jKaigK9H6yd
Push script:          node skills/skill-5-build-output/push_to_gsheets.js --niche {niche}

Setup (one-time):
  1. Google Cloud Console -- create project, enable Sheets API + Drive API
  2. Create service account -- download JSON key to config/google-service-account.json
  3. Create a Google Drive folder for results
  4. Share the folder with the service account email (client_email from JSON key)
  5. Paste the folder ID above

