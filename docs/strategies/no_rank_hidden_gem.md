# Strategy: `no_rank_hidden_gem`

**Type:** Long-tail discovery, Keepa-Product-Finder-driven
**Recipe:** [`no_rank_hidden_gem.json`](../../fba_engine/_legacy_keepa/skills/keepa-product-finder/recipes/no_rank_hidden_gem.json)
**Engine YAML:** [`keepa_finder.yaml`](../../fba_engine/strategies/keepa_finder.yaml)
**Cowork run:** [`orchestration/runs/keepa_finder.yaml`](../../orchestration/runs/keepa_finder.yaml)

---

## Thesis

Some ASINs lack a current sales rank entirely but still sell — recently
relisted products, category-edge cases, long-tail items. Filtering
no-rank listings by review count (with growth in the last 90 days) surfaces
products that ARE moving inventory but don't show up in BSR-driven scans.

Volume per ASIN is lower than mainstream listings. Margin compensates: the
competitive landscape is much thinner because most sellers filter by BSR
and miss these entirely.

## When to run it

- You're saturated on the high-volume listings everyone else is fighting
  over and want a less-contested vertical
- You're willing to accept lower velocity (5–10/month) for higher
  per-unit margins
- You have a category in mind — long-tail items are still
  category-bound (don't try this on "All")

This is a low-volume, high-margin play. SHORTLIST count per run will be
small but the ROI per SHORTLIST item should compensate.

## Filter rationale

| Filter | Value | Why |
|---|---|---|
| `BUY_BOX_SHIPPING_current` | £15 – £50 | Higher floor than other recipes — lower volume needs higher AOV to be worth it |
| `COUNT_REVIEWS_current` | 10 – 1000 | Has reviews (proves it sells) but not viral (still uncrowded) |
| `totalOfferCount` | ≤ 5 | Genuinely uncrowded |
| `noSalesRank` | true (UI checkbox) | The defining filter |
| Global (auto-merged) | hazmat=No, exclude `Clothing, Shoes & Jewellery` | From `shared/config/global_exclusions.yaml` |

**Open knob:** ideally we'd require `delta90_count_reviews ≥ 3` (≥3 new
reviews in last 90 days) to confirm steady selling. The recipe notes flag
this for first-run verification + save-back if the field key turns out to
be different than expected.

## Engine config

The recipe's `decide_overrides` block lowers the velocity floors:

```yaml
decide_overrides:
  min_sales_shortlist: 5    # default 20
  min_sales_review:    2    # default 10
```

Without this override, the engine REJECTs every no-rank ASIN as "Sales
estimate X/month below minimum 10". The override is the whole point of
the strategy: judge no-rank items on margin, not velocity.

No `calculate_config` extras — stability score isn't load-bearing here
(no-rank items are inherently lower-traffic; stability is a smaller
predictor of cash flow than for high-velocity items).

## Expected output

Typical SHORTLIST count: **very small per run** (often single digits).
That's the design — finding a couple of profitable hidden gems in a
500-row export is the success metric, not finding 50.

Common verdicts:
- **REJECT — Sales estimate below minimum 2:** the lowered floor still
  excludes truly dead listings
- **REVIEW — INSUFFICIENT_HISTORY:** common, since no-rank items often
  have <30 days of qualifying data
- **SHORTLIST:** profitable + cleared the lowered velocity gate +
  uncrowded — these are the gems

## How to run

```bash
$keepa-product-finder recipe=no_rank_hidden_gem category="Office Products" \
    output=./output/2026-05-02/keepa_no_rank.csv

python run.py --strategy keepa_finder \
    --csv ./output/2026-05-02/keepa_no_rank.csv \
    --recipe no_rank_hidden_gem \
    --output-dir ./output/2026-05-02
```

## Gotchas

- **Sparse outputs are correct.** If a SHORTLIST per run feels low, the
  recipe is working — long-tail discovery is naturally low-yield. Don't
  loosen filters to manufacture more SHORTLISTs; that defeats the
  thesis.
- **Don't repurpose the override mechanism.** `min_sales_shortlist=5`
  is justified for *no-rank* items. Cargoing the override into other
  strategies leaks low-velocity products into outputs that have
  conservative-stock-fee assumptions baked in.
- **Per-row supplier outreach is harder.** No-rank ASINs are often
  long-discontinued or made by brands you've never heard of. Expect a
  high "no supplier found" rate downstream.
- **The `noSalesRank` Keepa field key needs first-run verification.**
  The recipe notes flag this — the skill should resolve via UI
  inspection on first use and save-back to the reference file.
