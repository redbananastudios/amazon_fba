# Amazon FBA Sourcing System

> **Step 3 update (2026-04-28):** Repo restructured. One engine, named
> strategies, ordered steps. The two former pipelines (`keepa_niche_finder/`,
> `supplier_pricelist_finder/`) no longer exist as separate top-level trees.
> See `docs/architecture.md`.

## Current State
**Last updated:** 2026-04-29 (continuing same session)
**Currently working on:** Step 4c (Skill 5 build output) is now FULLY ported to Python — all three sub-steps shipped this session. Step 4c.3 (GSheets push) is the open PR. Once it merges, the legacy Keepa pipeline at `fba_engine/_legacy_keepa/` has no remaining unported code paths used by the active strategies.
**Status:** PRs #10 (4b decision), #12 (4c.1 merge), #13 (4c.2 xlsx) merged. PR open for 4c.3 (push_to_gsheets).

**Latest tests baseline:**
```bash
cd services/amazon-fba-fees-mcp && npm test                  # 110/110 unit
cd services/amazon-fba-fees-mcp && npm run test:integration  # 5/5 live SP-API
cd shared/lib/python && pytest tests/ sourcing_engine/tests/ # 68/68
pytest fba_engine/steps/tests/                               # 384/384 (67 ip_risk + 155 decision + 60 build_output + 65 build_xlsx + 37 push_gsheets)
```

### What landed this session

**Step 4b — Decision Engine port** (PR #10, MERGED): 1:1 port of `phase6_decision.js` (651 LOC) → 155 pytest cases. Two-sheet Excel shortlist via openpyxl.

**Step 4c.1 — Build Output merge** (PR #12, MERGED): 1:1 port of `phase5_build.js` (~330 LOC) → 60 pytest cases. Reviewer surfaced 2 MEDIUM porting bugs (sort key divergences) + 1 latent pd.NA crash; all addressed.

**Step 4c.2 — XLSX styling** (PR #13, MERGED): port of `build_final_xlsx.js` (529 LOC) via openpyxl → 65 pytest cases. Two deliberate JS divergences (64→67 column extension; Trade Price NUMERIC_COLS fix). Reviewer surfaced coverage gap; addressed with 9 extra branch-specific tests.

**Step 4c.3 — Google Sheets push** (PR open):
- Port of `push_to_gsheets.js` (311 LOC) at `fba_engine/steps/push_to_gsheets.py` (~440 LOC).
- 3-strategy fallback chain: xlsx-conversion upload → raw-xlsx upload → Sheets API create+populate.
- Uses `google-api-python-client` + `google-auth` (already installed in env; lazy-imported in `_build_clients` so the module loads cleanly on systems without the deps).
- 37 pytest cases all using mocked googleapis (no network).
- Reviewer flagged 1 HIGH (`_is_quota_error` would silently miss real `HttpError 403` because httplib2 may surface status as a string) + 3 MEDIUMs (full-chain test, `PushFailedError` test, sequencing test) + LOWs (em-dash in title, datetime import location). All addressed.

### Prior session highlights (kept for context)

**PRs #6–#9** (prior session): doc-drift fixes, MCP M1-M4 + L1-L7 follow-ups
(110/110 vitest), step 4a IP Risk port (67 pytest), handoff doc. All merged.

**Credentials infrastructure** (prior session): `F:/My Drive/workspace/credentials.env`
now quotes values containing bash special chars; `sync-credentials.ps1` strips
those quotes when writing to `settings.json`. Bash `source` now works on the file.

### Step 4 roadmap status

| Skill / Sub-step | LOC (JS) | Status |
|---|---|---|
| 4 — IP Risk | 351 | **MERGED PR #8** |
| 6 — Decision Engine | 651 | **MERGED PR #10** (step 4b) |
| 5.1 — Build Output (merge logic) | ~330 | **MERGED PR #12** (step 4c.1) |
| 5.2 — Build Output (XLSX styling) | 529 | **MERGED PR #13** (step 4c.2) |
| 5.3 — Build Output (GSheets push) | 311 | **PR open** (step 4c.3, this session) |
| 3 — Scoring | 0 (SKILL.md) | **Scope decision needed**: extract a canonical scoring step, or keep agent-driven? Per-niche scripts get generated under `data/{niche}/working/`. |
| 1 — Keepa Finder | 0 (browser) | **Separate scoping**: Keepa API integration vs Playwright vs keep as Claude Code skill |
| 2 — SellerAmp | 0 (browser) | Same scoping question as Skill 1 |

**Blockers:** None remaining for step 4. Once 4c.3 merges, the only outstanding
items are the (deferred) `_helpers.py` extraction across step files, Skill 3
scope decision (extract canonical scoring step or keep agent-driven?), and
the browser-vs-API scoping for Skills 1 + 2.

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
