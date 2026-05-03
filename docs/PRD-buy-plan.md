# PRD: Buy Plan — Step `08_buy_plan`

**Status:** Ready for implementation
**Author:** Peter Farrell (with Claude)
**Target branch:** `feat/buy-plan` (off `main`)
**Authoritative spec it builds on:** `docs/SPEC.md` §8c (validate_opportunity), §3 (decision logic), §4 (pricing rules), §5 (fee paths)
**Architecture it conforms to:** `docs/architecture.md` (single engine, ordered steps)
**Supersedes:** none — this is a new step

---

## 1. Objective

Today the engine emits a verdict (`BUY` / `SOURCE_ONLY` / `NEGOTIATE` / `WATCH` / `KILL`) and a velocity range (`predicted_velocity_low/mid/high`) but does **not** roll those signals up into an actionable buy list. The single-ASIN stdout printer prints a test-order recommendation; the bulk XLSX/CSV outputs do not.

This step closes that gap. It runs after `validate_opportunity` and before `output`, and appends a small set of columns that turn a validated row into a line-of-a-purchase-order:

- For `BUY` rows with `buy_cost`: order quantity, capital required, projected 30-day units / revenue / profit, sell-through (payback) days.
- For `SOURCE_ONLY` rows: ROI-targeted target buy cost (the ceiling a supplier must land at to clear the BUY gate), plus a stretch target.
- For `NEGOTIATE` rows: gap to the BUY ceiling in £ and %, and the supplier discount required to close it.
- For `WATCH` / `KILL`: blanks (verdict + blockers already say everything).

The step is **pure additive** — it does not mutate `decision`, `opportunity_verdict`, or any upstream column. It composes the existing `predicted_velocity_*`, `raw_conservative_price`, `fees_conservative`, `profit_conservative`, `buy_cost`, `moq`, `capital_exposure`, `risk_flags`, `data_confidence`, and `opportunity_confidence` fields into a buy-list-ready slice.

---

## 2. Out of scope

This PRD is deliberately narrow. The following are real follow-ups, **not** in this step:

- **Cross-supplier cost backfill.** A separate PRD will cover indexing every loaded supplier pricelist into a queryable EAN/brand index so a `SOURCE_ONLY` row can be promoted to `BUY` if the cost exists in another supplier's file. This step assumes `buy_cost` is already populated (or already absent) by the time it runs.
- **Supplier-website scraping.** Browser automation against trade-account websites for cost discovery is a separate process writing to a cache file, mirroring the `keepa_browser` cache pattern. Out of scope here.
- **Per-ASIN order history.** First-order vs reorder is determined by a per-run flag in v1. Tracking which ASINs you've previously bought (so reorder mode auto-applies) is a future step.
- **PO generation / supplier email composition.** v1 produces buy-list rows. Turning those into a sent PO is downstream.
- **Multi-marketplace.** UK only.

---

## 3. Pipeline placement

```
01_discover → 02_resolve → 03_enrich → 04_calculate
            → 04.5_candidate_score → 05_decide
            → 07_validate_opportunity → 08_buy_plan → 06_output
```

`08_buy_plan` runs immediately after `validate_opportunity` and before any output writer. It must run after validation because it consumes `opportunity_verdict`, `opportunity_confidence`, and `predicted_velocity_*`. It must run before output so the new columns appear in the CSV/XLSX/MD writers.

Implemented as `fba_engine/steps/buy_plan.py`. Core logic in `shared/lib/python/sourcing_engine/buy_plan.py` so both `supplier_pricelist` and `keepa_finder` strategies (and any future strategy) share the same rules. Same shape as `validate_opportunity` (runner-compatible wrapper + shared core).

Wired into every strategy YAML that currently composes `validate_opportunity`:
- `supplier_pricelist.yaml`
- `keepa_finder.yaml`
- `keepa_niche.yaml`
- `oa_csv.yaml`
- `seller_storefront.yaml`
- `seller_storefront_csv.yaml`
- `single_asin.yaml` (also drives the printer — see §8)

---

## 4. New columns

Eleven columns appended to every row. None replace existing columns.

| Column | Type | Populated when | Notes |
|---|---|---|---|
| `order_qty_recommended` | int \| None | `verdict == BUY` AND `buy_cost > 0` AND `predicted_velocity_mid > 0` | After risk dampening, MOQ, and capital cap |
| `capital_required` | float (GBP) \| None | as above | `order_qty_recommended × buy_cost` |
| `projected_30d_units` | int \| None | `predicted_velocity_mid > 0` | Risk-dampened mid (see §5.1); independent of buy_cost so populated for SOURCE_ONLY too |
| `projected_30d_revenue` | float (GBP) \| None | `projected_30d_units > 0` | `projected_30d_units × raw_conservative_price` |
| `projected_30d_profit` | float (GBP) \| None | `projected_30d_units > 0` AND `profit_conservative` present | `projected_30d_units × profit_conservative`; reflects per-unit profit at the conservative price |
| `payback_days` | float \| None | `verdict == BUY` AND `order_qty_recommended > 0` AND `projected_30d_units > 0` | `order_qty_recommended / projected_30d_units × 30`; days to sell through the order |
| `target_buy_cost_buy` | float (GBP) \| None | `raw_conservative_price` AND `fees_conservative` present | Supplier ceiling to clear `min_roi_buy` AND `min_profit_absolute_buy` (whichever is more conservative) |
| `target_buy_cost_stretch` | float (GBP) \| None | as above | Same calc at `min_roi_buy × stretch_roi_multiplier` (default 1.5×) |
| `gap_to_buy_gbp` | float (GBP) \| None | `verdict == NEGOTIATE` AND `buy_cost` AND `target_buy_cost_buy` populated | `buy_cost - target_buy_cost_buy` (positive number = supplier is over the ceiling) |
| `gap_to_buy_pct` | float \| None | as above | `gap_to_buy_gbp / buy_cost` |
| `buy_plan_status` | string | always | One of `OK`, `INSUFFICIENT_VELOCITY`, `INSUFFICIENT_DATA`, `NO_BUY_COST`, `BLOCKED_BY_VERDICT`, `UNECONOMIC_AT_ANY_PRICE` |

`buy_plan_status` is the operator's "why is this row blank?" answer. Values:
- **`OK`** — fields populated as the verdict allows.
- **`INSUFFICIENT_VELOCITY`** — `predicted_velocity_mid` is None or 0 after dampening; sizing fields blank.
- **`INSUFFICIENT_DATA`** — required inputs missing (e.g. `raw_conservative_price` or `fees_conservative` absent); target buy cost cannot be computed.
- **`NO_BUY_COST`** — `verdict == SOURCE_ONLY` and step is doing the right thing (target costs computed, sizing fields blank).
- **`BLOCKED_BY_VERDICT`** — `verdict == WATCH` or `KILL`; step intentionally leaves all sizing fields blank.
- **`UNECONOMIC_AT_ANY_PRICE`** — `raw_conservative_price - fees_conservative <= min_profit_absolute_buy`; no positive `target_buy_cost_buy` exists. Surfaces a structural unprofitability that the verdict layer might not flag if buy_cost is currently absent.

---

## 5. Logic

### 5.1 Sizing formula (BUY only)

```
mid = predicted_velocity_mid                    # already share-aware
if mid is None or mid <= 0:
    buy_plan_status = INSUFFICIENT_VELOCITY
    skip sizing block

risk_factor = 1.0
if opportunity_confidence == "LOW":     risk_factor *= cfg.risk_low_confidence       # 0.70
elif opportunity_confidence == "MEDIUM": risk_factor *= cfg.risk_medium_confidence   # 0.85
if "INSUFFICIENT_HISTORY" in flags:     risk_factor *= cfg.risk_insufficient_history # 0.85
if "LISTING_TOO_NEW" in flags:          risk_factor *= cfg.risk_listing_too_new      # 0.85
if "COMPETITION_GROWING" in flags:      risk_factor *= cfg.risk_competition_growing  # 0.75
if "BSR_DECLINING" in flags:            risk_factor *= cfg.risk_bsr_declining        # 0.85
if "PRICE_UNSTABLE" in flags:           risk_factor *= cfg.risk_price_unstable       # 0.85
risk_factor = max(risk_factor, cfg.risk_floor)  # 0.50 — never under half mid

projected_30d_units = max(0, round(mid * risk_factor))

# Days of cover: per-run flag, default first-order
days_of_cover = cfg.first_order_days if order_mode == "first" else cfg.reorder_days
                                       # 21                           # 45

order_qty_raw = ceil(projected_30d_units * days_of_cover / 30)
order_qty = order_qty_raw

# First-order cap — UNIT-based (operator preference: cap by number
# of units, not £ exposure, so the safety net doesn't drift with
# cost-of-goods). Reorders aren't capped — sell-through is validated.
if order_mode == "first":
    order_qty = min(order_qty, cfg.max_first_order_units)   # default 50

# min_test_qty floor wins even when the cap brings us below it.
# Loader invariant pins max_first_order_units >= min_test_qty.
order_qty = max(order_qty, cfg.min_test_qty)   # never less than 5

# MOQ — supplier-imposed lower bound. MOQ wins even over the cap
# (operator sees `capital_required` reflect the over-cap exposure).
if moq is not None and moq > 0:
    order_qty = max(order_qty, moq)

capital_required = order_qty * buy_cost
```

`projected_30d_units` is always populated when `mid` is, regardless of verdict. It is **not** capped by `order_qty` — it is the engine's view of "how many would sell in a month at your share". `payback_days` is the operator's view of "how long until the order is sold through" using the same number.

### 5.2 Target buy cost (SOURCE_ONLY, NEGOTIATE, BUY all benefit)

Always populated when `raw_conservative_price` and `fees_conservative` are present. Independent of whether `buy_cost` is known.

```
gross_after_fees = raw_conservative_price - fees_conservative

if gross_after_fees <= cfg.min_profit_absolute_buy:
    buy_plan_status = UNECONOMIC_AT_ANY_PRICE
    target_buy_cost_buy = None
    target_buy_cost_stretch = None
else:
    # Ceiling that satisfies BOTH gates: ROI ≥ min_roi_buy AND profit ≥ min_profit_absolute_buy.
    # ROI gate: buy_cost = gross_after_fees / (1 + min_roi_buy)
    # Absolute gate: buy_cost = gross_after_fees - min_profit_absolute_buy
    # Take the lower (more conservative) of the two.
    roi_ceiling = gross_after_fees / (1 + cfg.min_roi_buy)
    abs_ceiling = gross_after_fees - cfg.min_profit_absolute_buy
    target_buy_cost_buy = round(min(roi_ceiling, abs_ceiling), 2)

    # Stretch — the price the operator should aim for in negotiation, not the ceiling
    stretch_roi = cfg.min_roi_buy * cfg.stretch_roi_multiplier   # 0.30 × 1.5 = 0.45
    roi_stretch = gross_after_fees / (1 + stretch_roi)
    abs_stretch = gross_after_fees - (cfg.min_profit_absolute_buy * cfg.stretch_roi_multiplier)
    target_buy_cost_stretch = round(min(roi_stretch, abs_stretch), 2)
```

Both numbers are visible on every row that has the inputs. For BUY rows, the operator can read off "we are paying X but the ceiling is Y, headroom is Z%" without computation. For SOURCE_ONLY this is the supplier-outreach target. For NEGOTIATE this drives `gap_to_buy_*`.

Note: this **replaces** the existing `max_buy_price` field (which is profit-absolute only) only conceptually. `max_buy_price` stays for backward compatibility; new code references `target_buy_cost_buy`. The two values converge when the absolute gate is the binding one and diverge when ROI is.

### 5.3 NEGOTIATE gap

```
if verdict == NEGOTIATE and buy_cost is not None and target_buy_cost_buy is not None:
    gap_to_buy_gbp = round(buy_cost - target_buy_cost_buy, 2)
    gap_to_buy_pct = round(gap_to_buy_gbp / buy_cost, 4)
```

Positive `gap_to_buy_gbp` means the supplier is over the ceiling and needs to come down. Negative would imply the row should have been BUY — defensive only; expect rows hitting NEGOTIATE to always show positive gap.

### 5.4 Verdict-driven population matrix

| Verdict | order_qty / capital / payback | projected_30d_* | target_buy_cost_* | gap_to_buy_* | buy_plan_status |
|---|---|---|---|---|---|
| `BUY` | populated | populated | populated | blank | `OK` (or `INSUFFICIENT_VELOCITY`) |
| `SOURCE_ONLY` | blank | populated (no buy_cost needed for revenue/profit projection — see §5.5) | populated | blank | `NO_BUY_COST` |
| `NEGOTIATE` | blank | populated | populated | populated | `OK` |
| `WATCH` | blank | populated (informational) | populated | blank | `BLOCKED_BY_VERDICT` |
| `KILL` | blank | blank | blank | blank | `BLOCKED_BY_VERDICT` |

Rationale for `WATCH` carrying `projected_30d_*` and `target_buy_cost_*`: WATCH rows often resolve to BUY-able later when a flag clears. Showing the economics and ceiling means the operator can decide whether the row is worth re-checking next week.

### 5.5 SOURCE_ONLY revenue/profit projection

`projected_30d_revenue` is computable without `buy_cost` — `predicted_30d_units × raw_conservative_price`. Useful for SOURCE_ONLY: it lets the operator rank prospects by potential revenue before sourcing.

`projected_30d_profit` requires `profit_conservative`, which requires `buy_cost`. So:

- For BUY / NEGOTIATE: compute as `predicted_30d_units × profit_conservative` (true expected profit at current cost).
- For SOURCE_ONLY: compute a **best-case** at `target_buy_cost_buy` — `predicted_30d_units × (raw_conservative_price - fees_conservative - target_buy_cost_buy)`. This is the profit the operator would make if they hit the ceiling. Document it in the column header (`Projected 30d Profit (at target cost)`).

If the operator finds a supplier and the cost is *better* than ceiling, real profit will exceed this. If worse, the row drops below the BUY gate and routes to NEGOTIATE/WATCH on the next run. The number is honest as "best case if you negotiate to the ceiling".

---

## 6. Configuration

New block in `shared/config/decision_thresholds.yaml`:

```yaml
buy_plan:
  # --- Sizing — days of cover ---
  first_order_days: 21                    # default first-order cover
  reorder_days: 45                        # default reorder cover
  min_test_qty: 5                         # never order less than this if BUY
  max_first_order_units: 50               # unit cap for first orders
                                          # (operator-preferred cap mechanism:
                                          #  units don't drift with cost-of-goods)

  # --- Risk dampener (multiplied with predicted_velocity_mid) ---
  risk_low_confidence: 0.70               # opportunity_confidence == LOW
  risk_medium_confidence: 0.85            # opportunity_confidence == MEDIUM
  risk_insufficient_history: 0.85         # INSUFFICIENT_HISTORY flag present
  risk_listing_too_new: 0.85              # LISTING_TOO_NEW flag present
  risk_competition_growing: 0.75          # COMPETITION_GROWING flag present
  risk_bsr_declining: 0.85                # BSR_DECLINING flag present
  risk_price_unstable: 0.85               # PRICE_UNSTABLE flag present
  risk_floor: 0.50                        # never below 50% of mid

  # --- Target buy cost ---
  stretch_roi_multiplier: 1.5             # stretch ROI = min_roi_buy × this
                                          # default: 0.30 × 1.5 = 0.45
```

`min_roi_buy` and `min_profit_absolute_buy` are reused from `opportunity_validation` — do not duplicate.

Loaded via a new dataclass in `fba_config_loader.py` (mirror `OpportunityValidation`). Permissive defaults so existing yaml files still load.

Per-run override:
- `--order-mode {first|reorder}` CLI flag on `run.py` (default: `first`)
- `order_mode` context value passed to the runner for YAML strategies

---

## 7. Edge cases

The step must never crash the pipeline. All of the following degrade to `buy_plan_status` values, never exceptions:

1. **`buy_cost` is 0 or negative** — sizing fields blank, `buy_plan_status = INSUFFICIENT_DATA`. Should never happen post-resolve but defensive.
2. **`predicted_velocity_mid` is None** — sizing + projection fields blank, `INSUFFICIENT_VELOCITY`.
3. **`predicted_velocity_mid` is 0 after dampening** — sizing fields blank, `INSUFFICIENT_VELOCITY`. Target buy cost still computed.
4. **`raw_conservative_price - fees_conservative <= min_profit_absolute_buy`** — `target_buy_cost_*` blank, `UNECONOMIC_AT_ANY_PRICE`.
5. **`moq > max_first_order_units`** — `order_qty = moq` (MOQ wins). `capital_required` reflects the over-cap exposure so the operator sees what they're committing to. Existing `HIGH_MOQ` flag from §3.3 already surfaces this; buy_plan does not duplicate the flag, just respects MOQ.
6. **Verdict is `WATCH` or `KILL`** — sizing + gap fields blank by design, `BLOCKED_BY_VERDICT`. Target costs and projections still populated where data allows (so WATCH rows remain re-evaluable).
7. **Row is `REJECT` (always KILL)** — same as KILL above.
8. **`order_qty_raw` after the unit cap is < `min_test_qty`** — bump to `min_test_qty`. The loader invariant pins `max_first_order_units >= min_test_qty` so the cap can't be tighter than the floor; this case only fires when the velocity-driven raw qty was already small.
9. **Empty DataFrame** — return frame with all 11 columns added (matches `validate_opportunity`'s empty-frame handling).
10. **Per-row exception in the core function** — log via `logger.exception`, populate `buy_plan_status = INSUFFICIENT_DATA`, blank all numeric fields, continue. Never abort the run.

---

## 8. Output integration

### 8.1 Excel writer (`shared/lib/python/sourcing_engine/output/excel_writer.py`)

Insert the eleven columns into `EXCEL_COLUMNS` immediately after the existing opportunity / velocity block:

```
... predicted_velocity_high, predicted_velocity_share_source,
    order_qty_recommended,        "Order Qty",            10, "int",
    capital_required,             "Capital £",            12, "gbp",
    projected_30d_units,          "Proj 30d Units",       14, "int",
    projected_30d_revenue,        "Proj 30d Revenue",     16, "gbp",
    projected_30d_profit,         "Proj 30d Profit",      16, "gbp",
    payback_days,                 "Payback (days)",       14, "num1",
    target_buy_cost_buy,          "Target Buy Cost",      16, "gbp",
    target_buy_cost_stretch,      "Stretch Target",       14, "gbp",
    gap_to_buy_gbp,               "Gap to BUY £",         12, "gbp",
    gap_to_buy_pct,               "Gap to BUY %",         12, "pct",
    buy_plan_status,              "Buy Plan Status",      18, "text",
    ...
```

Conditional formatting (added to existing rules):
- `order_qty_recommended` colour-graded by capital_required (£0–50 light, £50–150 mid, £150+ dark).
- `payback_days` < 30 green, 30–60 amber, > 60 red.
- `gap_to_buy_pct` red gradient — closer to 0 = lighter (closer to BUY-able).

Sort order remains `opportunity_verdict` primary; within BUY, secondary sort changes from `candidate_score desc` to `projected_30d_profit desc` (the operator's actual ranking question is "which order makes me the most money?"). Configurable in the writer.

### 8.2 CSV writer

All eleven columns appended to the existing schema. No format-specific handling. Audit trail.

### 8.3 Markdown report

For BUY rows, add a single line per row beneath the existing decision/verdict block:

```
**Order plan:** 47 units · £188.00 capital · projected 30d: £312.45 revenue, £64.20 profit · payback 24 days
```

For SOURCE_ONLY rows:

```
**Source target:** ≤ £4.85/unit (stretch £4.10) · projected at target: £312.45 30d revenue
```

For NEGOTIATE rows:

```
**Negotiation ask:** down £0.62/unit (12.4%) — currently £5.00, needs ≤ £4.38
```

For WATCH/KILL: no buy-plan line (verdict + blockers already say it).

### 8.4 Single-ASIN stdout printer (`cli/strategy.py`)

Replace the existing `Test-order rec:` block with a fuller breakdown. Keep the labels short — the printer is dense already:

```
Buy plan (mode: first-order, 21d cover):
  Order qty:           47 units (capital £188.00)
  Projected 30d:       42 units → £312.45 revenue / £64.20 profit
  Payback:             24 days
  Target buy cost:     £4.85 (stretch £4.10) — currently £4.00, headroom 17%
```

For SOURCE_ONLY single-ASIN runs, swap the order block for the supplier-target block. For WATCH/KILL, omit the section entirely (existing `next_action` covers it).

---

## 9. Tests

Mirror the patterns in `fba_engine/steps/tests/test_validate_opportunity.py` and `shared/lib/python/sourcing_engine/tests/test_opportunity.py`. Aim for ~30 new tests across:

**Unit (core logic, `shared/lib/python/sourcing_engine/tests/test_buy_plan.py`):**
- Sizing formula at each risk level (HIGH/MEDIUM/LOW confidence × each flag combination)
- Risk floor — verify dampener never goes below 0.5
- MOQ wins over computed qty
- Capital cap binds correctly
- `min_test_qty` floor binds correctly
- Target buy cost — ROI gate binds (cheap fast-mover)
- Target buy cost — absolute gate binds (expensive slow-mover where 30% ROI is below £2.50 profit)
- Stretch target lower than BUY target (always)
- `UNECONOMIC_AT_ANY_PRICE` triggers correctly
- Gap calc correct sign, correct percentage
- `payback_days` arithmetic
- All edge cases from §7 — each one has a dedicated test

**Step (`fba_engine/steps/tests/test_buy_plan.py`):**
- Empty DataFrame returns frame with all 11 columns
- Per-verdict population matrix (one test per verdict, asserts which fields are populated/blank)
- Exception in core → row gets `INSUFFICIENT_DATA`, run continues
- `order_mode` context flag flips first → reorder
- Multi-row frame: one BUY, one SOURCE_ONLY, one NEGOTIATE, one WATCH, one KILL — assert all populate correctly in a single pass

**Strategy (`fba_engine/strategies/tests/test_strategy_*.py` — extend existing):**
- Each strategy YAML that includes `validate_opportunity` now also includes `buy_plan` and produces buy_plan columns in its smoke test.

**Integration:**
- Re-run the existing fixtures (e.g. ABGEE, Connect Beauty fixtures) and assert buy_plan columns exist and are non-empty for at least one BUY row.

---

## 10. Acceptance criteria

A row in a real `python run.py --supplier abgee` (or `--strategy keepa_finder ...`) run shows:

```
opportunity_verdict       BUY
predicted_velocity_mid    18
opportunity_confidence    HIGH
buy_cost                  4.00
order_qty_recommended     13
capital_required          52.00
projected_30d_units       18
projected_30d_revenue     303.30
projected_30d_profit      64.80
payback_days              21.7
target_buy_cost_buy       6.85
target_buy_cost_stretch   5.20
gap_to_buy_gbp            (blank)
gap_to_buy_pct            (blank)
buy_plan_status           OK
```

A SOURCE_ONLY row (no buy_cost, strong demand):

```
opportunity_verdict       SOURCE_ONLY
buy_cost                  (blank)
order_qty_recommended     (blank)
projected_30d_units       42
projected_30d_revenue     710.00
projected_30d_profit      136.00   (at target_buy_cost_buy)
target_buy_cost_buy       4.85
target_buy_cost_stretch   4.10
buy_plan_status           NO_BUY_COST
```

A NEGOTIATE row (cost present but conservative profit too thin):

```
opportunity_verdict       NEGOTIATE
buy_cost                  5.00
target_buy_cost_buy       4.38
gap_to_buy_gbp            0.62
gap_to_buy_pct            0.124
buy_plan_status           OK
```

All 1268 existing Python tests still pass. New tests pass. MCP suite untouched.

XLSX output sorts BUY rows by `projected_30d_profit` desc within the verdict tier.

The single-ASIN printer renders the buy-plan block for B0B636ZKZQ-style real ASINs without crashing on any of the existing test fixtures.

---

## 11. Versioning + handoff to SPEC.md

After ship and sign-off:

1. Fold this PRD into `docs/SPEC.md` as section **§8d — Buy plan**, mirroring the §8c style.
2. Move this file to `docs/archive/PRD-buy-plan.md`.
3. Update `CLAUDE.md` Current State block.

The signal table in SPEC §9 gets one extension noting which signals the dampener consumes (`opportunity_confidence`, the four history-derived flags, plus existing data-confidence inputs).

---

## 12. Non-objectives, restated

This step is **not** the operator's full sourcing tool. It is the column rollup that makes the existing engine's output a buy list. Cross-supplier cost backfill, supplier-website scraping, per-ASIN order history, and PO generation are **separately specced and shipped**. Anything that requires reading a second supplier's pricelist or a third-party website is out of scope for `08_buy_plan` by design — keep the step pure-transformation and let the data-acquisition steps be their own modules with their own PRDs.
