# Amazon FBA Sourcing System

> **Step 3 update (2026-04-28):** Repo restructured. One engine, named
> strategies, ordered steps. The two former pipelines (`keepa_niche_finder/`,
> `supplier_pricelist_finder/`) no longer exist as separate top-level trees.
> See `docs/architecture.md`.

## Current State
**Last updated:** 2026-05-01 (Phase 3 complete — 4 PRs merged this session)
**Currently working on:** Phase 3 scoping items resolved. `oa_csv` now produces full SHORTLIST/REVIEW/REJECT verdicts; `seller_storefront` enriches with SP-API gating/eligibility/hazmat/brand (SellerAmp dropped); `keepa_niche` chain has its missing Phase 3 (canonical scoring step replacing the agent-driven per-niche scripts).
**Status:** Main is at PR #35 (commit `76fe945`). **820 Python tests + 110 MCP vitest = 930 total green.**

**Latest tests baseline:**
```bash
cd services/amazon-fba-fees-mcp && npm test                          # 110/110 unit
cd services/amazon-fba-fees-mcp && npm run test:integration          # 5/5 live SP-API
pytest shared/lib/python/ fba_engine/steps/tests/ \
       fba_engine/strategies/tests/ cli/tests/                       # 820/820 in ~11s
```

### What landed this session (2026-05-01, Phase 3)

**PR #32 — keepa_enrich foundation** (MERGED): the missing connector that lets ASIN-only sources chain into `calculate→decide`. `KeepaProduct.market_snapshot()` extracts canonical engine columns from Keepa stats indices (0=AMAZON, 3=SALES, 10=NEW_FBA, 11=COUNT_NEW, 18=BUY_BOX_SHIPPING). New `fba_engine/steps/keepa_enrich.py` joins per-ASIN market data via `KeepaClient.get_products()`. Both single + batch product paths now request `stats=90`. `_estimate_for` scales with N ASINs + stats overhead so the token bucket doesn't silently over-issue under heavy batch load. Pre-PR review caught 2 HIGH (product_name=None bug + seller_storefront chain clash), both fixed by dropping descriptive fields from canonical enrich schema. +29 tests.

**PR #33 — oa_csv full chain** (MERGED): promotes `oa_csv` from leads-only to full decision pipeline. New chain `discover → keepa_enrich → calculate → decide → output`. Two name-bridges: `monthly_sales_estimate → sales_estimate` in market_snapshot (canonical engine reads sales_estimate directly); `retail_cost_inc_vat → buy_cost` in oa_csv discovery output (per PRD §6.4). End-to-end smoke test pins that cheap rows SHORTLIST and expensive rows REJECT.

**PR #34 — SellerAmp drop / SP-API enrich leads mode** (MERGED): replaces the legacy SellerAmp skill with the existing SP-API MCP for non-Buy-Box-% checks. New `enrich.LEADS_INCLUDE = (restrictions, fba, catalog)` + `include: "leads"` YAML alias. `_row_to_item(allow_no_price=True)` lets ASIN-only rows preflight without a market_price. `seller_storefront.yaml` chain extended: discover → enrich (leads) → supplier_leads. Legacy `skill-2-selleramp/SKILL.md` marked deprecated. +8 tests.

**PR #35 — Skill 3 scoring extraction** (MERGED): canonical `fba_engine/steps/scoring.py` replaces the agent-driven per-niche `phase3_scoring.js` scripts. 4-dimension scoring (Demand/Stability/Competition/Margin) + 30/30/20/20 composite + 3 lane scores (Cash Flow/Profit/Balanced) + lane classification + 9-verdict ladder (YES/MAYBE/MAYBE-ROI/BRAND APPROACH/BUY THE DIP/PRICE EROSION/GATED/HAZMAT/NO). Pre-PR review caught 2 HIGH bugs by comparing against real `phase2_enriched.csv` + the legacy `phase3_scoring.js`: wrong column names (`Buy Box Drop % 90d` → `Price Drop % 90d`, `Buy Box Amazon Share` → `Buy Box Amazon %`) and margin-tier off-by-one (strict `>` not `>=`). `keepa_niche.yaml` chain now `scoring → ip_risk → build_output → decision_engine`. +64 tests.

### Prior session highlights (kept for context)

**Phase 2 (PRs #26-#30, MERGED, prior session):** canonical engine refactor (6 step modules); keepa_client batch + stale-on-error; seller_storefront discovery step; seller_storefront.yaml + oa_csv.yaml; run_summary.json + strategy docs.

**Phase 1 (PRs #20-#25, MERGED, prior session):** docs/PRD-sourcing-strategies.md (PR #20), keepa_client foundation (PR #21), supplier_leads step / Skill 99 v1 (PR #22), oa_csv discovery + SellerAmp 2DSorter importer (PR #23), CLI launch helpers (PR #24), sourcing_engine integration test as PR #7 safety net (PR #25).

**Step 4 + 5 (PRs #8-#18, MERGED, prior session):** ip_risk, decision_engine, build_output (3-part: merge/XLSX/GSheets), cross-cutting fixes, helpers extraction, YAML strategy runner with `keepa_niche.yaml`.

### Roadmap status (where we are)

**Phase 3 (post-PRD scoping items): COMPLETE** ✅ (PRs #32–#35 this session)

| Phase 3 deliverable | Status |
|---|---|
| `keepa_enrich` step (foundation for ASIN→market data) | ✅ #32 |
| `oa_csv` chains into `calculate → decide` (full verdicts) | ✅ #33 |
| Drop SellerAmp — SP-API MCP enrich leads mode | ✅ #34 |
| Skill 3 scoring → canonical step | ✅ #35 |
| **Skill 1 (Keepa Finder) — keep browser flow** | ⏸️ Deferred per user (2026-05-01) |

**Engine state:** structurally complete for the strategies in scope. Future PRs are polish, not missing functionality.

**Open low-priority polish (not blocking):**
- **Buy Box %** signal — SellerAmp's only unique field. Could be derived from Keepa `Buy Box: Is FBA` time series or via Keepa `buy_box_avg90` ratio. Not yet wired.
- **TA + OAXray oa_importers** stubbed out — add their parsers when those tools are needed.
- **Skill 1 (Keepa Finder)** — currently browser-driven; if/when API path becomes useful, we have the keepa_client + keepa_enrich foundations to build on.
- **Niche-specific scoring weights** — `shared/config/scoring/<niche>.yaml` overrides could be added when operators need per-niche tuning. Universal weights work today.
- Resumable upload progress logging dropped in PR #15 (`_status` discarded)
- Title clamp at 200 chars vs Sheets API's actual 100-char limit
- Strategy 2 silently falls through on auth failures (pre-existing, JS-faithful)
- Dead `last_err` defensive branch in `_retry_with_backoff`

### Workflow notes (cumulative across sessions)
- **Worktree gotcha:** `[[ -d .git ]]` checks fail in worktrees because `.git` is a file pointer. Use `[[ -e .git ]]`.
- **`gh pr merge` in worktrees:** Local cleanup fails because main is checked out at the parent worktree (`fatal: 'main' is already used by worktree at 'O:/fba'`). Use `gh pr merge <N> --merge --delete-branch --admin` — the merge succeeds on GitHub even when local cleanup fails. Verify via `gh pr view <N> --json state,mergedAt`.
- **Always fetch before branching:** After merging a PR, run `git fetch origin && git checkout -b <new-branch> origin/main` (NOT just `origin/main` from stale local cache). Branching off pre-merge state silently drops the merged work.
- **NaN-truthy trap (pandas):** `pd.DataFrame.from_records(list_of_dicts)` fills missing dict keys with NaN, which is **truthy** for floats. The naive `if row_dict.get("decision"):` short-circuits on rows that came through DataFrame construction even when decision is genuinely absent. Use `is_missing()` from `fba_engine/steps/_helpers.py` (catches None / NaN / pd.NA / pd.NaT).
- **TS imports:** This project uses ESM/nodenext — relative imports MUST end in `.js` even when source is `.ts` (e.g., `import {X} from "./foo.js"`). Otherwise `npm run build` fails (vitest is more lenient).
- **MCP test path:** Always run `npm` commands inside the actual worktree's `services/amazon-fba-fees-mcp/`, not `O:/fba/services/amazon-fba-fees-mcp/`. They're separate copies.
- **Credential sync:** After editing `F:\My Drive\workspace\credentials.env`, run `& 'F:\My Drive\workspace\sync-credentials.ps1'` (PowerShell — bash quoting breaks on the space in "My Drive"). Then verify with `grep '"SP_API_' "C:/Users/peter/.claude/settings.json"`.
- **MCP `.mcp.json` path** at repo root references the MCP at `services/amazon-fba-fees-mcp/dist/index.js` (corrected from old root-level path during cleanup).
- **SP-API endpoint group names** (amazon-sp-api lib): catalogItems, productFees, listingsRestrictions, fbaInboundEligibility, productPricing. Use `client.callAPI({ operation, endpoint, ... })`.
- **Disk cache layout:** `<repo>/.cache/fba-mcp/<resource>/<key-parts>__joined.json` — gitignored. `DiskCache.get()` returns `{ hit, stale, data }` enabling stale-on-error fallback.
- **Keepa cache layout:** `<keepa_cache_root>/<namespace>/<key>.json` per `shared/lib/python/keepa_client/cache.py`. Use `DiskCache.get_stale()` for stale-on-error fallback (introduced in PR #27).
- **Strategy YAML `input.discover: true`:** when first step creates the DataFrame from API/files, set this flag instead of `input.path`. Strict bool coercion at load time — quoted `"true"` / `"false"` get rejected.
- **Pandas strict-string dtype trap:** `out[col] = ""` initializes a string-only series, then assigning ints/floats raises `TypeError: Invalid value '10' for dtype 'str'`. Fix: `out[col] = pd.Series([None] * len(out), dtype=object, index=out.index)` before writing mixed-type values. Hit this in scoring.py — the column-init pattern needs object dtype if you'll write scores AND verdict strings to the same row.
- **SKILL.md ≠ shipped behaviour:** When porting a legacy skill, treat the `SKILL.md` as the spec but verify against the actually-shipped JS (`fba_engine/_legacy_keepa/scripts/*.js` or `data/{niche}/working/*.js`). The reviewer caught 2 HIGH bugs in scoring.py (wrong column names + margin tier off-by-one) by reading the real CSV headers + the legacy phase3_scoring.js — both differed from SKILL.md. Always check both sources during a port.
- **Keepa stats indices:** Per `https://keepa.com/#!discuss/t/keepa-time-series-data/116`. The constants we consume: 0=AMAZON, 3=SALES (rank), 10=NEW_FBA, 11=COUNT_NEW (offer count), 18=BUY_BOX_SHIPPING. Stored as integer cents (`stats.current[18] = 1525` means £15.25). `-1` is the "no current value" sentinel — `KeepaProduct._stat_money/_stat_int` coerces both `-1` and missing arrays to `None`.
- **`_estimate_for` token scaling (PR #32):** Single product no-stats: 6 tokens. Single product with `stats=N`: 7. 100-ASIN batch with stats: 5 + 100*2 = 205. Without per-ASIN scaling, the bucket would silently over-issue and rely on Keepa returning HTTP 429.
- **Vitest integration tests:** Live in `src/__integration__/*.integration.test.ts`. Excluded from default `npm test` via `vitest.config.ts` exclude. Run with `npm run test:integration` (separate `vitest.integration.config.ts`).

## Session Protocol
- At the end of each session, update the "Current State" section above
- If you learned something about how this project works that would help next time, add it to this file
- Commit CLAUDE.md changes as part of your work

---

## Read these first

For any work in this repo, read in this order:

1. **`docs/SPEC.md`** — business logic, decision rules, the truth (supersedes the v5 PRD)
2. **`docs/architecture.md`** — how the system is laid out
3. **`AGENTS.md`** — agent behaviour rules, what not to do

For specific work:

- **Strategy work** → `docs/strategies/<strategy>.md` for that strategy
- **Adapter work** (adding/fixing a supplier) → `fba_engine/adapters/<supplier>/` and look at sibling adapters as templates
- **Threshold tuning** → `shared/config/decision_thresholds.yaml` (the single tunable knob is `target_roi`)

---

## Top-level layout

```
amazon_fba/
├── README.md                # human-facing
├── CLAUDE.md                # this file
├── AGENTS.md                # agent behaviour rules
├── run.py                   # launcher
├── docs/                    # SPEC.md, architecture.md, strategies/, archive/
├── shared/                  # config/, niches/, lib/python/ (engine + libs)
├── fba_engine/              # adapters/, data/ (gitignored), _legacy_keepa/ (temporary)
├── services/                # amazon-fba-fees-mcp/
└── orchestration/           # Cowork-facing run definitions
```

For details on each, see `docs/architecture.md`.

---

## Common operations

### Run the supplier pricelist strategy
```bash
python run.py --supplier connect-beauty
# or with explicit market data
python run.py --supplier abgee --market-data fba_engine/data/pricelists/abgee/raw/keepa_combined.csv
```

### Run all tests
```bash
# Shared library + canonical engine
cd shared/lib/python && pytest tests/ sourcing_engine/tests/ && cd ../../..

# Per-supplier adapter tests (run from supplier folder so relative paths resolve)
for s in abgee connect-beauty shure zappies; do
  cd fba_engine/data/pricelists/$s && pytest ../../../adapters/$s/tests/ && cd ../../../..
done
```

Note: the supplier adapter tests use relative paths like `raw/some_file.pdf`,
so they must be invoked from the supplier's data folder.

### Add a new supplier
1. Create `fba_engine/adapters/<new-supplier>/` (use `_template/` as starting point)
2. Implement `ingest.py` and `normalise.py` for that supplier's file format
3. Create `fba_engine/data/pricelists/<new-supplier>/raw/` and drop in price lists
4. Run `python run.py --supplier <new-supplier>`

### Tune the ROI target
Edit `shared/config/decision_thresholds.yaml`:
```yaml
target_roi: 0.30   # change to taste
```
That's it. All downstream gates derive from this.

---

## What changed in steps 1-3 (recap)

- **Step 1:** Centralised all thresholds into `shared/config/`. Replaced the
  margin-based SHORTLIST gate with an ROI-based gate (single tunable: `target_roi`).
  Doc drift fixed.

- **Step 2:** Deduplicated the sourcing engine — was 4× copied across
  supplier folders, now one canonical copy at `shared/lib/python/sourcing_engine/`.
  Per-supplier code reduced to just the legitimately-different
  `ingest.py` and `normalise.py` files.

- **Step 3:** Restructured the repo. The two old top-level pipelines
  (`keepa_niche_finder/`, `supplier_pricelist_finder/`) no longer exist as
  separate trees. Engine code is in `shared/`, supplier adapters and data
  are in `fba_engine/`, MCP is in `services/`. Vestigial files removed.
  v5 PRD/BUILD_PROMPT moved to `docs/archive/`.

---

## What's coming in steps 4-6

- **Step 4:** Extract the legacy Keepa phases (currently in `fba_engine/_legacy_keepa/`)
  into composable steps at `fba_engine/steps/` (Python translation of the
  Node.js implementation). After step 4, `_legacy_keepa/` is gone.

- **Step 5:** Express both existing strategies as YAML compositions in
  `fba_engine/strategies/`. A `runner.py` reads a strategy YAML and executes
  its steps. Cowork orchestrates this.

- **Step 6:** Implement Skill 99 (Find Suppliers For Keepa-Discovered ASINs)
  as a new strategy composing existing steps + one new discovery step.
  Future strategies (brand outreach, retail arbitrage) follow the same pattern.
