# FBA Engine — Architecture

This document describes the system shape: how the engine, strategies, and
shared services fit together. For the business logic the engine implements,
see `docs/SPEC.md`.

---

## Top-level layout

```
amazon_fba/
├── README.md                # human-facing
├── CLAUDE.md                # agent quick-start
├── AGENTS.md                # agent behaviour rules
├── run.py                   # launcher — sets PYTHONPATH and forwards to engine
│
├── docs/                    # this directory
│   ├── SPEC.md              # business logic source of truth
│   ├── architecture.md      # this file
│   ├── strategies/          # per-strategy documentation
│   └── archive/             # historical: superseded PRDs, improvement plans
│
├── shared/                  # cross-engine concerns
│   ├── config/              # YAML configs (single source of truth)
│   ├── niches/              # per-niche Keepa filter configs
│   └── lib/python/          # importable Python libraries
│       ├── fba_config_loader.py
│       ├── fba_roi_gate.py
│       └── sourcing_engine/ # the canonical engine
│
├── fba_engine/              # the engine and its callers
│   ├── adapters/            # per-supplier ingest+normalise
│   │   ├── _template/       # template for new suppliers
│   │   ├── abgee/
│   │   ├── connect-beauty/
│   │   ├── shure/
│   │   └── zappies/
│   ├── data/                # runtime inputs/outputs (gitignored except exclusions.csv)
│   │   ├── pricelists/      # supplier raw + results
│   │   └── niches/          # niche-finder working data
│   ├── _legacy_keepa/       # TEMPORARY — Keepa pipeline, dismantled in step 4
│   ├── steps/               # built in step 4: ordered, composable pipeline steps
│   ├── strategies/          # built in step 5: strategy YAMLs (compositions)
│   └── tests/
│
├── services/                # infrastructure called by the engine
│   └── amazon-fba-fees-mcp/ # SP-API MCP server
│
└── orchestration/           # cross-pipeline glue (Cowork-facing)
    ├── CLAUDE.md
    └── runs/                # run definitions, schedules
```

---

## Why each top-level directory exists

- **`shared/`** — anything used by more than one part of the system: configs,
  reusable Python libraries, the canonical sourcing engine. The defining
  property is "no specific part of the system owns this; everyone reads it."

- **`fba_engine/`** — the engine itself plus its data and strategy
  compositions. Singular: there is one engine, expressed as ordered steps
  composed by named strategies. Currently houses the supplier adapters and
  per-supplier data; will house steps + strategies after step 4-5.

- **`services/`** — things the engine calls but doesn't own. The SP-API MCP
  is a service: independently versioned, independently deployable, called
  via a defined interface (MCP protocol).

- **`orchestration/`** — the layer above the engine. When Cowork runs a
  daily report, it does so via orchestration definitions, not by invoking
  the engine directly. This is where schedule definitions, run manifests,
  and cross-strategy logic will live.

- **`docs/`** — the contract. SPEC.md is what the engine should do;
  architecture.md (this file) is how it's organised; strategies/ documents
  individual compositions.

---

## How a run flows

For the **`supplier_pricelist`** strategy (today's default reseller workflow):

```
1. User invokes: python run.py --supplier abgee
2. run.py adds shared/lib/python/ to PYTHONPATH
3. sourcing_engine.main is invoked with --supplier=abgee
4. main.py loads the abgee adapter from fba_engine/adapters/abgee/
   (via sourcing_engine.adapters.loader.load_supplier_adapter)
5. Adapter's ingest_directory() reads files from fba_engine/data/pricelists/abgee/raw/
6. Adapter's normalise() maps to canonical schema
7. Engine pipeline runs: case_detection → match → enrichment → fees →
   conservative price → profit → ROI gate → decision
8. Output writers produce CSV/XLSX/MD in fba_engine/data/pricelists/abgee/results/<ts>/
```

For the **`keepa_niche`** strategy (Amazon-listing-first discovery), the
pipeline today still lives in `fba_engine/_legacy_keepa/` as a Node.js
implementation. Step 4 of the reorganisation extracts it into Python steps
that compose with the same engine.

---

## Single source of truth principles

These three principles underpin the architecture:

1. **One engine.** No duplicated pipeline logic. The supplier sourcing engine
   was 4× duplicated before step 2; now it's one canonical copy at
   `shared/lib/python/sourcing_engine/`.

2. **One config.** All thresholds in `shared/config/`, loaded via
   `fba_config_loader`. No inline values in code or docs. The single tunable
   knob is `target_roi`.

3. **One spec.** `docs/SPEC.md` is the business logic source of truth.
   Code disagreements with the spec are bugs to be fixed, not tolerated.

---

## Adapters explained

A "supplier adapter" is the only per-supplier code that should ever exist:

- `adapters/{supplier}/ingest.py` — reads supplier-specific file formats
  (PDF for abgee, CSV for shure, etc.)
- `adapters/{supplier}/normalise.py` — maps supplier-specific column names
  to the canonical schema, applies VAT resolution

Adding supplier #5 requires:

1. Create `fba_engine/adapters/<new-supplier>/` with `ingest.py` + `normalise.py`
2. Create `fba_engine/data/pricelists/<new-supplier>/{raw,results}/`
3. Run `python run.py --supplier <new-supplier>`

No engine changes. No config changes. No new tests required (though writing
adapter-level tests is recommended).

---

## What this is NOT

- **Not an ML system.** Decisions are deterministic from data. AI is used
  in agents at the orchestration layer (image matching, anomaly investigation,
  outreach drafting), never in pricing or decisions.
- **Not multi-tenant.** Single seller, single Amazon marketplace.
- **Not real-time.** Batch-oriented; daily runs are the typical cadence.
- **Not a web application.** Console-driven engine; orchestration via Cowork.
