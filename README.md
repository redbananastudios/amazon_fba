# Amazon FBA Sourcing System

A Python pipeline for finding profitable products to sell on Amazon UK via FBA / FBM. Operated by a single UK seller; the engine consumes Keepa + Amazon SP-API data and produces actionable BUY / SOURCE_ONLY / NEGOTIATE / WATCH / KILL verdicts per ASIN with reasons, blockers, and predicted unit velocity.

Real money is at stake, so the engine prefers conservative-by-design verdicts over clever ones — it refuses to call BUY on a single healthy data point even when the economics look great.

## What it does

Given a list of ASINs (from a supplier price list, Keepa Product Finder export, competitor storefront walk, or a single ASIN), the engine:

1. **Enriches** with Keepa market data (Buy Box price/avg, sales rank, FBA seller count, history)
2. **Calculates** profit + ROI at conservative + current pricing using the FBA fee MCP
3. **Decides** SHORTLIST / REVIEW / REJECT based on the operator's `target_roi` (default 30%)
4. **Scores** each candidate 0–100 across 4 dimensions (Demand / Stability / Competition / Margin) and reports `data_confidence` (HIGH / MEDIUM / LOW)
5. **Validates the opportunity** — emits BUY / SOURCE_ONLY / NEGOTIATE / WATCH / KILL with reasons and blockers
6. **Predicts unit velocity** — share-aware (median of FBA sellers' Buy-Box %) when per-seller data is available, equal-split fallback otherwise
7. **Checks SP-API** — gating status, FBA eligibility, ungate links, listing-quality signals
8. **Outputs** colour-coded XLSX (auto-uploaded to Google Drive), markdown report, run-summary JSON

For single-ASIN deep dives, an optional **Keepa Browser scrape cache** layer pulls chart-quality per-seller data the API doesn't expose.

## Strategies

All strategies share the same engine. Pick the one that matches your input.

| Strategy | Input | Cost | Use it when… |
|---|---|---|---|
| **`keepa_finder`** | Keepa Pro Product Finder CSV export | Free (Keepa Pro subscription) | You're sweeping a category for opportunities |
| **`single_asin`** | One ASIN | ~7 Keepa API tokens (60-bucket holds 8) | You want a chart-quality verdict on a specific listing |
| **`seller_storefront_csv`** | Keepa Pro Seller Storefront CSV export | Free | Walking a competitor's full inventory |
| **`oa_csv`** | Online-arbitrage tool CSV (TA / OAXray / SellerAmp) | Free | Re-validating someone else's discovery list |
| **`supplier_pricelist`** | Supplier's price list (CSV / XLSX / PDF / HTML) | Free | A wholesaler sent you their full catalogue |
| **`keepa_niche`** | Niche folder with multi-phase Keepa exports | Free | Legacy multi-stage niche workflow |
| **`seller_storefront`** | Live Keepa API per-seller call | API tokens | Programmatic storefront walk (rare) |

Recipe JSONs at `_legacy_keepa/skills/keepa-product-finder/recipes/` encode named filter sets for the bulk flow:

- `amazon_oos_wholesale` — Amazon-out-of-stock products with healthy demand (best wholesale leads)
- `brand_wholesale_scan` — listings for specific target brands
- `no_rank_hidden_gem` — low-rank products with steady velocity
- `stable_price_low_volatility` — long-term low-risk listings

## Quick start

```bash
# Sweep a category (the most common flow)
python run.py --strategy keepa_finder \
    --csv output/2026-05-02/keepa_amazon_oos_wholesale.csv \
    --recipe amazon_oos_wholesale \
    --output-dir output/2026-05-02/

# Verdict on one ASIN
python run.py --strategy single_asin --asin B0B636ZKZQ --buy-cost 4.00

# Walk a competitor's storefront
python run.py --strategy seller_storefront_csv \
    --csv KeepaExport-...-SellerOverview-2-A1B2C3.csv \
    --seller-id A1B2C3 \
    --recipe seller_storefront \
    --output-dir ./out/

# Process a supplier's price list
python run.py --supplier connect-beauty
```

Outputs land in the `--output-dir` (or for supplier mode, `fba_engine/data/pricelists/<supplier>/results/<timestamp>/`). Every run produces:

- `<run_id>.xlsx` — colour-coded sheet sorted **BUY → SOURCE_ONLY → NEGOTIATE → WATCH → KILL**, then by candidate score desc
- `<run_id>.md` — markdown report with leading bullet per row
- `summary.json` — per-step metrics + Google Sheet URL when uploaded

## Reading the verdict

Single-ASIN runs print a stdout block:

```
========================================================================
VERDICT: WATCH   (LOW confidence)   score 80/100
ASIN:    B0B636ZKZQ
========================================================================
  >> Monitor price, seller count, and Buy Box movement
  Decision (engine):  SHORTLIST - Passes all thresholds at conservative price

Market:
  Buy Box (current):  GBP 16.90
  Buy Box (90d avg):  GBP 16.32   delta: +3.6%
  ...

Velocity (predicted units/mo at your share)  [share: median-of-4-sellers]:
  Low (worst case):   2/mo  (~GBP 8.92)
  Mid (equal share):  5/mo  (~GBP 22.29)
  High (best case):   8/mo  (~GBP 35.66)
  Test-order rec:     5 units (~3 weeks of mid)

Risk flags:  INSUFFICIENT_HISTORY, SIZE_TIER_UNKNOWN, BUY_BOX_ABOVE_FLOOR_365D
Blockers:    data_confidence=LOW; sales_estimate=70.0 < 100
```

| Field | Meaning |
|---|---|
| **VERDICT** | Action signal: BUY = act now, SOURCE_ONLY = find supplier, NEGOTIATE = price supplier down, WATCH = monitor, KILL = skip |
| **score** | 0–100 candidate score (Demand 25 / Profit 25 / Competition 20 / Stability 15 / Operational 15) |
| **confidence** | HIGH / MEDIUM / LOW based on data completeness — STRONG/HIGH = act with confidence; STRONG/LOW = trust score less |
| **share_source** | `median-of-N-sellers` = real per-seller Buy Box share data fed the prediction; `equal-split` = fallback (treat with skepticism) |
| **Blockers** | Exactly what's between this row and a BUY verdict |
| **Risk flags** | Conditions that reduce confidence or rule out specific verdicts |

## Keepa Browser scrape cache (optional, single-ASIN only)

For chart-quality per-seller data on niche listings, the engine can read a JSON cache produced by a separate scraping process:

```
.cache/keepa_browser/<asin>.json
```

When the cache exists, the engine merges in:
- **Per-seller %BB-won with real seller names** — replaces the API's anonymous-merchant-ID `buyBoxStats`
- **Browser-precomputed 365d signals** — `buy_box_lowest_365d`, `buy_box_avg_*d`, `buy_box_oos_pct_90`, `bsr_drops_30d`
- **Currently-active offers** — stock + sold-30d per live seller

When the cache is absent, the step is a silent no-op — the engine never breaks if you haven't scraped.

The scraper is a separate process (Claude+MCP today, Playwright tomorrow). The cache JSON file is the contract between them. See [`docs/KEEPA_BROWSER_SCRAPE.md`](docs/KEEPA_BROWSER_SCRAPE.md) for the operator workflow.

## Configuration

All thresholds live in [`shared/config/decision_thresholds.yaml`](shared/config/decision_thresholds.yaml). Common tweaks:

```yaml
# Loosen BUY for high-volume products
target_roi: 0.25                       # was 0.30
target_monthly_sales: 50               # was 100

# Tolerate more Amazon presence
max_amazon_bb_share_buy: 0.40          # was 0.30

# Allow gated listings to BUY (you have a brand letter)
allow_gated_buy: true                  # was false
```

Validators in `fba_config_loader.py` reject sane-but-broken combinations at load time (e.g. won't let you set kill thresholds above buy thresholds).

Other config:
- [`shared/config/global_exclusions.yaml`](shared/config/global_exclusions.yaml) — hazmat, restricted categories, title keywords
- [`shared/config/scoring/`](shared/config/scoring/) — per-niche candidate-score overrides (when needed)

## Repo layout

```
.
├── run.py                # launcher (--strategy <name> | --supplier <name>)
├── cli/                  # CLI dispatch + verdict printer
├── docs/                 # SPEC.md, architecture.md, strategies/, OPERATOR_PLAYBOOK.md
├── shared/
│   ├── config/           # decision_thresholds.yaml, global_exclusions.yaml, scoring/
│   └── lib/python/       # canonical engine
│       ├── sourcing_engine/   # decide, opportunity, output writers, models
│       ├── keepa_client/      # Keepa API client + browser_cache
│       └── tests/
├── fba_engine/
│   ├── adapters/         # per-supplier ingest + normalise (one folder per supplier)
│   ├── steps/            # ordered pipeline steps (enrich, calculate, decide, ...)
│   ├── strategies/       # strategy YAMLs that compose steps
│   └── data/             # gitignored — supplier pricelists, niche exports, results
├── services/
│   └── amazon-fba-fees-mcp/   # TypeScript MCP wrapping SP-API
└── orchestration/        # Cowork workflow definitions
```

For details, see [`docs/architecture.md`](docs/architecture.md).

## Tests

```bash
# Python (engine + steps + strategies + CLI) — 1268 tests in ~46s
pytest shared/lib/python/ fba_engine/steps/tests/ \
       fba_engine/strategies/tests/ cli/tests/

# MCP unit tests (114) + live SP-API integration (5)
cd services/amazon-fba-fees-mcp && npm test
cd services/amazon-fba-fees-mcp && npm run test:integration
```

## Onboarding a new supplier

```bash
# 1. Copy the template adapter
cp -r fba_engine/adapters/_template fba_engine/adapters/<new-supplier>

# 2. Implement ingest.py (parses the supplier's file format)
# 3. Implement normalise.py (maps to canonical schema)
# 4. Drop the supplier's pricelists in fba_engine/data/pricelists/<new-supplier>/raw/

# 5. Run
python run.py --supplier <new-supplier>
```

No engine changes needed. The strategy YAML resolves the adapter automatically.

## Key documents

| File | What's in it |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | Engine business logic — decision rules, validator gates, signal tables |
| [`docs/architecture.md`](docs/architecture.md) | Repo layout, how strategies compose, design principles |
| [`docs/OPERATOR_PLAYBOOK.md`](docs/OPERATOR_PLAYBOOK.md) | Daily-use runbook with cheat-sheets |
| [`docs/KEEPA_BROWSER_SCRAPE.md`](docs/KEEPA_BROWSER_SCRAPE.md) | Browser cache scraping protocol |
| [`docs/strategies/`](docs/strategies/) | Per-strategy documentation |
| [`AGENTS.md`](AGENTS.md) | Agent behaviour rules |
| [`CLAUDE.md`](CLAUDE.md) | Project quick-start + Current State (rolling) |

## License & disclaimer

Personal sourcing tooling. Not licensed for redistribution. Use at your own risk — the engine produces decisions based on data; final purchase judgement is the operator's responsibility.
