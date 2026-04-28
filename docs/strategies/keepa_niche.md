# Strategy: `keepa_niche`

**Type:** Amazon-listing-first niche discovery
**Status:** Implementation in transition (Node.js → Python during step 4)
**Implementation today:** `fba_engine/_legacy_keepa/` (temporary location)

---

## What this strategy does

For a defined niche (e.g. kids-toys, pet-care), discover Amazon ASINs that
meet velocity, BSR, seller count, and category criteria. Score them. Apply
IP-risk analysis. Produce a shortlist of products worth sourcing — without
yet knowing whether you can source them.

This is the inverse of `supplier_pricelist`: instead of starting from "what
do I have access to?", it starts from "what's worth selling?". Sourcing is
the problem solved separately (eventually by Skill 99 / find-suppliers).

---

## When to use it

- You're researching a category to enter
- You want to know what high-velocity products exist before committing to a supplier base
- You're hunting for brand-direct opportunities (small brands with weak Amazon listings)

For supplier-already-in-hand sourcing, use `supplier_pricelist`.

---

## Phases (legacy structure, until step 4)

The legacy implementation runs as 6 sequential phases:

1. **Phase 1** — Keepa Product Finder discovery via URL-encoded JSON. Outputs raw CSV.
2. **Phase 2** — SellerAmp enrichment (browser-based) for fees, ROI, gating, hazmat.
3. **Phase 3** — Scoring across demand, stability, competition, margin dimensions.
4. **Phase 4** — IP risk analysis (advisory; doesn't filter).
5. **Phase 5** — Build final XLSX deliverable with 64 columns.
6. **Phase 6** — Decision engine: BUY / NEGOTIATE / WATCH / KILL.

In step 4 of the reorganisation, these phases are extracted into reusable
steps under `fba_engine/steps/` so this strategy and `supplier_pricelist`
share the same building blocks.

---

## Inputs

- **Niche config** in `shared/niches/<niche>.yaml` (Keepa filters, exclusion
  keywords, focus brands, supplier directory hints)
- **Keepa account** for the URL-based Product Finder export
- **SellerAmp credentials** for Phase 2 enrichment

---

## Run (legacy)

Today the strategy is invoked phase by phase via `claude` (Claude Code) using
the SKILL.md files in `fba_engine/_legacy_keepa/skills/`. Step 4 will replace
this with a single composed run.

---

## Outputs

In `fba_engine/data/niches/<niche>/`:

- `working/` — intermediate CSVs per phase (audit trail)
- `<niche>_final_results.xlsx` — Phase 5 deliverable
- `<niche>_phase6_shortlist.xlsx` — BUY + NEGOTIATE rows only (Phase 6)

---

## Configured niches (today)

- `afro-hair`, `educational-toys`, `kids-toys`, `pet-care`, `sports-goods`, `stationery`

Each in `shared/niches/<niche>.yaml`. To add a new niche, add a YAML following
the schema of one of the existing files.

---

## Limitations

- Phases 1-2 are browser-driven (Keepa Product Finder UI scraping, SellerAmp
  page scraping). Brittle and slow. Step 4 replaces with API/SP-API calls
  where possible.
- No automated sourcing step. Phase 5's "supplier placeholder" CSV requires
  manual sourcing research. Skill 99 (step 6) closes this loop.
- Implementation language is Node.js for legacy reasons; mid-pipeline
  Python is awkward. Step 4 ports to Python end-to-end.

---

## What changes after step 4

- Phases become composable steps shared with `supplier_pricelist`
- Single `python run.py --strategy keepa_niche --niche kids-toys` invocation
- Single language (Python)
- Same output format, byte-identical to legacy where possible
