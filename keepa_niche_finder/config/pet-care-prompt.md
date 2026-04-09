================================================================
PET CARE
================================================================
You are the lead agent for the Pet Care niche.

Read CLAUDE.md fully before doing anything.
Read config\niche-configs\pet-care.md for all filter values.
Category ID for Keepa: 340840031 (Pet Supplies, amazon.co.uk)

PHASE 1 -- Skill path: skills\skill-1-keepa-finder\SKILL.md
  Niche: pet-care
  Output: data\pet-care\pet_care_phase1_raw.csv
  Target: 1000 products
  When complete: confirm row count and save handoff before Phase 2.

PHASE 2 -- Skill path: skills\skill-2-selleramp\SKILL.md
  Input: data\pet-care\pet_care_phase1_raw.csv
  Output: data\pet-care\pet_care_phase2_enriched.csv
  When complete: confirm enrichment stats and save handoff before Phase 3.

PHASE 3 -- Skill path: skills\skill-3-scoring\SKILL.md
  Input: data\pet-care\pet_care_phase2_enriched.csv
  Output: data\pet-care\pet_care_phase3_shortlist.csv
  Target: 50-100 products on shortlist.
  When complete: confirm shortlist count and save handoff before Phase 4.

PHASE 4 -- Skill path: skills\skill-4-ip-risk\SKILL.md
  Input: data\pet-care\pet_care_phase3_shortlist.csv
  Output: data\pet-care\pet_care_phase4_ip_risk.csv
  When complete: confirm IP risk output saved.

PHASE 5 -- Skill path: skills\skill-5-build-output\SKILL.md
  Input: data\pet-care\pet_care_phase4_ip_risk.csv
  Output: data\pet-care\pet_care_final_results.xlsx
  When complete: confirm final workbook saved.

PHASE 6 -- Skill path: skills\skill-6-decision-engine\SKILL.md
  Input: data\pet-care\pet_care_final_results.csv
  Output: data\pet-care\pet_care_phase6_shortlist.xlsx
  When complete: confirm decision counts and shortlist saved.

Do not proceed to the next phase until the current one is complete
and its handoff file has been saved.
