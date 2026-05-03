# Amazon FBA Sourcing System

> **Step 3 update (2026-04-28):** Repo restructured. One engine, named
> strategies, ordered steps. The two former pipelines (`keepa_niche_finder/`,
> `supplier_pricelist_finder/`) no longer exist as separate top-level trees.
> See `docs/architecture.md`.

## Current State
**Last updated:** 2026-05-03 late evening — Decision-data discipline pipeline complete (PRs #82–#86). Engine now refuses to confidently route any actionable row to BUY without the data needed to make that call.
**Currently working on:** Nothing in flight. End-state for the supplier_pricelist data pipeline: live SP-API pricing on survivors, Keepa-API enrichment with Amazon-fallback for niche listings, Keepa Browser scrape gate for the irreducible historical-data gap. Single branch (main) holds all work.
**Status:** main at HEAD of PR #86. **1209 Python tests pass** in fast suite (was 1196 pre-PR; +13 in #86). MCP suite at 114 + 5 live SP-API still green. Working tree clean.

### What landed this session (2026-05-03 evening — decision-data discipline)

Peter's directive was *"we need solid data to make decisions — do whatever is required"*. Five PRs deliver a layered data-coverage architecture where the engine refuses to confidently ship a BUY verdict without the data needed to back it up.

| PR | Summary |
|---|---|
| **#82** | Survivor refresh chain. Bulk supplier_pricelist now refreshes non-REJECT rows with live Keepa per-ASIN data + Amazon-fallback in `market_snapshot` for BB-derived signals when csv[18] (BB) is empty but csv[0] (Amazon) is rich. Adds `recalculate` flag to calculate, `force` flag to decide, new `keepa_enrich_survivors` step. New `price_history_basis: "BB" \| "AMAZON" \| None` flag tells the analyst which series fed each signal; metric labels get "(Amazon-tracked)" suffix when basis is Amazon. ABGEE: stale 17 actionable rows → live-fresh 9 rows; Britains tractor went Stability 0/25 score 52 → 21/25 score 73 from reading the rich Amazon series that was always there. +37 tests. |
| **#83** | SP-API live pricing for survivors. The preflight step already fetched live BB price + offer counts via getItemOffersBatch — but wrote them to dedicated `live_*` columns the engine's calculate step never read. Added `survivors_only` flag to enrich (filter to non-REJECT before MCP call → 5720-row run drops from 10+ minutes to 40 seconds). New `merge_live_pricing` step maps `live_buy_box → buy_box_price`, `live_offer_count_fba → fba_seller_count`, `live_buy_box_seller=="AMZN" → amazon_status`. Live wins over stale. After this PR: every active ASIN gets current state regardless of Keepa coverage — 4 dark-data ABGEE ASINs that were entirely blank now have live BB price + seller count. +27 tests. |
| **#84** | Decision-data audit validator. `scripts/validate_decision_data.py` audits a buyer-report run and reports each row's coverage band: FULL_DATA / LOW_CONFIDENCE / PROBE_ONLY / INSUFFICIENT_DATA. Surfaced two real findings: CSV writer was dropping ~30 populated columns (operator-visibility issue) + 4 niche ASINs have genuine historical-signal gaps (only Browser scrape can fill). |
| **#85** | CSV writer exposes every engine-computed signal. OUTPUT_COLUMNS expanded from ~52 to **119**. Added all the previously-dropped fields: `roi_conservative`, `candidate_score`, `opportunity_verdict`, `next_action`, `bsr_drops_30d`, `buy_box_oos_pct_90`, `price_volatility_90d`, `listing_age_days`, `fba_pick_pack_fee`, `referral_fee_pct`, predicted_velocity tuple, etc. List fields join with "; ", dict fields (buy_box_seller_stats) JSON-stringify. +6 schema-pinning tests. |
| **#86** | Browser-scrape gate. New `flag_browser_scrape_needed` step. For survivors lacking the historical Browser-only signals (`amazon_bb_pct_90`, `buy_box_drop_pct_90`, `buy_box_min_365d`, `buy_box_oos_pct_90`) AND no cache file: adds `BROWSER_SCRAPE_NEEDED` to risk_flags, drops `data_confidence` to LOW (validator routes away from BUY), writes `<run_dir>/keepa_browser_scrape_needed.json` manifest. Operator runs Claude+MCP scrape per `docs/KEEPA_BROWSER_SCRAPE.md` for those ASINs, re-runs engine. ABGEE produces 4 ASINs in manifest (Schleich + 3 TUBBZ niche listings). +13 tests. |

### Decision quality contract (now enforced)

| Decision support | Source | Status after this session |
|---|---|---|
| Current BB price | SP-API live | ✅ All actionable rows |
| FBA seller count | SP-API live | ✅ All actionable rows |
| Amazon presence | SP-API live | ✅ All actionable rows |
| Gating / restrictions | SP-API | ✅ All actionable rows |
| Sales velocity (rank-based) | Keepa-API | ✅ All rows where rank tracked |
| Listing age, BSR slope, joiners | Keepa-API | ✅ Most rows |
| BB price history 90d/365d | Keepa-API w/ Amazon fallback | ✅ Most rows |
| **Per-seller BB share %** | **Keepa Browser only** | **⚠️ Engine blocks BUY without** |
| **Historical BB drop / OOS pattern** | **Keepa Browser only** | **⚠️ Engine blocks BUY without** |

Operator workflow now:

```
1. python run.py --supplier abgee
   → produces buyer report with full data on most rows
   → flags 4 dark-data ASINs in keepa_browser_scrape_needed.json

2. Operator triggers Claude+MCP scrape on those 4 ASINs
   (manual workflow per docs/KEEPA_BROWSER_SCRAPE.md)
   → cache files appear at .cache/keepa_browser/<asin>.json

3. Re-run engine
   → keepa_browser_enrich step merges cache data into rows
   → flag clears, validator can confidently route those rows
```

### Architectural pattern: cache-as-contract

The `keepa_browser_enrich` step reads from `.cache/keepa_browser/<asin>.json`. The cache file format IS the contract — any future scraper (Claude+MCP today, Playwright tomorrow) that writes the same JSON shape works without engine changes. This deliberately keeps the engine MCP-free while the scraper layer is operator-driven.

**Latest tests baseline:**
```bash
cd services/amazon-fba-fees-mcp && npm test                          # 114/114 unit
cd services/amazon-fba-fees-mcp && npm run test:integration          # 5/5 live SP-API
pytest shared/lib/python/ fba_engine/steps/tests/ \
       fba_engine/strategies/tests/ cli/tests/                       # 1268/1268 in ~46s
```

### What landed this session (2026-05-02 late night — Keepa Browser cache)

| PR | Summary |
|---|---|
| **#74** | `keepa_browser_enrich` step + per-ASIN browser cache. Scraper (Claude+MCP today / Playwright tomorrow) writes `.cache/keepa_browser/<asin>.json`; engine reads and merges chart-level signals into the row. Browser data overrides API where Browser is more accurate (precomputed 365d signals, per-seller %BB-won with names, current active offers). Silent no-op when cache missing. Locked `pct_won` convention to raw percent (0-100) — sub-1% tail sellers are real values; the dual-format heuristic was caught + fixed in pre-merge code review. ASIN path-traversal guard. Wired into `single_asin.yaml` + `keepa_finder.yaml`. Single-ASIN printer adds "Buy Box dominance (Keepa Browser scrape)" block. +18 tests. Live demo on B001Y54F88 (Okatsune secateurs): velocity went from 1/mo → 6/mo via `[share: median-of-7-sellers]` after writing the cache. |

**Key new files / contracts:**
- `shared/lib/python/keepa_client/browser_cache.py` — Pydantic schema (`BrowserScrape`, `BrowserSellerStat`, `BrowserActiveOffer`, `BrowserProductDetails`) + cache I/O (`read`, `write`, `cache_path_for`, `cache_root`). The cache file is the contract — any future scraper that writes the same JSON shape works.
- `fba_engine/steps/keepa_browser_enrich.py` — `add_browser_enrich(df)` reads cache per row. Browser-derived `buy_box_seller_stats` replaces API-shape dict with real seller names + isFBA. Browser-precomputed `buy_box_min_365d` / `buy_box_avg_*d` / `buy_box_oos_pct_90` / `sales_rank_avg_365d` / `bsr_drops_30d` override API-derived versions. `BROWSER_ENRICH_COLUMNS` tuple for downstream output writers.
- `docs/KEEPA_BROWSER_SCRAPE.md` — operator workflow ("ask Claude to scrape ASIN X" → run engine).
- `.cache/keepa_browser/<asin>.json` is gitignored.

### Earlier in this session (2026-05-02 night — velocity + Browser-CSV parity)

Three more PRs (#67-#69) on top of the operator-validator-fidelity
sweep, answering "how many units would I sell?" and closing a real
parity bug on the Browser-CSV path.

| PR | Summary |
|---|---|
| **#67** (F) | `bsr_drops_30d` snapshot field + `predict_seller_velocity()` helper. Returns {low, mid, high} units/mo for a new entrant. Uses `min(monthlySold, bsr_drops × 1.5)` so Keepa's over-estimating monthlySold model on niche listings doesn't inflate the prediction. Multipliers for joiners / OOS / BSR-slope. Wired into validator output + single_asin printer (Velocity section + test-order recommendation). |
| **#68** (G) | Share-aware velocity from `stats.buyBoxStats`. Keepa already returned per-seller `percentageWon` in the response — we just weren't modelling it. New entrant's expected share = median of existing FBA sellers' shares. Falls back to equal-split when buyBoxStats empty. Output carries `predicted_velocity_share_source` so operator sees which assumption fed the prediction. **Zero new API tokens** — already in our `/product?stats=90` response. |
| **#69** (H) | **Real bug**: `keepa_finder_csv.py` field names mismatched the validator's expected names (e.g. `buy_box_pct_amazon_90d` vs `amazon_bb_pct_90`). Every keepa_finder run since PR #55 was silently emitting None for those fields → validator gates / flags didn't fire. Renamed + added new mappings (`buy_box_min_365d`, `bsr_drops_30d`, `buy_box_avg30`, `rating`, `review_count`). Same updates in market_data.py. SPEC.md §9 now carries a "Signal availability by enrichment path" table. |

### Browser-first workflow (current default per Peter)

```
1. Operator opens Keepa Pro → Product Finder → applies filters → exports CSV
2. python run.py --strategy keepa_finder \
     --csv ./output/<run_id>/keepa_<recipe>.csv \
     --recipe amazon_oos_wholesale --output-dir ./output/<run_id>/
3. Open the auto-uploaded XLSX. Sorted BUY → SOURCE_ONLY → NEGOTIATE
   → WATCH → KILL with all validator flags + candidate-score +
   data-confidence visible.
```

For single-ASIN deep dives (occasional, when you want every signal):
```
python run.py --strategy single_asin --asin B0XXX --buy-cost X
```
Costs ~7 Keepa tokens (60-bucket holds 8). Adds slope / CV /
per-seller-share signals not in the Browser CSV.

### B0B636ZKZQ live verdict (final form across all PRs)

```
VERDICT: WATCH (LOW confidence) score 80/100
  >> Monitor price, seller count, and Buy Box movement
  Decision (engine): SHORTLIST - Passes all thresholds at conservative price

Velocity (predicted units/mo at your share)  [share: median-of-4-sellers]:
  Low (worst case):      2/mo  (~£8.92)
  Mid (equal share):     5/mo  (~£22.29)
  High (best case):      8/mo  (~£35.66)
  Test-order rec:        5 units (~3 weeks of mid)

Risk flags: INSUFFICIENT_HISTORY, SIZE_TIER_UNKNOWN, BUY_BOX_ABOVE_FLOOR_365D
Blockers:   data_confidence=LOW (need HIGH); sales_estimate=70.0 < 100
```

Cross-check vs the Browser BB Statistics tab (Just This Retail 46%,
MRPM 29%, Trego 10%, ebebekeu ~5%): median 19.5% × 25 monthly =
~5/mo. **Engine output matches manual chart-reading.**

### Signal availability by enrichment path

Documented in SPEC.md §9 as a table. Summary:

- **Both paths (API + Browser CSV):** amazon_bb_pct_90, buy_box_min_365d, buy_box_avg30/90, bsr_drops_30d, rating, review_count, buy_box_oos_pct_90, sales_rank, sales_rank_avg90
- **API-only (need raw csv arrays):** bsr_slope_30d/90d/365d, price_volatility_90d, sales_rank_cv_90d, listing_age_days, yoy_bsr_ratio, variation_count, buy_box_seller_stats

Validator gracefully degrades — missing signals drop to None and
treat as "signal missing" (lowers confidence) rather than "bad
signal" (false flags).

### Operational notes

**Conservative-by-design verdict on niche listings.** B0B636ZKZQ
at £4.00 buy cost has 111% ROI but routes to WATCH because:
- INSUFFICIENT_HISTORY drops data_confidence to LOW
- sales_estimate 70 < 100/mo BUY target
- BUY_BOX_ABOVE_FLOOR_365D fires (current £16.90 vs 12mo low £8)

This is the design — refuse to call BUY on a single healthy data
point. The 5-unit test-order recommendation is the right entry
size: validates sell-through at low capital exposure (~£20).

**Velocity prediction confidence.** When `share_source =
median-of-N-sellers` appears in the printer output, the prediction
used real per-seller BB share data from Keepa's stats.buyBoxStats.
When `share_source = equal-split` appears, fell back to the
1/fba_seller_count assumption. Operator should treat the latter
with more skepticism.

### What landed this session (2026-05-02 late evening — fidelity sweep)

Five PRs (#61-#65) closing the gap between "what a human reads off the
Keepa chart + Amazon listing" and "what the validator sees". Triggered
by Peter running `single_asin` on B0B636ZKZQ and getting WATCH/LOW
because `amazon_bb_pct_90` was missing from the API path.

| PR | Summary | New snapshot fields / flags |
|---|---|---|
| **#61** (A) | `amazon_bb_pct_90` derived from csv arrays — closes the API-path gap vs Browser CSV. Step-function alignment of csv[18] (BB) vs csv[0] (Amazon), ±1p tolerance | `amazon_bb_pct_90` |
| **#62** (B) | 12mo BB floor + 90d sales-rank CV — chart-readable "have we ever seen this cheaper?" + "is this a steady or spiky seller?" | `buy_box_min_365d`, `sales_rank_cv_90d` |
| **#63** (C) | Variation cluster size — `KeepaProduct.variations` was being ignored; now exposes count so niche-looking parents get visible context | `variation_count` |
| **#64** (D) | SP-API listing quality — extends existing catalog-item call; zero new SP-API quota | `catalog_image_count`, `catalog_has_aplus_content`, `catalog_release_date` |
| **#65** (E) | Validator wiring — 2 new flags fire from calculate.py; rank-CV feeds candidate_score Demand penalty | `BUY_BOX_ABOVE_FLOOR_365D`, `LOW_LISTING_QUALITY` |

**Before the sweep**, B0B636ZKZQ at £4.00 buy cost:
```
VERDICT: WATCH (LOW confidence) score 60/100
Why: sales=70/mo→10; ROI=111%+£4.46→20; amazon_bb_pct_90 missing→0; stable→15; ungated+fba→15
Blockers: candidate_score=74 < 75; data_confidence=LOW; sales=70 < 100
```

**After the sweep**, same ASIN + buy cost:
```
VERDICT: WATCH (LOW confidence) score 80/100
Why: sales=70/mo→10; ROI=111%+£4.46→20; AMZ BB=2%+sellers ok→20; stable→15; ungated+fba→15
Risk flags: INSUFFICIENT_HISTORY, SIZE_TIER_UNKNOWN, BUY_BOX_ABOVE_FLOOR_365D
Blockers: data_confidence=LOW; sales=70 < 100
```

Score lifted 60 → 80 (the strong_score threshold). Three blockers
became two (candidate_score gate now passes). Risk flags now include
the chart-readable peak-buying signal (`BUY_BOX_ABOVE_FLOOR_365D` —
current £16.90 vs ~£9 12mo floor).

**Browser CSV vs API question** (Peter asked):
The Keepa Browser CSV export carries **everything you'd see on the
chart** as precomputed columns and is sufficient for ~95% of the
work (bulk discovery, store stalking, niche sweeps). The 1-token/min
API tier is only needed for occasional single-ASIN deep dives — and
the 60-token bucket covers ~8 single-ASIN runs before refilling.
4 of the 5 enhancements above derive from csv[] arrays we already
pull (no new spend); 1 is on SP-API which is free under our quota.

**Operational rule for niche / low-history listings:** the validator
will route them to WATCH (not BUY) even with great economics
because:
- INSUFFICIENT_HISTORY drops data_confidence to LOW
- Sales velocity below the 100/mo BUY target
- BUY_BOX_ABOVE_FLOOR_365D fires when current is well above 12mo low

This is by design — the validator refuses to call BUY on a single
healthy data point. Operator's judgement call from there: a small
test order at 111% ROI is the right move, but locking in 100 units of
supplier inventory based on one chart point is not.

### What landed this session (2026-05-02 evening — final opportunity validation)

**PR #58** — Adds `07_validate_opportunity` step. Pure additive — never alters
SHORTLIST/REVIEW/REJECT. Decides whether a profitable product is **actually
worth acting on now**.

Six new output columns:
- `opportunity_verdict` — BUY / SOURCE_ONLY / NEGOTIATE / WATCH / KILL
- `opportunity_score` — 0-100 (Demand 25 / Profit 25 / Competition 20 / Stability 15 / Operational 15)
- `opportunity_confidence` — HIGH/MEDIUM/LOW (degrades on missing critical inputs)
- `opportunity_reasons` — per-dimension contributors
- `opportunity_blockers` — KILL reasons or BUY blockers
- `next_action` — operator playbook keyed by verdict

Verdict precedence (first match wins):
1. **KILL** — REJECT, profit < 0, ROI < 0.15, sales < 20, `PRICE_FLOOR_HIT`,
   severe volatility (≥0.40), severe BSR decline (≥0.10), Amazon BB ≥ 0.90,
   RESTRICTED, FBA-ineligible
2. **SOURCE_ONLY** — `buy_cost` missing AND demand strong (sales ≥ 100,
   candidate_score ≥ 75, confidence not LOW, BB share < 0.70, volatility ≤ 0.20)
3. **BUY** — every gate passes (decision SHORTLIST + candidate_score ≥ 75 +
   confidence HIGH + sales ≥ 100 + ROI/profit/BB/volatility/OOS/joiners gates)
4. **NEGOTIATE** — strong demand + currently profitable + conservative profit
   < £2.50 (reasons line carries `max_buy_cost` ceiling)
5. **WATCH** — default; blockers list explains what's holding it back

Implementation:
- `shared/lib/python/sourcing_engine/opportunity.py` (~440 lines) — pure
  function core, NaN-safe coercion, string-typed-CSV-numerics safe
- `fba_engine/steps/validate_opportunity.py` — DataFrame wrapper +
  runner step, per-row try/except → KILL fallback
- `OpportunityValidation` dataclass + `get_opportunity_validation()` accessor
  in `fba_config_loader.py` with invariant validator (BB-share ladder,
  ROI/sales kill ≤ buy thresholds)
- 27-key `opportunity_validation:` block in `decision_thresholds.yaml`
  (permissive defaults so older configs load cleanly)
- Wired into both `supplier_pricelist.yaml` (after `enrich`) and
  `keepa_niche.yaml` (after `decision_engine`)
- XLSX: 6 new columns immediately after `decision`. Sort priority extended
  to opportunity_verdict → decision → candidate_score desc → opportunity_score
  desc (stable). Per-cell verdict colour: BUY=green, SOURCE_ONLY=blue,
  NEGOTIATE=amber, WATCH=yellow, KILL=grey
- Markdown: verdict + score + next_action added to display_cols
- SPEC.md §8c documents the full contract

Tests: +38 in `test_validate_opportunity.py` covering every verdict path,
all 12 handoff acceptance scenarios, missing-data robustness (NaN-safe,
string-typed numerics, completely empty row), and the **load-bearing
decision-column-unchanged invariant**.

Code-reviewed: approved with no blocking issues. Reviewer verified verdict
precedence, `_required_buy_cost` algebra, NaN handling, output sort key
composition, and the decision-invariant pin.

**Operator-facing answer to "is this worth acting on now?"** is now visible
in 5 seconds at the top-left of every XLSX, colour-coded by urgency, sorted
so BUY rows lead. Across PRs #52-#58 (this session), test count grew from
1021 → 1204 (+183) with zero regressions.

### What landed this session (2026-05-02 afternoon — candidate validation handoff)

Implementation of [`docs/HANDOFF_candidate_validation.md`](docs/HANDOFF_candidate_validation.md)
across 6 reviewable PRs. Each PR reviewed by code-reviewer agent before
merge; reviewer caught real bugs in PRs #53, #56, #57 (all fixed pre-commit).

| PR | Workstream | Summary | Tests |
|---|---|---|---|
| **#52** (bug fix) | WS1.1 | `fba_seller_count` was sourced from `stats.current[11]` (FBM+FBA combined). New `count_live_fba_offers` helper returns FBA-only count from offers list; falls back to COUNT_NEW when `with_offers=False` (degraded precision documented). New `total_offer_count` snapshot key for callers that legitimately want the combined total. | +9 |
| **#53** (feature) | WS1.2 + 1.3 | Schema unification — API path (`market_snapshot`) now emits the same column set as the CSV-export path (`load_market_data`). 9 new keys (`buy_box_avg30`, `sales_rank_avg90`, `rating`, `review_count`, `parent_asin`, `package_weight_g`, `package_volume_cm3`, `category_root`). KeepaStats gains `avg30` lane. New `_csv_last_value` helper (with odd-length-array fix caught by reviewer). Dead `PRICE_UNSTABLE` constant removed. Schema-parity test pinned. | +19 |
| **#54** (feature) | WS2.1 | New `keepa_client/history.py` time-series module with 8 helpers: `parse_keepa_csv_series`, `bsr_slope` (LSQ slope, mean-normalised, fraction-per-day units), `offer_count_trend`, `out_of_stock_pct`, `buy_box_winner_flips`, `price_volatility` (population CV), `listing_age_days`, `yoy_bsr_ratio`. **90% line coverage** on the module. `_window_pairs_with_sentinels` explicitly named for OOS-vs-everyone-else asymmetry. | +44 |
| **#55** (feature) | WS2.2 + 2.3 + 2.4 | Wired history into `market_snapshot()`: 9 new fields (`bsr_slope_30d/90d/365d`, `fba_offer_count_90d_start/joiners`, `buy_box_oos_pct_90`, `price_volatility_90d`, `listing_age_days`, `yoy_bsr_ratio`). 5 new REVIEW flags fire from `calculate.py`: `LISTING_TOO_NEW`, `COMPETITION_GROWING`, `BSR_DECLINING`, `HIGH_OOS`, `PRICE_UNSTABLE` (re-introduced with real computation). New `data_signals:` config block + `DataSignals` dataclass + `get_data_signals()` accessor with permissive defaults for old configs. | +24 |
| **#56** (feature) | WS3.1-3.5 | New `fba_engine/steps/candidate_score.py` step. **Pure additive** — does NOT alter SHORTLIST/REVIEW/REJECT. Adds `candidate_score` (0-100 int), `candidate_band` (STRONG/OK/WEAK/FAIL), `candidate_reasons`, `data_confidence` (HIGH/MEDIUM/LOW), `data_confidence_reasons`. 4 dimensions × 25 points (Demand/Stability/Competition/Margin). All thresholds in YAML (`candidate_scoring:` block) — zero magic numbers. `_validate_tier_arrays` enforces `len(points) == len(thresholds)+1` at config load. New `review_velocity_90d` field via `history.review_count_change`. Wired into both `supplier_pricelist.yaml` and `keepa_niche.yaml`. | +35 |
| **#57** (feature) | WS3.6 | XLSX colour-coding + sort by `candidate_score` desc within each decision band + per-row leading line in markdown report (`**STRONG** (HIGH confidence) — score 82/100`). Stable sort (`kind="stable"`) preserves insertion order on ties. NaN-truthy bug in markdown summary caught by reviewer + fixed via `_clean_str` helper. | +14 |

**Operator-facing change:** every SHORTLIST/REVIEW row in the XLSX now
carries `candidate_band` + `candidate_score` + `data_confidence` columns
adjacent to `decision`. Colour-coded:
- STRONG / HIGH → green fill + bold green font (act with confidence)
- STRONG / LOW or MEDIUM → amber fill + bold dark-amber font (score might be right, trust it less)
- OK / WEAK → no special highlight (band label visible)
- FAIL → grey fill + grey font (greyed out)

Sheet sorted SHORTLIST first, then REVIEW; within each band,
candidate_score desc. Markdown report carries the same data with a
per-row bullet summary above each band's table.

**Decision logic unchanged.** SHORTLIST/REVIEW/REJECT counts identical
before and after this work. The handoff was strict on this: candidate
scoring is purely additive visibility, never gating.

**The four sign-off questions** the operator can now answer in 5
seconds at a glance:
1. *Is this product profitable?* → `decision`
2. *Is the data telling a strong story?* → `candidate_band`
3. *Do I trust the data?* → `data_confidence`
4. *Why?* → `candidate_reasons`, `data_confidence_reasons`

### What landed this session (2026-05-01)

| PR | Summary |
|---|---|
| **#44** (security) | Untracked leaked Google service account JSON; rotated to key `296eef282133`. gitignore line 76 catches future copies. |
| **#45** (bug fix) | `gated` Y/N/UNKNOWN derived from `restriction_status` in preflight._coerce_result + _seed_row. +13 tests. |
| **#46** (feature) | `seller_storefront_csv` strategy — browser-driven store stalking via Keepa Browser export. +16 tests. |
| **#47** (feature) | `BUY_BOX_ABOVE_AVG90` peak-buying flag in calculate.py (current vs 90d avg ≥20%). Browser-tier-friendly. +9 tests. |
| **#48** (cleanup) | `decide()` dedups flag names in `decision_reason` when a flag is in both SHORTLIST_BLOCKERS and REVIEW_FLAGS. +2 tests. |
| **#49** (feature) | `single_asin` verdict strategy — single-ASIN entry point with stdout verdict block + categoryTree=null pydantic fix. +16 tests. |
| **#50** (engine tuning) | `_pick_market_price` 3-arg fall-through to amazon_price + AMAZON_ONLY_PRICE flag; `single_asin.yaml` ships with `min_sales floors=0` overrides. Triggered by B0B636ZKZQ false-REJECT. +4 tests. |
| **#51** (engine tuning) | Use Keepa offers list for real market price + BSR-drop sales fallback. Engine now reads the live offer table via `offers=20` and counts rank-improvement events as sale proxies when `monthly_sold` is None. +11 tests. |

### B0B636ZKZQ calibration (Casdon Morphy Richards Toaster — Peter's actual product)

PRs #50 + #51 driven by this real-world test. Engine pre-fix REJECTed with "No valid market price"; post-fix:
- Buy Box (current): **£16.90** (matches Keepa Browser screenshot exactly)
- Buy Box 90d avg: **£16.32**
- Sales/month: **73** (BSR-drop heuristic — Keepa's `monthly_sold` was None)
- Verdict (no buy_cost): REVIEW + max_buy_price **£5.96** + "WORTH A SUPPLIER ASK"
- Verdict with `--buy-cost 4.00`: SHORTLIST + 111.4% ROI + PURSUE
- Amazon's £23.86 offer (dormant inventory) correctly visible in printer but not driving economics

**Workspace Shared Drive (gsheet auto-upload):**
- Service account `claude@mcp-access-490812.iam.gserviceaccount.com` is a Content Manager on the **Amazon FBA** Shared Drive (`0ABr-7qEsFb7FUk9PVA`).
- `GOOGLE_DRIVE_FOLDER_ID` → drive root via `credentials.env` + sync-credentials.ps1.
- `runner.py._push_xlsx_to_gsheet` invokes `push_to_gsheets.js` after XLSX writes, captures URL into `summary.json.outputs.gsheet_url`.

**Keepa API verified at 1 token/min tier:**
- `KEEPA_API_KEY` added to credentials. Bucket capacity 60, refill 1/min — usable for tiny smoke tests, not full storefront walks.
- 5-ASIN smoke (SPARES-2-GO storefront niche) cost 4 tokens. Findings: low-popularity ASINs have empty Buy Box (idx 18) AND NEW_FBA (idx 10) history — the 15th-percentile conservative-price gate doesn't apply for these even with API access. The browser-CSV-derived peak flag (PR #47) is the right tool for these.

**Store-stalking workflow (operationally usable today):**
```
1. Keepa Browser → Pro → Seller Lookup → <competitor> → Storefront tab → Export
2. Engine:
   python run.py --strategy seller_storefront_csv \
     --csv <KeepaExport-...-SellerOverview-2-<seller_id>.csv> \
     --seller-id <seller_id> \
     --recipe seller_storefront \
     --output-dir ./out/
3. Open the auto-uploaded Sheet in the Amazon FBA Shared Drive.
   Brand-gated rows → click Ungate Links → apply on Seller Central
   Peak-buying rows (BUY_BOX_ABOVE_AVG90 flag) → wait or negotiate harder
```

Validated against SPARES-2-GO (UK FBA, 14,521 listings): 100-row sample → 94 REVIEW / 6 REJECT, 96 BRAND_GATED / 4 UNRESTRICTED. Brand mix revealed: Henry (3rd-party they ungated) + Spares2go (their private label).

### Operationally usable workflow (post real-run)

```
1. Browser (Cowork or Claude Code instance with $keepa-product-finder skill):
   $keepa-product-finder recipe=amazon_oos_wholesale category="Toys & Games"
   → exports CSV to ./output/<run_id>/keepa_<recipe>.csv

2. Engine:
   python run.py --strategy keepa_finder \
     --csv ./output/<run_id>/keepa_<recipe>.csv \
     --recipe amazon_oos_wholesale \
     --output-dir ./output/<run_id>/

3. Operator opens the resulting <recipe>_<ts>.xlsx — every gated row carries:
   - Amazon URL (clickable → product listing)
   - Ungate Links (clickable → Apply-to-sell page)
   - Ungate Status / Required Docs / Brand Required / Attempted At / Message
     (5 reserved columns, blank by default — operator fills as ungate apps progress)
```

### First real Toys & Games run (2026-05-01, validated)

| Filter funnel | Count |
|---|---|
| Keepa Product Finder UI hits (Toys & Games + AMAZON_outOfStock + recipe filters) | 265 |
| After global title-keyword exclusions | 251 |
| **Engine verdicts** | **0 SHORTLIST / 250 REVIEW / 1 REJECT** |

The 0 SHORTLIST is by design — wholesale flow uses `buy_cost = 0.0`, so the ROI gate emits `no_buy_cost` → REVIEW with `max_buy_price` populated as the supplier-negotiation ceiling.

| SP-API gating breakdown (174 gated rows) | Count |
|---|---|
| `BRAND_GATED` — needs brand outreach OR account-metric auto-approve | 161 |
| `RESTRICTED` — different gating class | 13 |
| `UNRESTRICTED` (immediately listable) | 77 |
| `catalog_hazmat = true` (caught by post-enrich safety net) | 2 |

Top wholesale leads surfaced: **Hasbro Transformers, Mattel WWE, Games Workshop Warhammer, Funko, BABESIDE / JIZHI Reborn Dolls.** Run artefacts in `fba_engine/data/strategies/keepa_finder/20260501_122031/`.

### What landed this session (2026-05-02, Keepa Product Finder strategies)

**Approach:** browser-export-driven, not API-driven. Peter doesn't have a Keepa API subscription and the existing `$keepa-product-finder` skill (in `_legacy_keepa/skills/keepa-product-finder/`) already produces CSV exports from the Keepa Product Finder UI. This branch wires those exports into the canonical engine via a thin column-mapper step + 4 recipe JSONs that encode named filter sets.

| Commit | Summary |
|---|---|
| 1 | `shared/config/global_exclusions.yaml` + `GlobalExclusions` loader. Three exclusions ship: hazmat, `Clothing, Shoes & Jewellery` root, title keywords (clothing/apparel/shoe/boot/footwear). Permissive defaults if file absent. |
| 2 | 4 recipe JSONs in `_legacy_keepa/skills/keepa-product-finder/recipes/`. Each declares Keepa filter set + `global_exclusions: "auto"` + optional `calculate_config` / `decide_overrides`. |
| 3 | `keepa-product-finder` SKILL.md update — Recipes section, recipe loading workflow, recipe_metadata.json sidecar, Cowork two-task prompt example. |
| 4 | `fba_engine/steps/keepa_finder_csv.py` — column mapper (175-col Keepa export → canonical schema), ASIN dedup against `data/niches/exclusions.csv`, post-export keyword + category filter. Smoke test against real 10k-row `kids_toys_phase1_raw.csv`. |
| 5 | `fba_engine/strategies/keepa_finder.yaml` — generic chain: discover → enrich (leads) → calculate → decide → supplier_leads. Discovery schema aligned with `04_calculate`'s expected column names (`buy_box_price`, `new_fba_price`, `referral_fee_pct` /100, `amazon_status` derived, wholesale defaults `buy_cost=0` + `moq=1`). |
| 6 | `04_calculate` consumes `compute_stability_score` config flag. New `add_stability_score()` derives 0.0–1.0 score from Buy Box delta-30d/90d. Default off — backwards compat preserved. |
| 7 | `05_decide` consumes `config["overrides"]` dict — generic per-call threshold override (no_rank_hidden_gem lowers `min_sales_shortlist` 20→5). `decide()` gains optional `overrides=` kwarg in the canonical engine. Unknown keys + invariant violations raise loud. |
| 8 | `orchestration/runs/keepa_finder.yaml` — Cowork two-task definition (browser-driven discovery + engine). Generic across all 4 recipes. |
| 9 | `run.py --strategy <name>` CLI dispatch via `cli/strategy.py`. Loads strategy YAML + recipe JSON, mutates StrategyDef so recipe configs flow to calculate/decide steps, runs the chain, prints verdict summary. End-to-end smoke against synthetic Keepa CSV. |
| 10 | Per-recipe docs in `docs/strategies/` (4 markdown files); CLAUDE.md update. |

**Test count delta:**
- Commit 1: +11 (global_exclusions loader, helpers, missing-file fallback)
- Commit 4: +30 (column mapping, exclusions, malformed input, sidecar, real-export smoke)
- Commit 5: +6 (4 schema-alignment tests + 2 strategy YAML tests)
- Commit 6: +8 (stability_score helper + run_step config plumbing)
- Commit 7: +9 (override mechanism per key, invariants, alias, no-op)
- Commit 9: +22 (argparse, YAML/recipe resolution, recipe→config wiring, full dispatch smoke)
- **Total: +86 tests, 820 → 906 → 902** (the -4 net is because some tests in the keepa_finder_csv update changed shape — verified all 902 pass green).

**Engine deltas summary** (additive, backwards-compat preserved):
- `fba_config_loader.GlobalExclusions` + `get_global_exclusions()` accessor
- `calculate.run_step` consumes `compute_stability_score: bool`
- `decide.run_step` consumes `config["overrides"]: dict`
- `decide()` in `sourcing_engine.pipeline.decision` gains optional `overrides=` kwarg
- `run.py --strategy <name>` dispatch (existing `--supplier` and `open` paths unchanged)

**No live Keepa API integration.** The `keepa_client` library exists from Phase 2 (PRs #26-#30) but has no `product_finder()` method — that path was deliberately not built. If/when Peter trials the API, adding it is a separate workstream that doesn't change anything here.

### Prior sessions

### What landed in the 2026-05-01 session (Phase 3)

**PR #32 — keepa_enrich foundation** (MERGED): the missing connector that lets ASIN-only sources chain into `calculate→decide`. `KeepaProduct.market_snapshot()` extracts canonical engine columns from Keepa stats indices (0=AMAZON, 3=SALES, 10=NEW_FBA, 11=COUNT_NEW, 18=BUY_BOX_SHIPPING). New `fba_engine/steps/keepa_enrich.py` joins per-ASIN market data via `KeepaClient.get_products()`. Both single + batch product paths now request `stats=90`. `_estimate_for` scales with N ASINs + stats overhead so the token bucket doesn't silently over-issue under heavy batch load. Pre-PR review caught 2 HIGH (product_name=None bug + seller_storefront chain clash), both fixed by dropping descriptive fields from canonical enrich schema. +29 tests.

**PR #33 — oa_csv full chain** (MERGED): promotes `oa_csv` from leads-only to full decision pipeline. New chain `discover → keepa_enrich → calculate → decide → output`. Two name-bridges: `monthly_sales_estimate → sales_estimate` in market_snapshot (canonical engine reads sales_estimate directly); `retail_cost_inc_vat → buy_cost` in oa_csv discovery output (per PRD §6.4). End-to-end smoke test pins that cheap rows SHORTLIST and expensive rows REJECT.

**PR #34 — SellerAmp drop / SP-API enrich leads mode** (MERGED): replaces the legacy SellerAmp skill with the existing SP-API MCP for non-Buy-Box-% checks. New `enrich.LEADS_INCLUDE = (restrictions, fba, catalog)` + `include: "leads"` YAML alias. `_row_to_item(allow_no_price=True)` lets ASIN-only rows preflight without a market_price. `seller_storefront.yaml` chain extended: discover → enrich (leads) → supplier_leads. Legacy `skill-2-selleramp/SKILL.md` marked deprecated. +8 tests.

**PR #35 — Skill 3 scoring extraction** (MERGED): canonical `fba_engine/steps/scoring.py` replaces the agent-driven per-niche `phase3_scoring.js` scripts. 4-dimension scoring (Demand/Stability/Competition/Margin) + 30/30/20/20 composite + 3 lane scores (Cash Flow/Profit/Balanced) + lane classification + 9-verdict ladder (YES/MAYBE/MAYBE-ROI/BRAND APPROACH/BUY THE DIP/PRICE EROSION/GATED/HAZMAT/NO). Pre-PR review caught 2 HIGH bugs by comparing against real `phase2_enriched.csv` + the legacy `phase3_scoring.js`: wrong column names (`Buy Box Drop % 90d` → `Price Drop % 90d`, `Buy Box Amazon Share` → `Buy Box Amazon %`) and margin-tier off-by-one (strict `>` not `>=`). `keepa_niche.yaml` chain now `scoring → ip_risk → build_output → decision_engine`. +64 tests.

### Older session highlights

**Phase 2 (PRs #26-#30, MERGED, prior session):** canonical engine refactor (6 step modules); keepa_client batch + stale-on-error; seller_storefront discovery step; seller_storefront.yaml + oa_csv.yaml; run_summary.json + strategy docs.

**Phase 1 (PRs #20-#25, MERGED, prior session):** docs/PRD-sourcing-strategies.md (PR #20), keepa_client foundation (PR #21), supplier_leads step / Skill 99 v1 (PR #22), oa_csv discovery + SellerAmp 2DSorter importer (PR #23), CLI launch helpers (PR #24), sourcing_engine integration test as PR #7 safety net (PR #25).

**Step 4 + 5 (PRs #8-#18, MERGED, prior session):** ip_risk, decision_engine, build_output (3-part: merge/XLSX/GSheets), cross-cutting fixes, helpers extraction, YAML strategy runner with `keepa_niche.yaml`.

### Roadmap status (where we are)

**Keepa Product Finder strategies: COMPLETE** ✅ (this session, branch `feat/keepa-finder-strategies`)

| Recipe | Strategy YAML | Status |
|---|---|---|
| `amazon_oos_wholesale` | `keepa_finder.yaml` + recipe JSON | ✅ shipped |
| `brand_wholesale_scan` | `keepa_finder.yaml` + recipe JSON | ✅ shipped |
| `no_rank_hidden_gem` | `keepa_finder.yaml` + recipe JSON | ✅ shipped |
| `stable_price_low_volatility` | `keepa_finder.yaml` + recipe JSON | ✅ shipped |
| `a2a_flip` | — | ⏸️ Deferred (PRD §6.2 future) |

**Phase 3 (post-PRD scoping items): COMPLETE** ✅ (PRs #32–#35 prior session)

| Phase 3 deliverable | Status |
|---|---|
| `keepa_enrich` step (foundation for ASIN→market data) | ✅ #32 |
| `oa_csv` chains into `calculate → decide` (full verdicts) | ✅ #33 |
| Drop SellerAmp — SP-API MCP enrich leads mode | ✅ #34 |
| Skill 3 scoring → canonical step | ✅ #35 |
| **Skill 1 (Keepa Finder) — keep browser flow** | ✅ wired in this session via `keepa_finder` |

**Engine state:** structurally complete for the strategies in scope. Future PRs are polish, not missing functionality.

**Open low-priority polish (not blocking):**
- **Buy Box %** signal — SellerAmp's only unique field. Could be derived from Keepa `Buy Box: Is FBA` time series or via Keepa `buy_box_avg90` ratio. Not yet wired.
- **TA + OAXray oa_importers** stubbed out — add their parsers when those tools are needed.
- **Skill 1 (Keepa Finder)** — currently browser-driven; if/when API path becomes useful, we have the keepa_client + keepa_enrich foundations to build on.
- **Niche-specific scoring weights** — `shared/config/scoring/<niche>.yaml` overrides could be added when operators need per-niche tuning. Universal weights work today.
- Resumable upload progress logging dropped in PR #15 (`_status` discarded)
- Title clamp at 200 chars vs Sheets API's actual 100-char limit
- Strategy 2 silently falls through on auth failures (pre-existing, JS-faithful)
- Dead `last_err` defensive branch in `_retry_with_backoff`

### Workflow notes (cumulative across sessions)
- **Worktree gotcha:** `[[ -d .git ]]` checks fail in worktrees because `.git` is a file pointer. Use `[[ -e .git ]]`.
- **`gh pr merge` in worktrees:** Local cleanup fails because main is checked out at the parent worktree (`fatal: 'main' is already used by worktree at 'O:/fba'`). Use `gh pr merge <N> --merge --delete-branch --admin` — the merge succeeds on GitHub even when local cleanup fails. Verify via `gh pr view <N> --json state,mergedAt`.
- **Always fetch before branching:** After merging a PR, run `git fetch origin && git checkout -b <new-branch> origin/main` (NOT just `origin/main` from stale local cache). Branching off pre-merge state silently drops the merged work.
- **NaN-truthy trap (pandas):** `pd.DataFrame.from_records(list_of_dicts)` fills missing dict keys with NaN, which is **truthy** for floats. The naive `if row_dict.get("decision"):` short-circuits on rows that came through DataFrame construction even when decision is genuinely absent. Use `is_missing()` from `fba_engine/steps/_helpers.py` (catches None / NaN / pd.NA / pd.NaT).
- **TS imports:** This project uses ESM/nodenext — relative imports MUST end in `.js` even when source is `.ts` (e.g., `import {X} from "./foo.js"`). Otherwise `npm run build` fails (vitest is more lenient).
- **MCP test path:** Always run `npm` commands inside the actual worktree's `services/amazon-fba-fees-mcp/`, not `O:/fba/services/amazon-fba-fees-mcp/`. They're separate copies.
- **Credential sync:** After editing `F:\My Drive\workspace\credentials.env`, run `& 'F:\My Drive\workspace\sync-credentials.ps1'` (PowerShell — bash quoting breaks on the space in "My Drive"). Then verify with `grep '"SP_API_' "C:/Users/peter/.claude/settings.json"`.
- **MCP `.mcp.json` path** at repo root references the MCP at `services/amazon-fba-fees-mcp/dist/index.js` (corrected from old root-level path during cleanup).
- **SP-API endpoint group names** (amazon-sp-api lib): catalogItems, productFees, listingsRestrictions, fbaInboundEligibility, productPricing. Use `client.callAPI({ operation, endpoint, ... })`.
- **Disk cache layout:** `<repo>/.cache/fba-mcp/<resource>/<key-parts>__joined.json` — gitignored. `DiskCache.get()` returns `{ hit, stale, data }` enabling stale-on-error fallback.
- **Keepa cache layout:** `<keepa_cache_root>/<namespace>/<key>.json` per `shared/lib/python/keepa_client/cache.py`. Use `DiskCache.get_stale()` for stale-on-error fallback (introduced in PR #27).
- **Strategy YAML `input.discover: true`:** when first step creates the DataFrame from API/files, set this flag instead of `input.path`. Strict bool coercion at load time — quoted `"true"` / `"false"` get rejected.
- **Pandas strict-string dtype trap:** `out[col] = ""` initializes a string-only series, then assigning ints/floats raises `TypeError: Invalid value '10' for dtype 'str'`. Fix: `out[col] = pd.Series([None] * len(out), dtype=object, index=out.index)` before writing mixed-type values. Hit this in scoring.py — the column-init pattern needs object dtype if you'll write scores AND verdict strings to the same row.
- **SKILL.md ≠ shipped behaviour:** When porting a legacy skill, treat the `SKILL.md` as the spec but verify against the actually-shipped JS (`fba_engine/_legacy_keepa/scripts/*.js` or `data/{niche}/working/*.js`). The reviewer caught 2 HIGH bugs in scoring.py (wrong column names + margin tier off-by-one) by reading the real CSV headers + the legacy phase3_scoring.js — both differed from SKILL.md. Always check both sources during a port.
- **Keepa stats indices:** Per `https://keepa.com/#!discuss/t/keepa-time-series-data/116`. The constants we consume: 0=AMAZON, 3=SALES (rank), 10=NEW_FBA, 11=COUNT_NEW (offer count), 18=BUY_BOX_SHIPPING. Stored as integer cents (`stats.current[18] = 1525` means £15.25). `-1` is the "no current value" sentinel — `KeepaProduct._stat_money/_stat_int` coerces both `-1` and missing arrays to `None`.
- **`_estimate_for` token scaling (PR #32):** Single product no-stats: 6 tokens. Single product with `stats=N`: 7. 100-ASIN batch with stats: 5 + 100*2 = 205. Without per-ASIN scaling, the bucket would silently over-issue and rely on Keepa returning HTTP 429.
- **Vitest integration tests:** Live in `src/__integration__/*.integration.test.ts`. Excluded from default `npm test` via `vitest.config.ts` exclude. Run with `npm run test:integration` (separate `vitest.integration.config.ts`).
- **ASINs are exactly 10 chars.** Every test fixture ASIN that's 11 chars (`B0KEEP00001`, `B0SMOKE0001`) gets silently dropped by the canonical 10-char check in any discovery step that validates length (keepa_finder_csv does). Hit this twice this session — both times the test failure mode was "0 rows in output" with no obvious cause until tracing through the discovery step. Use 10-char ASINs in fixtures.
- **Keepa CSV column names with commas** (`"New, 3rd Party FBA: Current"`) MUST be CSV-quoted. Real Keepa exports do this correctly; hand-built test CSVs that join columns with bare commas silently shift every data column right by one. Use `pd.DataFrame(...).to_csv(...)` to write fixture CSVs — pandas handles the quoting.
- **Keepa Referral Fee % format:** Keepa exports `"15 %"` / `"15.01 %"` (with space + percent sign). `parse_money()` strips the `%` but doesn't divide; the canonical engine expects the fraction (0.15). Always divide by 100 when bridging this column. Same shape for "Buy Box: % Amazon 90 days" — though that one is informational and the divide isn't load-bearing yet.
- **Wholesale flow buy_cost convention:** `buy_cost = 0.0` is the load-bearing signal that tells `calculate.calculate_profit` to emit `max_buy_price` (the supplier-negotiation ceiling) instead of a literal ROI. Used by `seller_storefront`, `keepa_finder`, and any future leads-only strategy. Don't pass `None` — the engine's direct `match["buy_cost"]` access KeyErrors on missing keys; 0.0 is the intentional sentinel.
- **Strategy YAML interpolation is string-only and one-level-deep.** The `runner._interpolate_config` function only substitutes `{name}` in string values, not in dict/list values, and missing context keys raise `StrategyConfigError`. To forward a dict config (like recipe `decide_overrides`), mutate the loaded `StrategyDef` from the dispatcher (see `cli/strategy.py:_apply_recipe_to_strategy`) rather than trying to interpolate it through YAML.
- **Recipe JSONs live with the skill that consumes them:** `_legacy_keepa/skills/keepa-product-finder/recipes/{name}.json`. Same convention as `keepa-finder-values.md` — co-located with the consumer. Future strategies that don't go through this skill should put their recipes elsewhere.
- **`pd.read_csv(on_bad_lines='skip')`:** Real Keepa Product Finder exports occasionally have malformed lines (e.g. `kids_toys_phase1_raw.csv` line 4993 has unbalanced quotes). Skip them rather than crashing the whole run — the engine's "never crash on a single bad row" principle applies at the row-parser level too.
- **Keepa column rename (post 2026-04):** `"Bought in past month"` → `"Monthly Sales Trends: Bought in past month"`. Both names map to `sales_estimate` in `keepa_finder_csv._KEEPA_TO_CANONICAL`. Test fixtures using the old name (`kids_toys_phase1_raw.csv` from 2026-03) still work via the alias resolution in `_row_from_keepa` (groups source columns by destination, picks the first source with non-empty data). Watch for similar renames — refresh the smoke fixture periodically or the next column drift slips through.
- **Keepa Browser Pro vs API tier are different products.** Peter has the Browser tier (~£19/mo, gives access to the Product Finder UI + CSV export). The API tier is separate (~£49/mo for Power, more for higher rates). The keepa_finder pipeline uses the BROWSER tier — driven via Claude in Chrome MCP against the logged-in Product Finder UI. No `keepa_client.product_finder()` method exists in the engine because the API path was deliberately not built.
- **`subprocess` + `timeout` orphans on Windows:** `bash`'s `timeout 120 python run.py ...` sends SIGTERM after 120s, but a Python child blocked on a synchronous SP-API call doesn't release the interpreter to handle the signal. The Python process keeps running after `timeout` exits, eventually overwriting the output file with the original (pre-fix) result. Symptom: stdout shows the right summary (post-fix engine ran), but the on-disk CSV reflects the orphaned earlier run. Detection: compare CSV mtime vs the engine's run_summary.json `started_at` field. Cleanup: `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object CommandLine -like "*run.py*--strategy*" | ForEach-Object Stop-Process` from PowerShell. `pkill` doesn't exist in this bash; PowerShell is the right escape hatch.
- **`autocomplete-rootCategory` value lives in a hidden field, not the input:** when the keepa-product-finder skill clicks a Toys & Games dropdown match, the visible `<input id="autocomplete-rootCategory">` clears (Keepa UI pattern). The actual selection persists in a sibling hidden field `<input name="autocompleteReal-rootCategory">` carrying Keepa's internal category ID (e.g. `468292` for Toys & Games). Verify post-click via the hidden field, not the visible input.
- **`autocomplete-categories_exclude` is sub-category scoped, not root scoped:** when `rootCategory` is set, the `categories_exclude` autocomplete searches only sub-categories of that root. Setting "Clothing, Shoes & Jewellery" in `categories_excluded` (the global YAML) is a no-op for any run scoped to "Toys & Games" — the post-export title-keyword filter is the actual safety net for keyword-based exclusions when the root scope already excludes the bad category. Document per-recipe whether category exclusion is meaningful for that scope.
- **SP-API restrictions endpoint already returns ungate URLs:** `getListingsRestrictions` returns `restrictions[].reasons[].links[].resource` per gated reason. The MCP forwards this as `r["link"]` (singular) — first link only. The engine's `preflight._coerce_result` extracts and surfaces it as the `restriction_links` column (semicolon-joined, deduplicated). Future enhancement: extend the MCP to forward the full `links[]` array if multiple application paths matter.
- **Reserved schema for ungate-tracking:** `ungate_status`, `ungate_required_docs`, `ungate_brand_required`, `ungate_attempted_at`, `ungate_message` are seeded as None by `preflight._seed_row` and `_coerce_result`. Engine never writes them — operator fills by hand (or future click-through bot fills automatically). Locked in `UNGATE_COLUMNS` constant at top of `preflight.py`. Renaming any of these breaks operator spreadsheets that reference the column names; rename only with a migration plan.

## Session Protocol
- At the end of each session, update the "Current State" section above
- If you learned something about how this project works that would help next time, add it to this file
- Commit CLAUDE.md changes as part of your work

---

## Read these first

For any work in this repo, read in this order:

1. **`docs/SPEC.md`** — business logic, decision rules, the truth (supersedes the v5 PRD)
2. **`docs/architecture.md`** — how the system is laid out
3. **`AGENTS.md`** — agent behaviour rules, what not to do

For specific work:

- **Strategy work** → `docs/strategies/<strategy>.md` for that strategy
- **Adapter work** (adding/fixing a supplier) → `fba_engine/adapters/<supplier>/` and look at sibling adapters as templates
- **Threshold tuning** → `shared/config/decision_thresholds.yaml` (the single tunable knob is `target_roi`)

---

## Top-level layout

```
amazon_fba/
├── README.md                # human-facing
├── CLAUDE.md                # this file
├── AGENTS.md                # agent behaviour rules
├── run.py                   # launcher
├── docs/                    # SPEC.md, architecture.md, strategies/, archive/
├── shared/                  # config/, niches/, lib/python/ (engine + libs)
├── fba_engine/              # adapters/, data/ (gitignored), _legacy_keepa/ (temporary)
├── services/                # amazon-fba-fees-mcp/
└── orchestration/           # Cowork-facing run definitions
```

For details on each, see `docs/architecture.md`.

---

## Common operations

### Run the supplier pricelist strategy
```bash
python run.py --supplier connect-beauty
# or with explicit market data
python run.py --supplier abgee --market-data fba_engine/data/pricelists/abgee/raw/keepa_combined.csv
```

### Run all tests
```bash
# Shared library + canonical engine
cd shared/lib/python && pytest tests/ sourcing_engine/tests/ && cd ../../..

# Per-supplier adapter tests (run from supplier folder so relative paths resolve)
for s in abgee connect-beauty shure zappies; do
  cd fba_engine/data/pricelists/$s && pytest ../../../adapters/$s/tests/ && cd ../../../..
done
```

Note: the supplier adapter tests use relative paths like `raw/some_file.pdf`,
so they must be invoked from the supplier's data folder.

### Add a new supplier
1. Create `fba_engine/adapters/<new-supplier>/` (use `_template/` as starting point)
2. Implement `ingest.py` and `normalise.py` for that supplier's file format
3. Create `fba_engine/data/pricelists/<new-supplier>/raw/` and drop in price lists
4. Run `python run.py --supplier <new-supplier>`

### Tune the ROI target
Edit `shared/config/decision_thresholds.yaml`:
```yaml
target_roi: 0.30   # change to taste
```
That's it. All downstream gates derive from this.

---

## What changed in steps 1-3 (recap)

- **Step 1:** Centralised all thresholds into `shared/config/`. Replaced the
  margin-based SHORTLIST gate with an ROI-based gate (single tunable: `target_roi`).
  Doc drift fixed.

- **Step 2:** Deduplicated the sourcing engine — was 4× copied across
  supplier folders, now one canonical copy at `shared/lib/python/sourcing_engine/`.
  Per-supplier code reduced to just the legitimately-different
  `ingest.py` and `normalise.py` files.

- **Step 3:** Restructured the repo. The two old top-level pipelines
  (`keepa_niche_finder/`, `supplier_pricelist_finder/`) no longer exist as
  separate trees. Engine code is in `shared/`, supplier adapters and data
  are in `fba_engine/`, MCP is in `services/`. Vestigial files removed.
  v5 PRD/BUILD_PROMPT moved to `docs/archive/`.

---

## What's coming in steps 4-6

- **Step 4:** Extract the legacy Keepa phases (currently in `fba_engine/_legacy_keepa/`)
  into composable steps at `fba_engine/steps/` (Python translation of the
  Node.js implementation). After step 4, `_legacy_keepa/` is gone.

- **Step 5:** Express both existing strategies as YAML compositions in
  `fba_engine/strategies/`. A `runner.py` reads a strategy YAML and executes
  its steps. Cowork orchestrates this.

- **Step 6:** Implement Skill 99 (Find Suppliers For Keepa-Discovered ASINs)
  as a new strategy composing existing steps + one new discovery step.
  Future strategies (brand outreach, retail arbitrage) follow the same pattern.
