# PRD: Keepa-Driven Sourcing Strategies

**Status:** Ready for implementation
**Author:** Peter Farrell (with Claude)
**Target branch:** `feat/keepa-sourcing-strategies` (off `main`, after dependencies land)
**Authoritative spec it builds on:** `docs/SPEC.md`
**Architecture it conforms to:** `docs/architecture.md`
**Companion to:** PRD `feat/sourcing-strategies` (`seller_storefront` + `oa_csv` + Skill 99)

---

## 1. Objective

Add five new sourcing strategies built on the **Keepa Product Finder API endpoint**, each expressing a different sourcing thesis as a parameterised filter set. All five share the new `keepa_product_finder` discovery step, the existing `02_resolve → 06_output` pipeline, and the new `07_supplier_leads` step.

End-state: every proven Keepa-based sourcing pattern in the FBA reseller community is expressible as a YAML strategy file. Adding a sixth or seventh pattern later becomes a config change, not a code change.

---

## 2. Out of scope

- Browser scraping of Keepa.com. Everything goes through the Keepa REST API (`/query` for the product finder, `/product` for enrichment) via the `keepa_client` library introduced in the companion PRD.
- New decision logic. SHORTLIST/REVIEW/REJECT gates from `docs/SPEC.md §3` apply unchanged.
- Replacing the legacy `keepa_niche` pipeline in `_legacy_keepa/`. That dies as part of reorg step 4. This PRD assumes step 4 is complete.
- Any strategy that doesn't start from a Keepa Product Finder query. Storefront stalking and OA CSV import are in the companion PRD.
- Image / EAN matching to off-Amazon retailers. That's Tactical Arbitrage's job and out of scope.
- Real-time deal alerting. The Keepa Notifier / Keepa Deals API is a future strategy (`keepa_deals_stream`), explicitly deferred.

---

## 3. Dependencies

| Dependency | Status | Why |
|---|---|---|
| Reorg step 4 — Keepa pipeline ported to Python steps | Required | This PRD adds a new discovery step to that catalogue |
| Reorg step 5 — Strategies as YAMLs | Required | All five strategies are YAML compositions |
| Companion PRD `feat/sourcing-strategies` — `keepa_client` library | Required | All discovery uses the typed, rate-limited, cached client |
| Companion PRD — `07_supplier_leads` step | Required | Every shortlisted ASIN needs supplier search URLs |
| `feat/mcp-sourcing-tools` — SP-API MCP expansion | Required | `03_enrich` uses `preflight_asin` for fees/restrictions/eligibility |

If the companion PRD lands first (as planned), this PRD is purely additive — one new discovery step, one new score field, five new strategy YAMLs, plus tests.

---

## 4. The five strategies

Each is a research-validated FBA sourcing pattern. Each maps to a **Keepa Product Finder filter composition**, which is just a JSON payload to the Keepa `/query` endpoint.

| # | Strategy ID | Sourcing thesis | Source channel |
|---|---|---|---|
| 1 | `amazon_oos_wholesale` | Amazon has stopped stocking; brand wants resellers; we negotiate trade pricing | Wholesale |
| 2 | `a2a_flip` | Temporary price dip on Amazon's own offer; buy from Amazon, resell on Amazon at recovered price | Amazon-to-Amazon |
| 3 | `brand_wholesale_scan` | We hold a trade account; pull every ASIN for a brand list and rank by profitability | Wholesale |
| 4 | `no_rank_hidden_gem` | Listings without a sales rank but with steady review velocity — low-competition long-tail | Wholesale / OA |
| 5 | `stable_price_low_volatility` | Boring products with predictable economics; lower upside but predictable cash conversion | Wholesale |

A sixth, `keepa_deals_stream` (real-time deal page polling for A2A), is documented in §13 as a future addition, not built here.

---

## 5. Shared discovery step: `keepa_product_finder`

All five strategies use the same new step at `fba_engine/steps/01_discover/keepa_product_finder.py`.

### 5.1 Behaviour

1. Read the strategy's `discovery.filters` block from the strategy YAML.
2. Translate it to a Keepa Product Finder JSON payload (the snake_case → camelCase mapping is well-defined; see §5.3).
3. Call `keepa_client.product_finder(domain="GB", filters=..., n_products=...)`.
4. Filter against `data/exclusions.csv`.
5. Emit `{asin, source: "keepa_product_finder", strategy: <strategy_id>, finder_payload_hash: <sha256>}` records to `02_resolve`.

The `finder_payload_hash` lets reruns of the same query within 24h hit the keepa_client's seller/finder cache (new cache namespace, see §5.4).

### 5.2 Step config

```yaml
# Strategy YAMLs use this shape under their discovery: block
discovery:
  step: keepa_product_finder
  config:
    domain: GB             # marketplace
    n_products: 500        # max ASINs to return; capped at Keepa's 10k limit
    sort:
      - current_SALES      # field name per Keepa product finder docs
      - asc                # asc | desc
    filters:
      # See per-strategy YAMLs in §6 for filter shapes
      ...
```

### 5.3 Filter translation

Keepa Product Finder filter names are **case-sensitive camelCase** with `_lte`/`_gte` suffixes for ranges. For readability, our YAML uses **snake_case range objects** which translate at runtime:

```yaml
# Our YAML
filters:
  current_amazon: { lte: 0 }       # Amazon out of stock (Amazon price = -1)
  current_buy_box: { gte: 1200, lte: 4000 }   # £12.00–£40.00 (Keepa stores in cents)
  current_sales: { lte: 100000 }   # BSR ≤ 100k
  avg90_sales: { lte: 80000 }      # 90-day avg BSR ≤ 80k
  out_of_stock_percentage_90_amazon: { gte: 70 }   # Amazon OOS ≥ 70% of last 90 days
  fba_seller_count: { gte: 2, lte: 10 }
  brand: ["acme", "widgetco"]      # OR-list for brand names
  productGroup: ["Toys & Games"]   # Amazon top-level category
  hazardousMaterialType: { eq: 0 } # not hazmat

# Translated to Keepa payload
{
  "current_AMAZON_lte": 0,
  "current_BUY_BOX_SHIPPING_gte": 1200,
  "current_BUY_BOX_SHIPPING_lte": 4000,
  "current_SALES_lte": 100000,
  "avg90_SALES_lte": 80000,
  "outOfStockPercentage90_AMAZON_gte": 70,
  ...
  "perPage": 500,
  "page": 0,
  "sort": ["current_SALES", "asc"]
}
```

The translator is a single file (`shared/lib/python/keepa_client/finder_filters.py`) with a complete field-name map sourced from Keepa's documented enum. Unknown YAML keys raise at startup, not at runtime — bad strategy YAMLs fail on load, not mid-pipeline.

### 5.4 Cache namespace

Adds a new namespace to the keepa_client disk cache (introduced in companion PRD §7):

```
.cache/keepa/
├── product/<marketplace>/<asin>.json          (existing, 24h)
├── seller/<marketplace>/<seller_id>.json      (existing, 7d)
├── category/<marketplace>/<cat_id>.json       (existing, 30d)
└── finder/<marketplace>/<payload_hash>.json   (NEW, 24h default)
```

Same TTL behaviour. A second run of the same strategy within 24h returns cached ASIN list — zero token cost.

### 5.5 Token cost reality

Keepa Product Finder costs scale with results returned, not query complexity. As of the $49 Power tier:

- 1 token per page (50 ASINs/page)
- Plus `n_products / 50` tokens for the page fetches
- A 500-ASIN scan ≈ 10 tokens
- Followed by 500× `/product` enrichment in `03_enrich` ≈ 3000 tokens (6 tokens each)

**Implication:** discovery is cheap; enrichment is expensive. The `01_discover` step must aggressively pre-filter at the Keepa Product Finder level so we don't pull 500 ASINs and then enrich 500 — we want to pull 500 *good candidates* and enrich them all. Get the filters right.

This shapes the strategy designs in §6: each one uses 4–8 Product Finder filters to pre-narrow before enrichment.

---

## 6. Strategy specifications

For each strategy: thesis, filter spec, expected outcome shape, and one open question for tuning during implementation.

### 6.1 `amazon_oos_wholesale` — Amazon out-of-stock wholesale plays

**Thesis:** When Amazon stops selling a product (often because the brand pulled them or supply ran out), the Buy Box becomes available to third-party sellers. Checking out-of-stock on the Amazon-price filter is one of the most popular wholesale strategies; it finds products where Amazon has stopped selling, leaving the Buy Box open for third-party sellers. Best applied with the OOS percentage filter to confirm the absence is sustained, not a temporary glitch. An Amazon OOS percentage of 70–100% means Amazon has essentially abandoned the product — better for wholesale sellers who can maintain consistent stock.

**Strategy YAML:** `fba_engine/strategies/amazon_oos_wholesale.yaml`

```yaml
name: amazon_oos_wholesale
description: |
  Wholesale plays where Amazon has effectively abandoned the listing.
  ≥70% Amazon OOS over 90 days, FBA sellers present (so it's actually selling),
  BSR healthy, price in our window. Each shortlisted ASIN goes to supplier
  outreach to negotiate trade.

discovery:
  step: keepa_product_finder
  config:
    domain: GB
    n_products: 500
    sort: ["current_SALES", "asc"]   # best-selling first
    filters:
      current_amazon: { lte: 0 }                         # Amazon currently OOS
      out_of_stock_percentage_90_amazon: { gte: 70 }     # sustained absence
      current_buy_box: { gte: 1200, lte: 4000 }          # £12–£40
      current_sales: { lte: 100000 }                     # BSR ≤ 100k
      avg90_sales: { lte: 80000 }                        # consistently selling
      offer_count_fba_current: { gte: 2, lte: 10 }       # competitive but not crowded
      hazardousMaterialType: { eq: 0 }
      isAdultProduct: { eq: false }

steps:
  - { id: 02_resolve, impl: steps.02_resolve.asin_resolve }
  - { id: 03_enrich,  impl: steps.03_enrich.combined,
      config: { use_sp_api_preflight: true, use_keepa_history: true } }
  - { id: 04_calculate, impl: steps.04_calculate.fees_profit_roi }
  - { id: 05_decide,    impl: steps.05_decide.shortlist_review_reject }
  - { id: 06_output,    impl: steps.06_output.csv_xlsx_md }
  - { id: 07_supplier_leads, impl: steps.07_supplier_leads.google_search }

output_root: fba_engine/data/strategies/amazon_oos_wholesale/{run_date}/
```

**CLI:**
```bash
python run.py --strategy amazon_oos_wholesale
```

**Open question:** what `productGroup` (Amazon top-level category) should we run by default? Likely no default — user passes `--category "Toys & Games"` to scope a run, otherwise the 500-ASIN cap chops random categories. Add `--category` as an override that injects into `filters.productGroup`.

### 6.2 `a2a_flip` — Amazon-to-Amazon flips

**Thesis:** Amazon's own pricing engine occasionally drops a product's price below its established baseline (algorithmic repricing, clearance, error). The price typically recovers within hours-to-days because Amazon's algorithm favours historical pricing. One seller noticed how some items briefly dipped in price before bouncing back; with Keepa they set filters for products with recent low buy box prices, then checked how often the prices spiked again. Buy at the dip, list back on Amazon at recovered price.

**Critical compliance constraint** documented inline in the strategy: A2A flips require an **Amazon Business account** (not a personal Prime account); using personal Prime perks for resale violates Amazon's ToS. The `07_supplier_leads` output for this strategy emits an explicit reminder.

**Strategy YAML:** `fba_engine/strategies/a2a_flip.yaml`

```yaml
name: a2a_flip
description: |
  Amazon-to-Amazon flips. Find ASINs where Amazon's own price has dropped
  significantly in the last 30 days, with a stable historical baseline at a
  meaningfully higher level. Buy from Amazon, resell on Amazon when price
  recovers. Requires Amazon Business account.

compliance_notes:
  - "Buy via Amazon Business account only — personal Prime perks for resale violates ToS"
  - "Confirm Amazon is buyable now (not just historical OOS) before purchase"
  - "Keep return rate low; bad flips are better held than returned"

discovery:
  step: keepa_product_finder
  config:
    domain: GB
    n_products: 500
    sort: ["lastPriceChange", "desc"]   # most recently changed first
    filters:
      current_amazon: { gte: 1200 }                      # Amazon currently selling
      delta_percent_30_amazon: { lte: -25 }              # ≥25% drop in last 30d
      avg90_amazon: { gte: 1500 }                        # baseline above current
      current_sales: { lte: 50000 }                      # actually selling
      avg90_sales: { lte: 40000 }                        # consistently
      out_of_stock_percentage_90_amazon: { lte: 30 }     # Amazon usually IS in stock
      hazardousMaterialType: { eq: 0 }

steps:
  - { id: 02_resolve, impl: steps.02_resolve.asin_resolve }
  - { id: 03_enrich,  impl: steps.03_enrich.combined,
      config: { use_sp_api_preflight: true, use_live_pricing: true } }
  # NB: a2a_flip uses live pricing (the SP-API live offers tool) because
  # decision quality depends on the gap being open RIGHT NOW.
  - { id: 04_calculate, impl: steps.04_calculate.fees_profit_roi,
      config: { buy_cost_source: "current_amazon_price" } }
  - { id: 05_decide,    impl: steps.05_decide.shortlist_review_reject }
  - { id: 06_output,    impl: steps.06_output.csv_xlsx_md }
  - { id: 07_supplier_leads, impl: steps.07_supplier_leads.amazon_self_link }
  # NB: supplier leads for a2a_flip are just the Amazon listing itself + a
  # compliance reminder banner. Skill 99's google_search isn't useful here.

output_root: fba_engine/data/strategies/a2a_flip/{run_date}/
```

**Buy cost handling:** `04_calculate` for this strategy reads `buy_cost` from Amazon's own current price (provided by the SP-API live pricing tool in `03_enrich`). The `buy_cost_source: "current_amazon_price"` config flag tells the calculator to use that column instead of the supplier-derived path. This needs to be implemented in `04_calculate` as a small new branch.

**Open question:** A2A flips have a tight time window (hours, not days). Daily run cadence may miss the dip. Two options:
- (a) Run hourly via cron, accepting Keepa token burn
- (b) Use Keepa Deals API (real-time price-drop firehose) instead of Product Finder

Recommendation: build with Product Finder for v1; flag Deals API as the future `keepa_deals_stream` strategy if hit rate is low.

### 6.3 `brand_wholesale_scan` — paste in supplier brand list

**Thesis:** We hold trade accounts with specific brands. Pull every ASIN for those brands, rank by profitability against trade pricing. Wholesale sellers use this heavily — paste in a brand list from a supplier to instantly find all their products on Amazon.

**Strategy YAML:** `fba_engine/strategies/brand_wholesale_scan.yaml`

```yaml
name: brand_wholesale_scan
description: |
  Paste in a brand list (e.g. all brands a wholesaler carries). Pull every
  ASIN for those brands, rank by profitability. Best run per supplier, with
  the supplier's current price list pre-loaded for actual buy_cost.

inputs:
  required:
    - brands         # list of brand strings; matched OR
  optional:
    - supplier_pricelist_csv   # if provided, used as buy_cost source
    - root_category            # constrains the brand search

discovery:
  step: keepa_product_finder
  config:
    domain: GB
    n_products: 1000   # brand scans return more results — give them room
    sort: ["current_SALES", "asc"]
    filters:
      brand: "${inputs.brands}"
      current_buy_box: { gte: 1200, lte: 4000 }
      current_sales: { lte: 150000 }
      avg90_sales: { lte: 120000 }
      hazardousMaterialType: { eq: 0 }

steps:
  - { id: 02_resolve, impl: steps.02_resolve.asin_resolve }
  - { id: 03_enrich,  impl: steps.03_enrich.combined,
      config: { use_sp_api_preflight: true, use_keepa_history: true } }
  - { id: 04_calculate, impl: steps.04_calculate.fees_profit_roi }
  - { id: 05_decide,    impl: steps.05_decide.shortlist_review_reject }
  - { id: 06_output,    impl: steps.06_output.csv_xlsx_md }
  - { id: 07_supplier_leads, impl: steps.07_supplier_leads.google_search }

output_root: fba_engine/data/strategies/brand_wholesale_scan/{supplier_or_run_date}/
```

**CLI:**
```bash
# Standalone brand scan
python run.py --strategy brand_wholesale_scan \
  --brands "Acme,Widgetco,FooBar"

# Or with a supplier pricelist for buy_cost
python run.py --strategy brand_wholesale_scan \
  --supplier abgee \
  --use-supplier-brands   # auto-extracts brand list from the supplier's catalogue
```

**Brand input formats:** accept comma-separated, newline-separated, JSON list, or `@brands.txt` file. Internally normalise to lowercase, dedupe.

**Open question:** when both a supplier pricelist and a Keepa scan are present, the supplier pricelist already provides EAN→ASIN matches via the existing `supplier_pricelist` strategy. What does this strategy add? It catches **brand ASINs that aren't in the supplier's price list** — discontinued items the supplier still has stock of, items the supplier hasn't catalogued yet, multipack variants. Worth running both and diffing.

### 6.4 `no_rank_hidden_gem` — listings without a sales rank

**Thesis:** Some ASINs lack a sales rank entirely but still sell — particularly long-tail items, recently-relisted products, or category edge cases. No-sales-rank ASINs filtered by review count (and review-count growth) can surface low-competition products. Lower expected volume but lower competition.

**Strategy YAML:** `fba_engine/strategies/no_rank_hidden_gem.yaml`

```yaml
name: no_rank_hidden_gem
description: |
  Listings without a current sales rank, but with steady review accumulation.
  Long-tail / low-competition plays. Volume is lower; margins should compensate.

discovery:
  step: keepa_product_finder
  config:
    domain: GB
    n_products: 500
    sort: ["reviewCount", "desc"]
    filters:
      no_sales_rank: true                              # Keepa flag
      current_count_reviews: { gte: 10, lte: 1000 }    # has reviews but not viral
      delta90_count_reviews: { gte: 3 }                # ≥3 new reviews in 90d (proves selling)
      current_buy_box: { gte: 1500, lte: 5000 }        # slightly higher floor — lower volume needs higher AOV
      offer_count_new_current: { lte: 5 }              # genuinely uncrowded
      hazardousMaterialType: { eq: 0 }

steps:
  - { id: 02_resolve, impl: steps.02_resolve.asin_resolve }
  - { id: 03_enrich,  impl: steps.03_enrich.combined,
      config: { use_sp_api_preflight: true, use_keepa_history: true } }
  - { id: 04_calculate, impl: steps.04_calculate.fees_profit_roi }
  - { id: 05_decide,    impl: steps.05_decide.shortlist_review_reject,
      config: { override_min_sales_shortlist: 5 } }
  # Override: min_sales_shortlist defaults to 20/mo. No-rank items will fail
  # that automatically. We override to 5/mo so they're judged on margin.
  - { id: 06_output,    impl: steps.06_output.csv_xlsx_md }
  - { id: 07_supplier_leads, impl: steps.07_supplier_leads.google_search }

output_root: fba_engine/data/strategies/no_rank_hidden_gem/{run_date}/
```

**Open question:** does `05_decide` currently support the `override_min_sales_shortlist` config knob? If not, this needs to be added — a simple per-step config override of any threshold, falling back to the global config default. Probably worth adding generically rather than per-knob, so future strategies can override any threshold.

### 6.5 `stable_price_low_volatility` — boring but predictable

**Thesis:** Products with minimal price variance over 90 days are predictable cash conversion. Lower upside (no spike profits) but lower risk of getting trapped at a price floor. By using the "90 days drop %" filter on Buy Box and entering a range like -20 to 20, you find products with minimal price changes over the last 90 days — focused on products with steady pricing for better long-term profitability assessment.

**Strategy YAML:** `fba_engine/strategies/stable_price_low_volatility.yaml`

```yaml
name: stable_price_low_volatility
description: |
  Boring products with stable Buy Box over 90 days. Predictable economics,
  lower variance, suitable for repeat-buy wholesale. Especially useful for
  cash-flow planning vs. opportunistic flips.

discovery:
  step: keepa_product_finder
  config:
    domain: GB
    n_products: 500
    sort: ["avg90_SALES", "asc"]   # best-velocity first
    filters:
      delta90_buy_box: { gte: -10, lte: 10 }            # ±10% over 90d (very tight)
      delta30_buy_box: { gte: -10, lte: 10 }            # also tight at 30d
      current_buy_box: { gte: 1200, lte: 4000 }
      avg90_sales: { lte: 60000 }                        # genuinely selling
      offer_count_fba_current: { gte: 2, lte: 8 }
      out_of_stock_percentage_90_buy_box: { lte: 10 }    # always available — no scarcity premium
      hazardousMaterialType: { eq: 0 }

steps:
  - { id: 02_resolve, impl: steps.02_resolve.asin_resolve }
  - { id: 03_enrich,  impl: steps.03_enrich.combined,
      config: { use_sp_api_preflight: true, use_keepa_history: true } }
  - { id: 04_calculate, impl: steps.04_calculate.fees_profit_roi }
  - { id: 05_decide,    impl: steps.05_decide.shortlist_review_reject }
  - { id: 06_output,    impl: steps.06_output.csv_xlsx_md }
  - { id: 07_supplier_leads, impl: steps.07_supplier_leads.google_search }

output_root: fba_engine/data/strategies/stable_price_low_volatility/{run_date}/
```

**Why this strategy matters:** the rest of the system optimises for ROI. This one optimises for *predictability*. SHORTLIST results from this strategy get a `stability_score` attached (see §8) that's used for capital allocation decisions outside the engine.

---

## 7. Strategy contract — what every strategy must supply

To keep adding strategies friction-free, every strategy YAML must declare:

```yaml
name: <strategy_id>           # snake_case, matches filename
description: |
  Multi-line description of the sourcing thesis.
  Why does this strategy work? When should I run it?

# Optional — strategies that have legal/ToS implications
compliance_notes:
  - "Free-text bullet list of constraints"

# Optional — strategies that take user input beyond a CLI flag
inputs:
  required: [field1, field2]
  optional: [field3]

discovery:
  step: <step_id>             # the discovery step to invoke
  config: { ... }             # step-specific config

steps:                        # ordered list of post-discovery steps
  - { id: 02_resolve, impl: ..., config: ... }
  ...

output_root: <path template with {placeholders}>
```

The strategy loader (built in reorg step 5) validates this contract on startup. A malformed YAML fails loud at `python run.py --list-strategies`, not silently mid-pipeline.

---

## 8. New scoring field: `stability_score`

For strategies that care about price stability (`stable_price_low_volatility` and `amazon_oos_wholesale`), `04_calculate` adds a derived field to the row schema:

```
stability_score = 1.0 - (abs(delta90_buy_box) + abs(delta30_buy_box)) / 200
# Range: 0.0 (highly volatile) to 1.0 (rock steady)
```

This field is informational — it does not gate SHORTLIST. It appears in the output CSV/XLSX as a sortable column. Capital allocation decisions outside the engine can use it.

Only strategies that explicitly set `compute_stability_score: true` in their `04_calculate` config get the field populated; for all others it's null. Avoids polluting unrelated strategy outputs.

---

## 9. Anti-patterns the implementation must avoid

These are the failure modes that will tank result quality. The implementation must defend against each one.

### 9.1 Searching "All" categories

Per the Keepa Product Finder docs: Searching "All" returns a random 10,000-product subset of Amazon's entire catalogue — always pick a category first. The strategy loader must either:

- (a) Require a `productGroup` filter on every strategy, OR
- (b) Require the user to pass `--category` on the CLI

Recommendation: (b). Categories are too varied to fix in a YAML; let the user scope each run. Throw a clear error if neither the YAML nor the CLI supplies a category for any strategy that omits one.

### 9.2 Confusing "Out of stock" on Buy Box vs Amazon

Per the Keepa docs: "Out of stock" on Buy Box Price doesn't mean the product is out of stock — it means no seller has been awarded the Buy Box. The filter translator (§5.3) must use distinct YAML keys for each:

- `current_amazon: { lte: 0 }` → Amazon out of stock
- `current_buy_box: { lte: 0 }` → Buy Box suppressed (different thing)

Document both clearly in the filter translator's docstring.

### 9.3 Using Sales Rank Drops as a sales-velocity proxy

Sales Rank Drops do not equal the number of sales. The same drop-count can occur on a top-25 BSR product (huge volume) and a 4-million BSR product (essentially none). The strategy YAMLs and decision logic must use **average BSR over a window** (`avg90_SALES`) for velocity, never `salesRankDrops30` or similar.

The filter translator should reject `*_drops_*` keys with an error pointing the user at the avg-BSR alternative.

### 9.4 Stale finder cache during a flip strategy

For `a2a_flip`, the 24h finder cache TTL is wrong — by the time we run, the dip is over. Strategy-level cache TTL override:

```yaml
discovery:
  step: keepa_product_finder
  config:
    cache_ttl_seconds: 1800   # 30min for fast-moving strategies; default 86400
```

`a2a_flip` ships with `cache_ttl_seconds: 1800`. All others use the default.

---

## 10. Output additions

The canonical row schema gains:

```
discovery_strategy   # which strategy yaml produced this row (e.g. "amazon_oos_wholesale")
finder_payload_hash  # hash of the Keepa Product Finder query that produced this ASIN
stability_score      # 0.0–1.0, nullable (only set when strategy enables it)
```

`07_supplier_leads` output for `a2a_flip` is different from the others — instead of Google supplier searches, it emits:

```markdown
## B0XXXXXXX — Acme Widget Pro
Buy: [Amazon listing](https://www.amazon.co.uk/dp/B0XXXXXXX) — current price £24.99
Sell back at recovered: £39.99 (90d avg)
Compliance: Use Amazon Business account. NOT personal Prime. See strategy notes.
[Open Keepa chart](https://keepa.com/#!product/2-B0XXXXXXX)
```

Implemented via the existing supplier_leads template config (`shared/config/supplier_leads.yaml`) gaining a strategy-specific override block:

```yaml
strategy_overrides:
  a2a_flip:
    template_file: supplier_leads_a2a.md.j2
```

---

## 11. Test strategy

| Layer | Tests |
|---|---|
| `keepa_product_finder` step | Filter YAML→Keepa-payload translation against a known fixture (one per strategy = 5 tests); cache namespace key; payload hash stability; rejection of disallowed keys (sales_rank_drops, missing category) |
| Filter translator | Round-trip every filter in the spec; lte/gte boundary cases; brand list normalisation; unknown-key startup failure |
| Each strategy YAML | Loads, validates, has a description, has a productGroup or accepts `--category` |
| `04_calculate` `buy_cost_source: "current_amazon_price"` branch | A2A path produces correct profit when buy_cost comes from Amazon's price |
| `04_calculate` `compute_stability_score` flag | Score correct at 0%, ±5%, ±50% deltas |
| `05_decide` per-step threshold override | `override_min_sales_shortlist: 5` actually overrides; default still applies elsewhere |
| End-to-end smoke tests | One per strategy, mocked Keepa + mocked SP-API, asserts SHORTLIST/REVIEW/REJECT counts non-zero and outputs written |

**Total new tests target: ~45.** All existing tests must continue passing.

---

## 12. Build order

1. `feat(keepa-client): add product_finder method + finder cache namespace`
2. `feat(keepa-client): add filter translator (yaml snake_case → keepa camelCase)`
3. `feat(steps): add 01_discover.keepa_product_finder step`
4. `feat(calc): add buy_cost_source config branch (for a2a_flip) + stability_score flag`
5. `feat(decide): add per-step threshold override mechanism`
6. `feat(strategies): add amazon_oos_wholesale.yaml + tests`
7. `feat(strategies): add a2a_flip.yaml + a2a-specific supplier_leads template + tests`
8. `feat(strategies): add brand_wholesale_scan.yaml + brand-input parsing + tests`
9. `feat(strategies): add no_rank_hidden_gem.yaml + tests`
10. `feat(strategies): add stable_price_low_volatility.yaml + tests`
11. `docs: add docs/strategies/*.md for each new strategy; update SPEC §9 schema; update CLAUDE.md`

Steps 1–5 are infrastructure — must land before any strategy YAML can run end-to-end. Steps 6–10 can land in any order or as one batch.

---

## 13. Acceptance criteria

- [ ] `python run.py --list-strategies` shows all five new strategies with descriptions
- [ ] `python run.py --strategy amazon_oos_wholesale --category "Toys & Games"` produces SHORTLIST output
- [ ] `python run.py --strategy a2a_flip --category "Home & Kitchen"` produces SHORTLIST output with the A2A-specific supplier_leads template
- [ ] `python run.py --strategy brand_wholesale_scan --brands "LEGO,Hasbro,Mattel"` produces SHORTLIST output
- [ ] `python run.py --strategy no_rank_hidden_gem --category "Office Products"` produces output (may be sparse — that's fine)
- [ ] `python run.py --strategy stable_price_low_volatility --category "Pet Supplies"` produces SHORTLIST output with `stability_score` populated
- [ ] Running any strategy twice within the cache TTL serves the second run from cache (verifiable via token log: zero finder tokens consumed on rerun)
- [ ] `a2a_flip` cache TTL is 30 min, not 24 h, verified via `.cache/keepa/finder/` file mtimes
- [ ] Strategy with no category on either YAML or CLI fails with a clear error before calling Keepa
- [ ] Strategy YAML using `sales_rank_drops_*` filter fails at load with a pointer to the avg-BSR alternative
- [ ] All 45 new tests pass; all previously-passing tests still pass
- [ ] `docs/strategies/<each>.md` exists with thesis, filter rationale, and known caveats
- [ ] SPEC §9 schema updated with the three new columns

---

## 14. Future strategies (documented, not built)

These follow the same pattern and are explicitly deferred:

- **`keepa_deals_stream`** — poll Keepa Deals API (real-time price-drop firehose) instead of Product Finder; better for time-sensitive A2A
- **`seasonal_holdover`** — find products with predictable Q4 spikes that are cheap in Q1–Q2 (LEGO sets, Christmas-coded toys)
- **`fba_to_fbm_conversion`** — listings with 0 FBA sellers and ≥1 FBM sellers; we list FBA and capture the Buy Box at premium pricing
- **`multipack_arbitrage`** — same brand sells single + multipack; one is mispriced relative to the other; bundle or split for profit
- **`competitor_storefront_diff`** — diff a competitor's storefront over time; new ASINs they added are signals (overlaps with `seller_storefront` from the companion PRD, but adds the temporal dimension)

Adding any of these is: a new strategy YAML + maybe one new discovery step. Engine and decision logic don't change.

---

## 15. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Keepa Product Finder filter names drift in API updates | Low | Filter translator is one file; update + add migration test when it happens |
| `a2a_flip` hit rate too low to justify daily runs | Medium | Telemetry shows SHORTLIST count per run; if consistently 0 over 2 weeks, build `keepa_deals_stream` as a replacement |
| Strategies with overlapping filters return overlapping ASINs across runs | High | Expected and fine; the engine deduplicates via `data/exclusions.csv` and per-ASIN `verdict_cache` from companion PRD |
| `productGroup` filter doesn't map cleanly to UK Amazon's category tree | Low | Test against real GB marketplace early; may need a mapping table |
| $49 Keepa tier exhausted by 5-strategy daily cadence | Medium | Token log makes it visible immediately; user upgrades to £99 if sustained saturation; or runs strategies on alternate days |

---

**End of PRD.**

Hand to Claude Code with: "Read `docs/SPEC.md`, `docs/architecture.md`, the companion PRD, and this PRD. Confirm dependencies in §3 are merged. Then implement in the order in §12. Verify acceptance criteria in §13 at each milestone."
