# PRD: Store Stalking, OA, and Supplier Leads — Engine Expansion

**Status:** Ready for implementation
**Author:** Peter Farrell (with Claude)
**Target branch:** `feat/sourcing-strategies` (off `main`, after dependencies land)
**Authoritative spec it builds on:** `docs/SPEC.md`
**Architecture it conforms to:** `docs/architecture.md`
**Supersedes:** the standalone "FBA Sourcing Tool" briefs (those assumed a parallel C# app — wrong shape; this PRD is the corrected version)

---

## 1. Objective

Add two new sourcing strategies to the existing FBA engine and close the parked Skill 99 (supplier lead generation) gap. End-state: the engine can produce ranked, decision-gated shortlists from supplier feeds (existing), Amazon-listing niche searches (existing), **store-stalked seller catalogues (new)**, and **online-arbitrage CSV feeds (new)** — and for any shortlisted ASIN, surface the supplier search vectors needed to source it.

The two new strategies share the same `02_resolve` → `06_output` pipeline as everything else. Only the discovery step differs. The supplier-lead generator is a new post-decision step usable by all strategies.

---

## 2. Out of scope

- New decision logic. The existing SHORTLIST/REVIEW/REJECT gates in `docs/SPEC.md §3` apply unchanged.
- Auto-rejecting on gating. Per SPEC §3.2 and the SP-API SPEC §Non-goals: gated rows reach SHORTLIST with a `GATED` indicator. This PRD does not change that.
- New decision thresholds. `target_roi` remains the single tunable knob.
- Multi-marketplace (UK only).
- Multi-user / multi-tenant.
- Web UI.
- Direct supplier portal API integration (Faire, Ankorstore). v2.
- A central SQLite database. The engine remains file-output-driven; per-resource disk caches live where they already live (Keepa: new — see §7; SP-API: per the merged MCP SPEC at `~/.cache/fba-mcp/` or `<repo>/.cache/fba-mcp/`).

---

## 3. Dependencies

These must be merged before this PRD's work can be implemented end-to-end. They can be developed in parallel with stubs.

| Dependency | Status | Why this PRD needs it |
|---|---|---|
| Reorg step 4 — Keepa pipeline ported to Python steps in `fba_engine/steps/` | In progress | This PRD adds new steps to that catalogue; it requires the catalogue to exist |
| Reorg step 5 — Strategies as YAMLs in `fba_engine/strategies/` | Pending | New strategies are YAML compositions; needs the YAML loader |
| `feat/mcp-sourcing-tools` branch — SP-API MCP expansion (Q1–Q6 signed off) | Signed off, awaiting impl | Provides `preflight_asin`, eligibility, restrictions, batch fees, live pricing — used in `03_enrich` for both new strategies |

If step 4/5 slip, this PRD's Phase 1 work (the new discovery steps) can still be built and unit-tested standalone. Strategy YAMLs are deferred until step 5 lands.

---

## 4. Architecture summary

```
NEW DISCOVERY STEPS (fba_engine/steps/01_discover/):
  seller_storefront.py    — Keepa Seller Lookup → ASIN list
  oa_csv.py               — CSV feed import → ASIN list (importer abstraction)

NEW POST-DECISION STEP (fba_engine/steps/07_supplier_leads/):
  google_search.py        — generates search URL bundle per shortlisted ASIN

NEW STRATEGIES (fba_engine/strategies/):
  seller_storefront.yaml  — store-stalking wholesale flow
  oa_csv.yaml             — online arbitrage flow

NEW SHARED INFRASTRUCTURE (shared/lib/python/):
  keepa_client/           — typed Keepa client with token bucket + disk cache + usage log
  cli/launch.py           — browser launch helpers (open Keepa / Amazon / supplier search)

CONFIG ADDITIONS (shared/config/):
  keepa_client.yaml       — rate limits, cache TTLs, token-tier knobs
  strategies/seller_storefront.yaml  — strategy-specific overrides if any
  strategies/oa_csv.yaml             — strategy-specific overrides if any
```

Both new strategies feed the existing `02_resolve → 03_enrich → 04_calculate → 05_decide → 06_output → 07_supplier_leads` chain. Skill 99 (07_supplier_leads) runs for all strategies, not just the new ones.

---

## 5. Strategy A — `seller_storefront` (wholesale store stalking)

### 5.1 Purpose

Given a competitor's seller storefront URL (or Amazon seller ID), produce the ranked SHORTLIST of products from that catalogue we could profitably sell.

### 5.2 Discovery step: `seller_storefront.py`

**Input (CLI):**

```bash
python run.py --strategy seller_storefront --seller A1B2C3D4E5
python run.py --strategy seller_storefront --storefront-url "https://www.amazon.co.uk/sp?ie=UTF8&seller=A1B2C3D4E5"
```

**Behaviour:**

1. If `--storefront-url` provided: extract `seller` query parameter (or `me=`). Reject if not parseable — do not fall back to scraping.
2. Call Keepa `/seller` endpoint with `storefront=true` (Keepa's Seller Lookup) — returns full ASIN list for that seller.
3. Filter ASINs against the existing `data/exclusions.csv` (seen-and-rejected pre-list).
4. Cache the seller→ASIN mapping with a 7-day TTL keyed by `seller_id`.
5. Emit a canonical list of `{asin, source: "seller_storefront", seller_id}` records to feed `02_resolve`.

**Out of scope for this step:** scraping Amazon directly. If Keepa doesn't return a seller's catalogue, mark the run failed with a clear message; don't fall back.

**Open question (for impl):** Keepa's Seller endpoint cost varies — confirm token cost per call against the live API and document in code. Estimated 50 tokens per `storefront=true` call for an established seller.

### 5.3 Strategy YAML: `seller_storefront.yaml`

```yaml
name: seller_storefront
description: |
  Wholesale store stalking. Take a competitor seller's full catalogue and
  rank for our own profitability. New ASINs found here go through standard
  resolve → enrich → decide pipeline.

steps:
  - id: 01_discover
    impl: steps.01_discover.seller_storefront
  - id: 02_resolve
    impl: steps.02_resolve.asin_resolve
  - id: 03_enrich
    impl: steps.03_enrich.combined
    config:
      use_sp_api_preflight: true   # uses MCP preflight_asin
      use_keepa_history: true
  - id: 04_calculate
    impl: steps.04_calculate.fees_profit_roi
  - id: 05_decide
    impl: steps.05_decide.shortlist_review_reject
  - id: 06_output
    impl: steps.06_output.csv_xlsx_md
  - id: 07_supplier_leads
    impl: steps.07_supplier_leads.google_search

output_root: fba_engine/data/strategies/seller_storefront/{seller_id}/results/{timestamp}/
```

### 5.4 Buy cost — known limitation

For wholesale store-stalking, we don't have buy cost at discovery. The pipeline runs `03_enrich → 04_calculate` with `buy_cost = null`, which the existing engine handles by emitting `max_buy_price` (the maximum cost at which the row would still SHORTLIST at `target_roi`). The user takes `max_buy_price` to suppliers as their negotiation ceiling.

Action: confirm `04_calculate` in the post-step-4 engine produces `max_buy_price` when `buy_cost` is null. If it doesn't, that's a bug to file before this PRD lands. (The legacy keepa pipeline already does this — `_legacy_keepa/skills/skill-3-scoring/`.)

---

## 6. Strategy B — `oa_csv` (online arbitrage feeds)

### 6.1 Purpose

Ingest pre-filtered online arbitrage candidate CSVs from third-party tools (SellerAmp 2DSorter, Tactical Arbitrage, OAXray) and run them through the same engine for final ranking and decision.

### 6.2 Discovery step: `oa_csv.py`

**Input (CLI):**

```bash
python run.py --strategy oa_csv --feed selleramp --csv path/to/2dsorter-export.csv
python run.py --strategy oa_csv --feed tactical_arbitrage --csv path/to/ta-export.csv
```

**Behaviour:**

1. Resolve the importer for `--feed` value via the importer registry (see §6.3).
2. Importer parses CSV → list of canonical OA candidate records.
3. Filter against `data/exclusions.csv`.
4. Emit `{asin, source: "oa_csv", feed: "selleramp", retail_url, retail_cost_inc_vat, retail_name}` records to `02_resolve`.

### 6.3 Importer abstraction

```python
# shared/lib/python/oa_importers/base.py
class OaFeedImporter(Protocol):
    feed_id: str  # "selleramp" | "tactical_arbitrage" | "oaxray"

    def parse(self, csv_path: Path) -> Iterable[OaCandidate]:
        ...

# shared/lib/python/oa_importers/__init__.py
IMPORTERS: dict[str, OaFeedImporter] = {
    "selleramp": SellerAmp2DSorterImporter(),
    # tactical_arbitrage and oaxray registered as stubs that raise NotImplementedError
    # with a clear "format not yet supported, contributions welcome" message
}
```

**v1 implementation:** SellerAmp 2DSorter only. TA and OAXray importers are scaffolded as `NotImplementedError` stubs with a documented column-mapping TODO so adding them later is a 1–2 hour job.

### 6.4 Buy cost — present at discovery

Unlike wholesale, OA CSVs include retail cost. Map `retail_cost_inc_vat` directly to canonical `buy_cost`. The engine treats it identically to a supplier price (per SPEC §4.3 — non-VAT-registered seller, buy_cost is inc-VAT).

### 6.5 Strategy YAML: `oa_csv.yaml`

Identical shape to `seller_storefront.yaml` with discovery impl swapped. Output root: `fba_engine/data/strategies/oa_csv/{feed}/results/{timestamp}/`.

---

## 7. Keepa client hardening

A new shared library `shared/lib/python/keepa_client/` consolidates Keepa access. Replaces ad-hoc `requests` calls scattered through `_legacy_keepa/`.

### 7.1 Requirements

- **Typed responses.** Pydantic models for the product object, seller object, and the `csv` history arrays (the 30+ parallel time-series). Indexed by Keepa's documented enum positions, not magic numbers in calling code.
- **Token bucket rate limiter.** Configured for the $49/month tier in `shared/config/keepa_client.yaml`. Defaults: 20 tokens/min sustained, 100 token burst. Library callers don't manage rate limiting; they `await client.get_product(...)` and the bucket queues.
- **Persistent disk cache.** `<repo>/.cache/keepa/` (gitignored). Same shape as the SP-API MCP cache:
  ```
  .cache/keepa/
  ├── product/<marketplace>/<asin>.json    (TTL 24h default)
  ├── seller/<marketplace>/<seller_id>.json (TTL 7d default)
  └── category/<marketplace>/<cat_id>.json  (TTL 30d default)
  ```
- **Token usage log.** Every call appends to `<repo>/.cache/keepa/token_log.jsonl`:
  ```json
  {"ts":"2026-04-29T10:15:23Z","endpoint":"product","tokens":6,"cached":false,"asin":"B0XXXX"}
  ```
- **Batch ASIN lookups** where Keepa's API supports it (`/product` accepts up to 100 ASINs).
- **Stale-on-error.** If Keepa returns 5xx or 429 after retries, serve stale cache with `stale: true` flag; only raise if no cache exists.

### 7.2 Config: `shared/config/keepa_client.yaml`

```yaml
# Keepa API client configuration.
# Tier: $49/month "Power User" — 20 tokens/min sustained.

api:
  base_url: https://api.keepa.com
  marketplace: 2     # 2 = UK
  request_timeout_seconds: 30

rate_limit:
  tokens_per_minute: 20
  burst: 100
  retry_on_429:
    max_retries: 3
    backoff_base_seconds: 5
    backoff_jitter_seconds: 2

cache:
  root: .cache/keepa
  ttl_seconds:
    product: 86400      # 24h
    seller: 604800      # 7d
    category: 2592000   # 30d

batching:
  product_batch_size: 100   # Keepa /product max
```

### 7.3 Telemetry on run end

Every strategy run prints to stdout (and writes to the run's output directory as `run_summary.json`):

```
=== Run summary: seller_storefront / A1B2C3D4E5 ===
Discovered ASINs:        342
After exclusions:        298
Keepa tokens used:       1,847  (of which 421 served from cache, 24h TTL)
SP-API calls:            298    (preflight composite — fees, restrictions, eligibility, catalog)
Pipeline outcome:
  SHORTLIST:             14
  REVIEW:                 47
  REJECT:                237
Outputs written to:      fba_engine/data/strategies/seller_storefront/A1B2C3D4E5/results/2026-04-29T10-15Z/
Supplier leads file:     supplier_leads.md (14 ASINs × 3 search URLs)
```

---

## 8. Skill 99 — supplier lead generator (`07_supplier_leads`)

### 8.1 Purpose

Bridge the gap between "we should sell this ASIN" and "here's where to source it." For every SHORTLIST row, generate the search URLs a human would otherwise hand-craft.

### 8.2 Step: `google_search.py`

**Input:** the SHORTLIST rows from `05_decide` (each carries `asin`, `brand`, `product_name`, `category`).

**Output:**

1. New columns appended to existing CSV/XLSX outputs:
   - `supplier_search_brand_distributor` (URL)
   - `supplier_search_product_wholesale` (URL)
   - `supplier_search_brand_trade` (URL)
2. New file `supplier_leads.md` in the run output directory:
   ```markdown
   # Supplier leads — seller_storefront / A1B2C3D4E5 — 2026-04-29

   ## B0XXXXXXX — Acme Widget Pro
   Brand: Acme | Category: Tools | ROI: 41% | Profit: £6.20

   - [Brand distributor UK](https://www.google.com/search?q=Acme+distributor+UK)
   - [Product wholesale](https://www.google.com/search?q=Acme+Widget+Pro+wholesale)
   - [Brand trade account](https://www.google.com/search?q=Acme+trade+account)
   - [Open Keepa chart](https://keepa.com/#!product/2-B0XXXXXXX)
   - [Open Amazon listing](https://www.amazon.co.uk/dp/B0XXXXXXX)

   ...
   ```

### 8.3 Templates

Templates live in `shared/config/supplier_leads.yaml` so they're tunable without code changes:

```yaml
search_templates:
  - id: brand_distributor
    label: "Brand distributor UK"
    template: "{brand} distributor UK"
    skip_if_brand_missing: true
  - id: product_wholesale
    label: "Product wholesale"
    template: "{product_name} wholesale"
    skip_if_brand_missing: false
  - id: brand_trade
    label: "Brand trade account"
    template: "{brand} trade account"
    skip_if_brand_missing: true

search_engine_url: "https://www.google.com/search?q="
```

### 8.4 Future v2 (out of scope for this PRD)

- Faire / Ankorstore API lookups (auth complications)
- Brand contact email enrichment via WHOIS / company-house lookup
- LLM-drafted outreach emails (the legacy keepa pipeline already does this in `_legacy_keepa/skills/skill-4-ip-risk/` — port and reuse later)

---

## 9. CLI launch helpers

A new `cli/launch.py` for one-shot browser launches during manual validation. Used after a run, to inspect specific shortlisted ASINs.

```bash
python run.py open --asin B0XXXXXXX --target keepa
python run.py open --asin B0XXXXXXX --target amazon
python run.py open --asin B0XXXXXXX --target supplier  # opens all 3 supplier searches
python run.py open --seller A1B2C3D4E5 --target storefront
```

Implementation: `webbrowser.open()` with the URLs constructed from the same templates Skill 99 uses. No browser automation, no scraping — pure URL launch.

---

## 10. Configuration changes

### 10.1 New files

- `shared/config/keepa_client.yaml` (see §7.2)
- `shared/config/supplier_leads.yaml` (see §8.3)
- `shared/config/strategies/seller_storefront.yaml` (empty for v1; placeholder for future overrides)
- `shared/config/strategies/oa_csv.yaml` (empty for v1)

### 10.2 Existing files — no changes required

- `business_rules.yaml` — `price_range`, VAT, marketplace already correct
- `decision_thresholds.yaml` — `target_roi`, profit floors, history windows already correct

If during implementation a need arises to modify these, raise it as a separate change against `docs/SPEC.md` first. Do not silently change thresholds.

---

## 11. Output schema additions

The canonical row schema (`docs/SPEC.md §9`) gains three columns at the end (existing column positions unchanged):

```
... existing columns ...
supplier_search_brand_distributor   # URL or empty string
supplier_search_product_wholesale   # URL
supplier_search_brand_trade         # URL or empty string
```

Plus a new column `discovery_source` populated by `01_discover`:

```
discovery_source   # "supplier_pricelist" | "keepa_niche" | "seller_storefront" | "oa_csv"
```

---

## 12. Test strategy

| Layer | Tests |
|---|---|
| `keepa_client` | Pydantic round-trip tests on recorded fixtures (one per endpoint); rate limiter behaviour under burst; cache TTL respected; stale-on-error path; token log append correctness. **Target: ~25 tests.** |
| `seller_storefront` discovery | URL parsing (storefront URL → seller ID, with edge cases: `me=`, `seller=`, malformed); exclusion filter; canonical record shape. **Target: ~8 tests.** |
| `oa_csv` discovery | SellerAmp 2DSorter format parse against fixture CSV; importer registry lookup; stub raises `NotImplementedError` cleanly for TA/OAXray. **Target: ~6 tests.** |
| `supplier_leads` step | URL template substitution; missing-brand handling; markdown output format; CSV column injection. **Target: ~6 tests.** |
| CLI launch | URL construction (no browser actually opened in tests); `--target` validation. **Target: ~4 tests.** |
| Strategy YAML loader | Both new strategies load and validate; smoke test of full pipeline against mocked Keepa + mocked MCP. **Target: ~4 tests.** |

**Total new tests target: ~53.** Existing 49 must continue to pass.

Fixtures live under `fba_engine/tests/fixtures/` matching existing convention.

---

## 13. Build order and commit shape

Each is one PR / one merge. Stop and verify before moving to the next.

1. `feat(keepa-client): typed client with token bucket and disk cache`
2. `feat(keepa-client): batch product lookups + stale-on-error`
3. `feat(steps): add seller_storefront discovery step (against new keepa_client)`
4. `feat(steps): add oa_csv discovery step + SellerAmp 2DSorter importer`
5. `feat(steps): add supplier_leads step (Skill 99 v1 — Google search URLs)`
6. `feat(strategies): add seller_storefront.yaml and oa_csv.yaml`
7. `feat(cli): add open subcommand for browser launch helpers`
8. `feat(output): add discovery_source + supplier_search_* columns; supplier_leads.md per run`
9. `docs: add docs/strategies/seller_storefront.md and oa_csv.md; update SPEC §9 schema; update CLAUDE.md current state`

Steps 1–2 can land independently of the rest — they don't change pipeline behaviour. Steps 3–6 should land together in a coordinated batch (a strategy without its discovery step is half-built).

---

## 14. Acceptance criteria

The PRD is done when all of the following are demonstrably true:

- [ ] `python run.py --strategy seller_storefront --seller <real seller id>` produces a SHORTLIST CSV/XLSX/MD set + `supplier_leads.md` + `run_summary.json`
- [ ] `python run.py --strategy oa_csv --feed selleramp --csv <real 2dsorter export>` produces the same outputs
- [ ] Existing `python run.py --supplier abgee` regression: SHORTLIST/REVIEW/REJECT counts unchanged from before this PRD landed
- [ ] Keepa token usage for a 300-ASIN seller-storefront run is observable in stdout AND in `run_summary.json` AND in the rolling `.cache/keepa/token_log.jsonl`
- [ ] A second run of the same seller within 24h uses cache for ≥90% of product calls (verify via token log)
- [ ] SHORTLIST rows include populated `supplier_search_*` URLs
- [ ] `python run.py open --asin <shortlisted asin> --target supplier` opens 3 browser tabs with the correct Google searches
- [ ] All 53 new tests pass; all 49 existing tests pass; coverage on new code ≥80%
- [ ] `docs/strategies/seller_storefront.md` and `docs/strategies/oa_csv.md` exist and accurately describe the flows
- [ ] `docs/SPEC.md §9` updated with the four new schema fields

---

## 15. Open questions for implementation

These don't block starting work — they get answered during the build. Document the decision in the relevant PR.

1. **Keepa seller endpoint token cost.** Verify against live API for a 1k-ASIN storefront. If far higher than the 50-token estimate, we may need pagination logic or a per-tier safety cap. Impact: §5.2.
2. **OA CSV column map for SellerAmp 2DSorter.** Confirm exact 2DSorter column headers against a real export — the `oa_csv` importer's mapping table needs to be exact. Impact: §6.2.
3. **Where to put the `.cache/keepa/` cache directory.** Repo-root (matches MCP's choice) or `~/.cache/keepa/` (user-scoped, survives repo wipes). Recommendation: repo-root for consistency with MCP; revisit if multi-user becomes a thing.
4. **Storefront-URL parsing — what URL shapes do we accept?** Minimum: `?seller=ID`, `?me=ID`. Should we also accept short links (`amzn.to/...`)? Recommendation: no — require the canonical `amazon.co.uk/sp` form; print a clear error otherwise.
5. **Run output path for ad-hoc strategies.** `seller_storefront` outputs are keyed by `seller_id`. For OA, `feed` + `timestamp` should be enough. Confirm this matches what feels right after a few real runs and adjust before merge.

---

## 16. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| $49 Keepa tier insufficient for daily store-stalking volume | Medium | Telemetry from §7.3 makes this visible immediately; user accepts £49 first, upgrades to £99 if `token_log.jsonl` shows hourly exhaustion |
| Reorg step 4/5 slips, blocking strategy YAML work | Medium | Phase 1 (keepa_client) and Phase 2 (discovery steps in isolation) can land first; YAMLs deferred |
| SellerAmp changes 2DSorter export format | Low | Importer is one file; column-map config; failing tests will catch on next run |
| Keepa seller endpoint rate-limited beyond expectations | Medium | Stale-on-error fallback in keepa_client; 7-day seller cache means re-runs are nearly free |

---

**End of PRD.** Hand this to Claude Code with: "Read `docs/SPEC.md` and `docs/architecture.md` first, then implement this PRD section by section per §13. Verify acceptance criteria in §14 at each milestone."
