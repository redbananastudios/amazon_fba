# Strategy: `oa_csv`

**Type:** Online-arbitrage candidate-list import
**Status:** Production (leads-only)
**Implementation:** `fba_engine/steps/oa_csv.py` + `shared/lib/python/oa_importers/` + `fba_engine/strategies/oa_csv.yaml`

---

## What this strategy does

Read an OA candidate CSV exported from a third-party tool (SellerAmp
2DSorter, Tactical Arbitrage, OAXray), filter against the global
exclusions list, and emit a canonical candidates CSV with ASIN +
retail context.

This is an import strategy, not a discovery strategy — the upstream
tool already did the filtering. The job here is to pull the CSV into
the canonical schema and apply our exclusions list so we don't pursue
brands/ASINs we've already evaluated.

---

## When to use it

- You ran SellerAmp / Tactical Arbitrage / OAXray and have a CSV of
  candidate ASINs with retail-side prices
- You want to filter that list against the niche exclusions you've
  already accumulated
- You're going to manually retail-arbitrage these (clicking through
  to retailer URLs) — the buy path doesn't need supplier outreach

For wholesale leads from a competing seller, see `seller_storefront`.

---

## Inputs

- **OA candidate CSV** at any path. Pass via `--context csv_path=...`.
  The importer auto-detects column names (case-insensitive,
  punctuation-insensitive) for `asin`, `cost` / `buy_cost`,
  `url`, `name` / `title`. Required columns: `asin` and a recognisable
  cost column.
- **Importer feed name** — `selleramp` (only one with full schema
  support today), `tactical_arbitrage`, or `oaxray` (both stubbed).
- **Exclusions CSV** at `fba_engine/data/niches/exclusions.csv` (or
  override via `exclusions_path` in step config). Rows whose ASIN is
  in the exclusions are dropped.

---

## Run

```bash
python -m fba_engine.strategies.runner \
    --strategy fba_engine/strategies/oa_csv.yaml \
    --context feed=selleramp \
              csv_path=path/to/2dsorter-export.csv \
              run_dir=fba_engine/data/strategies/oa_csv/selleramp \
              timestamp=$(date +%Y%m%d_%H%M%S)
```

---

## Outputs

In `<run_dir>/`:

- `oa_candidates_<feed>_<ts>.csv` — canonical schema (asin, source,
  feed, retail_url, retail_cost_inc_vat, retail_name)
- `run_summary.json` — metadata: row counts, exclusion count, step
  timings, output paths.

---

## Schema

| Column | Source | Notes |
|---|---|---|
| `asin` | input CSV | Required |
| `source` | constant | `"oa_csv"` for downstream branching |
| `feed` | input | `selleramp` / `tactical_arbitrage` / `oaxray` |
| `retail_url` | input CSV | The retailer's product page (where the buy happens) |
| `retail_cost_inc_vat` | input CSV | Per PRD §6.4 this is the canonical `buy_cost` |
| `retail_name` | input CSV | Product display name from the upstream tool |

---

## Pipeline

```
discover → output CSV
```

Single step. OA buyers go directly to the `retail_url` already in the
discovery output, so `supplier_leads` would be redundant.

Full `calculate` → `decide` chaining requires a `keepa_enrich` step
(fetches `market_price` / `fees` / `sales_estimate` per ASIN) — that
step is future work. The existing `resolve` step is EAN-keyed and
assumes supplier-pricelist input, which doesn't fit OA's pre-resolved
ASINs.

---

## Configuration knobs

In `shared/lib/python/oa_importers/<feed>.py`:

- Column-name candidate list per importer. Add a new alias (e.g.
  `"unit cost"`) if a future export format needs it.

Per-run via context:

- `csv_path` — input file (required)
- `feed` — importer name (required)
- `exclusions_path` — override default exclusions CSV
- `run_dir` / `timestamp` — output location

---

## Known limitations

- **selleramp is the only fully-implemented importer.** TA + OAXray
  raise `NotImplementedError` at parse time. Add their schema parsers
  to `shared/lib/python/oa_importers/<feed>.py` when needed.
- **No decision pipeline.** Output is leads-only. The downstream user
  manually sorts the CSV by `retail_cost_inc_vat` (or by an external
  tool's profit estimate that they exported alongside) and clicks
  through to retailer pages.
- **No retailer-side enrichment.** The CSV's `retail_cost_inc_vat`
  is what the upstream tool snapshotted at export time. Live retailer
  prices may have moved.
