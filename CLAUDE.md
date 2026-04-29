# Amazon FBA Sourcing System

> **Step 3 update (2026-04-28):** Repo restructured. One engine, named
> strategies, ordered steps. The two former pipelines (`keepa_niche_finder/`,
> `supplier_pricelist_finder/`) no longer exist as separate top-level trees.
> See `docs/architecture.md`.

## Current State
**Last updated:** 2026-04-29 (end of session)
**Currently working on:** Nothing in flight. **MCP sourcing-tools expansion (PR #5) is merged into main.**
**Status:** Feature complete and live. Pipeline now auto-annotates matched rows with SP-API data (restrictions, FBA eligibility, live Buy Box, catalog brand, hazmat). Markdown report has a "🚫 Restriction notes" section listing gated SHORTLIST items.

**What was delivered (15 commits merged via PR #5, `f8dfe64`):**
- 7 MCP tools (`check_listing_restrictions`, `check_fba_eligibility`, `estimate_fees_batch`, `get_catalog_item`, `get_live_pricing`, `preflight_asin` composite, plus CLI mode)
- Python pipeline integration (`shared/lib/python/sourcing_engine/pipeline/preflight.py`)
- Persistent disk cache at `<repo>/.cache/fba-mcp/` (gitignored, TTLs configurable per resource via env vars)
- 100 vitest unit + 5 live SP-API integration + 42 pytest tests, all green
- Code reviewed (4 review findings landed in the PR), QA'd end-to-end on real connect-beauty data (3 production bugs found and fixed before merge)

**Tests baseline going forward:**
```bash
cd services/amazon-fba-fees-mcp && npm test                  # 100/100 unit
cd services/amazon-fba-fees-mcp && npm run test:integration  # 5/5 live (requires SP_API creds)
cd shared/lib/python && pytest sourcing_engine/tests/        # 42/42
```

**Possible follow-up tickets** (from pre-PR code review; none are blocking, all green-tested):

Medium-priority (real defects, low blast radius):

- **M1** `services/amazon-fba-fees-mcp/src/tools/get-live-pricing.ts:90`. `buy_box_price` reads `BuyBoxPrices[0]` without a condition filter. SP-API ordering is currently fine because the request always asks for `New`, but a future API quirk could land Used at index 0 silently. Fix: filter by requested condition explicitly, fall back to `[0]`.
- **M2** `services/amazon-fba-fees-mcp/src/tools/check-listing-restrictions.ts:44-52`. BRAND_GATED beats CATEGORY_GATED when both keywords appear in the message blob. SP-API actually returns structured `reasonCode` values (e.g. `APPROVAL_REQUIRED`, `ASIN_NOT_IN_PRODUCT_GROUP`) which are more reliable than message regex. Fix: prefer reasonCode discrimination, fall back to message hints.
- **M3** `services/amazon-fba-fees-mcp/src/types.ts:65,79,126,138`. Tools embed full SP-API `raw` payloads in every result. A 20-ASIN preflight serialises ~5MB through stdout; the Python side ignores `raw` entirely. Fix: add `include_raw?: boolean` (default false) to `PreflightInput`, propagate through to the sub-tools; keep `raw` for individual tool calls.
- **M4** `services/amazon-fba-fees-mcp/src/tools/get-catalog-item.ts:128-156`. Hazmat detection deny-lists `"no"`, `"false"`, `"not_applicable"` etc. but a value like `"none"`/`"non_dangerous"` would still flag as hazmat. Fix: add to deny-list, or switch to allow-list of known hazmat indicators (`un_*`, `class_*`, `true`, `yes`, `hazmat`).

Low-priority (cosmetic / future-proofing):

- **L1** `services/amazon-fba-fees-mcp/src/cli.ts:272-292`. `fees` subcommand ignores `--marketplace-id`. Thread it through as a per-item default.
- **L2** `services/amazon-fba-fees-mcp/src/cli.ts:50-65`. `parseArgs` treats a positional arg as a boolean-flag value if it follows `--<flag>`. No current bug because no subcommand has post-flag positionals; future footgun.
- **L3** `services/amazon-fba-fees-mcp/src/cli.ts:194,222-224`. `runPreflight` and `runRestrictions` resolve `seller_id` independently. Extract to `resolveSellerId(flags)`.
- **L4** `services/amazon-fba-fees-mcp/src/services/disk-cache.ts:91,111`. `get(...keyParts)` is spread, `set(keyParts, ...)` is array. Asymmetric. Pick one (recommend spread + options object).
- **L5** `shared/lib/python/sourcing_engine/pipeline/preflight.py:164-177`. Subprocess `text=True` + large `raw` payloads on Windows could approach pipe-buffer limits for very large batches. Mitigated if M3 lands.
- **L7** `services/amazon-fba-fees-mcp/src/types.ts:132`. `buy_box_seller` documents `"AMZN"` but the classifier never returns it (Amazon Retail gets bucketed as `"FBA"`). Either remove from the type union or implement marketplace-keyed seller-ID detection.

(M5 and L6 from the original review were resolved before the PR landed — see commit `2a513d9`.)

**Next steps:** None pending. User can pick from the M-series follow-ups or move to other work (e.g., reorganisation step 4: extract Keepa phases from `_legacy_keepa/`).

**Blockers:** None.

### Workflow notes (cumulative across sessions)
- **Worktree gotcha:** `[[ -d .git ]]` checks fail in worktrees because `.git` is a file pointer. Use `[[ -e .git ]]`.
- **TS imports:** This project uses ESM/nodenext — relative imports MUST end in `.js` even when source is `.ts` (e.g., `import {X} from "./foo.js"`). Otherwise `npm run build` fails (vitest is more lenient).
- **MCP test path:** Always run `npm` commands inside the actual worktree's `services/amazon-fba-fees-mcp/`, not `O:/fba/services/amazon-fba-fees-mcp/`. They're separate copies.
- **Credential sync:** After editing `F:\My Drive\workspace\credentials.env`, run `& 'F:\My Drive\workspace\sync-credentials.ps1'` (PowerShell — bash quoting breaks on the space in "My Drive"). Then verify with `grep '"SP_API_' "C:/Users/peter/.claude/settings.json"`.
- **MCP `.mcp.json` path** at repo root references the MCP at `services/amazon-fba-fees-mcp/dist/index.js` (corrected from old root-level path during cleanup).
- **SP-API endpoint group names** (amazon-sp-api lib): catalogItems, productFees, listingsRestrictions, fbaInboundEligibility, productPricing. Use `client.callAPI({ operation, endpoint, ... })`.
- **Disk cache layout:** `<repo>/.cache/fba-mcp/<resource>/<key-parts>__joined.json` — gitignored. `DiskCache.get()` returns `{ hit, stale, data }` enabling stale-on-error fallback.
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
