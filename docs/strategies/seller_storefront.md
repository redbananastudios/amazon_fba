# Strategy: `seller_storefront`

**Type:** Wholesale-leads sourcing (Amazon-seller-first)
**Status:** Production (leads-only)
**Implementation:** `fba_engine/steps/seller_storefront.py` + `fba_engine/strategies/seller_storefront.yaml`

---

## What this strategy does

Walk a competing FBA seller's storefront via Keepa. For each ASIN they're
currently selling, emit a row with product name, brand, category, and
supplier-search URLs (Brand distributor / Brand trade account / Product
wholesale). The output is a leads CSV plus a markdown side-output ready
for manual supplier outreach.

This is the wholesale-arbitrage workflow: find what a successful
competitor is winning, then go after those products through your own
supplier relationships. Pairs naturally with Skill 99 (supplier_leads).

---

## When to use it

- You've identified a competitor whose portfolio mix or category focus
  matches your sourcing capability
- You want a starting list of ASINs to take to wholesale suppliers
- You're not yet ready for full profit decisions (no buy_cost yet)

For supplier-first sourcing where you already have prices, see the
`supplier_pricelist` strategy. For Amazon-first niche discovery, see
`keepa_niche`.

---

## Inputs

- **Amazon merchant ID** of the target seller (e.g. `A1B2C3D4E5`).
  Find this in the seller's storefront URL: `amazon.co.uk/sp?seller=...`
- **Keepa API key** in `KEEPA_API_KEY` env var. The strategy makes one
  `/seller?storefront=1` call plus one batched `/product` call per
  `config.batching.product_batch_size` ASINs (default 100).

---

## Run

```bash
export KEEPA_API_KEY=...

python -m fba_engine.strategies.runner \
    --strategy fba_engine/strategies/seller_storefront.yaml \
    --context seller_id=A1B2C3D4E5 \
              run_dir=fba_engine/data/strategies/seller_storefront/A1B2C3D4E5 \
              timestamp=$(date +%Y%m%d_%H%M%S)
```

Or use the `open` subcommand to inspect a candidate ASIN:

```bash
python run.py open --asin B0XXXXXXX --target keepa
```

---

## Outputs

In `<run_dir>/`:

- `seller_storefront_<seller_id>_<ts>.csv` — leads CSV (asin, source,
  seller_id, seller_name, product_name, brand, category, amazon_url,
  supplier_search_*)
- `supplier_leads_<seller_id>_<ts>.md` — markdown side-output, one
  section per ASIN with brand/category, supplier search URLs, and an
  Amazon listing link. Operator-friendly format for outreach work.
- `run_summary.json` — metadata: row counts, step timings, output paths,
  Keepa token usage (rolls up the per-call entries from
  `<keepa_cache_root>/token_log.jsonl`).

---

## Schema

| Column | Source | Notes |
|---|---|---|
| `asin` | Keepa | Amazon Standard Identification Number |
| `source` | constant | `"seller_storefront"` for downstream branching |
| `seller_id` | input | Echo of the merchant ID |
| `seller_name` | Keepa | Display name for the storefront |
| `product_name` | Keepa | `KeepaProduct.title` |
| `brand` | Keepa | Empty string when missing (not None) |
| `category` | Keepa | Leaf of `categoryTree` |
| `amazon_url` | constructed | `https://www.amazon.co.uk/dp/<asin>` |
| `supplier_search_brand_distributor` | supplier_leads | Empty when brand is missing |
| `supplier_search_brand_trade` | supplier_leads | Empty when brand is missing |
| `supplier_search_product_wholesale` | supplier_leads | Always populated |

---

## Pipeline

```
discover → supplier_leads → output CSV
```

No `resolve` / `calculate` / `decide` steps — this is leads-only by
design. A heuristic `buy_cost` (e.g. `market_price * 0.5`) would
silently produce fake ROI verdicts. Get real supplier prices first,
then feed them into a `supplier_pricelist` run.

---

## Configuration knobs

In `shared/config/keepa_client.yaml`:

- `api.marketplace` — Keepa marketplace code (2 = UK)
- `batching.product_batch_size` — ASINs per `/product` call (default 100)
- `cache.ttl_seconds.product` — how long product responses stay fresh
  (default 24h)
- `cache.ttl_seconds.seller` — how long seller responses stay fresh
  (default 7d — storefronts change slowly)
- `rate_limit.tokens_per_minute` / `burst` — quota guard

In `shared/config/supplier_leads.yaml`:

- `search_engine_url` — base URL for supplier searches (default Google)
- `search_templates` — query templates (Brand distributor UK / Brand
  trade account / Product wholesale)

---

## Known limitations

- **No buy_cost** — leads only. Decision pipeline (calculate / decide)
  is not chained.
- **Single seller per run** — chain multiple runs in Cowork or shell
  scripts to walk multiple sellers.
- **Stale-on-error** — when Keepa is degraded, the strategy serves
  stale cache where available. Operators should check
  `<cache_root>/token_log.jsonl` for `"stale": true` entries to
  identify which ASINs may be out of date.
- **No FBA-only filter** — the storefront list includes any product
  the seller has stocked, including FBM/discontinued lines. Manual
  triage on the output CSV is recommended before supplier outreach.
