# AGENTS.md — Amazon FBA Sourcing System

This file describes how AI agents should operate within this workspace.

> **Step 3 update (2026-04-28):** Repo restructured. One engine, named
> strategies, ordered steps. The two former pipelines no longer exist as
> separate top-level trees. See `docs/architecture.md`.

---

## Workspace Structure

```
amazon_fba/
├── README.md                              ← Human-facing overview
├── CLAUDE.md                              ← Agent quick-start
├── AGENTS.md                              ← This file (agent behaviour rules)
├── run.py                                 ← Launcher
│
├── docs/
│   ├── SPEC.md                            ← Business logic source of truth
│   ├── architecture.md                    ← Repo layout and conventions
│   ├── strategies/                        ← Per-strategy docs
│   └── archive/                           ← Historical (legacy PRDs, etc.)
│
├── shared/                                ← Cross-engine concerns
│   ├── config/
│   │   ├── business_rules.yaml            ← VAT, marketplace, price range
│   │   └── decision_thresholds.yaml       ← TARGET_ROI + derived gates
│   ├── niches/                            ← Per-niche YAML (kids-toys, etc.)
│   └── lib/python/
│       ├── fba_config_loader.py
│       ├── fba_roi_gate.py
│       └── sourcing_engine/               ← Canonical engine (single copy)
│
├── fba_engine/
│   ├── adapters/                          ← Per-supplier ingest+normalise
│   │   ├── abgee/
│   │   ├── connect-beauty/
│   │   ├── shure/
│   │   └── zappies/
│   ├── data/                              ← Gitignored (pricelists, niche outputs)
│   ├── _legacy_keepa/                     ← TEMPORARY — Keepa pipeline
│   └── tests/
│
├── services/
│   └── amazon-fba-fees-mcp/               ← SP-API MCP server
│
└── orchestration/                         ← Cowork-facing
```

---

## Agent Rules

### 1. Read Before You Act

Read in this order before any work:
1. `docs/SPEC.md` — business logic source of truth
2. `docs/architecture.md` — how the system is laid out
3. The relevant `docs/strategies/<strategy>.md` if working on a specific strategy

The v5 PRD/BUILD_PROMPT in `docs/archive/` are kept for context only — they
are NOT authoritative. The current authoritative spec is `docs/SPEC.md`.

### 2. Path Handling

- **Never hardcode absolute paths.** All paths must be relative to repo root or resolved via `__dirname` / `path.resolve` / `Path(__file__)`.
- Python scripts accept `--input` and `--output` CLI arguments.
- Default paths follow the convention `fba_engine/data/pricelists/<supplier>/{raw,results}/`.

### 3. Accuracy Is Non-Negotiable

This system handles real money. Conservative assumptions always win over optimistic ones.

- **Never use `lowest_fba_price` alone as the sell price.** Use `min(buy_box_price, lowest_fba_price)` when both > 0.
- **Never strip VAT from the Amazon sell price.** Seller is not VAT registered.
- **Never use `floored_conservative_price` in profit, ROI, or decision logic.** Use `raw_conservative_price`.
- **Never mix FBA and FBM fee paths.** They are calculated separately.
- **Never crash on a single bad row.** Log the error, flag as REVIEW, continue processing.
- **Decision gate is ROI-based, not margin-based.** SHORTLIST requires `roi_conservative >= TARGET_ROI` AND `profit_conservative >= MIN_PROFIT_ABSOLUTE`. See `shared/lib/python/fba_roi_gate.py`.
- **Never hardcode a threshold.** Add to `shared/config/*.yaml` and import via `fba_config_loader`.

### 4. Strategy Execution

**`supplier_pricelist`** — supplier-feed-first reseller sourcing:
```bash
python run.py --supplier <supplier-name>
# Defaults to fba_engine/data/pricelists/<supplier>/{raw,results}/
```

**`keepa_niche`** — Amazon-listing-first niche discovery (legacy phases until step 4):
The phases live in `fba_engine/_legacy_keepa/` and run via Claude Code skill invocations until step 4 ports them to Python.

### 5. Testing

The full test suite must pass before any code change is deployed.

```bash
# Shared library + canonical engine (68 tests total)
cd shared/lib/python && pytest tests/ sourcing_engine/tests/ -v

# Pipeline steps (run from repo root for fba_engine package import)
pytest fba_engine/steps/tests/

# Per-supplier adapter tests (run from supplier data folder so relative paths resolve)
for s in abgee connect-beauty shure zappies; do
  cd fba_engine/data/pricelists/$s
  pytest ../../../adapters/$s/tests/
  cd ../../../..
done
```

Baseline counts as of step 4b:

| Suite | Pass | Fail | Notes |
|---|---|---|---|
| shared lib (config_loader, roi_gate) | 26 | 0 | clean |
| canonical engine | 42 | 0 | clean (was 23 pre-MCP; +19 preflight tests) |
| pipeline steps (`fba_engine/steps/`) | 421 | 0 | 4a IP risk (71) + 4b decision (157) + 4c.1 build output (65) + 4c.2 build xlsx (71) + 4c.3 push gsheets (57) |
| strategies (`fba_engine/strategies/`) | 23 | 0 | YAML strategy runner (step 5) — interpolation + chain execution + end-to-end keepa_niche |
| abgee adapter | 12 | 0 | clean |
| connect-beauty adapter | 15 | 0 | clean |
| shure adapter | 9 | 3 | pre-existing — `test_ingest.py` expects abgee PDF format |
| zappies adapter | 9 | 3 | pre-existing — same as shure |
| MCP server (vitest) | 110 | 0 | clean — `services/amazon-fba-fees-mcp/`, `npm test` |
| MCP server (live SP-API) | 5 | 0 | `npm run test:integration` — auto-skipped without creds |

The 6 pre-existing failures are NOT regressions; they exist because two
suppliers' adapter tests were copy-pasted from abgee but never adapted to
their actual file formats. Fixing them requires writing real tests, not part
of the structural reorganisation.

Critical tests that must never be broken:
- `test_profit_uses_raw_conservative_not_floored`
- `test_price_floor_hit_blocks_shortlist`
- `test_fbm_can_shortlist`
- `test_fbm_fee_path_no_fba_fee`
- `test_fba_fee_path_no_shipping_cost`
- `test_case_qty_1_no_duplicate_row`
- `test_vat_unclear_blocks_shortlist`
- `test_gated_y_shortlists_with_indicator`

### 6. Output Files

For `supplier_pricelist`, produced per run in `fba_engine/data/pricelists/<supplier>/results/<timestamp>/`:
- `shortlist_<ts>.csv` — all rows, all decisions, full schema
- `shortlist_<ts>.xlsx` — colour-coded SHORTLIST + REVIEW
- `report_<ts>.md` — per-supplier markdown summary

The CSV schema includes `roi_current` and `roi_conservative` alongside `margin_current` and `margin_conservative`.

### 7. Configuration

**Single source of truth:** `shared/config/`

- All thresholds → `decision_thresholds.yaml` (single tunable: `target_roi`)
- Cross-pipeline business rules → `business_rules.yaml`
- Per-niche filters → `shared/niches/<niche>.yaml`

Never hardcode a threshold in pipeline logic. Add to YAML and import via `fba_config_loader`.

### 8. Adding a New Supplier

1. Create `fba_engine/adapters/<new-supplier>/` (start from another supplier's adapter as a template)
2. Implement `ingest.py` (parses files into a DataFrame) and `normalise.py` (maps columns to canonical schema)
3. Create `fba_engine/data/pricelists/<new-supplier>/{raw,results}/`
4. Run `python run.py --supplier <new-supplier>`

No engine changes required.

### 9. When Multiple Agents Run in Parallel

Each agent operates on its own supplier's data folder, so there are no file conflicts. Agents should:
- Stay within their assigned supplier's data directory
- Not modify shared files (`shared/config/`, `shared/niches/`, the canonical engine) without coordination
- Write handoff files where appropriate so the next phase or agent knows where to pick up

### 10. MCP Preflight Annotation (informational)

The supplier pipeline auto-calls `services/amazon-fba-fees-mcp/dist/cli.js` after
the match loop to annotate each row with SP-API-derived informational columns:
restriction status, FBA eligibility, live Buy Box, catalog brand, etc.

**Auto-on requirements:**
- `services/amazon-fba-fees-mcp/dist/cli.js` exists (run `npm run build` in that folder)
- `node` is on `PATH`
- `SP_API_CLIENT_ID` is set (creds live in `~/.claude/settings.json` env block)

If any of these are missing, the step **skips silently** (logs "preflight: skipping (...)")
and the rows get the new columns set to `None`. Existing pipeline behaviour is
unchanged. Opt out explicitly with `python run.py --supplier <s> --no-preflight`.

**The preflight is INFORMATIONAL ONLY. Decision logic is unchanged.**

- `decision.py` does NOT consider any preflight column. SHORTLIST/REVIEW/REJECT
  counts are identical with and without preflight running.
- A SHORTLIST item that is `BRAND_GATED` or `RESTRICTED` still SHORTLISTs. The
  user decides whether to apply for ungating; the engine doesn't auto-reject.
- The markdown report adds a "🚫 Restriction notes" section listing
  SHORTLIST rows with non-`UNRESTRICTED` status, so they surface visibly.

**New columns appended to CSV/Excel/output_rows** (all default to `None` if
the preflight didn't run or the source erred):

| Column | Source |
|---|---|
| `restriction_status` | UNRESTRICTED / RESTRICTED / BRAND_GATED / CATEGORY_GATED |
| `restriction_reasons` | comma-joined reason codes |
| `fba_eligible` | True / False |
| `fba_ineligibility` | comma-joined ineligibility codes |
| `live_buy_box` | real-time Buy Box landed price (GBP) |
| `live_buy_box_seller` | "FBA" / "FBM" |
| `live_offer_count_new` | total new-condition offers |
| `live_offer_count_fba` | FBA offers (subset) |
| `catalog_brand` | SP-API canonical brand (per spec, wins over Keepa brand) |
| `keepa_brand` | original Keepa brand string (for diff tracking) |
| `catalog_hazmat` | True / None |
| `preflight_errors` | comma-joined per-source error messages |

**Disk cache:** lives at `<repo>/.cache/fba-mcp/` (gitignored). TTLs default to
restrictions 7d, FBA 7d, catalog 30d, fees 24h, pricing 5min. Override via
`MCP_CACHE_TTL_*_S` env vars. Wipe with `rm -rf .cache/fba-mcp/`.

**Direct CLI access** (for ad-hoc debugging or other tools):

```bash
node services/amazon-fba-fees-mcp/dist/cli.js preflight --input -
node services/amazon-fba-fees-mcp/dist/cli.js restrictions --asins B001,B002
node services/amazon-fba-fees-mcp/dist/cli.js --help
```

---

## Verdict Reference

### Supplier Pipeline Decisions
| Decision | Meaning |
|----------|---------|
| SHORTLIST | Profitable at conservative price (ROI ≥ TARGET_ROI, profit ≥ MIN_PROFIT_ABSOLUTE) — act on this. Gated rows reach SHORTLIST with a "GATED" indicator. |
| REVIEW | Profitable but flagged — needs human eyes (e.g. AMAZON_ON_LISTING, INSUFFICIENT_HISTORY, low sales 10-19) |
| REJECT | Hard block: invalid EAN, no Amazon match, sales below floor, or unprofitable at both current and conservative prices |

### Keepa Niche Finder Verdicts (legacy until step 4)
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

### Decision Engine Verdicts (Phase 6 / `_legacy_keepa`)
| Verdict | Action |
|---------|--------|
| BUY | Purchase now at current terms |
| NEGOTIATE | Pursue but negotiate better price |
| WATCH | Monitor for price recovery or better terms |
| KILL | Do not pursue |
