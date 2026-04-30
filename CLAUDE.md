# Amazon FBA Sourcing System

> **Step 3 update (2026-04-28):** Repo restructured. One engine, named
> strategies, ordered steps. The two former pipelines (`keepa_niche_finder/`,
> `supplier_pricelist_finder/`) no longer exist as separate top-level trees.
> See `docs/architecture.md`.

## Current State
**Last updated:** 2026-04-30 (Phase 2 complete — 5 PRs merged this session)
**Currently working on:** Phase 2 of `docs/PRD-sourcing-strategies.md` is fully shipped. Canonical engine refactored, keepa_client production-grade, two new sourcing strategies (seller_storefront, oa_csv) live, run_summary.json observability sidecar added.
**Status:** Main is at PR #30 (commit `ca0b281`). **719 Python tests + 110 MCP vitest = 829 total green.**

**Latest tests baseline:**
```bash
cd services/amazon-fba-fees-mcp && npm test                          # 110/110 unit
cd services/amazon-fba-fees-mcp && npm run test:integration          # 5/5 live SP-API
pytest shared/lib/python/ fba_engine/steps/tests/ \
       fba_engine/strategies/tests/ cli/tests/                       # 719/719 in ~11s
```

### What landed this session (2026-04-30, Phase 2)

**PR #26 — Canonical engine refactor** (MERGED): split `sourcing_engine.main.run_pipeline` into 6 composable step modules at `fba_engine/steps/` exposing the `run_step(df, config) -> df` contract:
- `supplier_pricelist_discover` — adapter ingest + normalise + case_detection
- `resolve` — EAN validation + Amazon market match (multi-match explosion)
- `calculate` — fees + conservative price + profit + risk flags
- `decide` — SHORTLIST / REVIEW / REJECT verdicts
- `enrich` — SP-API preflight passthrough
- `supplier_pricelist_output` — CSV + XLSX + MD writers

`run_pipeline` keeps its public signature; internally composes the new modules. Adds `fba_engine/strategies/supplier_pricelist.yaml`. Runner extended with `input.discover: true` (strict bool — quoted `"true"` rejected). Caught + fixed a NaN-truthy bug that would have skipped match rows after DataFrame round-trip (`pd.DataFrame` fills missing dict keys with NaN, which is truthy — `is_missing()` is the safe check). PR #25's 9-case integration test was the regression check.

**PR #27 — keepa_client batch + stale-on-error** (MERGED):
- `KeepaClient.get_products(asins)` — chunked batch lookup (dedupes input, preserves order, filters Keepa nulls, defensive against extras Keepa returns beyond request).
- `DiskCache.get_stale(namespace, key)` — TTL-ignoring lookup.
- All three Keepa methods (`get_product`, `get_products`, `get_seller`) fall back to expired cached data when API fails after retries. Token log records `cached=true, stale=true` so operators correlate degraded responses with upstream incidents. Single-ASIN methods raise when no fallback exists; the batch method silently drops affected ASINs (caller compares `len(out)` to `len(asins)`).

**PR #28 — seller_storefront discovery step** (MERGED): `fba_engine/steps/seller_storefront.py` walks an Amazon seller's storefront via Keepa and emits a canonical leads DataFrame (asin, source, seller_id, seller_name, product_name, brand, category, amazon_url). Wholesale-leads strategy. No buy_cost — discovery is leads-only by design (heuristic buy_cost would silently produce fake ROI verdicts). +15 tests with stubbed KeepaClient.

**PR #29 — strategy YAMLs** (MERGED):
- `fba_engine/strategies/seller_storefront.yaml` — 2-step chain: discover → supplier_leads → output CSV + supplier_leads.md.
- `fba_engine/strategies/oa_csv.yaml` — 1-step: discover → output (OA buyers go directly to retail_url; supplier_leads redundant).
- 4 YAML smoke tests including end-to-end runs with stubbed Keepa.

**PR #30 — run_summary.json + strategy docs** (MERGED): when `output.csv` is set, runner writes a `<csv-stem>.summary.json` sibling capturing strategy name, context, started_at/completed_at (ISO 8601 UTC), duration, initial_rows, final_rows, per-step `step_summary` (name, module, rows_in, rows_out, duration, error?), outputs paths. Failure path doesn't serialise the summary (operators read StrategyExecutionError + logs). Plus `docs/strategies/seller_storefront.md` + `docs/strategies/oa_csv.md` matching the existing `supplier_pricelist.md` shape. +4 tests.

### Prior session highlights (kept for context)

**Phase 1 (PRs #20-#25, MERGED):** docs/PRD-sourcing-strategies.md (PR #20), keepa_client foundation (PR #21), supplier_leads step / Skill 99 v1 (PR #22), oa_csv discovery + SellerAmp 2DSorter importer (PR #23), CLI launch helpers (PR #24), sourcing_engine integration test as PR #7 safety net (PR #25).

**Step 4 + 5 (PRs #8-#18, MERGED, prior session):** ip_risk, decision_engine, build_output (3-part: merge/XLSX/GSheets), cross-cutting fixes, helpers extraction, YAML strategy runner with `keepa_niche.yaml`.

### Roadmap status (where we are)

**Phase 2 of PRD: COMPLETE** ✅ (PRs #26–#30 this session)

| Phase 2 deliverable | Status |
|---|---|
| Canonical engine refactor (6 step modules) | ✅ #26 |
| keepa_client batch + stale-on-error | ✅ #27 |
| seller_storefront discovery step | ✅ #28 |
| seller_storefront.yaml + oa_csv.yaml | ✅ #29 |
| run_summary.json + strategy docs | ✅ #30 |

**Open scoping decisions (no PRs yet):**

1. **`keepa_enrich` step** — fetches `market_price` / `fees` / `sales_estimate` per ASIN so `oa_csv` can chain into `calculate→decide` for full ROI verdicts. Currently `oa_csv.yaml` stops at discovery (leads-only). The existing `resolve` step is EAN-keyed and assumes supplier-pricelist input, doesn't fit OA's pre-resolved ASINs. Implementation involves parsing Keepa's `csv` array indices (deliberately avoided in `KeepaProduct` model so far). Med-effort PR.

2. **Skill 1 (Keepa Finder) + Skill 2 (SellerAmp)** — both browser-based in the legacy. Three options each: official API (Keepa has one; SellerAmp has paid API), Playwright headless, or keep as Claude Code skills. Decision drives whether they get ports at all.

3. **Skill 3 (Scoring)** — `skills/skill-3-scoring/` is just a SKILL.md. Per-niche scoring scripts get generated under `data/{niche}/working/` by an agent. Question: extract canonical `fba_engine/steps/scoring.py` (matches rest of step 4), or leave agent-driven? Tradeoff: portability + testability vs flexibility per niche.

**Open low-priority polish (not blocking):**
- Resumable upload progress logging dropped in PR #15 (`_status` discarded)
- Title clamp at 200 chars vs Sheets API's actual 100-char limit
- Strategy 2 silently falls through on auth failures (pre-existing, JS-faithful)
- Dead `last_err` defensive branch in `_retry_with_backoff`
- TA + OAXray oa_importers stubbed out — add their parsers when needed

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
