# Strategy: `amazon_oos_wholesale`

**Type:** Wholesale leads, Keepa-Product-Finder-driven
**Recipe:** [`amazon_oos_wholesale.json`](../../fba_engine/_legacy_keepa/skills/keepa-product-finder/recipes/amazon_oos_wholesale.json)
**Engine YAML:** [`keepa_finder.yaml`](../../fba_engine/strategies/keepa_finder.yaml)
**Cowork run:** [`orchestration/runs/keepa_finder.yaml`](../../orchestration/runs/keepa_finder.yaml)

---

## Thesis

When Amazon stops selling a product (often because the brand pulled them or
supply ran out), the Buy Box becomes available to third-party sellers. An
Amazon out-of-stock percentage of 70%+ over 90 days means Amazon has
essentially abandoned the product — better for wholesale sellers who can
maintain consistent stock.

This strategy finds those abandoned-but-still-selling listings, then sends
each ASIN to supplier outreach so we can negotiate trade pricing direct
with the brand or its UK distributor.

## When to run it

- You want to build a brand-outreach pipeline (each SHORTLIST = one
  cold email to source the product)
- You're prepared to play the long game — wholesale negotiation is weeks,
  not days
- You have a category in mind (Toys & Games, Pet Supplies, etc.) — this
  strategy requires `--category` to scope the Keepa Finder query

## Filter rationale

The recipe's Keepa Product Finder filters:

| Filter | Value | Why |
|---|---|---|
| `BUY_BOX_SHIPPING_current` | £20 – £40 | Working price range per `business_rules.yaml` |
| `SALES_current` | ≤ 100,000 | BSR ceiling — actually selling at all |
| `SALES_avg90` | ≤ 80,000 | 90-day BSR ceiling — consistently selling, not just a recent spike |
| `COUNT_NEW_FBA_current` | 2 – 10 | Competitive but not crowded; ≥2 because single seller = private label risk |
| `monthlySold` | ≥ 50 | Velocity floor — confirms the listing is still viable |
| `AMAZON_outOfStock` | true (UI checkbox) | The actual "Amazon currently OOS" filter |
| Global (auto-merged) | hazmat=No, exclude `Clothing, Shoes & Jewellery` | From `shared/config/global_exclusions.yaml` |

**Open knob:** the Keepa field `outOfStockPercentage90_AMAZON` would let us
demand ≥70% sustained absence rather than just current OOS. The recipe
notes flag this for first-run verification + save-back to
`keepa-finder-values.md` once confirmed.

## Engine config

The recipe's `calculate_config` block enables `compute_stability_score`,
which adds a 0.0–1.0 score column derived from the 30-day + 90-day Buy Box
delta percentages. Used downstream (outside the engine) for capital
allocation decisions — does not gate SHORTLIST.

The engine wholesale flow applies: `buy_cost = 0.0`, `moq = 1`. The
calculate step emits `max_buy_price` (= `market_price - fees - £2.50`)
which is the supplier-negotiation ceiling — anything below that hits ROI
target on resell.

## Expected output

Typical SHORTLIST count per category-scoped run: **single-digit to
low-double-digit**. The 70% OOS criterion is genuinely tight — most
products fail it. SHORTLIST rows are high-quality leads (worth a
brand-outreach email each).

REVIEW count is usually higher — products with profitable economics but
flagged for some reason (Amazon recently came back, etc.).

REJECT is dominated by "ROI below target 30%" (because `buy_cost = 0`
gives ROI = None which fails the gate) and "Sales estimate below
minimum 10/month".

## How to run

**Cowork (recommended):** dispatch the run definition with the recipe and
category. Cowork orchestrates Task 1 (browser export) → Task 2 (engine).

**Manual / dev:**

```bash
# 1. Discovery — browser-driven Keepa Product Finder export
#    (in a Claude Code session with Playwright + keepa.com auth)
$keepa-product-finder recipe=amazon_oos_wholesale category="Toys & Games" \
    output=./output/2026-05-02/keepa_amazon_oos.csv

# 2. Engine — canonical pipeline against the export
python run.py --strategy keepa_finder \
    --csv ./output/2026-05-02/keepa_amazon_oos.csv \
    --recipe amazon_oos_wholesale \
    --output-dir ./output/2026-05-02
```

Outputs:
- `./output/2026-05-02/keepa_amazon_oos.csv` — raw Keepa export (Task 1)
- `./output/2026-05-02/recipe_metadata.json` — run metadata (Task 1)
- `./output/2026-05-02/keepa_finder_amazon_oos_wholesale_<ts>.csv` — verdicts (Task 2)
- `./output/2026-05-02/supplier_leads_amazon_oos_wholesale_<ts>.md` — outreach search URLs (Task 2)

## Gotchas

- **Don't run without `--category`.** Keepa's "All categories" returns a
  random 10k-product subset — completely useless for this thesis.
- **Recipe filter values are starting points.** Tighten or loosen in the
  recipe JSON if real runs return zero or thousands. Don't tweak engine
  thresholds to compensate — the engine reflects business policy; the
  recipe expresses the thesis.
- **The `outOfStockPercentage90_AMAZON` filter** isn't in the URL hash yet.
  When verified, append it to `url_filters` and the strategy gets sharper
  (currently we approximate with `AMAZON_outOfStock` checkbox + tight BSR
  averages).
- **Brand outreach is the bottleneck.** This strategy produces leads, not
  inventory. Track conversion rate (SHORTLIST → supplier reply → trade
  account opened) to know if it's working for you.
