# Strategy: `oa_csv`

**Type:** Online-arbitrage decision pipeline
**Status:** Production
**Implementation:** `fba_engine/steps/oa_csv.py` + `shared/lib/python/oa_importers/` + `fba_engine/strategies/oa_csv.yaml`

---

## What this strategy does

Read an OA candidate CSV exported from a third-party tool (SellerAmp
2DSorter, Tactical Arbitrage, OAXray), filter against the global
exclusions list, enrich each ASIN with Keepa market data, calculate
fees + profit, and assign **SHORTLIST / REVIEW / REJECT** verdicts.

This is the full decision pipeline, not just an import. The upstream
OA tool gives us candidates with retail prices; the strategy adds
Amazon-side market data and our profit thresholds to decide which
ones are actually worth ordering.

---

## When to use it

- You ran SellerAmp / Tactical Arbitrage / OAXray and have a CSV of
  candidate ASINs with retail-side prices
- You want decisions, not just a leads list — the strategy enriches
  with Keepa market data and applies our profit thresholds
- You're going to manually retail-arbitrage the SHORTLIST rows
  (clicking through to retailer URLs) — the buy path doesn't need
  supplier outreach

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
- **Keepa API key** in the `KEEPA_API_KEY` env var. The strategy
  makes one batched `/product?stats=90` call per
  `config.batching.product_batch_size` ASINs.

---

## Run

```bash
export KEEPA_API_KEY=...

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

- `oa_decisions_<feed>_<ts>.csv` — full row set with `decision` +
  `decision_reason` columns. Filter by `decision == "SHORTLIST"` for
  the actionable list.
- `oa_decisions_<feed>_<ts>.summary.json` — metadata: row counts, per-step
  timings, output paths.

---

## Schema

Discovery columns (from the OA tool import):

| Column | Source | Notes |
|---|---|---|
| `asin` | input CSV | Required |
| `source` | constant | `"oa_csv"` for downstream branching |
| `feed` | input | `selleramp` / `tactical_arbitrage` / `oaxray` |
| `retail_url` | input CSV | The retailer's product page (where the buy happens) |
| `buy_cost` | input CSV | Renamed from importer-side `retail_cost_inc_vat` per PRD §6.4 |
| `retail_name` | input CSV | Product display name from the upstream tool |

Enrichment columns (added by `keepa_enrich`):

| Column | Source | Notes |
|---|---|---|
| `amazon_price` | Keepa | Current Amazon offer |
| `new_fba_price` | Keepa | Lowest 3rd-party FBA |
| `buy_box_price` | Keepa | Current Buy Box winner |
| `buy_box_avg90` | Keepa | 90-day Buy Box average |
| `fba_seller_count` | Keepa | Total new offer count (proxy for FBA seller count) |
| `sales_rank` | Keepa | Current sales rank |
| `sales_estimate` | Keepa | "Bought in past month" |

Decision columns (added by `calculate` + `decide`):

| Column | Source | Notes |
|---|---|---|
| `market_price`, `fees_current`, `fees_conservative` | calculate | Pricing math |
| `profit_current`, `profit_conservative` | calculate | After fees + buy_cost |
| `roi_current`, `roi_conservative` | calculate | profit / buy_cost |
| `risk_flags` | calculate | Accumulated soft warnings |
| `decision` | decide | SHORTLIST / REVIEW / REJECT |
| `decision_reason` | decide | Verbatim explanation |

---

## Pipeline

```
discover → keepa_enrich → calculate → decide → output
```

ASINs Keepa hasn't tracked get None-filled market columns; `calculate`
REJECTs them with `"No valid market price"`. The verdict CSV always
contains every row from the input, even REJECTs — operators can
filter by `decision`.

---

## Configuration knobs

In `shared/lib/python/oa_importers/<feed>.py`:

- Column-name candidate list per importer. Add a new alias (e.g.
  `"unit cost"`) if a future export format needs it.

In `shared/config/keepa_client.yaml`:

- `api.marketplace` — Keepa marketplace code (2 = UK)
- `batching.product_batch_size` — ASINs per `/product` call (default 100)
- `cache.ttl_seconds.product` — how long product responses stay fresh

In `shared/config/decision_thresholds.yaml`:

- `target_roi` — single tunable for SHORTLIST gate (default 30%)
- `min_profit_absolute` — absolute profit floor (default £2.50)
- `min_sales_shortlist` / `min_sales_review` — velocity gates

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
- **Stale-on-error fallback** — when Keepa is degraded, `keepa_enrich`
  serves stale cache where available. ASINs with no fallback get
  None-filled market columns and REJECT downstream. Operators should
  check `<cache_root>/token_log.jsonl` for `"stale": true` entries.
- **No retailer-side enrichment.** The CSV's `buy_cost` is what the
  upstream tool snapshotted at export time. Live retailer prices may
  have moved.
- **No XLSX/MD output (yet).** The strategy writes CSV + summary JSON
  only. Run the supplier_pricelist output step against the resulting
  CSV if you need the styled deliverables.
