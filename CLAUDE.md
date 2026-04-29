# Amazon FBA Sourcing System

> **Step 3 update (2026-04-28):** Repo restructured. One engine, named
> strategies, ordered steps. The two former pipelines (`keepa_niche_finder/`,
> `supplier_pricelist_finder/`) no longer exist as separate top-level trees.
> See `docs/architecture.md`.

## Current State
**Last updated:** 2026-04-29 (end of long session)
**Currently working on:** Step 4 in flight — porting legacy Node.js Keepa pipeline to Python composable steps under `fba_engine/steps/`. Step 4a (IP Risk) is open as **PR #8** awaiting merge.
**Status:** Two PRs merged this session (#6 doc-drift, #7 M1-M4 + L1-L7 MCP follow-ups). One PR open (#8 step 4a IP Risk port). Credentials file fixed for bash-source compatibility.

**Latest tests baseline:**
```bash
cd services/amazon-fba-fees-mcp && npm test                  # 110/110 unit (was 100; +10 from #7)
cd services/amazon-fba-fees-mcp && npm run test:integration  # 5/5 live SP-API
cd shared/lib/python && pytest tests/ sourcing_engine/tests/ # 68/68
pytest fba_engine/steps/tests/                               # 67/67 (NEW — when #8 lands)
```

### What landed this session

**PR #6** (`docs: fix test-count and line-cite drift`) — merged.
- AGENTS.md baseline counts (34→42 engine, 99→100 vitest, 49→68 total)
- README.md test count
- CLAUDE.md M1 line cite (:85 → :90), L5 line cite (:128-135 → :164-177)
- Found while running documented test baselines clean during a fresh QA review

**PR #7** (`fix(mcp): resolve M1-M4 + L1-L7 follow-ups`) — merged. 10 atomic commits + cleanup:
- M1 BuyBoxPrices condition filter, M2 reasonCode classifier, M3 strip raw payloads from preflight (closes L5), M4 hazmat deny-list extended
- L1 marketplace-id through fees, L2 parseArgs stop-token + key=value, L3 DRY resolveSellerId, L4 DiskCache.set options object
- L7 AMZN buy_box_seller detection (UK only)
- Pre-PR code-reviewer agent surfaced one MEDIUM (mutation in M3 strip) + 3 LOW; all addressed before merge

**Credentials infrastructure fix:**
- `F:/My Drive/workspace/credentials.env` now quotes values containing bash special chars (lines 27 EBAY_CLIENT_TOKEN with `#`, line 33 SP_API_REFRESH_TOKEN with `|`). Bash `source` of the file would previously truncate those values; sync to settings.json was working only because Claude Code's harness pre-injects from settings.json.
- `F:/My Drive/workspace/sync-credentials.ps1` updated to strip surrounding quotes when parsing, so settings.json contains the unquoted values (verified — no spurious quote chars in the JSON).

**PR #8 OPEN** (`feat(steps): port IP risk phase to Python (step 4a)`):
- New package `fba_engine/steps/` with `__init__.py` + first step `ip_risk.py`
- 1:1 port of legacy `phase4_ip_risk.js` (351 LOC JS → ~350 LOC Python including docstrings)
- Caught one porting bug: JS `Math.round(6.5) === 7` vs Python `round(6.5) === 6` — fixed with `math.floor(score + 0.5)`
- 67 pytest cases (boundary cases at half-rounding, NaN safety, run_step contract, Unicode, edge inputs)
- Establishes the **`run_step(df, config) -> df`** contract for step 5's YAML runner
- Pre-PR code-reviewer surfaced HIGH NaN coercion bug + MEDIUM run_step shape + LOW BOM strip — all addressed in second commit

### Step 4 roadmap status

| Skill | LOC (JS) | Status |
|---|---|---|
| 4 — IP Risk | 351 | **PR #8 open** (this session) |
| 6 — Decision Engine | 651 | **NEXT** — pure logic, follows the same pattern. Smallest remaining real-code skill. |
| 5 — Build Output | 840 | After 6. XLSX (openpyxl is already in use) + GSheets (pulls in google-api-python-client, **decision needed**) |
| 3 — Scoring | 0 (SKILL.md) | **Scope decision needed**: extract a canonical scoring step, or keep agent-driven? Per-niche scripts get generated under `data/{niche}/working/`. |
| 1 — Keepa Finder | 0 (browser) | **Separate scoping**: Keepa API integration vs Playwright vs keep as Claude Code skill |
| 2 — SellerAmp | 0 (browser) | Same scoping question as Skill 1 |

**Blockers:** None for step 4b (Decision Engine).

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
