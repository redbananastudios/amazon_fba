# AGENTS.md — Amazon FBA Sourcing System

This file describes how AI agents should operate within this workspace.

---

## Workspace Structure

This workspace contains two independent projects. Each has its own `CLAUDE.md` with project-specific context. Always read the relevant `CLAUDE.md` before working in a project.

```
fba/
├── CLAUDE.md                              ← You are here (root overview)
├── AGENTS.md                              ← This file (agent behaviour rules)
├── keepa_niche_finder/                    ← Node.js niche research pipeline
│   ├── CLAUDE.md                          ← Project context + credentials
│   ├── skills/                            ← 6 pipeline skills (phases 1-6)
│   ├── config/niche-configs/              ← Per-niche filter parameters
│   ├── data/{niche}/                      ← Output per niche
│   └── scripts/                           ← Standalone processing scripts
└── supplier_pricelist_finder/             ← Python supplier analysis pipeline
    ├── CLAUDE.md                          ← Domain rules + configuration
    ├── PRD_Amazon_FBA_Sourcing_Engine_v5.md  ← Business logic (source of truth)
    ├── pricelists/{supplier}/             ← Per-supplier folders
    │   ├── sourcing_engine/               ← Python pipeline code
    │   ├── raw/                           ← Drop supplier price lists here
    │   └── results/                       ← Pipeline output
    └── skills/                            ← Shared skills
```

---

## Agent Rules

### 1. Read Before You Act

- Read the project's `CLAUDE.md` fully before touching any code.
- For the supplier pipeline, `PRD_Amazon_FBA_Sourcing_Engine_v5.md` is the source of truth for business logic.
- For the niche finder, read the relevant `SKILL.md` for the phase you are running.

### 2. Path Handling

- **Never hardcode absolute paths.** All paths must be relative to the project root or resolved via `__dirname` / `path.resolve`.
- JS scripts use `path.resolve(__dirname, '..', '..', ...)` to navigate from their location to project directories.
- Python scripts accept `--input` and `--output` CLI arguments.
- SKILL.md files reference paths as `./data/{niche}/...` (relative to project root).

### 3. Accuracy Is Non-Negotiable

This system handles real money. Conservative assumptions always win over optimistic ones.

- **Never use `lowest_fba_price` as the sell price.** Use Buy Box price.
- **Never strip VAT from the Amazon sell price.** Seller is not VAT registered.
- **Never use `floored_conservative_price` in profit calculations.** Use `raw_conservative_price`.
- **Never mix FBA and FBM fee paths.** They are calculated separately.
- **Never crash on a single bad row.** Log the error, flag as REVIEW, continue processing.

### 4. Pipeline Execution

**Keepa Niche Finder** runs phases sequentially:
```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6
```

Each phase reads the previous phase's output CSV from `data/{niche}/working/` and writes its own output there. Phase 5 produces the final `.xlsx` deliverable.

**Supplier Pricelist Finder** runs as a single pipeline:
```
python -m sourcing_engine.main --input ./raw/ --output ./results/
```

### 5. Testing (Supplier Pipeline)

22 tests must pass before any code change is deployed:
```bash
cd supplier_pricelist_finder/pricelists/abgee
pytest tests/ -v --tb=short
```

Critical tests that must never be broken:
- `test_profit_uses_raw_conservative_not_floored`
- `test_price_floor_hit_blocks_shortlist`
- `test_fbm_can_shortlist`
- `test_fbm_fee_path_no_fba_fee`
- `test_fba_fee_path_no_shipping_cost`
- `test_case_qty_1_no_duplicate_row`
- `test_vat_unclear_blocks_shortlist`
- `test_gated_y_rejects`

### 6. Output Files

**Keepa Niche Finder** produces per niche:
- `{niche}_final_results.xlsx` — primary deliverable (styled Excel)
- `{niche}_phase6_shortlist.xlsx` — BUY + NEGOTIATE only
- `working/` folder — all intermediate CSVs for audit trail

**Supplier Pipeline** produces per run:
- `shortlist_<timestamp>.csv` — all rows, all decisions
- `shortlist_<timestamp>.xlsx` — colour-coded (green/amber/red)
- `report_<timestamp>.md` — human-readable summary

### 7. Configuration

**Keepa Niche Finder:** Global rules are in `CLAUDE.md`. Per-niche overrides are in `config/niche-configs/{niche}.md`.

**Supplier Pipeline:** All thresholds are in `sourcing_engine/config.py`. Never hardcode a number in pipeline logic — use the config constant.

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
| MAYBE-ROI | ROI below 20% estimated |
| BRAND APPROACH | 2-3 sellers, weak listing, contact brand |
| BUY THE DIP | Price 30%+ below 90-day avg |
| PRICE EROSION | Consistent downward slope |
| GATED | Restricted listing |
| HAZMAT | Confirmed hazmat |
| NO | Fails filters |

### Supplier Pipeline Decisions
| Decision | Meaning |
|----------|---------|
| SHORTLIST | Profitable at conservative price — act on this |
| REVIEW | Potentially profitable but flagged — needs human eyes |
| REJECT | Below thresholds or hard block (gated, invalid EAN, no match) |

### Decision Engine Verdicts (Phase 6)
| Verdict | Action |
|---------|--------|
| BUY | Purchase now at current terms |
| NEGOTIATE | Pursue but negotiate better price |
| WATCH | Monitor for price recovery or better terms |
| KILL | Do not pursue |
