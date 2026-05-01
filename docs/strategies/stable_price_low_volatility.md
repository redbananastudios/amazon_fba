# Strategy: `stable_price_low_volatility`

**Type:** Boring-but-predictable wholesale, Keepa-Product-Finder-driven
**Recipe:** [`stable_price_low_volatility.json`](../../fba_engine/_legacy_keepa/skills/keepa-product-finder/recipes/stable_price_low_volatility.json)
**Engine YAML:** [`keepa_finder.yaml`](../../fba_engine/strategies/keepa_finder.yaml)
**Cowork run:** [`orchestration/runs/keepa_finder.yaml`](../../orchestration/runs/keepa_finder.yaml)

---

## Thesis

Products with minimal Buy Box variance over 90 days are predictable cash
conversion. Lower upside (no spike profits) but lower risk of getting
trapped at a price floor. Restricting to ±10% over 30d AND 90d windows
captures genuinely stable listings — products with steady seller
populations and predictable economics.

This strategy optimises for *predictability* over peak ROI. Suitable for
operators who want repeatable cash flow and can't (or won't) play
opportunistic flips.

## When to run it

- You're cash-flow-planning and need products with reliable repeat-buy
  economics
- You want to balance an aggressive opportunistic portfolio with a
  steady base of "boring" SKUs
- You're considering a brand for trade and want to verify their pricing
  doesn't get destroyed periodically

## Filter rationale

| Filter | Value | Why |
|---|---|---|
| `BUY_BOX_SHIPPING_current` | £12 – £40 | Working price range |
| `SALES_avg90` | ≤ 60,000 | Tighter than other recipes — we want established movers, not fringe |
| `COUNT_NEW_FBA_current` | 2 – 8 | Sweet spot — competitive enough to suggest stability, not crowded |
| `monthlySold` | ≥ 30 | Velocity floor — confirm genuine selling |
| Buy Box delta-30d | ±10% | Tight stability window (30-day) |
| Buy Box delta-90d | ±10% | Tight stability window (90-day) |
| `out_of_stock_percentage_90_buy_box` | ≤ 10% | Always available — no scarcity premium |
| Global (auto-merged) | hazmat=No, exclude `Clothing, Shoes & Jewellery` | From `shared/config/global_exclusions.yaml` |

**Open knob:** the Buy Box delta filters in the recipe (`buy_box_volatility`
block) need first-run verification of their Keepa URL-hash field keys —
likely `BUY_BOX_SHIPPING_delta30` / `BUY_BOX_SHIPPING_delta90` but the
skill should confirm and save-back.

## Engine config

The recipe's `calculate_config` enables `compute_stability_score`. For
this strategy specifically, stability_score is the headline output — it's
the metric that distinguishes an OK SHORTLIST from a top one.

Sort the output CSV by `stability_score DESC` and you have a ranked list
of the most predictable cash-flow opportunities the run found.

No `decide_overrides` — default velocity / profit thresholds apply.

## Expected output

Typical SHORTLIST count per category-scoped run: **moderate** — looser
than `amazon_oos_wholesale` (no AMAZON_outOfStock requirement) but
tighter than `brand_wholesale_scan` (volatility ceilings exclude many
listings).

Common verdicts:
- **SHORTLIST with stability_score 0.8–1.0:** the prize — very stable +
  profitable
- **REVIEW with stability_score 0.5–0.8:** OK stability but flagged for
  some other reason (single FBA seller, etc.)
- **REJECT — Sales estimate below minimum:** below the 30/mo floor
- **REJECT — ROI / profit gates:** the wholesale flow's standard fail mode

## How to run

```bash
$keepa-product-finder recipe=stable_price_low_volatility category="Pet Supplies" \
    output=./output/2026-05-02/keepa_stable.csv

python run.py --strategy keepa_finder \
    --csv ./output/2026-05-02/keepa_stable.csv \
    --recipe stable_price_low_volatility \
    --output-dir ./output/2026-05-02
```

## Gotchas

- **Stability ≠ Profit.** A stable listing at break-even is still
  break-even. The engine's normal SHORTLIST gates still apply — stability
  is informational.
- **The ±10% windows are tight.** A single 15% spike in 90 days excludes
  the listing. Loosen in the recipe (e.g. ±15%) if you want a wider net,
  but document why — the strategy's identity is the tightness.
- **Pet Supplies and similar staples** tend to dominate output — that's
  the point. Categories like Toys & Games are inherently more seasonal
  and produce fewer matches here.
- **Don't conflate stable with "safe".** A listing held by Amazon
  90% of the time with stable pricing is "stable" but you can't
  reliably win the Buy Box from Amazon. Filter manually post-export.
