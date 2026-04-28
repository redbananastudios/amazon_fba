# orchestration/

This directory is the layer **above** the FBA engine — Cowork-facing.

The engine knows how to run a single strategy against a single supplier or
niche. Orchestration knows how to compose multiple strategy runs into a
recurring workflow: daily pipeline executions, cross-pipeline integration,
notification dispatch, run history.

---

## Today

This directory is largely a placeholder. The real content arrives in step 6
(new strategies including Skill 99) and beyond.

What exists today:
- This `CLAUDE.md` describing the intent
- `runs/` — empty placeholder for run definitions

What does NOT yet exist:
- Daily run scheduler
- Cross-strategy integration (e.g. "run keepa_niche, take the shortlist, search
  for matching supplier feeds")
- Notification dispatch (Slack, email)
- Run history with comparison against prior runs

---

## How orchestration relates to the engine

```
Cowork                     orchestration/                  fba_engine/
─────                      ───────────────                 ───────────
"run today's daily"   →    daily_runner.py invokes  →     python run.py --supplier ...
                           1. supplier_pricelist             (one process per strategy)
                              for each supplier
                           2. keepa_niche
                              for each niche
                           3. (step 6+) skill_99
                              cross-references
                           4. assembles report
                           5. dispatches notification
```

The engine produces deterministic outputs (CSVs, XLSX, MD). Orchestration
composes those outputs into actions and reports.

---

## Boundary

**What goes in `orchestration/`:**
- Run definitions (what runs when)
- Cross-strategy logic (joining outputs from multiple runs)
- Notification dispatch
- Aggregated reports across runs

**What goes in `fba_engine/`:**
- All pipeline logic
- Strategy compositions (which steps run in what order)
- Step implementations

If a piece of code is needed by multiple strategies, it belongs in
`fba_engine/steps/`, not here. If it's needed by multiple Cowork-driven
workflows, it can live here.
