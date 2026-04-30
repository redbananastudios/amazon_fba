# Amazon FBA Sourcing System

> **Step 3 update (2026-04-28):** Repo restructured. One engine, named
> strategies, ordered steps. The two former pipelines (`keepa_niche_finder/`,
> `supplier_pricelist_finder/`) no longer exist as separate top-level trees.
> See `docs/architecture.md`.

## Current State
**Last updated:** 2026-04-30 (later in same session)
**Currently working on:** Step 5 (YAML strategy runner) just shipped. Step 4 + step 5 of the engine refactor are now both complete. The `keepa_niche` pipeline is fully expressible as a single YAML and runs end-to-end through ported Python steps.
**Status:** Main is at PR #18. 444 Python tests + 110 MCP vitest tests all green.

**Latest tests baseline:**
```bash
cd services/amazon-fba-fees-mcp && npm test                  # 110/110 unit
cd services/amazon-fba-fees-mcp && npm run test:integration  # 5/5 live SP-API
cd shared/lib/python && pytest tests/ sourcing_engine/tests/ # 68/68
pytest fba_engine/steps/tests/                               # 421/421 (71 ip_risk + 157 decision + 65 build_output + 71 build_xlsx + 57 push_gsheets)
pytest fba_engine/strategies/tests/                          # 23/23 (YAML runner)
```

### What landed today (2026-04-30)

**PR #15 — Cross-cutting followups** (independent reviewer, MERGED): 13 HIGH defects + 1 MEDIUM across all 5 step modules. +32 regression tests. Notable fixes: `bought_int` `int(nan)` ValueError; CSV writes now atomic via tmp+rename; UTF-8-sig encoding round-trip; `compute_workbook` schema validation; cols 40+42 NUMERIC_COLS/PCT_COLS membership; chunked resumable uploads with per-chunk retry; `_is_quota_error` tightening; previous-sheet delete deferred until after new upload succeeds; orphan-sheet cleanup; tightened title clamp.

**PR #16 — Strategy 3 reads from CSV** (this session, MERGED): fixed a HIGH-severity regression PR #15 introduced. `_csv_rows_from_xlsx` was reading title + group-header rows from the styled XLSX, polluting the Sheets-API fallback Sheet. New `_csv_rows_from_path` prefers a sibling CSV (matches legacy JS); xlsx fallback now skips the 2-row styling prelude. +5 tests.

**PR #17 — Helpers extraction** (this session, MERGED): consolidated 6 duplicated helpers (`coerce_str`, `parse_money`, `clamp`, `round_half_up`, `is_missing`, `atomic_write`) into `fba_engine/steps/_helpers.py`. Net -107 LOC across step files. Behavioural improvements landed alongside: `decision_engine.parse_money` now `pd.NA`-safe; `push_to_gsheets` id-file write now goes through `atomic_write` with crash cleanup. Zero test changes.

**Step 5 — YAML strategy runner** (later this session, PR open): new `fba_engine/strategies/` with `runner.py` (~310 LOC), `keepa_niche.yaml`, and 23 pytest cases. Composes ported step modules via the `run_step(df, config) -> df` contract. Variable interpolation (`{niche}`, `{base}`, etc.), `StrategyConfigError` vs `StrategyExecutionError` taxonomy, atomic CSV write via shared `atomic_write` helper, callable-checked step loading, friendly CLI error reporting. Smoke test confirmed: 3-step `keepa_niche` chain end-to-end produces 78-column output (44 input + 9 ip_risk + 14 build_output reshape + 11 decision_engine), verdict NEGOTIATE on the clean fixture row. Pre-PR code-reviewer surfaced 5 LOWs (verdict pin, atomic write, callable check, interpolation depth comment, CLI error wrapping) — all addressed before PR open.

### Prior session highlights (kept for context)

**Step 4b — Decision Engine port** (PR #10, MERGED): 1:1 port of `phase6_decision.js` (651 LOC) → 155 pytest cases.

**Step 4c.1 — Build Output merge** (PR #12, MERGED): 1:1 port of `phase5_build.js` (~330 LOC) → 60 pytest cases.

**Step 4c.2 — XLSX styling** (PR #13, MERGED): port of `build_final_xlsx.js` (529 LOC) via openpyxl → 65 pytest cases.

**Step 4c.3 — Google Sheets push** (PR #14, MERGED): Port of `push_to_gsheets.js` (311 LOC) → 37 pytest cases.

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
| 5.3 — Build Output (GSheets push) | 311 | **MERGED PR #14** (step 4c.3) |
| (cross-cutting) — Code-review followups | — | **MERGED PR #15** (13 HIGH + 1 MEDIUM defects fixed) |
| (fix) — Strategy 3 reads from CSV | — | **MERGED PR #16** (HIGH regression in #15 fixed) |
| (refactor) — `_helpers.py` extraction | — | **MERGED PR #17** (6 helpers consolidated) |
| Step 5 — YAML strategy runner | — | **PR open** (this session) — `fba_engine/strategies/runner.py` + `keepa_niche.yaml` + 23 tests |
| 3 — Scoring | 0 (SKILL.md) | **Scope decision needed**: extract a canonical scoring step, or keep agent-driven? Per-niche scripts get generated under `data/{niche}/working/`. |
| 1 — Keepa Finder | 0 (browser) | **Separate scoping**: Keepa API integration vs Playwright vs keep as Claude Code skill |
| 2 — SellerAmp | 0 (browser) | Same scoping question as Skill 1 |

**Blockers:** None remaining for steps 4 + 5 — both complete. The remaining
roadmap items all need *scoping decisions* before any implementation:

1. **Skill 3 (scoring)**: `skills/skill-3-scoring/` is just a SKILL.md — no
   ported code. Per-niche scoring scripts get generated under
   `data/{niche}/working/` by an agent. Question: extract a canonical
   `fba_engine/steps/scoring.py` (matches the rest of step 4), or leave
   agent-driven? Tradeoff: portability + testability vs flexibility per niche.

2. **Skill 1 (Keepa Finder) + Skill 2 (SellerAmp)**: both browser-based in
   the legacy. Three options each: official API integration (Keepa has one,
   SellerAmp has a paid API), Playwright headless automation, or keep as a
   Claude Code skill that the agent invokes via the browser. Decision drives
   whether they get ports at all.

3. **`supplier_pricelist` strategy YAML**: the canonical engine at
   `shared/lib/python/sourcing_engine/` doesn't yet expose a
   `run_step(df, config) -> df` contract — it's a top-down `main.py`
   orchestrator. Refactoring it into composable steps so it can also be
   expressed as a strategy YAML is a meaningful project but unblocked by
   step 5 landing. Lower priority than the scoping decisions above.

**Open low-priority polish (not blocking):**
- Resumable upload progress logging dropped in PR #15 (`_status` discarded)
- Title clamp at 200 chars vs Sheets API's actual 100-char limit
- Strategy 2 silently falls through on auth failures (pre-existing, JS-faithful)
- Dead `last_err` defensive branch in `_retry_with_backoff`

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
