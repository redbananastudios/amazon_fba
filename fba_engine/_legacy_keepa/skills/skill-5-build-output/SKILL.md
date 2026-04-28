---
name: skill-5-build-output
description: >
  FBA sourcing Phase 5 build-only step. Use after Phase 4 IP risk analysis
  or directly after Phase 3 if Phase 4 was skipped. Triggers on:
  "run phase 5", "build final output", "create xlsx", "build xlsx",
  "generate report". Builds the 64-column final_results.csv, styled XLSX,
  phase5 supplier placeholder CSV, stats, and handoff files.
---

# Skill 5 -- Build Final Output (Phase 5)

Build the final output files from the scored shortlist, preferring Phase 4
IP-risk output when it exists. This is a build-only step. It does not do
supplier research, web scraping, outreach drafting, or trade price lookups.

After Phase 5 completes, Phase 6 Decision Engine can be run on the generated
`{niche}_final_results.csv` to add BUY / NEGOTIATE / WATCH / KILL decisions
and create a separate shortlist workbook.

**Supplier research is parked in Skill 99 and is not implemented.**

---

## Before You Start

1. Preferred input:
   `./data/{niche}\working/{niche}_phase4_ip_risk.csv`
2. Fallback input:
   `./data/{niche}\working/{niche}_phase3_shortlist.csv`
3. Confirm the shared build tools exist:
   - `skills/skill-5-build-output/build_final_xlsx.js`
   - `skills/skill-5-build-output/push_to_gsheets.js`

---

## Step 1 -- Build final_results.csv

Run the niche build script in `data/{niche}\working/phase5_build.js`.

The script:
  - prefers `{niche}_phase4_ip_risk.csv`
  - falls back to `{niche}_phase3_shortlist.csv`
  - writes `{niche}_final_results.csv`
  - writes `{niche}_phase5_suppliers.csv` as a placeholder-only supplier file
  - writes `{niche}_phase5_rejected_private_label.csv` for confirmed private-label rows
  - writes `{niche}_phase5_stats.txt`
  - writes `{niche}_phase5_handoff.md`

The final CSV schema is `64` columns.

Columns added by Phase 4 and carried into Phase 5:
  `Brand Seller Match`
  `Fortress Listing`
  `Brand Type`
  `A+ Content Present`
  `Brand Store Present`
  `Category Risk Level`
  `IP Risk Score`
  `IP Risk Band`
  `IP Reason`

Supplier columns remain placeholders only:
  `Route Code`, `Supplier Name`, `Supplier Website`, `Supplier Contact`,
  `MOQ`, `Trade Price Found`, `Trade Price`, `Real ROI %`,
  `Supplier Notes`, `Outreach Email File`

Confirmed private-label rows are excluded from the final CSV and sent to the
Phase 5 reject audit CSV instead. Only uncertain or viable resale candidates
stay in the main final output.

---

## Step 2 -- Build Styled XLSX

Run the XLSX builder:
  node skills/skill-5-build-output/build_final_xlsx.js --niche {niche}

The builder reads `{niche}_final_results.csv`, produces
`{niche}_final_results.xlsx`, and applies workbook styling for the full
64-column schema, including IP Risk Band colouring.

---

## Step 3 -- Upload to Google Sheets

Optional:

  node skills/skill-5-build-output/push_to_gsheets.js --niche {niche}

This uploads the XLSX and writes `{niche}_gsheet_id.txt`.

---

## Step 4 -- Review outputs

Expected build outputs in `data/{niche}\working/`:
  `{niche}_phase5_suppliers.csv`
  `{niche}_phase5_rejected_private_label.csv`
  `{niche}_phase5_stats.txt`
  `{niche}_phase5_handoff.md`

Expected final deliverables in `data/{niche}\`:
  `{niche}_final_results.csv`
  `{niche}_final_results.xlsx`
  `{niche}_gsheet_id.txt` if Google Sheets push ran successfully

---

## Quality Check

  [ ] final_results.csv exists with all 64 columns
  [ ] final_results.xlsx built successfully
  [ ] Phase 4 IP risk columns are present in the final CSV
  [ ] confirmed private-label rows are excluded to phase5_rejected_private_label.csv
  [ ] phase5_suppliers.csv exists and contains placeholder-only supplier values
  [ ] phase5_stats.txt and phase5_handoff.md are saved
  [ ] XLSX builder and Google Sheets push use the `skill-5-build-output` paths
