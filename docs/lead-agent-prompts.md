# FBA LEAD AGENT PROMPTS
# One prompt per niche. Paste into Claude Code terminal.
# cd to keepa_niche_finder project root && claude && /model opus

================================================================
AFRO HAIR PRODUCTS
================================================================
You are the lead agent for the Afro Hair Products niche.

Read CLAUDE.md fully before doing anything.
Read config\niche-configs\afro-hair.md for all filter values.

Run the four skills in sequence:

PHASE 1 -- Skill path: skills/skill-1-keepa-finder/SKILL.md
  Niche: afro-hair
  Output target: data\afro-hair\afro_hair_phase1_raw.csv
  When complete: confirm row count and save handoff before Phase 2.

PHASE 2 -- Skill path: skills/skill-2-selleramp/SKILL.md
  Input: data\afro-hair\afro_hair_phase1_raw.csv
  Output: data\afro-hair\afro_hair_phase2_enriched.csv
  When complete: confirm enrichment stats and save handoff before Phase 3.

PHASE 3 -- Skill path: skills/skill-3-scoring/SKILL.md
  Input: data\afro-hair\afro_hair_phase2_enriched.csv
  Output: data\afro-hair\afro_hair_phase3_shortlist.csv
  Target: 50-100 products on shortlist.
  When complete: confirm shortlist count and save handoff before Phase 4.

PHASE 4 -- Skill path: skills/skill-4-ip-risk/SKILL.md
  Input: data\afro-hair\afro_hair_phase3_shortlist.csv
  Output: data\afro-hair\afro_hair_phase4_ip_risk.csv
  When complete: confirm IP risk output saved.

PHASE 5 -- Skill path: skills/skill-5-build-output/SKILL.md
  Input: data\afro-hair\afro_hair_phase4_ip_risk.csv
  Output: data\afro-hair\afro_hair_final_results.xlsx
  When complete: confirm final workbook saved.

PHASE 6 -- Skill path: skills/skill-6-decision-engine/SKILL.md
  Input: data\afro-hair\afro_hair_final_results.csv
  Output: data\afro-hair\afro_hair_phase6_shortlist.xlsx
  When complete: confirm decision counts and shortlist saved.

Do not proceed to the next phase until the current one is complete
and its handoff file has been saved.


================================================================
KIDS TOYS
================================================================
You are the lead agent for the Kids Toys niche.

Read CLAUDE.md fully before doing anything.
Read config\niche-configs\kids-toys.md for all filter values.

Note: If data/kids-toys/kids_toys_phase1_raw.csv already exists,
confirm row count. If 500+ rows present, skip Phase 1 and start Phase 2.

Run the four skills in sequence:

PHASE 1 -- Skill path: skills/skill-1-keepa-finder/SKILL.md
  Niche: kids-toys
  Output: data/kids-toys/kids_toys_phase1_raw.csv

PHASE 2 -- Skill path: skills/skill-2-selleramp/SKILL.md
  Input: data/kids-toys/kids_toys_phase1_raw.csv
  Output: data/kids-toys/kids_toys_phase2_enriched.csv

PHASE 3 -- Skill path: skills/skill-3-scoring/SKILL.md
  Input: data/kids-toys/kids_toys_phase2_enriched.csv
  Output: data/kids-toys/kids_toys_phase3_shortlist.csv

PHASE 4 -- Skill path: skills/skill-4-ip-risk/SKILL.md
  Input: data/kids-toys/kids_toys_phase3_shortlist.csv
  Output: data/kids-toys/kids_toys_phase4_ip_risk.csv

PHASE 5 -- Skill path: skills/skill-5-build-output/SKILL.md
  Input: data/kids-toys/kids_toys_phase4_ip_risk.csv
  Output: data/kids-toys/kids_toys_final_results.xlsx

PHASE 6 -- Skill path: skills/skill-6-decision-engine/SKILL.md
  Input: data/kids-toys/kids_toys_final_results.csv
  Output: data/kids-toys/kids_toys_phase6_shortlist.xlsx

Save handoff after each phase before proceeding.


================================================================
EDUCATIONAL TOYS
================================================================
You are the lead agent for the Educational Toys niche.

Read CLAUDE.md fully before doing anything.
Read config\niche-configs\educational-toys.md for all filter values.

PHASE 1 -- Skill path: skills/skill-1-keepa-finder/SKILL.md
  Niche: educational-toys
  Output: data/educational-toys/educational_toys_phase1_raw.csv

PHASE 2 -- Skill path: skills/skill-2-selleramp/SKILL.md
  Input: data/educational-toys/educational_toys_phase1_raw.csv
  Output: data/educational-toys/educational_toys_phase2_enriched.csv

PHASE 3 -- Skill path: skills/skill-3-scoring/SKILL.md
  Input: data/educational-toys/educational_toys_phase2_enriched.csv
  Output: data/educational-toys/educational_toys_phase3_shortlist.csv

PHASE 4 -- Skill path: skills/skill-4-ip-risk/SKILL.md
  Input: data/educational-toys/educational_toys_phase3_shortlist.csv
  Output: data/educational-toys/educational_toys_phase4_ip_risk.csv

PHASE 5 -- Skill path: skills/skill-5-build-output/SKILL.md
  Input: data/educational-toys/educational_toys_phase4_ip_risk.csv
  Output: data/educational-toys/educational_toys_final_results.xlsx

PHASE 6 -- Skill path: skills/skill-6-decision-engine/SKILL.md
  Input: data/educational-toys/educational_toys_final_results.csv
  Output: data/educational-toys/educational_toys_phase6_shortlist.xlsx

Save handoff after each phase before proceeding.


================================================================
STATIONERY
================================================================
You are the lead agent for the Stationery niche.

Read CLAUDE.md fully before doing anything.
Read config\niche-configs\stationery.md for all filter values.

PHASE 1 -- Skill path: skills/skill-1-keepa-finder/SKILL.md
  Niche: stationery
  Output: data/stationery/stationery_phase1_raw.csv

PHASE 2 -- Skill path: skills/skill-2-selleramp/SKILL.md
  Input: data/stationery/stationery_phase1_raw.csv
  Output: data/stationery/stationery_phase2_enriched.csv

PHASE 3 -- Skill path: skills/skill-3-scoring/SKILL.md
  Input: data/stationery/stationery_phase2_enriched.csv
  Output: data/stationery/stationery_phase3_shortlist.csv

PHASE 4 -- Skill path: skills/skill-4-ip-risk/SKILL.md
  Input: data/stationery/stationery_phase3_shortlist.csv
  Output: data/stationery/stationery_phase4_ip_risk.csv

PHASE 5 -- Skill path: skills/skill-5-build-output/SKILL.md
  Input: data/stationery/stationery_phase4_ip_risk.csv
  Output: data/stationery/stationery_final_results.xlsx

PHASE 6 -- Skill path: skills/skill-6-decision-engine/SKILL.md
  Input: data/stationery/stationery_final_results.csv
  Output: data/stationery/stationery_phase6_shortlist.xlsx

Save handoff after each phase before proceeding.


================================================================
SPORTS GOODS (TENNIS AND BADMINTON ONLY)
================================================================
You are the lead agent for the Sports Goods niche.
SCOPE: Tennis and Badminton products ONLY. Reject all other sports.

Read CLAUDE.md fully before doing anything.
Read config\niche-configs\sports-goods.md for all filter values.
Pay special attention to the seasonal risk notes in that file.

PHASE 1 -- Skill path: skills/skill-1-keepa-finder/SKILL.md
  Niche: sports-goods
  Output: data/sports-goods/sports_goods_phase1_raw.csv

PHASE 2 -- Skill path: skills/skill-2-selleramp/SKILL.md
  Input: data/sports-goods/sports_goods_phase1_raw.csv
  Output: data/sports-goods/sports_goods_phase2_enriched.csv

PHASE 3 -- Skill path: skills/skill-3-scoring/SKILL.md
  Input: data/sports-goods/sports_goods_phase2_enriched.csv
  Output: data/sports-goods/sports_goods_phase3_shortlist.csv
  Note: Apply seasonal flag to products with summer-only BSR patterns.

PHASE 4 -- Skill path: skills/skill-4-ip-risk/SKILL.md
  Input: data/sports-goods/sports_goods_phase3_shortlist.csv
  Output: data/sports-goods/sports_goods_phase4_ip_risk.csv

PHASE 5 -- Skill path: skills/skill-5-build-output/SKILL.md
  Input: data/sports-goods/sports_goods_phase4_ip_risk.csv
  Output: data/sports-goods/sports_goods_final_results.xlsx

PHASE 6 -- Skill path: skills/skill-6-decision-engine/SKILL.md
  Input: data/sports-goods/sports_goods_final_results.csv
  Output: data/sports-goods/sports_goods_phase6_shortlist.xlsx

Save handoff after each phase before proceeding.


================================================================
PET CARE
================================================================
You are the lead agent for the Pet Care niche.

Read CLAUDE.md fully before doing anything.
Read config\niche-configs\pet-care.md for all filter values.
Category ID for Keepa: 340840031 (Pet Supplies, amazon.co.uk)

PHASE 1 -- Skill path: skills/skill-1-keepa-finder/SKILL.md
  Niche: pet-care
  PRIMARY: Run the Playwright script:
    node skills/skill-1-keepa-finder/scripts\keepa_finder.js --niche pet-care
  FALLBACK: If script unavailable, follow SKILL.md manually in browser.
  Output: data/pet-care/pet_care_phase1_raw.csv
  Target: 1000 products
  When complete: confirm row count and save handoff before Phase 2.

PHASE 2 -- Skill path: skills/skill-2-selleramp/SKILL.md
  Input: data/pet-care/pet_care_phase1_raw.csv
  Output: data/pet-care/pet_care_phase2_enriched.csv
  When complete: confirm enrichment stats and save handoff before Phase 3.

PHASE 3 -- Skill path: skills/skill-3-scoring/SKILL.md
  Input: data/pet-care/pet_care_phase2_enriched.csv
  Output: data/pet-care/pet_care_phase3_shortlist.csv
  Target: 50-100 products on shortlist.
  When complete: confirm shortlist count and save handoff before Phase 4.

PHASE 4 -- Skill path: skills/skill-4-ip-risk/SKILL.md
  Input: data/pet-care/pet_care_phase3_shortlist.csv
  Output: data/pet-care/pet_care_phase4_ip_risk.csv
  When complete: confirm IP risk output saved.

PHASE 5 -- Skill path: skills/skill-5-build-output/SKILL.md
  Input: data/pet-care/pet_care_phase4_ip_risk.csv
  Output: data/pet-care/pet_care_final_results.xlsx
  When complete: confirm final workbook saved.

PHASE 6 -- Skill path: skills/skill-6-decision-engine/SKILL.md
  Input: data/pet-care/pet_care_final_results.csv
  Output: data/pet-care/pet_care_phase6_shortlist.xlsx
  When complete: confirm decision counts and shortlist saved.

Do not proceed to the next phase until the current one is complete
and its handoff file has been saved.
