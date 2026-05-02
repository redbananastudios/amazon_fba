# Handoff — Data-Driven Candidate Validation

**For:** Claude Code working on `redbananastudios/amazon_fba`
**Date:** 2026-05-02
**Status:** Ready to implement
**Estimated scope:** ~3 work sessions, sequenced WS1 → WS2 → WS3

---

## 1. Why we're doing this

The engine currently produces `SHORTLIST / REVIEW / REJECT` based on profitability gates. That answers *"will this make money on the numbers as given?"* It does **not** answer *"do we trust the numbers, and is the product itself a strong candidate?"*

For the operator's sign-off, we need both. After this work, every output row carries:

- A **candidate strength** score (0–100, banded STRONG/OK/WEAK/FAIL) derived purely from data
- A **data confidence** score (HIGH/MEDIUM/LOW) reflecting how complete the inputs were

The two are independent. `STRONG / LOW` and `STRONG / HIGH` are different decisions and the operator needs to see them differently.

---

## 2. Read first (do not skip)

Before touching code:

1. `docs/SPEC.md` — source of truth. Any change here must be paired with a code change.
2. `shared/lib/python/keepa_client/models.py` — the `KeepaProduct` and `KeepaStats` models. Note the `_CSV_*` index constants and `market_snapshot()`.
3. `shared/lib/python/sourcing_engine/utils/flags.py` — the flag inventory and `SHORTLIST_BLOCKERS` / `REVIEW_FLAGS` sets.
4. `shared/lib/python/sourcing_engine/pipeline/market_data.py` — the CSV-export reader. Note the rich `_KEEPA_COLUMN_MAP`.
5. `fba_engine/steps/scoring.py` — existing 4-dimension scoring used by `keepa_niche`. We will **reuse**, not duplicate.
6. `fba_engine/strategies/supplier_pricelist.yaml` and `keepa_niche.yaml` — strategy compositions.
7. `AGENTS.md` — agent behaviour rules. **Read this.** Don't break the conventions.

When in doubt, SPEC.md wins. If SPEC.md and code disagree, raise it as an issue before deciding which is correct.

---

## 3. Design principles for this work

These are non-negotiable:

- **Data over judgement.** No new "feel" thresholds without a defined signal source. Every threshold lives in `shared/config/decision_thresholds.yaml`.
- **One engine, one schema.** When done, `keepa_enrich.py` (live API) and `market_data.py` (CSV export) must produce **the same column set**. No more two-paths-different-shape.
- **Candidate strength does not gate profitability.** A WEAK candidate that's still profitable goes to REVIEW with the score visible — the operator decides. Hard rejects stay where they are (EAN missing, no match, etc.).
- **Data confidence is computed from what's present, not what's hoped for.** If `history_days < 30`, confidence drops regardless of how shiny the other numbers look.
- **Reuse `scoring.py`.** It's battle-tested on the niche pipeline. Wire it into `supplier_pricelist.yaml`. Do not write a parallel scorer.

---

## 4. Workstream 1 — Schema unification + bug fixes (P0)

This must land first. Everything in WS2 and WS3 depends on consistent inputs across both enrichment paths.

### 4.1 Fix `fba_seller_count` (real bug)

**File:** `shared/lib/python/keepa_client/models.py`

In `market_snapshot()` (around line 282–295), the field labelled `fba_seller_count` is actually `_CSV_COUNT_NEW` (index 11), which is the total new-offer count — FBA + FBM combined. Every decision rule that uses `fba_seller_count` (single-seller flag, dynamic seller ceiling, FBM-only detection) is therefore wrong by an unknown amount.

**Fix:**

1. Add a helper `count_live_fba_offers(offers: list[KeepaOffer]) -> int` next to `lowest_live_fba_price`. Same filter logic — `condition == 1`, `is_fba`, not Amazon, `is_live()`. Returns the count.
2. In `market_snapshot()`, prefer this count when `self.offers` is populated; fall back to `_stat_int(self.stats, _CSV_COUNT_NEW)` only when offers wasn't requested.
3. Add a second key `total_offer_count` to the snapshot pulling from `_CSV_COUNT_NEW` for completeness — some calculators legitimately want the total.
4. Update `KEEPA_ENRICH_COLUMNS` in `keepa_enrich.py` to include `total_offer_count`.

**Tests required:**
- New unit test in `shared/lib/python/keepa_client/tests/test_keepa_client.py`: ASIN with 3 FBA + 2 FBM live offers returns `fba_seller_count=3, total_offer_count=5`.
- Test fallback path: empty offers list, `stats.current[11]=7` returns `fba_seller_count=7` (best we can do) and the existing comment is updated to flag the imprecision.

**Watch out:** `with_offers=False` is the default for bulk enrichment to save Keepa tokens. The fallback must keep working. Document the precision difference.

### 4.2 Remove or implement `PRICE_UNSTABLE`

**Files:** `shared/lib/python/sourcing_engine/utils/flags.py`, plus wherever it's referenced (only in `flags.py` today).

It's declared, never set. Pick one:

**Option A — Remove (preferred for now).** Delete the constant. Update `docs/SPEC.md` section 10 "Visible flags" to drop the line. Cleaner.

**Option B — Implement.** Compute `cv = stdev(buy_box_csv_90d) / mean(buy_box_csv_90d)`. Fire flag when `cv > price_volatility_threshold` (new config key, default `0.20`). This requires reading `csv[18]` (Buy Box Shipping series) — see WS2 for the time-series scaffolding.

**Decision: do Option A in WS1.** Don't ship a half-built signal. We re-add it properly in WS2 once the time-series helpers exist. Note in the commit message: *"Removing dead flag; will reintroduce in WS2 with real computation."*

### 4.3 Bring CSV-path fields into `market_snapshot()`

**Files:**
- `shared/lib/python/keepa_client/models.py` (extend `_CSV_*` constants and snapshot)
- `fba_engine/steps/keepa_enrich.py` (extend `KEEPA_ENRICH_COLUMNS`)

Add these fields, all derivable from data Keepa already returns. Naming must match `_KEEPA_COLUMN_MAP` in `market_data.py` so both paths produce the same schema:

| Field | Source | Notes |
|---|---|---|
| `rating` | `csv[16]` (RATING) | Keepa stores rating × 10; divide by 10 to get e.g. 4.5 |
| `review_count` | `csv[17]` (COUNT_REVIEWS) — last value | Last non -1 value in the series |
| `buy_box_avg30` | `stats.avg30[18]` | Add `avg30` to `KeepaStats` model |
| `sales_rank_avg90` | `stats.avg90[3]` | Already in stats, just expose |
| `parent_asin` | `KeepaProduct.parent_asin` field | Add `parent_asin: Optional[str] = Field(default=None, alias="parentAsin")` to model |
| `package_weight_g` | `KeepaProduct.package_weight` | Add field |
| `package_volume_cm3` | derived from `packageHeight × packageLength × packageWidth` | Add fields, divide by 1000 (mm³ → cm³) |
| `category_root` | `categoryTree[0].name` if present | Already partially modelled |

For `_CSV_RATING = 16` and `_CSV_COUNT_REVIEWS = 17`, add the constants. Add a `_stat_money_at` variant if needed for non-money series — or just add `_stat_at(stats, idx, avg=False)` that returns the raw int and let callers divide.

**Tests required:**
- Extend `test_keepa_client.py` to assert all new fields appear in snapshot when stats are populated.
- New test: snapshot from fixture with `parent_asin="B0XXX"` returns it correctly.
- Schema parity test: assert `set(market_snapshot().keys()) == set(KEEPA_ENRICH_COLUMNS) | {"asin"}`.

### 4.4 Acceptance criteria for WS1

- [ ] All existing tests pass: `cd shared/lib/python && pytest tests/ sourcing_engine/tests/`
- [ ] All adapter tests pass for `abgee` and `connect-beauty` (95 tests total)
- [ ] `python run.py --supplier abgee` runs end-to-end with no schema errors
- [ ] Output CSV from a supplier_pricelist run contains the new columns (filled where data exists, empty where it doesn't)
- [ ] `fba_seller_count` is now FBA-only when offers are loaded; document this in SPEC.md §3.3 and §10
- [ ] SPEC.md section 9 schema table updated with new fields
- [ ] No new dead flags introduced

---

## 5. Workstream 2 — New validation data + history analysis

This is the largest workstream. The goal: every signal in the candidate score (WS3) must have a defined source, a sensible default for missing data, and a corresponding flag for low-confidence cases.

### 5.1 New file: `shared/lib/python/keepa_client/history.py`

Most of the value in `KeepaProduct.csv` is unused today. Create a dedicated module for time-series analysis. Functions to implement:

```python
def parse_keepa_csv_series(series: list[int]) -> list[tuple[datetime, int | None]]:
    """Convert Keepa's [t, v, t, v, ...] interleaved array to typed pairs.

    -1 sentinels become None. Timestamps converted from Keepa-epoch minutes
    via the existing _keepa_minutes_to_datetime helper in models.py.
    """

def bsr_slope(rank_csv: list[int], *, window_days: int) -> float | None:
    """Least-squares slope of (rank vs time) over the window.

    Negative = improving (rank getting smaller = better). Positive =
    declining. None when fewer than 5 data points in window.

    Returns slope normalised by the mean rank in the window so values
    are comparable across listings of different rank magnitudes.
    """

def offer_count_trend(count_csv: list[int], *, window_days: int = 90) -> dict:
    """Returns {start, end, peak, joiners_90d, current}.

    `joiners_90d` = max(end - start, 0) — net new sellers entering the
    listing in the window. The single most useful early-warning for
    price erosion.
    """

def out_of_stock_pct(buy_box_csv: list[int], *, window_days: int = 90) -> float | None:
    """% of time in window where Buy Box was missing (-1 sentinel).

    Returns 0.0 — 1.0. None when fewer than 5 data points.
    """

def buy_box_winner_flips(buy_box_seller_csv: list[int], *, window_days: int = 90) -> int | None:
    """Count of distinct sellers that won the Buy Box in the window.

    csv[3] (rank) doesn't help here — Keepa's csv[27] / csv[28] track
    buy-box winner seller IDs. If the index isn't reliably populated
    for the marketplace, return None and document.

    NOTE: verify the index for UK marketplace before relying on this.
    """

def price_volatility(buy_box_csv: list[int], *, window_days: int = 90) -> float | None:
    """Coefficient of variation: stdev / mean over the window.

    Used to fire the (re-introduced) PRICE_UNSTABLE flag.
    """

def listing_age_days(tracking_since_minutes: int | None) -> int | None:
    """Days since Keepa first started tracking this ASIN.

    None when tracking_since not provided.
    """

def yoy_bsr_ratio(rank_csv: list[int]) -> float | None:
    """Mean rank in same week last year / mean rank in same week this year.

    Values >1 = better than last year (rank improved). <1 = worse.
    None when not enough history (need at least 365 days).
    """
```

**Critical:** verify each `_CSV_*` index against Keepa's published enum *before* trusting it. Keepa documents this at the URL in the existing file header. Do not guess from pattern. If an index isn't documented for UK marketplace, that function returns `None` and the corresponding signal is treated as missing, not zero.

**Tests required:**
- Synthetic fixtures with known shapes (improving line, declining line, flat, V-shape)
- Edge cases: empty array, single point, all -1 sentinels, mixed
- Window edges: data exactly at window boundary
- Sufficient unit coverage that we trust the math for downstream scoring

### 5.2 Wire history fields into `market_snapshot()`

Extend `KeepaProduct.market_snapshot()` to include:

```python
"bsr_slope_30d": ...
"bsr_slope_90d": ...
"bsr_slope_365d": ...      # None when history insufficient
"fba_offer_count_90d_start": ...
"fba_offer_count_90d_joiners": ...
"buy_box_oos_pct_90": ...
"price_volatility_90d": ...
"listing_age_days": ...
"yoy_bsr_ratio": ...        # None when <365d history
```

All optional, all `None` when input data is insufficient.

### 5.3 Add new flags

**File:** `shared/lib/python/sourcing_engine/utils/flags.py`

```python
LISTING_TOO_NEW = "LISTING_TOO_NEW"           # listing_age_days < listing_age_min (default 365)
COMPETITION_GROWING = "COMPETITION_GROWING"   # joiners_90d >= competition_joiners_threshold
BSR_DECLINING = "BSR_DECLINING"               # bsr_slope_90d > bsr_decline_threshold
HIGH_OOS = "HIGH_OOS"                         # buy_box_oos_pct_90 > oos_threshold
PRICE_UNSTABLE = "PRICE_UNSTABLE"             # reintroduced — price_volatility_90d > threshold
```

These belong in `REVIEW_FLAGS` (visible, route to REVIEW). **Not** in `SHORTLIST_BLOCKERS`. The candidate score in WS3 incorporates them; we don't double-penalise by also blocking shortlist.

### 5.4 Configuration

**File:** `shared/config/decision_thresholds.yaml`

Add a new section. All values configurable, all with defaults that are conservative:

```yaml
data_signals:
  listing_age_min_days: 365              # under this fires LISTING_TOO_NEW
  history_days_high_confidence: 90       # for data_confidence = HIGH
  history_days_medium_confidence: 30     # for data_confidence = MEDIUM
  competition_joiners_warn: 5            # warn at this
  competition_joiners_critical: 10       # COMPETITION_GROWING flag
  bsr_decline_threshold: 0.05            # normalised slope; tune empirically
  oos_threshold_pct: 0.15                # 15% of window OOS = HIGH_OOS
  price_volatility_threshold: 0.20       # CV > 0.20 = PRICE_UNSTABLE
  amazon_bb_share_warn_pct: 0.30
  amazon_bb_share_block_pct: 0.70        # already used; consolidate here
```

Update `fba_config_loader.py` to expose these. Follow the existing pattern (legacy constant aliases for back-compat).

### 5.5 Acceptance criteria for WS2

- [ ] `keepa_client/history.py` exists with full unit test coverage (target ≥90% line coverage on this module specifically)
- [ ] All snapshot fields populate correctly from a fixture ASIN with known history
- [ ] All new flags fire correctly in synthetic tests
- [ ] Config keys load via `fba_config_loader`
- [ ] Running `python run.py --supplier abgee` produces output rows with the new history columns populated where data exists
- [ ] SPEC.md §10 updated with new flags
- [ ] No regressions in existing tests

---

## 6. Workstream 3 — Candidate strength + data confidence scoring

Now we have the inputs. Time to make the verdict.

### 6.1 New file: `fba_engine/steps/candidate_score.py`

This is a new step. It runs **after** `calculate` and **before** `decide` in the supplier_pricelist pipeline. Output columns:

```
candidate_score          # 0-100 integer
candidate_band           # STRONG | OK | WEAK | FAIL
candidate_reasons        # short list of contributors (e.g. "BSR improving; joiners=2; OOS=4%")
data_confidence          # HIGH | MEDIUM | LOW
data_confidence_reasons  # missing inputs (e.g. "no rating; <30d history")
```

### 6.2 Scoring model (data confidence)

Compute first. The score is meaningless without it.

```
HIGH:    history_days >= 90 AND
         all of {rating, review_count, fba_seller_count, sales_estimate, buy_box_oos_pct_90} not None

MEDIUM:  history_days >= 30 AND
         at least 3 of the above present

LOW:     anything else
```

Track *which* inputs are missing in `data_confidence_reasons` so the operator can see the gap.

### 6.3 Scoring model (candidate strength)

Reuse the dimension structure from `scoring.py` but compute from the new richer inputs. Four dimensions, 0–25 each, sum to 100.

**Demand (0–25)**

- Sales estimate (0–10): banded against thresholds in config — 200+/mo → 10, 100–200 → 7, 50–100 → 4, 20–50 → 2, else 0.
- BSR slope direction (0–10): improving = 10, flat = 7, declining = 0.
- Review velocity (0–5): rising review count last 90d > 0 → 5, flat → 2, falling/null → 0.

**Stability (0–25)**

- OOS % (0–10): <5% → 10, 5–15% → 6, 15–30% → 2, >30% → 0.
- Price volatility (0–10): CV <0.10 → 10, 0.10–0.20 → 6, 0.20–0.35 → 2, >0.35 → 0.
- Listing age (0–5): >2yr → 5, 1–2yr → 3, 6–12mo → 1, <6mo → 0.

**Competition (0–25)**

- FBA seller count vs sales (0–10): use existing dynamic ceiling table from scoring.py — well below ceiling = 10, at ceiling = 5, over = 0.
- Joiners over 90d (0–10): 0–2 joiners → 10, 3–5 → 6, 6–10 → 2, >10 → 0.
- Amazon BB share (0–5): <10% → 5, 10–30% → 3, 30–70% → 1, >70% → 0 (already gates REVIEW elsewhere).

**Margin (0–25)**

Use what `calculate` already produced. Don't re-derive.

- ROI conservative (0–15): >50% → 15, 30–50% → 10, 20–30% → 5, <20% → 0.
- Profit conservative absolute (0–10): >£8 → 10, £4–£8 → 6, £2.50–£4 → 3, <£2.50 → 0.

**Bands**

```
candidate_score >= 75  → STRONG
candidate_score >= 50  → OK
candidate_score >= 25  → WEAK
else                   → FAIL
```

All band thresholds, all sub-thresholds, all weights → `shared/config/decision_thresholds.yaml` under a new `candidate_scoring:` section. **No magic numbers in code.**

### 6.4 Confidence-adjusted reporting

Don't blend confidence into the score itself. Output them as two columns and let the operator filter. In the XLSX writer, surface them as adjacent columns and colour-code:

- `candidate_band == STRONG` and `data_confidence == HIGH` → green
- `candidate_band == STRONG` and `data_confidence != HIGH` → amber + reason
- `candidate_band in {OK, WEAK}` → no special highlight, just the band label
- `candidate_band == FAIL` → grey out

### 6.5 Wire into `supplier_pricelist.yaml`

Insert between `calculate` and `decide`:

```yaml
  - name: candidate_score
    module: fba_engine.steps.candidate_score
```

Optionally also add to `keepa_niche.yaml` after `scoring` — they're complementary, not duplicative (existing `scoring.py` has lane logic and verdicts; `candidate_score` is the data-quality-aware companion). Keep them both, document the difference.

### 6.6 Output integration

**Files:** `shared/lib/python/sourcing_engine/output/excel_writer.py`, `fba_engine/steps/build_output.py`, `fba_engine/steps/supplier_pricelist_output.py`

- Add the four new columns to the canonical headers
- Sort SHORTLIST sheet by `candidate_score` descending within each `decision` band
- In the markdown report, add a leading line per row: `**STRONG** (HIGH confidence) — score 82/100`

### 6.7 Acceptance criteria for WS3

- [ ] `candidate_score.py` ships with full unit test coverage of every band edge
- [ ] Tests cover: missing-data cases, all-thresholds-on-the-edge cases, perfect-data case, worst-data case
- [ ] `python run.py --supplier abgee` produces an XLSX with the new columns populated and colour-coded
- [ ] At least one row in the test fixtures lands in each band (STRONG, OK, WEAK, FAIL)
- [ ] At least one row demonstrates the LOW data_confidence path
- [ ] SPEC.md §3 updated with the new step's contract — note that it does **not** affect SHORTLIST/REVIEW/REJECT, only adds visibility
- [ ] All existing tests pass

---

## 7. What NOT to do

These are the failure modes I'm pre-empting:

- **Don't change SHORTLIST/REVIEW/REJECT logic.** This work is purely additive. The existing decision_engine stays intact. Operators with running pipelines should not see different shortlist rows after this lands — they should see the same rows with extra columns.
- **Don't merge candidate_score into existing scoring.py.** They serve different strategies. `scoring.py` runs after Keepa-finder discovery on niche pipelines and produces lane verdicts. `candidate_score.py` runs on all pipelines and produces a data-quality-aware strength signal. Both useful, separate concerns.
- **Don't hardcode any threshold.** Every numeric threshold goes in `decision_thresholds.yaml`. Tests assert config-driven behaviour, not hardcoded values.
- **Don't introduce a new dead flag.** If you add a flag constant, it must be set somewhere in code in the same PR.
- **Don't conflate confidence and strength.** Two columns. Two scores. Two purposes.
- **Don't expand the engine's responsibilities.** Discovery → resolve → enrich → calculate → candidate_score → decide → output. Keep candidate_score's only job: read inputs, write 4 columns. No side effects.
- **Don't re-derive what `calculate` already produced.** Read `roi_conservative`, `profit_conservative` etc. from the row. If they're missing or null, score that dimension as 0 and add a confidence reason.
- **Don't use Keepa CSV indices without verification.** Confirm against Keepa's published enum. If unsure, return None and let the confidence score reflect it.

---

## 8. Order of work and PRs

Suggested PR breakdown — keeps each change reviewable:

1. **PR 1 (WS1.1):** `fba_seller_count` fix + tests. Smallest possible diff, gets a real bug out of the way.
2. **PR 2 (WS1.2 + WS1.3):** Schema unification — extend `market_snapshot()` and `KEEPA_ENRICH_COLUMNS`. Remove `PRICE_UNSTABLE` constant. Update SPEC.md.
3. **PR 3 (WS2.1):** New `history.py` module + tests. Pure addition, no engine wiring yet.
4. **PR 4 (WS2.2 + WS2.3 + WS2.4):** Wire history fields into snapshot, add new flags, add config keys.
5. **PR 5 (WS3.1 + WS3.2 + WS3.3 + WS3.4 + WS3.5):** New `candidate_score.py` step + strategy wiring + tests.
6. **PR 6 (WS3.6):** Output integration — XLSX colour-coding, MD report, sort order.

Each PR has its own acceptance criteria from above and must leave the engine in a runnable state. Don't merge a PR that breaks `python run.py --supplier abgee`.

---

## 9. When stuck

- If a Keepa CSV index doesn't behave as documented, log a warning and return None. Don't guess.
- If a test fixture is wrong, update the fixture before changing the code. Test fixtures are documentation.
- If SPEC.md and code diverge, raise it as a decision before fixing either. Don't quietly resolve in favour of one.
- If the candidate score doesn't feel right on a real run (e.g., obviously bad listings score STRONG), the thresholds in config are wrong, not the model. Tune in config, don't add code overrides.

---

## 10. Final acceptance — for the operator (Peter)

After all six PRs land, running `python run.py --supplier abgee` should produce an XLSX where, for every SHORTLIST and REVIEW row, the operator can answer in one glance:

- *Is this product profitable?* → existing decision column
- *Is the data telling a strong story?* → `candidate_band`
- *Do I trust the data?* → `data_confidence`
- *Why?* → `candidate_reasons`, `data_confidence_reasons`

That's the sign-off contract. If the output doesn't make that judgement obvious to a human in 5 seconds, the work isn't done.
