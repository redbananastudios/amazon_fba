# FBA Engine — Specification

**Status:** Authoritative as of 2026-04-28
**Supersedes:** `docs/archive/PRD_v5.md`, `docs/archive/BUILD_PROMPT_v5.md`

This document is the source of truth for the FBA engine's business logic.
Where this document and code disagree, file an issue — both must be brought
into alignment, never one quietly tolerated.

---

## 1. Context

A UK-based, **non-VAT-registered** Amazon seller. Sells on amazon.co.uk via
both FBA (Fulfilled by Amazon) and FBM (Fulfilled by Merchant). Sources from
UK trade suppliers; price lists arrive as CSV/XLSX/PDF/HTML downloads.

Implications:

- **Buy cost = supplier price including VAT.** Seller cannot reclaim VAT.
- **Sell price = full gross revenue on Amazon.** Seller charges no VAT.
- **VAT rate is fixed at 20%.** No other rate exists in this system.

---

## 2. Pipeline shape

The engine processes data through six logical phases. Per the architecture
direction (single engine, ordered steps, named strategies), these are
expressed as steps in `fba_engine/steps/` (built out in step 4 of the
reorganisation; today the logic lives in `shared/lib/python/sourcing_engine/`).

```
01_discover    Get candidate ASINs (from supplier feeds OR Keepa niche search)
02_resolve     ASIN ↔ EAN ↔ supplier matching, case detection
03_enrich      Market data (Keepa, SellerAmp, SP-API, IP risk)
04_calculate   Fees, conservative price, profit, ROI
05_decide      SHORTLIST / REVIEW / REJECT (and BUY / NEGOTIATE / WATCH / KILL)
06_output      CSV, Excel, Markdown, Sheets
```

A **strategy** is a named composition of steps with config. Today there are
two:

- **`supplier_pricelist`** — supplier-feed-first, EAN→ASIN resolution
- **`keepa_niche`** — Amazon-listing-first, ASIN-with-velocity discovery

Future strategies (Skill 99 / find-suppliers, brand-direct outreach, retail
arbitrage) are added without changes to the engine — they're new compositions
of existing steps plus possibly one new discovery step.

---

## 3. Decision logic

### 3.1 Hard rejections

A row is REJECTed (no human review needed) if any of:

- EAN missing or fails checksum (EAN-8 / EAN-13 / UPC-A)
- No Amazon match
- `VAT_UNCLEAR` and no valid `buy_cost`
- `sales_estimate < min_sales_review` (default 10/month)
- Both `profit_current < min_profit_absolute` AND `profit_conservative < min_profit_absolute`
- `PRICE_MISMATCH_RRP` flag set (Amazon price wildly different from supplier RRP — usually a wrong-EAN match)

### 3.2 SHORTLIST gate

A row reaches SHORTLIST iff **all** of:

- `profit_conservative >= min_profit_absolute` (default £2.50)
- `roi_conservative >= target_roi` (default 30%)
- `sales_estimate >= min_sales_shortlist` (default 20/month)
- No flag in `SHORTLIST_BLOCKERS`:
  - `PRICE_FLOOR_HIT` — historical conservative below break-even
  - `VAT_FIELD_MISMATCH` — supplier VAT fields conflict
  - `VAT_UNCLEAR` — cannot determine buy cost

**Gated rows DO reach SHORTLIST** with a "GATED" indicator in the decision
reason. Ungating is a real revenue strategy; rejecting on gating is too
conservative.

ROI replaces the legacy margin gate. ROI = profit / buy_cost is the truer
measure of capital efficiency for a reseller. Margin is computed and shown
in output for human reference but does not gate decisions.

### 3.3 REVIEW

Rows that fail SHORTLIST but aren't hard-rejected. Specifically:

- Profitable but with one of: `HIGH_MOQ`, `SIZE_TIER_RISK`, `SIZE_TIER_UNKNOWN`,
  `SINGLE_FBA_SELLER`, `AMAZON_ON_LISTING`, `AMAZON_STATUS_UNKNOWN`,
  `MULTI_ASIN_MATCH`, `STORAGE_FEE_RISK`, `PRICE_BASIS_AMBIGUOUS`,
  `CASE_MATCH_SKIPPED`, `CASE_QTY_UNKNOWN`
- `min_sales_review <= sales_estimate < min_sales_shortlist`
- `gated == "UNKNOWN"` while otherwise SHORTLIST-eligible
- Profitable at `current` but not at `conservative` — needs human eyes to assess
  whether the conservative price is reachable

### 3.4 What does NOT block SHORTLIST

- `INSUFFICIENT_HISTORY` — flag visible, human decides
- `SIZE_TIER_UNKNOWN` — fallback fee applied (conservative), flag visible
- FBM (no FBA sellers) — FBM is a valid sourcing path, fee model switches
- `gated == "Y"` — SHORTLIST with indicator (was REJECT in v5 PRD)

---

## 4. Pricing rules

### 4.1 Sell price

`market_price = min(buy_box_price, lowest_fba_seller_price)` when both > 0,
else whichever is positive. Lowest FBA alone is too distressed; Buy Box alone
ignores undercutting. Taking the lower of the two is conservative.

**Never** use lowest_fba_price alone as sell price.

### 4.2 Conservative price

The number that matters for SHORTLIST decisions. Two values:

- **`raw_conservative_price`** — 15th percentile of FBA prices over the last
  90 days, excluding periods where `fba_seller_count == 0`. Minimum 30 days
  of qualifying data required; below that, raw = market_price and
  `INSUFFICIENT_HISTORY` flag fires.

- **`floored_conservative_price`** — `max(raw_conservative_price, break_even)`
  for display only. Never used in profit, ROI, or decision logic.

### 4.3 Buy cost

```
buy_cost = supplier_price_inc_vat   # if supplier provides inc-VAT
         = supplier_price_ex_vat × 1.20   # if only ex-VAT given
```

Resolution states:

- Both fields, consistent (within £0.02 tolerance) → use inc-VAT
- Both fields, inconsistent → use inc-VAT, flag `VAT_FIELD_MISMATCH`
- Only one field → derive the other
- Neither → `VAT_UNCLEAR`, blocks SHORTLIST, blocks REJECT (depending on
  whether profit can be computed at all)

---

## 5. Fee paths

### 5.1 FBA path

```
fees_total = referral_fee + fba_fulfilment_fee + storage_fee_estimate
```

- `referral_fee = sell_price × category_referral_rate` (default 15% if unknown)
- `fba_fulfilment_fee` from size tier; if size_tier UNKNOWN, use
  `fba_fee_conservative_fallback` (£4.50 default) and flag `SIZE_TIER_UNKNOWN`
- `storage_fee_estimate = volume_cbft × storage_rate / sales_estimate`

### 5.2 FBM path

```
fees_total = referral_fee + fbm_shipping_estimate + fbm_packaging_estimate
```

No FBA fee. No storage fee. Always sets `FBM_SHIPPING_ESTIMATED` flag because
the shipping cost is a default — must be overridden with real cost before
trusting the result.

### 5.3 Two passes per match

Fees are calculated independently at each price point:

- `fees_current` uses `market_price`
- `fees_conservative` uses `raw_conservative_price`

Referral fees are percentage-based, so they differ at each price.

---

## 6. Case vs unit matching

A single supplier row can produce up to two output rows: one matched against
a single-unit ASIN, one against a multipack ASIN.

### 6.1 Detecting `supplier_price_basis`

Priority order:

1. Explicit column header (`case price`, `box price`, `unit price`, `each`, etc.)
2. Implied unit price heuristic: `supplier_price / case_qty < min_plausible_unit_price` (£0.50 default) → CASE
3. RRP comparison: `supplier_price > rrp × 0.90` → UNIT (price near RRP can't be a case)
4. Otherwise → AMBIGUOUS, flag `PRICE_BASIS_AMBIGUOUS`, route REVIEW

### 6.2 Match attempts

- **Unit attempt**: lookup `ean` → single-unit ASIN. `buy_cost = unit_cost_inc_vat`.
- **Case attempt** (only if `case_qty > 1` AND basis ≠ AMBIGUOUS):
  - Lookup `case_ean` if different from `ean`
  - Or `ean` lookup with title filter for "{case_qty} x" / "pack of {case_qty}"
  - `buy_cost = case_cost_inc_vat`

### 6.3 Edge cases

- `case_qty == 1` — unit and case are identical; produce one output row only
- `case_qty == 0` — treat as 1
- `case_qty == null` — flag `CASE_QTY_UNKNOWN`, skip case attempt
- Single EAN → multiple ASINs — flag `MULTI_ASIN_MATCH`, route REVIEW (don't auto-select)

---

## 7. Configuration

All thresholds in `shared/config/`:

- `business_rules.yaml` — VAT, marketplace, currency, price range
- `decision_thresholds.yaml` — `target_roi` (the one tunable knob) + derived gates
- `niches/{niche}.yaml` — per-niche Keepa filters and brand lists

Loaded via `shared/lib/python/fba_config_loader.py`. Legacy constant aliases
exposed for backward compatibility (`MIN_PROFIT`, `MIN_SALES_SHORTLIST`, etc.).

`MIN_MARGIN` is not exported. Use `TARGET_ROI` via `fba_roi_gate`.

---

## 8. Error handling

- **Never crash on a single bad row.** Catch, log, mark REVIEW with
  `decision_reason = <exception message>`, continue.
- **Never crash on a file parse failure.** Mark all rows from that file as
  `MANUAL_REVIEW`, log, continue to next file.
- **Always produce output**, even partial.
- Log format: `[TIMESTAMP] [SUPPLIER] [ROW_N] [EAN] — <message>`
- Pipeline summary at end of run: counts of suppliers / rows / matched /
  shortlisted / review / rejected / errors.

---

## 9. Output schema

Three files per run in `fba_engine/data/pricelists/{supplier}/results/{timestamp}/`:

| File | Contents |
|---|---|
| `shortlist_<ts>.csv` | All rows, all decisions, full schema (audit trail) |
| `shortlist_<ts>.xlsx` | SHORTLIST + REVIEW only, colour-coded |
| `report_<ts>.md` | Per-supplier markdown tables |

Schema (top-level fields):

```
supplier, supplier_sku, ean, case_ean, asin, amazon_url, product_name
match_type                  # UNIT | CASE
supplier_price_basis        # UNIT | CASE | AMBIGUOUS
case_qty
unit_cost_ex_vat, unit_cost_inc_vat
case_cost_ex_vat, case_cost_inc_vat
buy_cost                    # used in profit calc; unit or case per match_type
market_price
raw_conservative_price      # used in all decisions
floored_conservative_price  # display only
price_basis                 # FBA | FBM
fees_current, fees_conservative
profit_current, profit_conservative
margin_current, margin_conservative   # display only
roi_current, roi_conservative         # roi_conservative gates SHORTLIST
sales_estimate
max_buy_price
capital_exposure
size_tier
history_days
gated                       # Y | N | UNKNOWN
decision                    # SHORTLIST | REVIEW | REJECT
decision_reason             # populated for every row
risk_flags                  # list of flag strings (joined with "; " in output)
```

In addition to the decision-critical fields above, every output row
carries the Keepa enrichment columns listed in
`fba_engine/steps/keepa_enrich.py::KEEPA_ENRICH_COLUMNS` —
`amazon_price`, `buy_box_avg30`, `buy_box_avg90`, `fba_seller_count`,
`total_offer_count`, `sales_rank_avg90`, `rating`, `review_count`,
`parent_asin`, `package_weight_g`, `package_volume_cm3`,
`category_root` etc. These are informational; the decision logic in
section 3 is purely a function of the fields above.

`fba_seller_count` is FBA-only when the offers list was loaded
(`with_offers=True` — single-ASIN strategies and any path that needs
precise SINGLE_FBA_SELLER detection). Bulk paths default to
`with_offers=False` for token economy and the field falls back to
`stats.current[11]` (FBM + FBA combined) — degraded precision,
preserved historical behaviour. `total_offer_count` always holds the
combined count for callers that legitimately want it.

---

## 10. Risk flags reference

### Hard SHORTLIST blockers

- `PRICE_FLOOR_HIT` — `raw_conservative_price < buy_cost + fees + min_profit`
- `VAT_FIELD_MISMATCH` — supplier VAT fields conflict beyond tolerance
- `VAT_UNCLEAR` — cannot determine buy cost

### REVIEW flags

- `HIGH_MOQ` — `MOQ × buy_cost > capital_exposure_limit` (£200 default)
- `SIZE_TIER_RISK` — within 10% of next size tier boundary
- `SIZE_TIER_UNKNOWN` — fallback FBA fee applied
- `SINGLE_FBA_SELLER` — one FBA seller controls listing
- `AMAZON_ON_LISTING` — Amazon is selling
- `AMAZON_STATUS_UNKNOWN` — can't confirm Amazon presence
- `STORAGE_FEE_RISK` — low velocity, storage fees disproportionate
- `MULTI_ASIN_MATCH` — single EAN matches multiple ASINs
- `PRICE_BASIS_AMBIGUOUS` — can't tell unit vs case price
- `CASE_MATCH_SKIPPED`, `CASE_QTY_UNKNOWN` — case-side incomplete
- `PRICE_MISMATCH_RRP` — Amazon price differs >2x from supplier RRP

### History-derived REVIEW flags

Added in HANDOFF WS2.3. Each fires off a field populated by
`keepa_client.history` helpers and surfaced via `market_snapshot()`.
Thresholds are configurable in `decision_thresholds.yaml::data_signals`.

- `LISTING_TOO_NEW` — `listing_age_days < listing_age_min_days` (default 365)
- `COMPETITION_GROWING` — `fba_offer_count_90d_joiners ≥ competition_joiners_critical` (default 10)
- `BSR_DECLINING` — `bsr_slope_90d > bsr_decline_threshold` (default 0.05 normalised slope)
- `HIGH_OOS` — `buy_box_oos_pct_90 > oos_threshold_pct` (default 0.15)
- `PRICE_UNSTABLE` — `price_volatility_90d > price_volatility_threshold` (default 0.20 CV)

### Visible flags (don't block SHORTLIST)

- `INSUFFICIENT_HISTORY` — under 30 qualifying days of Keepa data
- `FBM_ONLY` — no FBA sellers; FBM fee path applied
- `FBM_SHIPPING_ESTIMATED` — FBM shipping cost is an estimate
- `POSSIBLE_PRIVATE_LABEL` — listing meets private-label heuristics

---

## 11. Versioning

This document is version-controlled with the code. Material changes require
a corresponding code change and test update. Cosmetic changes (typos,
clarifications) can be docs-only.

When the engine's behaviour diverges from this spec, the divergence is a
bug in either this spec or the code. Either fix the code or update the spec
— don't leave them out of sync.
