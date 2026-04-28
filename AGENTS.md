# AGENTS.md — Amazon FBA Sourcing System

This file describes how AI agents should operate within this workspace.

> **Step 1 update (2026-04-28):** Centralised config and ROI-based decision gate.
> All thresholds now live in `shared/config/`. The supplier decision engine uses
> ROI (`profit/buy_cost`) as the SHORTLIST gate, replacing the previous margin
> gate. See section 3 below.

---

## Workspace Structure

This workspace contains two projects plus shared config. Each project has its own `CLAUDE.md` with project-specific context. Always read the relevant `CLAUDE.md` before working in a project.

```
fba/
├── CLAUDE.md                              ← Root overview
├── AGENTS.md                              ← This file (agent behaviour rules)
├── shared/                                ← Single source of truth (added in step 1)
│   ├── config/
│   │   ├── business_rules.yaml            ← VAT, marketplace, price range
│   │   └── decision_thresholds.yaml       ← TARGET_ROI and derived gates
│   ├── niches/                            ← Per-niche YAML configs
│   └── lib/python/
│       ├── fba_config_loader.py           ← Reads YAMLs; legacy constant aliases
│       └── fba_roi_gate.py                ← ROI-based decision gate
├── keepa_niche_finder/                    ← Node.js niche research pipeline
│   ├── CLAUDE.md
│   ├── skills/                            ← 6 pipeline skills (phases 1-6)
│   ├── config/niche-configs/              ← Will move to shared/niches/ in step 3
│   ├── data/{niche}/                      ← Output per niche
│   └── scripts/
└── supplier_pricelist_finder/             ← Python supplier analysis pipeline
    ├── CLAUDE.md
    ├── PRD_Amazon_FBA_Sourcing_Engine_v5.md  ← Historical reference (not authoritative)
    └── pricelists/{supplier}/
        ├── sourcing_engine/               ← Engine code; config.py is now a shim
        ├── raw/
        └── results/
```

---

## Agent Rules

### 1. Read Before You Act

- Read the project's `CLAUDE.md` fully before touching any code.
- For the supplier pipeline, business logic is encoded in the code itself; the v5 PRD/BUILD_PROMPT documents are historical and may be inaccurate.
- For the niche finder, read the relevant `SKILL.md` for the phase you are running.
- Threshold values: read `shared/config/decision_thresholds.yaml`. Never trust inline values in code or docs.

### 2. Path Handling

- **Never hardcode absolute paths.** All paths must be relative to the project root or resolved via `__dirname` / `path.resolve` / `Path(__file__)`.
- JS scripts use `path.resolve(__dirname, '..', '..', ...)`.
- Python scripts accept `--input` and `--output` CLI arguments.
- SKILL.md files reference paths as `./data/{niche}/...` (relative to project root).

### 3. Accuracy Is Non-Negotiable

This system handles real money. Conservative assumptions always win over optimistic ones.

- **Never use `lowest_fba_price` as the sell price.** Use Buy Box price.
- **Never strip VAT from the Amazon sell price.** Seller is not VAT registered.
- **Never use `floored_conservative_price` in profit calculations.** Use `raw_conservative_price`.
- **Never mix FBA and FBM fee paths.** They are calculated separately.
- **Never crash on a single bad row.** Log the error, flag as REVIEW, continue processing.
- **Decision gate is ROI-based, not margin-based.** SHORTLIST requires `roi_conservative >= TARGET_ROI` (currently 30%) AND `profit_conservative >= MIN_PROFIT_ABSOLUTE` (currently £2.50). Margin is computed and shown in output for human reference but no longer gates decisions. See `shared/lib/python/fba_roi_gate.py`.
- **Never hardcode a threshold value.** Import from `sourcing_engine.config` (which now reads from `shared/config/`). If the value isn't in YAML yet, add it to YAML rather than hardcoding.

### 4. Pipeline Execution

**Keepa Niche Finder** runs phases sequentially:
```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6
```

Each phase reads the previous phase's output CSV from `data/{niche}/working/` and writes its own output there. Phase 5 produces the final `.xlsx` deliverable.

**Supplier Pricelist Finder** runs as a single pipeline:
```
python -m sourcing_engine.main --input ./raw/ --output ./results/ --market-data ./raw/keepa_<supplier>.csv
```

### 5. Testing (Supplier Pipeline)

The full test suite must pass before any code change is deployed. Counts vary slightly by supplier (per-supplier ingest/normalise tests differ):

```bash
cd supplier_pricelist_finder/pricelists/<supplier>
pytest sourcing_engine/tests/ -v --tb=short
```

Baseline counts as of 2026-04-28: abgee 35, connect-beauty 38, shure 32 passing + 3 pre-existing failures (test_ingest format mismatches, addressed in step 2 of the reorg), zappies 32 + 3 same.

Critical tests that must never be broken:
- `test_profit_uses_raw_conservative_not_floored`
- `test_price_floor_hit_blocks_shortlist`
- `test_fbm_can_shortlist`
- `test_fbm_fee_path_no_fba_fee`
- `test_fba_fee_path_no_shipping_cost`
- `test_case_qty_1_no_duplicate_row`
- `test_vat_unclear_blocks_shortlist`
- `test_gated_y_shortlists_with_indicator`
- `test_single_supplier_row_produces_two_output_rows_when_both_match`

The shared library has its own test suite that must also pass:
```bash
cd shared/lib/python
pytest tests/ -v
```

### 6. Output Files

**Keepa Niche Finder** produces per niche:
- `{niche}_final_results.xlsx` — primary deliverable (styled Excel)
- `{niche}_phase6_shortlist.xlsx` — BUY + NEGOTIATE only
- `working/` folder — all intermediate CSVs for audit trail

**Supplier Pipeline** produces per run:
- `shortlist_<timestamp>.csv` — all rows, all decisions, full schema
- `shortlist_<timestamp>.xlsx` — colour-coded (green/amber/red)
- `report_<timestamp>.md` — human-readable summary

The CSV schema includes `roi_current` and `roi_conservative` alongside `margin_current` and `margin_conservative` (added in step 1).

### 7. Configuration

**All thresholds:** `shared/config/decision_thresholds.yaml`. Single tunable knob is `target_roi`. See `shared/lib/python/fba_config_loader.py` for the loader.

**Cross-pipeline business rules:** `shared/config/business_rules.yaml` (VAT, marketplace, currency, price range).

**Per-niche filters:** `shared/niches/{niche}.yaml` (added in step 1; the `keepa_niche_finder/config/niche-configs/*.md` originals remain in place until step 3).

**Supplier pipeline:** `sourcing_engine/config.py` is now a shim that re-exports from `fba_config_loader`. Never add new constants to the shim — add to YAML.

### 8. When Multiple Agents Run in Parallel

The niche finder supports running 5 niches simultaneously in separate terminals. Each niche operates on its own `data/{niche}/` directory, so there are no file conflicts. Agents should:
- Stay within their assigned niche's data directory
- Not modify shared files (`exclusions.csv`, skills, config) without coordination
- Write handoff files after each phase so the next phase (or agent) knows where to pick up

---

## Verdict Reference

### Keepa Niche Finder Verdicts
| Verdict | Meaning |
|---------|---------|
| YES | Composite 8.5+, all filters pass |
| MAYBE | Composite 7-8.4, one concern |
| MAYBE-ROI | ROI below `target_roi` |
| BRAND APPROACH | 2-3 sellers, weak listing, contact brand |
| BUY THE DIP | Price 30%+ below 90-day avg |
| PRICE EROSION | Consistent downward slope |
| GATED | Restricted listing |
| HAZMAT | Confirmed hazmat |
| NO | Fails filters |

### Supplier Pipeline Decisions
| Decision | Meaning |
|----------|---------|
| SHORTLIST | Profitable at conservative price (ROI ≥ TARGET_ROI, profit ≥ MIN_PROFIT_ABSOLUTE) — act on this. Gated rows reach SHORTLIST with a "GATED" indicator. |
| REVIEW | Profitable but flagged — needs human eyes (e.g. AMAZON_ON_LISTING, INSUFFICIENT_HISTORY, low ROI but acceptable margin) |
| REJECT | Hard block: invalid EAN, no Amazon match, sales below floor, or unprofitable at both current and conservative prices |

### Decision Engine Verdicts (Phase 6)
| Verdict | Action |
|---------|--------|
| BUY | Purchase now at current terms |
| NEGOTIATE | Pursue but negotiate better price |
| WATCH | Monitor for price recovery or better terms |
| KILL | Do not pursue |
