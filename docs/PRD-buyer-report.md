# PRD: Buyer Report — Step `09_buy_plan_html` (v2)

**Status:** Implemented (PR #79 — `feat/buyer-report`)
**Author:** Peter Farrell (with Claude)
**Authoritative spec it builds on:** `docs/SPEC.md` §3-§5, `docs/PRD-buy-plan.md` (08_buy_plan), `docs/architecture.md`
**Supersedes:** v1 of this PRD (preserved in git history at commit 8971923) — that design used the engine's verdict + a single LLM narrative paragraph; v2 makes Claude the buyer's analyst and the engine becomes a data layer.

**Schema versions:** `schema_version=2`, `prompt_version=2`. Bumping either invalidates Cowork's cache.

---

## 1. Objective

Today the engine emits a CSV (audit trail), an XLSX (operator working file), a markdown report (per-supplier summary), and a single-ASIN stdout block (per-ASIN deep dive). What's missing is the **buyer's view** — a per-product, share-friendly card that reads like a human chart-reader interpreting Keepa data.

This step closes that gap. It runs after `08_buy_plan` and produces two new artefacts per run:

1. A structured `buyer_report_<ts>.json` payload — one entry per non-KILL row, carrying identity, economics, buy-plan, **trends**, the engine's static metrics with traffic-light judgments, an **engine cross-check signal** (engine_verdict / engine_opportunity_score), and an **analyst block** (verdict / score / 4-dim breakdown / trend story / narrative / action prompt).

2. A single self-contained `buyer_report_<ts>.html` — verdict-grouped cards (BUY → NEGOTIATE → SOURCE → WAIT → SKIP), each leading with the analyst's verdict, score breakdown, narrative + action, trend arrows, and economics. Static engine metrics are demoted to a supporting-data section at the bottom of each card. Engine cross-check tucked away in a collapsed `<details>` panel.

**Two-layer architecture:**

- **Engine** (deterministic, pure-Python) — produces signals: metrics, trends, economics, ceilings, predicted velocity, risk flags. Filters KILL rows out.
- **Analyst** (Claude via Cowork at runtime; deterministic fallback when no Cowork) — reads the engine's signals like a human reads charts, returns verdict + 0-100 score + 4-dim reasoning + trend story + narrative + action.

The analyst layer is what the operator reads. The engine layer is the source of truth the analyst consumes. The engine never assigns the operator-facing verdict in v2 — it only filters KILL.

The step is **pure additive** — it does not mutate `decision`, `opportunity_verdict`, `buy_plan_*`, or any upstream column. It composes existing fields into a buyer-friendly slice.

---

## 2. Out of scope

This PRD is deliberately narrow. The following are real follow-ups, **not** in this step:

- **Real-time Amazon scraping** for prices, reviews, or BSR at render time.
- **Sortable / filterable interactive tables.** Operators who need slicing use the XLSX.
- **Email send-out integration.** The HTML is single-file and email-friendly, but actually sending it (Resend, SES, SMTP) is downstream tooling.
- **Multi-language prose.** English only.
- **Multi-marketplace.** UK only.
- **PDF rendering.** The HTML is print-friendly via `@media print`, no separate PDF artefact.
- **Extending SP-API `enrich` to capture image URLs.** Catalog response provides `image_count` (int) but no URL. The PRD uses the deterministic public URL pattern (§4.5) — first-party image URLs are a separate workstream.
- **Per-strategy orchestration YAMLs for the 5 strategies that don't yet have one** (only `orchestration/runs/keepa_finder.yaml` exists today). This PRD creates the generic `orchestration/runs/buyer_report_prose.yaml` task; wiring it into per-strategy orchestrations is deferred.
- **Uploading `buyer_report_<ts>.json` to Google Drive alongside the XLSX.** Cowork-internal contract.
- **Changes to `csv_writer.py`, `excel_writer.py`, or `markdown_report.py`.** Unchanged.

---

## 3. Pipeline placement

```
01_discover → 02_resolve → 03_enrich → 04_calculate
            → 04.5_candidate_score → 05_decide
            → 07_validate_opportunity → 08_buy_plan
            → 06_output (CSV / XLSX / MD)
            → 09_buy_plan_html
              ├─ engine produces JSON payload (with deterministic fallback analyst block)
              ├─ engine renders HTML from populated JSON
              └─ Cowork orchestration (post-engine, optional):
                   ├─ reads JSON
                   ├─ Claude per row → upgraded analyst block
                   ├─ writes JSON back
                   └─ re-renders HTML via the engine's CLI
```

**Test-count baseline:** branch `feat/buyer-report` cuts off `main` at the head of PR #78 (08_buy_plan, merged). The Python test count at branch-cut is the post-merge baseline; this PRD's acceptance criterion is that the existing tests still pass at whatever count `main` shows when implementation starts, plus ~110 new tests for v2.

---

## 4. Data shape — JSON payload (v2)

### 4.1 Top-level structure

```json
{
  "schema_version": 2,
  "prompt_version": 2,
  "run_id": "20260503_120000",
  "strategy": "supplier_pricelist",
  "supplier": "abgee",
  "generated_at": "2026-05-03T12:00:00Z",
  "verdict_counts": {
    "BUY": 6, "SOURCE_ONLY": 12, "NEGOTIATE": 4, "WATCH": 220, "KILL": 5478
  },
  "rows": [...]
}
```

Note: `verdict_counts` reflects the **engine's** verdicts. Analyst counts are derived at render time from each row's `analyst.verdict`.

### 4.2 Per-row entry

```json
{
  "asin": "B0...",
  "title": "...", "brand": "...", "supplier": "abgee", "supplier_sku": "...",
  "amazon_url": "https://www.amazon.co.uk/dp/B0...",
  "image_url": "https://images-na.ssl-images-amazon.com/images/P/B0....jpg",

  "engine_verdict": "WATCH",                  // engine's deterministic verdict (cross-check)
  "engine_verdict_confidence": "LOW",
  "engine_opportunity_score": 70,
  "next_action": "Monitor price, seller count, ...",

  "analyst": {                                 // populated by analyst.py or Cowork
    "verdict": "WAIT",                         // BUY / NEGOTIATE / SOURCE / WAIT / SKIP
    "verdict_confidence": "LOW",               // HIGH / MEDIUM / LOW
    "score": 70,
    "dimensions": [
      {"name": "Profit",      "score": 22, "max": 25, "rationale": "..."},
      {"name": "Competition", "score": 18, "max": 25, "rationale": "..."},
      {"name": "Stability",   "score": 8,  "max": 25, "rationale": "..."},
      {"name": "Operational", "score": 22, "max": 25, "rationale": "..."}
    ],
    "trend_arrows": {"sales": "↗", "sellers": "↗", "price": "?"},
    "trend_story": "Sales rising, supply expanding faster — race to share before margins compress.",
    "narrative": "Economics are excellent — £4.46/unit at 111% ROI...",
    "action_prompt": "Re-check in 4 weeks; INSUFFICIENT_HISTORY clears as more data accumulates."
  },

  "economics": {
    "buy_cost_gbp": 4.00, "market_price_gbp": 16.85,
    "profit_per_unit_gbp": 4.46, "roi_conservative_pct": 1.114,
    "target_buy_cost_gbp": 5.96,                // BUY ceiling — "Don't exceed"
    "target_buy_cost_stretch_gbp": 4.71         // tighter goal — "Aim for"
  },

  "buy_plan": {
    "order_qty_recommended": null,
    "capital_required_gbp": null,
    "projected_30d_units": 5,
    "projected_30d_revenue_gbp": 84.50,
    "projected_30d_profit_gbp": 22.29,
    "payback_days": null,
    "gap_to_buy_gbp": null,
    "gap_to_buy_pct": null,
    "buy_plan_status": "BLOCKED_BY_VERDICT"
  },

  "trends": {
    "bsr_slope_30d": -0.0042, "bsr_slope_90d": -0.0076, "bsr_slope_365d": 0.002,
    "joiners_90d": 5,                            // net seller change in 90d
    "fba_count_90d_start": 1,
    "bb_drop_pct_90": null,
    "buy_box_avg_30d": 16.96, "buy_box_avg_90d": 16.32,
    "buy_box_min_365d": 8.0,
    "buy_box_oos_pct_90": 0.26,
    "listing_age_days": 1388
  },

  "metrics": [...],                              // 7-row traffic-light table — supporting data
  "engine_reasons": [...],
  "engine_blockers": [...],
  "risk_flags": [...]
}
```

### 4.3 Verdict taxonomy (analyst-side)

5 verdicts, each mapping to a distinct operator action:

| Verdict | What it means | Operator action |
|---|---|---|
| `BUY` | Economics work; trends not actively negative; act now. | Place a test order. |
| `NEGOTIATE` | Healthy listing, but `buy_cost > target_buy_cost_buy`. Gap is closeable. | Push supplier price down; re-run. |
| `SOURCE` | No supplier cost yet (`buy_cost == 0` or null) but listing looks workable. | Find a supplier; aim for ≤ target_buy_cost_buy. |
| `WAIT` | Not BUY-grade, but reason is fixable by the passage of time (history depth, OOS spike, recent volatility). | Re-check in N weeks. |
| `SKIP` | Structurally viable but the chart story doesn't justify time. | Move on. |

KILL is never visible in the report — those rows are filtered out before the analyst step.

### 4.4 KILL filter (engine-side, unchanged)

KILL = "structurally impossible to act on". 9 specific triggers in `_check_kill`:
1. `decision == REJECT` (upstream — invalid EAN, no Amazon match, no valid market price, hazmat-strict)
2. `profit_conservative < 0`
3. `roi_conservative < kill_min_roi` (15% default)
4. `sales_estimate < kill_min_sales` (currently disabled — set to 0 per operator preference)
5. `PRICE_FLOOR_HIT` flag (current at all-time price floor)
6. `price_volatility_90d ≥ 0.40`
7. `bsr_slope_90d ≥ 0.10` (BSR climbing fast = sales falling fast = listing dying)
8. `amazon_bb_pct_90 ≥ 0.90` (Amazon owns Buy Box almost entirely)
9. `fba_eligible == False`

The KILL filter is the engine's only verdict-side responsibility in v2. Everything subjective (volume gates, trend reading, share calculations) is the analyst's call.

### 4.5 Image URL convention (unchanged from v1)

`image_url` is always populated as `https://images-na.ssl-images-amazon.com/images/P/{asin}.jpg`. Empirical pattern, not contractual. ~80% hit rate in practice. Card uses `<img onerror="this.style.display='none'">` to hide cleanly when broken.

---

## 5. HTML layout — card structure (v2)

Single self-contained `<!DOCTYPE html>` document, embedded `<style>` block, no JS (other than `<img onerror>` inline fallback).

### 5.1 Card hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│ [Image] Casdon Toaster Toy            ┌───────────────┐   │
│         B0B636ZKZQ · Casdon           │  WAIT         │   │
│                                        │  LOW conf.    │   │
│                                        │  Score 74/100 │   │
│                                        └───────────────┘   │
├─────────────────────────────────────────────────────────────┤
│ Score breakdown                                             │
│   Profit       22/25  ████████████████░░  £4.46/unit; ...  │
│   Competition  20/25  ████████████████░░  3 sellers; ...   │
│   Stability    10/25  ████████░░░░░░░░░░  OOS 26%; ...     │
│   Operational  22/25  ████████████████░░  ungated; ...     │
├─────────────────────────────────────────────────────────────┤
│ Buyer's read                                                │
│   Economics are excellent — £4.46/unit at 111% ROI with     │
│   £1.96 headroom — but data confidence is too thin to       │
│   commit. Sales improving while five new sellers joined...  │
│   Next step: Re-check in 4 weeks; INSUFFICIENT_HISTORY...   │
├─────────────────────────────────────────────────────────────┤
│ Direction (90d)                                             │
│   Sales ↗   Sellers ↗   Price ?                             │
│   Sales rising, supply expanding faster — race to share.    │
├─────────────────────────────────────────────────────────────┤
│ Economics                                                   │
│   Your cost              £4.00 (inc)                        │
│   Aim for                £4.71 (inc) — negotiate to this    │
│   Don't exceed           £5.96 (inc) — absolute BUY ceiling │
│   Projected 30d revenue  5 units · £84.50 (inc)             │
│   Projected 30d profit   5 units · £22.29 (inc)             │
├─────────────────────────────────────────────────────────────┤
│ Supporting metrics (7-row traffic-light table)              │
│   ●  FBA Sellers          3            ≤ 3 ceiling          │
│   ●  Amazon on Listing    No                                 │
│   ●  Amazon BB Share 90d  2%           below 30% threshold  │
│   ●  Price Consistency    0.09         stable               │
│   ●  Listing Sales/mo     71           moderate demand      │
│   ●  Your Share/mo        9 /mo        bottom-quartile      │
│   ●  Sales Activity (30d) 26 sales     ~39/mo implied       │
├─────────────────────────────────────────────────────────────┤
│ ▸ Engine cross-check (collapsed details)                    │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Section ordering (top-to-bottom)

1. **Card header** — image left, identity centre, verdict block right (badge + confidence + score)
2. **Score breakdown** — 4 horizontal bars (one per dimension) with score / max + rationale
3. **Buyer's read** — narrative paragraph + bold "Next step:" action_prompt. Tinted background to draw the eye.
4. **Direction (90d)** — 3 arrow cells (Sales / Sellers / Price) + 1-line trend story
5. **Economics** — fixed labels: "Your cost" / "Aim for" / "Don't exceed" / "Projected 30d revenue" / "Projected 30d profit". Every £ value annotated `(inc)`.
6. **Supporting metrics** — 7-row traffic-light table (engine's static signals, demoted)
7. **Engine cross-check** — `<details>` collapsed by default; expands to show the engine's verdict, blockers, risk flags

### 5.3 Within-verdict sort

- BUY by `buy_plan.projected_30d_profit_gbp` desc
- Other verdicts by `analyst.score` desc
- Stable sort

### 5.4 TOC

Rendered when `len(rows) >= 4`. Links per ASIN within each verdict section. Omitted on small runs (single-ASIN, etc.).

---

## 6. Cowork orchestration (v2)

Cowork-side task at `orchestration/runs/buyer_report_prose.yaml`. Three steps:

### 6.1 generate_analyst

For each row in the JSON payload, call Claude with the v2 prompt template (`orchestration/runs/buyer_report_prose_prompt.md`). Claude returns a structured-JSON analyst block matching §4.2's `analyst` field shape.

**Cache** keyed on `hash(schema_version + prompt_version + asin + economics + buy_plan + trends + metrics + risk_flags)`. Re-runs against unchanged engine output cost zero LLM calls.

**Fail-soft**: rate-limit / API error / malformed JSON / schema mismatch → log + skip the row. The engine's deterministic fallback analyst block (already in the JSON) stays in place.

### 6.2 merge_analyst_into_json

Walk the JSON payload. For each ASIN with a Claude-generated analyst block from step 6.1, overwrite the engine-fallback analyst block. Atomic write (tmp + rename).

### 6.3 rerender_html

Invoke the engine's renderer CLI:

```bash
python -m sourcing_engine.buy_plan_html.cli render-from-json \
  buyer_report_<ts>.json buyer_report_<ts>.html
```

Atomic write. Idempotent — running twice with unchanged JSON produces byte-identical HTML.

### 6.4 Engine-alone path (no Cowork)

Engine's `buy_plan_html` step calls `analyst.fallback_analyse(row)` per row at render time, populating the analyst block deterministically before HTML render. The HTML is fully usable. Cowork-orchestrated runs UPGRADE this fallback to LLM analysis.

---

## 7. Configuration

```yaml
buy_plan_html:
  enabled: true   # produce JSON+HTML by default
```

`--no-html` CLI flag on `run.py` skips the writer. Per-strategy YAMLs interpolate `{order_mode}` etc. into the step config.

The analyst step has no engine-side config — its prompt template lives at `orchestration/runs/buyer_report_prose_prompt.md` and is versioned via `prompt_version` in the engine.

---

## 8. Edge cases

The step never crashes the pipeline. All of these degrade gracefully:

1. **Empty DataFrame** — write JSON with `rows: []` and HTML with "no actionable rows" notice.
2. **All rows are KILL** — same as empty.
3. **Per-row exception in payload builder** — log via `logger.exception`, skip the row.
4. **Per-row exception in analyst fallback** — log + leave analyst block as nulls; renderer falls through.
5. **Per-row exception in renderer** — log + emit minimal "render failed" article for that ASIN; run continues.
6. **Cowork analyst returns malformed JSON** — log + skip; engine fallback persists.
7. **Cowork analyst returns valid JSON with bad verdict** (not in 5-state taxonomy) — log + skip; engine fallback persists.
8. **Cowork crashes mid-run** — already-cached rows remain cached; `merge_analyst_into_json` is idempotent; HTML re-render produces deterministic output. Resume cleanly.
9. **Re-running orchestration on unchanged engine output** — cache hits all rows, zero LLM calls.
10. **Image URL fails to load** — `<img onerror>` hides cleanly.
11. **Single-ASIN runs** with ≤3 rows — TOC omitted.
12. **Strategy with `null` supplier** (`keepa_finder`, `single_asin`) — HTML title falls back to `Buyer Report — {strategy} — {date}`.

---

## 9. Tests

**Total at v2 ship:** 1467 Python tests pass; 106 of them are buyer-report-specific.

- **`test_payload.py`** — 49 tests covering top-level shape + per-metric traffic-light judgment + fallback paths
- **`test_renderer.py`** — 16 tests covering verdict-led card structure, dimension bars, buyer's read, direction arrows, economics labels, sort order, TOC threshold, HTML escape
- **`test_buy_plan_html.py`** (step wrapper) — 11 tests covering atomic write, disabled-config behaviour, single-ASIN filename pattern, analyst-fallback population
- **`test_html_snapshot.py`** — 1 snapshot pinning the structural HTML across the 4-verdict fixture (regenerated on layout changes via `--snapshot-update`)
- **`test_template_prose.py`** — retained for the `template_prose.py` module (unused in v2 but kept; deletion in a follow-up)

**Manual verification (one-off):**
- Real `python run.py --supplier abgee` → HTML with verdict-led cards, correct sort, no orphan markers
- B0B636ZKZQ single-ASIN at £4.00 → WAIT / LOW / 70-78 score / four populated dimensions / direction arrows / "Aim for £4.71 / Don't exceed £5.96"
- Print preview produces clean per-card pagination
- Forwards to Gmail / pastes into Notion preserving layout

---

## 10. Acceptance criteria (v2 — met)

A real `python run.py --strategy single_asin --asin B0B636ZKZQ --buy-cost 4.00` produces a `buyer_report_B0B636ZKZQ_<ts>.html` where:

1. JSON validates against the v2 schema in §4.
2. HTML parses cleanly (BeautifulSoup, all tags balanced).
3. Card has analyst-led header (verdict badge + score + 4-dim breakdown bars).
4. Buyer's read section is populated (narrative + action_prompt) — either from Cowork-Claude or engine fallback.
5. Direction section shows 3 arrows + trend story.
6. Economics labels are "Aim for" / "Don't exceed" — no "stretch" jargon, all values `(inc)`.
7. Supporting metrics table renders 7 traffic-light rows.
8. Engine cross-check is in a collapsed `<details>` element.
9. Print to PDF works without broken card splits.
10. All Python tests pass; MCP suite untouched.

---

## 11. Versioning + handoff to SPEC.md

After ship and sign-off:

1. Fold this PRD into `docs/SPEC.md` as section **§8e — Buyer report**, mirroring the §8c / §8d style.
2. Move this file to `docs/archive/PRD-buyer-report.md`.
3. Update `CLAUDE.md` Current State block.

The buyer report introduces no new engine signals (the trends block surfaces fields the engine already computed; `image_url` is derived deterministically from `asin`).

---

## 12. Non-objectives, restated

This step is **not** the operator's full buyer cockpit. It is:
- A per-product card view that turns engine output into a buyer-readable analysis
- An analyst-led report that names what to do and why, not a rule-based verdict + thresholds

Sortable interactive UI, live Amazon scraping, email send-out, multi-language, A/B prose styles, and PDF generation are **separately specced and shipped**. Anything that requires reading external services at render time (live Amazon, live SP-API) or interactive UI affordances (sort, filter, slice) is out of scope for `09_buy_plan_html` by design — the card is a snapshot of the engine's run, augmented by the analyst's read of that snapshot.
