# Operator Playbook

Daily workflow for using the FBA sourcing engine. Concise; assumes
you've already cloned the repo, `pip install -e shared/lib/python`,
and have credentials sync'd via the global CLAUDE.md instructions.

---

## The two main flows

| You want to… | Use this | Cost |
|---|---|---|
| Sweep a category for opportunities | **Bulk** flow (Keepa Browser → CSV → engine) | Free; limited only by Keepa Pro subscription |
| Verdict on a specific ASIN | **Single-ASIN** flow (engine → Keepa API + SP-API) | ~7 Keepa tokens per call (60-bucket holds 8) |
| Walk a competitor's storefront | **Storefront** flow (Keepa Browser → CSV → engine) | Free |
| Process a supplier price list | **Supplier** flow (`run.py --supplier <name>`) | SP-API only; free under our quota |

---

## Bulk flow — daily category sweep

Most common workflow. Find products worth selling that you don't yet have a supplier for.

### 1. Export from Keepa Browser

1. Open [keepa.com](https://keepa.com) (logged in to Pro)
2. Click **Search** → **Product Finder**
3. Apply a recipe's filters — see `_legacy_keepa/skills/keepa-product-finder/recipes/` for canonical filter sets:
   - `amazon_oos_wholesale.json` — Amazon-out-of-stock products with healthy demand (best wholesale leads)
   - `brand_wholesale_scan.json` — listings for specific target brands
   - `no_rank_hidden_gem.json` — low-rank products with steady velocity (overlooked gems)
   - `stable_price_low_volatility.json` — long-term low-risk listings
4. Export the result as CSV → save as `output/<date>/keepa_<recipe>.csv`

### 2. Run the engine

```bash
python run.py --strategy keepa_finder \
  --csv output/<date>/keepa_<recipe>.csv \
  --recipe amazon_oos_wholesale \
  --output-dir output/<date>/
```

The engine adds:
- **Validator verdicts** (BUY / SOURCE_ONLY / NEGOTIATE / WATCH / KILL)
- **Candidate scores** (0-100) + bands (STRONG / OK / WEAK / FAIL)
- **Risk flags** (LISTING_TOO_NEW, BUY_BOX_ABOVE_FLOOR_365D, HIGH_OOS, etc.)
- **SP-API enrichment** (gating status, FBA eligibility, ungate links)
- **Auto-uploaded Google Sheet** (URL printed at end of run)

### 3. Read the XLSX

Sorted **BUY → SOURCE_ONLY → NEGOTIATE → WATCH → KILL**, then by candidate_score desc. Operator hits the top of the file first.

| Column | What it tells you |
|---|---|
| **Verdict** | Bright green BUY / blue SOURCE_ONLY / amber NEGOTIATE / yellow WATCH / grey KILL |
| **Next Action** | Verbatim playbook for this verdict |
| **Opp. Score** | 0-100 (4 dimensions × 25) |
| **Opp. Confidence** | HIGH / MEDIUM / LOW based on data completeness |
| **Strength / Score / Confidence** | Candidate-score companion (different dimensions) |
| **Risk Flags** | Semicolon-joined |
| **Decision Reason** | Why the engine chose this verdict |

### 4. Pick your shortlist

For each BUY or SOURCE_ONLY row that catches your eye:
1. **Click the Amazon URL** — confirm the listing is real and not an obvious mismatch
2. **Click the Ungate Link** (if gated) — apply for ungating in Seller Central
3. For SOURCE_ONLY: research a supplier (brand outreach, wholesale directory, etc.)
4. For BUY: place a small test order

**Don't act on rows below WATCH unless you have a specific reason.** The engine routes them there because something is genuinely off (hard rejects, missing data, declining trajectory, etc.).

---

## Single-ASIN flow — verdict on demand

When you want a chart-quality verdict on one specific ASIN.

```bash
# Wholesale lead (no supplier yet — engine emits max_buy_price)
python run.py --strategy single_asin \
  --asin B0B636ZKZQ \
  --output-dir ./out/

# With your real supplier cost (engine emits ROI verdict)
python run.py --strategy single_asin \
  --asin B0B636ZKZQ \
  --buy-cost 4.00 \
  --output-dir ./out/
```

You get a stdout block:

```
========================================================================
VERDICT: WATCH   (LOW confidence)   score 80/100
ASIN:    B0B636ZKZQ
========================================================================
  >> Monitor price, seller count, and Buy Box movement
  Decision (engine):     SHORTLIST - Passes all thresholds at conservative price

Market:
  Buy Box (current):     GBP 16.90
  Buy Box (90d avg):     GBP 16.32   delta: +3.6%
  ...

Velocity (predicted units/mo at your share)  [share: median-of-4-sellers]:
  Low (worst case):      2/mo  (~GBP 8.92)
  Mid (equal share):     5/mo  (~GBP 22.29)
  High (best case):      8/mo  (~GBP 35.66)
  Test-order rec:        5 units (~3 weeks of mid)

Risk flags:  INSUFFICIENT_HISTORY, SIZE_TIER_UNKNOWN, BUY_BOX_ABOVE_FLOOR_365D
Blockers:    data_confidence=LOW (need HIGH); sales_estimate=70.0 < 100
```

### Reading the verdict block

- **VERDICT** is your action signal. BUY = act now. SOURCE_ONLY = find supplier. NEGOTIATE = price the supplier down. WATCH = monitor. KILL = skip.
- **score** is 0-100. ≥80 = strong candidate. 60-79 = moderate. <60 = weak.
- **confidence** tells you how much to trust the score. STRONG/HIGH = act with confidence. STRONG/LOW = score might be right, trust it less. WATCH/HIGH = product is genuinely weak.
- **Velocity** answers "how many would I sell?". Test-order rec is ~3 weeks of the mid prediction.
- **Share Source**: `median-of-N-sellers` means real per-seller BB share data fed the prediction (high confidence). `equal-split` means the engine fell back to the 1/N assumption (treat with skepticism).
- **Blockers** lists what's between this row and a BUY verdict.

---

## Storefront flow — competitor walk

When you've identified a UK FBA seller making money and want to see their full inventory.

### 1. Export from Keepa Browser

1. Keepa → **Pro** → **Seller Lookup**
2. Enter the seller name or merchant ID
3. Click the **Storefront** tab
4. Click **Export** → save as `KeepaExport-...-SellerOverview-2-<seller_id>.csv`

### 2. Run the engine

```bash
python run.py --strategy seller_storefront_csv \
  --csv KeepaExport-...-SellerOverview-2-A1B2C3.csv \
  --seller-id A1B2C3 \
  --recipe seller_storefront \
  --output-dir ./out/
```

Validated against SPARES-2-GO (UK FBA, 14,521 listings) — produces a verdict per ASIN, sorted with the brand-gated rows surfaced for ungate-application.

---

## Supplier flow — pricelist analysis

When a supplier sends you their full pricelist (CSV/XLSX/PDF/HTML) and you want to spot the SHORTLIST candidates.

```bash
# Engine handles supplier-specific parsing automatically
python run.py --supplier abgee
python run.py --supplier connect-beauty
python run.py --supplier shure
python run.py --supplier zappies
```

Adapter looks for files in `fba_engine/data/pricelists/<supplier>/raw/`. Output lands in `fba_engine/data/pricelists/<supplier>/results/<timestamp>/`.

For a new supplier, copy the `_template/` adapter and implement `ingest.py` + `normalise.py`. No engine changes needed.

---

## Tuning thresholds

Every threshold lives in `shared/config/decision_thresholds.yaml`. Common tweaks:

```yaml
# Loosen BUY for high-volume products (e.g. consumables)
target_roi: 0.25                       # was 0.30
target_monthly_sales: 50               # was 100

# Tolerate more Amazon presence
max_amazon_bb_share_buy: 0.40          # was 0.30

# Allow gated listings to BUY (you have a brand letter)
allow_gated_buy: true                  # was false

# Ship-or-skip behaviour for niche listings
source_only_min_candidate_score: 65    # was 75 — more rows route to SOURCE_ONLY
```

Save the file. Next run uses the new values. Tests with `pytest shared/lib/python/tests/test_config_loader.py` validate the new values are sane (e.g. won't let you set kill thresholds above buy thresholds).

---

## Daily-use cheat sheet

### Find products to sell

```bash
# 1. Keepa Pro → Product Finder → apply filters → export CSV
# 2. Engine
python run.py --strategy keepa_finder \
  --csv output/$(date +%Y%m%d)/keepa_amazon_oos_wholesale.csv \
  --recipe amazon_oos_wholesale \
  --output-dir output/$(date +%Y%m%d)/
# 3. Open the auto-uploaded Sheet — filter Verdict = BUY / SOURCE_ONLY
# 4. Click Amazon URLs to validate, ungate links to apply
# 5. For each shortlist, run single_asin with the operator's quoted buy_cost
```

### Verdict on one ASIN

```bash
python run.py --strategy single_asin --asin B0XXX --buy-cost 4.50 --output-dir ./out/
```

### Walk a competitor

```bash
# 1. Keepa Pro → Seller Lookup → Storefront tab → Export
# 2. Engine
python run.py --strategy seller_storefront_csv \
  --csv KeepaExport-*-SellerOverview-2-<seller>.csv \
  --seller-id <seller> --recipe seller_storefront \
  --output-dir ./out/
```

### Tune thresholds

```bash
# Edit shared/config/decision_thresholds.yaml
# Run validates next time
python run.py --supplier abgee   # or any strategy — config loads at startup
```

---

## When verdicts surprise you

Read `opportunity_blockers` (XLSX column) or the `Blockers:` line (single_asin stdout). It tells you exactly what gate failed.

Common patterns:
- **`data_confidence=LOW`** — Keepa's history is sparse on this listing. Wait 30-60 days for more data, or accept the LOW-confidence verdict
- **`sales_estimate < 100`** — listing's velocity is below the BUY target. Either wait for it to lift (low priority) or accept WATCH and place a small test order
- **`BUY_BOX_ABOVE_FLOOR_365D` flag** — current price is well above the 12-month low. Don't lock in supplier costs now; wait for a price reversion or negotiate harder
- **`amazon_bb_share > 0.30`** — Amazon competes for the BB. Hard to win share against them; skip unless you have a Buy-Box-specific advantage

If the verdict still feels wrong after reading the blockers, it's a **threshold-tuning** issue, not a code issue. Edit `decision_thresholds.yaml`.

---

## See also

- `docs/SPEC.md` — engine business-logic source of truth
- `docs/architecture.md` — repo layout and how strategies compose
- `AGENTS.md` — agent behaviour rules for this codebase
- `docs/strategies/<strategy>.md` — per-strategy quirks
