# Amazon FBA Sourcing System

> **Step 3 update (2026-04-28):** Repo restructured. One engine, named
> strategies, ordered steps. The two former pipelines (`keepa_niche_finder/`,
> `supplier_pricelist_finder/`) no longer exist as separate top-level trees.
> See `docs/architecture.md`.

## Current State
**Last updated:** 2026-05-02 (post-real-run cleanup — Keepa Product Finder strategies validated end-to-end against live data)
**Currently working on:** Nothing in flight. The `keepa_finder` strategy family is shipped, validated against real data, and operationally usable today. PR #37 (infrastructure), #38 (column-rename fix), #39 (`restriction_links`), #40 (5 reserved ungate-tracking columns) all merged to main.
**Status:** main is at `32ff276`. **933 Python tests pass.** MCP suite untouched (110 unit + 5 integration still green). Working tree clean except for two pre-existing modifications carried from prior sessions (`docs/PRD-sourcing-strategies.md`, `fba_engine/data/pricelists/connect-beauty/raw/eans_for_keepa.txt`).

**Latest tests baseline:**
```bash
cd services/amazon-fba-fees-mcp && npm test                          # 110/110 unit
cd services/amazon-fba-fees-mcp && npm run test:integration          # 5/5 live SP-API
pytest shared/lib/python/ fba_engine/steps/tests/ \
       fba_engine/strategies/tests/ cli/tests/                       # 933/933 in ~17s
```

### Operationally usable workflow (post real-run)

```
1. Browser (Cowork or Claude Code instance with $keepa-product-finder skill):
   $keepa-product-finder recipe=amazon_oos_wholesale category="Toys & Games"
   → exports CSV to ./output/<run_id>/keepa_<recipe>.csv

2. Engine:
   python run.py --strategy keepa_finder \
     --csv ./output/<run_id>/keepa_<recipe>.csv \
     --recipe amazon_oos_wholesale \
     --output-dir ./output/<run_id>/

3. Operator opens the resulting <recipe>_<ts>.xlsx — every gated row carries:
   - Amazon URL (clickable → product listing)
   - Ungate Links (clickable → Apply-to-sell page)
   - Ungate Status / Required Docs / Brand Required / Attempted At / Message
     (5 reserved columns, blank by default — operator fills as ungate apps progress)
```

### First real Toys & Games run (2026-05-01, validated)

| Filter funnel | Count |
|---|---|
| Keepa Product Finder UI hits (Toys & Games + AMAZON_outOfStock + recipe filters) | 265 |
| After global title-keyword exclusions | 251 |
| **Engine verdicts** | **0 SHORTLIST / 250 REVIEW / 1 REJECT** |

The 0 SHORTLIST is by design — wholesale flow uses `buy_cost = 0.0`, so the ROI gate emits `no_buy_cost` → REVIEW with `max_buy_price` populated as the supplier-negotiation ceiling.

| SP-API gating breakdown (174 gated rows) | Count |
|---|---|
| `BRAND_GATED` — needs brand outreach OR account-metric auto-approve | 161 |
| `RESTRICTED` — different gating class | 13 |
| `UNRESTRICTED` (immediately listable) | 77 |
| `catalog_hazmat = true` (caught by post-enrich safety net) | 2 |

Top wholesale leads surfaced: **Hasbro Transformers, Mattel WWE, Games Workshop Warhammer, Funko, BABESIDE / JIZHI Reborn Dolls.** Run artefacts in `fba_engine/data/strategies/keepa_finder/20260501_122031/`.

### What landed this session (2026-05-02, Keepa Product Finder strategies)

**Approach:** browser-export-driven, not API-driven. Peter doesn't have a Keepa API subscription and the existing `$keepa-product-finder` skill (in `_legacy_keepa/skills/keepa-product-finder/`) already produces CSV exports from the Keepa Product Finder UI. This branch wires those exports into the canonical engine via a thin column-mapper step + 4 recipe JSONs that encode named filter sets.

| Commit | Summary |
|---|---|
| 1 | `shared/config/global_exclusions.yaml` + `GlobalExclusions` loader. Three exclusions ship: hazmat, `Clothing, Shoes & Jewellery` root, title keywords (clothing/apparel/shoe/boot/footwear). Permissive defaults if file absent. |
| 2 | 4 recipe JSONs in `_legacy_keepa/skills/keepa-product-finder/recipes/`. Each declares Keepa filter set + `global_exclusions: "auto"` + optional `calculate_config` / `decide_overrides`. |
| 3 | `keepa-product-finder` SKILL.md update — Recipes section, recipe loading workflow, recipe_metadata.json sidecar, Cowork two-task prompt example. |
| 4 | `fba_engine/steps/keepa_finder_csv.py` — column mapper (175-col Keepa export → canonical schema), ASIN dedup against `data/niches/exclusions.csv`, post-export keyword + category filter. Smoke test against real 10k-row `kids_toys_phase1_raw.csv`. |
| 5 | `fba_engine/strategies/keepa_finder.yaml` — generic chain: discover → enrich (leads) → calculate → decide → supplier_leads. Discovery schema aligned with `04_calculate`'s expected column names (`buy_box_price`, `new_fba_price`, `referral_fee_pct` /100, `amazon_status` derived, wholesale defaults `buy_cost=0` + `moq=1`). |
| 6 | `04_calculate` consumes `compute_stability_score` config flag. New `add_stability_score()` derives 0.0–1.0 score from Buy Box delta-30d/90d. Default off — backwards compat preserved. |
| 7 | `05_decide` consumes `config["overrides"]` dict — generic per-call threshold override (no_rank_hidden_gem lowers `min_sales_shortlist` 20→5). `decide()` gains optional `overrides=` kwarg in the canonical engine. Unknown keys + invariant violations raise loud. |
| 8 | `orchestration/runs/keepa_finder.yaml` — Cowork two-task definition (browser-driven discovery + engine). Generic across all 4 recipes. |
| 9 | `run.py --strategy <name>` CLI dispatch via `cli/strategy.py`. Loads strategy YAML + recipe JSON, mutates StrategyDef so recipe configs flow to calculate/decide steps, runs the chain, prints verdict summary. End-to-end smoke against synthetic Keepa CSV. |
| 10 | Per-recipe docs in `docs/strategies/` (4 markdown files); CLAUDE.md update. |

**Test count delta:**
- Commit 1: +11 (global_exclusions loader, helpers, missing-file fallback)
- Commit 4: +30 (column mapping, exclusions, malformed input, sidecar, real-export smoke)
- Commit 5: +6 (4 schema-alignment tests + 2 strategy YAML tests)
- Commit 6: +8 (stability_score helper + run_step config plumbing)
- Commit 7: +9 (override mechanism per key, invariants, alias, no-op)
- Commit 9: +22 (argparse, YAML/recipe resolution, recipe→config wiring, full dispatch smoke)
- **Total: +86 tests, 820 → 906 → 902** (the -4 net is because some tests in the keepa_finder_csv update changed shape — verified all 902 pass green).

**Engine deltas summary** (additive, backwards-compat preserved):
- `fba_config_loader.GlobalExclusions` + `get_global_exclusions()` accessor
- `calculate.run_step` consumes `compute_stability_score: bool`
- `decide.run_step` consumes `config["overrides"]: dict`
- `decide()` in `sourcing_engine.pipeline.decision` gains optional `overrides=` kwarg
- `run.py --strategy <name>` dispatch (existing `--supplier` and `open` paths unchanged)

**No live Keepa API integration.** The `keepa_client` library exists from Phase 2 (PRs #26-#30) but has no `product_finder()` method — that path was deliberately not built. If/when Peter trials the API, adding it is a separate workstream that doesn't change anything here.

### Prior sessions

### What landed in the 2026-05-01 session (Phase 3)

**PR #32 — keepa_enrich foundation** (MERGED): the missing connector that lets ASIN-only sources chain into `calculate→decide`. `KeepaProduct.market_snapshot()` extracts canonical engine columns from Keepa stats indices (0=AMAZON, 3=SALES, 10=NEW_FBA, 11=COUNT_NEW, 18=BUY_BOX_SHIPPING). New `fba_engine/steps/keepa_enrich.py` joins per-ASIN market data via `KeepaClient.get_products()`. Both single + batch product paths now request `stats=90`. `_estimate_for` scales with N ASINs + stats overhead so the token bucket doesn't silently over-issue under heavy batch load. Pre-PR review caught 2 HIGH (product_name=None bug + seller_storefront chain clash), both fixed by dropping descriptive fields from canonical enrich schema. +29 tests.

**PR #33 — oa_csv full chain** (MERGED): promotes `oa_csv` from leads-only to full decision pipeline. New chain `discover → keepa_enrich → calculate → decide → output`. Two name-bridges: `monthly_sales_estimate → sales_estimate` in market_snapshot (canonical engine reads sales_estimate directly); `retail_cost_inc_vat → buy_cost` in oa_csv discovery output (per PRD §6.4). End-to-end smoke test pins that cheap rows SHORTLIST and expensive rows REJECT.

**PR #34 — SellerAmp drop / SP-API enrich leads mode** (MERGED): replaces the legacy SellerAmp skill with the existing SP-API MCP for non-Buy-Box-% checks. New `enrich.LEADS_INCLUDE = (restrictions, fba, catalog)` + `include: "leads"` YAML alias. `_row_to_item(allow_no_price=True)` lets ASIN-only rows preflight without a market_price. `seller_storefront.yaml` chain extended: discover → enrich (leads) → supplier_leads. Legacy `skill-2-selleramp/SKILL.md` marked deprecated. +8 tests.

**PR #35 — Skill 3 scoring extraction** (MERGED): canonical `fba_engine/steps/scoring.py` replaces the agent-driven per-niche `phase3_scoring.js` scripts. 4-dimension scoring (Demand/Stability/Competition/Margin) + 30/30/20/20 composite + 3 lane scores (Cash Flow/Profit/Balanced) + lane classification + 9-verdict ladder (YES/MAYBE/MAYBE-ROI/BRAND APPROACH/BUY THE DIP/PRICE EROSION/GATED/HAZMAT/NO). Pre-PR review caught 2 HIGH bugs by comparing against real `phase2_enriched.csv` + the legacy `phase3_scoring.js`: wrong column names (`Buy Box Drop % 90d` → `Price Drop % 90d`, `Buy Box Amazon Share` → `Buy Box Amazon %`) and margin-tier off-by-one (strict `>` not `>=`). `keepa_niche.yaml` chain now `scoring → ip_risk → build_output → decision_engine`. +64 tests.

### Older session highlights

**Phase 2 (PRs #26-#30, MERGED, prior session):** canonical engine refactor (6 step modules); keepa_client batch + stale-on-error; seller_storefront discovery step; seller_storefront.yaml + oa_csv.yaml; run_summary.json + strategy docs.

**Phase 1 (PRs #20-#25, MERGED, prior session):** docs/PRD-sourcing-strategies.md (PR #20), keepa_client foundation (PR #21), supplier_leads step / Skill 99 v1 (PR #22), oa_csv discovery + SellerAmp 2DSorter importer (PR #23), CLI launch helpers (PR #24), sourcing_engine integration test as PR #7 safety net (PR #25).

**Step 4 + 5 (PRs #8-#18, MERGED, prior session):** ip_risk, decision_engine, build_output (3-part: merge/XLSX/GSheets), cross-cutting fixes, helpers extraction, YAML strategy runner with `keepa_niche.yaml`.

### Roadmap status (where we are)

**Keepa Product Finder strategies: COMPLETE** ✅ (this session, branch `feat/keepa-finder-strategies`)

| Recipe | Strategy YAML | Status |
|---|---|---|
| `amazon_oos_wholesale` | `keepa_finder.yaml` + recipe JSON | ✅ shipped |
| `brand_wholesale_scan` | `keepa_finder.yaml` + recipe JSON | ✅ shipped |
| `no_rank_hidden_gem` | `keepa_finder.yaml` + recipe JSON | ✅ shipped |
| `stable_price_low_volatility` | `keepa_finder.yaml` + recipe JSON | ✅ shipped |
| `a2a_flip` | — | ⏸️ Deferred (PRD §6.2 future) |

**Phase 3 (post-PRD scoping items): COMPLETE** ✅ (PRs #32–#35 prior session)

| Phase 3 deliverable | Status |
|---|---|
| `keepa_enrich` step (foundation for ASIN→market data) | ✅ #32 |
| `oa_csv` chains into `calculate → decide` (full verdicts) | ✅ #33 |
| Drop SellerAmp — SP-API MCP enrich leads mode | ✅ #34 |
| Skill 3 scoring → canonical step | ✅ #35 |
| **Skill 1 (Keepa Finder) — keep browser flow** | ✅ wired in this session via `keepa_finder` |

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
- **ASINs are exactly 10 chars.** Every test fixture ASIN that's 11 chars (`B0KEEP00001`, `B0SMOKE0001`) gets silently dropped by the canonical 10-char check in any discovery step that validates length (keepa_finder_csv does). Hit this twice this session — both times the test failure mode was "0 rows in output" with no obvious cause until tracing through the discovery step. Use 10-char ASINs in fixtures.
- **Keepa CSV column names with commas** (`"New, 3rd Party FBA: Current"`) MUST be CSV-quoted. Real Keepa exports do this correctly; hand-built test CSVs that join columns with bare commas silently shift every data column right by one. Use `pd.DataFrame(...).to_csv(...)` to write fixture CSVs — pandas handles the quoting.
- **Keepa Referral Fee % format:** Keepa exports `"15 %"` / `"15.01 %"` (with space + percent sign). `parse_money()` strips the `%` but doesn't divide; the canonical engine expects the fraction (0.15). Always divide by 100 when bridging this column. Same shape for "Buy Box: % Amazon 90 days" — though that one is informational and the divide isn't load-bearing yet.
- **Wholesale flow buy_cost convention:** `buy_cost = 0.0` is the load-bearing signal that tells `calculate.calculate_profit` to emit `max_buy_price` (the supplier-negotiation ceiling) instead of a literal ROI. Used by `seller_storefront`, `keepa_finder`, and any future leads-only strategy. Don't pass `None` — the engine's direct `match["buy_cost"]` access KeyErrors on missing keys; 0.0 is the intentional sentinel.
- **Strategy YAML interpolation is string-only and one-level-deep.** The `runner._interpolate_config` function only substitutes `{name}` in string values, not in dict/list values, and missing context keys raise `StrategyConfigError`. To forward a dict config (like recipe `decide_overrides`), mutate the loaded `StrategyDef` from the dispatcher (see `cli/strategy.py:_apply_recipe_to_strategy`) rather than trying to interpolate it through YAML.
- **Recipe JSONs live with the skill that consumes them:** `_legacy_keepa/skills/keepa-product-finder/recipes/{name}.json`. Same convention as `keepa-finder-values.md` — co-located with the consumer. Future strategies that don't go through this skill should put their recipes elsewhere.
- **`pd.read_csv(on_bad_lines='skip')`:** Real Keepa Product Finder exports occasionally have malformed lines (e.g. `kids_toys_phase1_raw.csv` line 4993 has unbalanced quotes). Skip them rather than crashing the whole run — the engine's "never crash on a single bad row" principle applies at the row-parser level too.
- **Keepa column rename (post 2026-04):** `"Bought in past month"` → `"Monthly Sales Trends: Bought in past month"`. Both names map to `sales_estimate` in `keepa_finder_csv._KEEPA_TO_CANONICAL`. Test fixtures using the old name (`kids_toys_phase1_raw.csv` from 2026-03) still work via the alias resolution in `_row_from_keepa` (groups source columns by destination, picks the first source with non-empty data). Watch for similar renames — refresh the smoke fixture periodically or the next column drift slips through.
- **Keepa Browser Pro vs API tier are different products.** Peter has the Browser tier (~£19/mo, gives access to the Product Finder UI + CSV export). The API tier is separate (~£49/mo for Power, more for higher rates). The keepa_finder pipeline uses the BROWSER tier — driven via Claude in Chrome MCP against the logged-in Product Finder UI. No `keepa_client.product_finder()` method exists in the engine because the API path was deliberately not built.
- **`subprocess` + `timeout` orphans on Windows:** `bash`'s `timeout 120 python run.py ...` sends SIGTERM after 120s, but a Python child blocked on a synchronous SP-API call doesn't release the interpreter to handle the signal. The Python process keeps running after `timeout` exits, eventually overwriting the output file with the original (pre-fix) result. Symptom: stdout shows the right summary (post-fix engine ran), but the on-disk CSV reflects the orphaned earlier run. Detection: compare CSV mtime vs the engine's run_summary.json `started_at` field. Cleanup: `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object CommandLine -like "*run.py*--strategy*" | ForEach-Object Stop-Process` from PowerShell. `pkill` doesn't exist in this bash; PowerShell is the right escape hatch.
- **`autocomplete-rootCategory` value lives in a hidden field, not the input:** when the keepa-product-finder skill clicks a Toys & Games dropdown match, the visible `<input id="autocomplete-rootCategory">` clears (Keepa UI pattern). The actual selection persists in a sibling hidden field `<input name="autocompleteReal-rootCategory">` carrying Keepa's internal category ID (e.g. `468292` for Toys & Games). Verify post-click via the hidden field, not the visible input.
- **`autocomplete-categories_exclude` is sub-category scoped, not root scoped:** when `rootCategory` is set, the `categories_exclude` autocomplete searches only sub-categories of that root. Setting "Clothing, Shoes & Jewellery" in `categories_excluded` (the global YAML) is a no-op for any run scoped to "Toys & Games" — the post-export title-keyword filter is the actual safety net for keyword-based exclusions when the root scope already excludes the bad category. Document per-recipe whether category exclusion is meaningful for that scope.
- **SP-API restrictions endpoint already returns ungate URLs:** `getListingsRestrictions` returns `restrictions[].reasons[].links[].resource` per gated reason. The MCP forwards this as `r["link"]` (singular) — first link only. The engine's `preflight._coerce_result` extracts and surfaces it as the `restriction_links` column (semicolon-joined, deduplicated). Future enhancement: extend the MCP to forward the full `links[]` array if multiple application paths matter.
- **Reserved schema for ungate-tracking:** `ungate_status`, `ungate_required_docs`, `ungate_brand_required`, `ungate_attempted_at`, `ungate_message` are seeded as None by `preflight._seed_row` and `_coerce_result`. Engine never writes them — operator fills by hand (or future click-through bot fills automatically). Locked in `UNGATE_COLUMNS` constant at top of `preflight.py`. Renaming any of these breaks operator spreadsheets that reference the column names; rename only with a migration plan.

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
