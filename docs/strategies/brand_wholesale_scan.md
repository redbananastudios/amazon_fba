# Strategy: `brand_wholesale_scan`

**Type:** Wholesale catalogue scan, Keepa-Product-Finder-driven
**Recipe:** [`brand_wholesale_scan.json`](../../fba_engine/_legacy_keepa/skills/keepa-product-finder/recipes/brand_wholesale_scan.json)
**Engine YAML:** [`keepa_finder.yaml`](../../fba_engine/strategies/keepa_finder.yaml)
**Cowork run:** [`orchestration/runs/keepa_finder.yaml`](../../orchestration/runs/keepa_finder.yaml)

---

## Thesis

We hold trade accounts (or could open one) with specific brands. Pull
every ASIN those brands currently have on Amazon UK, rank by economics
against trade pricing. Wholesale sellers use this heavily — paste a
brand list from a supplier to instantly find every product they carry
on Amazon.

The complementary strategy is `supplier_pricelist` — that one starts from
the supplier's actual SKU list. This one starts from Amazon's view of the
brand, which catches:

- Discontinued items the supplier still has stock of
- Items the supplier hasn't catalogued yet
- Multipack / bundle variants the supplier sells as singles
- Brand variations / sub-brands

Worth running both and diffing.

## When to run it

- You're evaluating whether to open a trade account with a specific brand
  (or list of brands) — this tells you their Amazon footprint
- You already have a trade account — pull the catalogue periodically to
  spot new SKUs the supplier added
- You're researching competitor brands you don't yet hold — see what
  shape their Amazon presence takes

Unlike `amazon_oos_wholesale`, this strategy does NOT require a category
— the brand list itself is the scope.

## Filter rationale

| Filter | Value | Why |
|---|---|---|
| `BUY_BOX_SHIPPING_current` | £12 – £40 | Wider than `amazon_oos` floor — brand catalogues include lower-priced SKUs |
| `SALES_current` | ≤ 150,000 | BSR ceiling, looser than other recipes — long-tail brand SKUs included |
| `SALES_avg90` | ≤ 120,000 | 90-day ceiling, matches |
| `COUNT_NEW_FBA_current` | ≥ 1 | Min 1 (not 2) — brand-direct sellers may legitimately be solo on a listing |
| `brand` (UI autocomplete) | from `--brands` arg | The defining filter — multi-select via Keepa's autocomplete-brand field |
| Global (auto-merged) | hazmat=No, exclude `Clothing, Shoes & Jewellery` | From `shared/config/global_exclusions.yaml` |

**Brand handling.** The CLI accepts `--brands "LEGO,Hasbro,Mattel"` —
the keepa-product-finder skill resolves each via Keepa's autocomplete and
clicks the matching dropdown entry. Brand names must be Keepa-canonical
(case-insensitive substring match against their dropdown text).

## Engine config

No `calculate_config` extras — `stability_score` isn't applied here
because brand-list scans deliberately span volatile and stable products.

No `decide_overrides` — default thresholds apply. The wholesale flow
emits `max_buy_price` per row.

If the trade pricelist is loaded into a sibling `supplier_pricelist` run
later, real `buy_cost` replaces the £0 default and ROI gates apply
properly.

## Expected output

Typical row count: **wide range, depends on brand size.** A scan of
"LEGO" alone might return 1000+ ASINs. A small UK brand might return 20.

The wider seller-count window (`≥1`) means more SHORTLIST candidates,
including some that other recipes would have rejected as private-label
risk. Pair with manual review — a single FBA seller on a brand listing
is suspicious unless you know it's the brand themselves.

## How to run

**Cowork:** dispatch with `recipe=brand_wholesale_scan` and `brands="..."`.

**Manual / dev:**

```bash
$keepa-product-finder recipe=brand_wholesale_scan \
    brands="LEGO,Hasbro,Mattel,Schleich" \
    output=./output/2026-05-02/keepa_brand_scan.csv

python run.py --strategy keepa_finder \
    --csv ./output/2026-05-02/keepa_brand_scan.csv \
    --recipe brand_wholesale_scan \
    --output-dir ./output/2026-05-02
```

## Gotchas

- **Brand names must match Keepa's dropdown.** "Lego" might match "LEGO"
  (case-insensitive); "Schleich" might match "Schleich" exactly but not
  "Schleich Bayala". Verify via Keepa Product Finder UI before adding
  brands the skill hasn't resolved before — the skill saves verified
  matches back to `keepa-finder-values.md` for reuse.
- **Multipack variants count as separate ASINs.** A LEGO set sold as
  single + 4-pack = 2 ASINs — both will appear, with different BSRs and
  sometimes wildly different unit economics.
- **No category filter.** Brand-list scans intentionally span every
  category the brand sells in. If you only want toys from a brand that
  also sells homeware, scope manually after the export.
- **Brand outreach already done?** Run with the supplier's current
  pricelist via `supplier_pricelist` for accurate ROI; this strategy is
  for *exploring* a brand's catalogue, not for deciding what to reorder.
