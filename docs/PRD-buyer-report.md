# PRD: Buyer Report — Step `09_buy_plan_html`

**Status:** Ready for implementation
**Author:** Peter Farrell (with Claude)
**Target branch:** `feat/buyer-report` (off `main`)
**Authoritative spec it builds on:** `docs/SPEC.md` §3-§5, `docs/PRD-buy-plan.md` (08_buy_plan), `docs/architecture.md`
**Supersedes:** none — this is a new step

---

## 1. Objective

Today the engine emits a CSV (audit trail), an XLSX (operator working file), a markdown report (per-supplier summary), and a single-ASIN stdout block (per-ASIN deep dive). What's missing is the **buyer's view** — a per-product, human-readable, share-friendly artefact that turns each validated row into a small "should I buy this, and why" decision card.

This step closes that gap. It runs after `08_buy_plan` and produces two new artefacts per run:

1. A structured `buyer_report_<ts>.json` payload — one entry per non-KILL row, carrying identity, verdict, economics, the eleven buy-plan columns, the seven scoring metrics with traffic-light judgments, and the engine's existing reasons / blockers / next-action lists.

2. A single self-contained `buyer_report_<ts>.html` — verdict-grouped cards (BUY → SOURCE_ONLY → NEGOTIATE → WATCH), each with a product image left rail, an economics mini-grid, a traffic-light scoring table, and a placeholder for a 2-3 sentence narrative paragraph.

The narrative paragraphs are populated by a **Cowork orchestration step** that reads the JSON, calls Claude per row to generate the prose, and walks the HTML to inject paragraphs into per-card placeholder markers. When the engine runs without Cowork, a deterministic template-prose fallback fills the placeholders so the HTML is always usable on its own.

The step is **pure additive** — it does not mutate `decision`, `opportunity_verdict`, `buy_plan_*`, or any upstream column. It composes existing fields into a buyer-friendly slice.

---

## 2. Out of scope

This PRD is deliberately narrow. The following are real follow-ups, **not** in this step:

- **Real-time Amazon scraping** for prices, reviews, or BSR at render time. The HTML reflects the engine's snapshot at run time; live freshness is a separate concern.
- **Sortable / filterable interactive tables.** The HTML is static; operators who need slicing use the XLSX.
- **Email send-out integration.** The HTML is single-file and email-friendly, but actually sending it (Resend, SES, SMTP) is downstream tooling.
- **Multi-language prose.** English only.
- **A/B prose styles.** The Cowork agent's prompt is pinned (one tone, one length).
- **Multi-marketplace.** UK only.
- **PDF rendering.** The HTML is print-friendly via `@media print`, but no separate PDF artefact is produced.

---

## 3. Pipeline placement

```
01_discover → 02_resolve → 03_enrich → 04_calculate
            → 04.5_candidate_score → 05_decide
            → 07_validate_opportunity → 08_buy_plan
            → 06_output → 09_buy_plan_html
```

`09_buy_plan_html` runs **after** the existing CSV / XLSX / MD writers (`06_output`). It must run last so any prior writer failure surfaces first, and so the buyer report is the freshest deliverable.

Implemented as `fba_engine/steps/buy_plan_html.py` (runner-compatible step wrapper). Core logic in `shared/lib/python/sourcing_engine/buy_plan_html/`:

- `payload.py` — JSON shape + per-metric traffic-light builder
- `renderer.py` — HTML skeleton writer (verdict-grouped cards + placeholders)
- `template_prose.py` — deterministic fallback prose composer (when no Cowork)

Wired into 6 strategy YAMLs — every strategy that produces buy_plan output:
- `supplier_pricelist.yaml`
- `keepa_finder.yaml`
- `keepa_niche.yaml`
- `oa_csv.yaml`
- `seller_storefront_csv.yaml`
- `single_asin.yaml`

NOT `seller_storefront.yaml` (leads-only, no `validate_opportunity` upstream → no buy_plan → nothing to report on).

---

## 4. Data shape — JSON payload

`buyer_report_<ts>.json` carries one entry per non-KILL row, written in verdict-grouped order (BUY first, WATCH last). The JSON is the contract between the engine (write side) and the Cowork orchestration step (read + LLM prose injection side).

### 4.1 Top-level structure

```json
{
  "schema_version": 1,
  "run_id": "20260503_120000",
  "strategy": "supplier_pricelist",
  "supplier": "abgee",
  "generated_at": "2026-05-03T12:00:00Z",
  "verdict_counts": {
    "BUY": 6, "SOURCE_ONLY": 12, "NEGOTIATE": 4, "WATCH": 220, "KILL": 5478
  },
  "rows": [
    {...},
    {...}
  ]
}
```

### 4.2 Per-row entry

```json
{
  "asin": "B0B636ZKZQ",
  "title": "Casdon Morphy Richards Toaster Toy",
  "brand": "Casdon",
  "supplier": "Casdon",
  "supplier_sku": "12345",
  "amazon_url": "https://www.amazon.co.uk/dp/B0B636ZKZQ",
  "image_url": "https://m.media-amazon.com/images/I/...jpg",
  "image_url_fallback": "https://images-na.ssl-images-amazon.com/images/P/B0B636ZKZQ.jpg",

  "verdict": "BUY",
  "verdict_confidence": "HIGH",
  "opportunity_score": 85,
  "next_action": "Check live price, confirm stock, place test order",

  "economics": {
    "buy_cost_gbp": 4.00,
    "market_price_gbp": 16.85,
    "profit_per_unit_gbp": 8.35,
    "roi_conservative_pct": 1.114,
    "target_buy_cost_gbp": 9.50,
    "target_buy_cost_stretch_gbp": 8.52
  },

  "buy_plan": {
    "order_qty_recommended": 13,
    "capital_required_gbp": 52.00,
    "projected_30d_units": 18,
    "projected_30d_revenue_gbp": 303.30,
    "projected_30d_profit_gbp": 150.30,
    "payback_days": 21.7,
    "gap_to_buy_gbp": null,
    "gap_to_buy_pct": null,
    "buy_plan_status": "OK"
  },

  "metrics": [
    {"key": "fba_seller_count",   "label": "FBA Sellers",         "value_display": "4",       "verdict": "green",  "rationale": "≤ 5 ceiling at this volume"},
    {"key": "amazon_on_listing",  "label": "Amazon on Listing",   "value_display": "No",      "verdict": "green",  "rationale": "Buy Box rotation safe"},
    {"key": "amazon_bb_pct_90",   "label": "Amazon BB Share 90d", "value_display": "10%",     "verdict": "green",  "rationale": "below 30% buy threshold"},
    {"key": "price_volatility",   "label": "Price Consistency",   "value_display": "0.10",    "verdict": "green",  "rationale": "stable (< 0.20 cap)"},
    {"key": "sales_estimate",     "label": "Volume (units/mo)",   "value_display": "250",     "verdict": "green",  "rationale": "above 100 target"},
    {"key": "predicted_velocity", "label": "Your Expected Sales", "value_display": "18 /mo",  "verdict": "amber",  "rationale": "share-of-rotation = 18; mid-tier"},
    {"key": "bsr_drops_30d",      "label": "Stock Replenishments","value_display": "45 /mo",  "verdict": "green",  "rationale": "healthy turnover"}
  ],

  "engine_reasons": ["sales=250/mo→25", "ROI=111%+£8.35→25", "AMZ BB=10%+sellers ok→20"],
  "engine_blockers": [],
  "risk_flags": []
}
```

### 4.3 Per-metric traffic-light keys (locked schema)

Exactly these seven, in this order:

| key                    | label                       | source column                  | green threshold                       |
|------------------------|-----------------------------|--------------------------------|---------------------------------------|
| `fba_seller_count`     | FBA Sellers                 | `fba_seller_count`             | `_is_seller_count_healthy()` per OV   |
| `amazon_on_listing`    | Amazon on Listing           | `amazon_on_listing`            | "N" (or absent)                       |
| `amazon_bb_pct_90`     | Amazon BB Share 90d         | `amazon_bb_pct_90`             | `< OV.max_amazon_bb_share_buy` (0.30) |
| `price_volatility`     | Price Consistency           | `price_volatility_90d`         | `< OV.max_price_volatility_buy` (0.20) |
| `sales_estimate`       | Volume (units/mo)           | `sales_estimate`               | `>= OV.target_monthly_sales` (100)    |
| `predicted_velocity`   | Your Expected Sales         | `predicted_velocity_mid`       | `> 0.5 × sales_estimate × (1 - bb%)` (top-quartile rotation share) |
| `bsr_drops_30d`        | Stock Replenishments        | `bsr_drops_30d`                | `>= max(20, sales_estimate × 0.5)`    |

Each metric has 3 verdicts: `green` / `amber` / `red`. Amber thresholds are halfway between green threshold and severe ("red") cutoff. Concrete amber/red logic captured in `payload.py::_judge_metric` and pinned by parametrised tests.

When a source column is missing/None, `verdict = "grey"` and `value_display = "—"`. Rationale = "signal missing".

### 4.4 Field-level rules

- `image_url` populated **only when** the row carries `catalog_image_url` (set by `enrich` step when SP-API ran with creds). Else `null`.
- `image_url_fallback` always populated as `https://images-na.ssl-images-amazon.com/images/P/{asin}.jpg`. The HTML renderer uses `image_url` if non-null, falls back via `<img src="" onerror="this.src='{fallback}'">` (one-time fallback; we accept that some ASINs will show neither).
- `buy_plan` block: copy of the eleven `08_buy_plan` columns. `null` for blanks.
- `economics` block: derived from the calculate-step columns; `null` when buy_cost is absent (SOURCE_ONLY / wholesale-flow rows).
- `metrics[].value_display` is always a pre-formatted string (the renderer doesn't apply formatting). Engine emits `"4"`, `"10%"`, `"45 /mo"`, etc. — same format the operator sees.

---

## 5. HTML layout — card structure

Single self-contained `<!DOCTYPE html>` document, embedded `<style>` block, no JS, no external CSS, no CDN dependencies.

### 5.1 Document structure

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Buyer Report — abgee — 2026-05-03</title>
  <style>/* embedded CSS */</style>
</head>
<body>
  <header class="report-header">
    <h1>Buyer Report</h1>
    <div class="run-meta">abgee · 2026-05-03 12:00 UTC</div>
    <div class="verdict-counts">
      <span class="vc vc-buy">BUY 6</span>
      <span class="vc vc-source">SOURCE_ONLY 12</span>
      <span class="vc vc-negotiate">NEGOTIATE 4</span>
      <span class="vc vc-watch">WATCH 220</span>
    </div>
  </header>

  <nav class="toc">
    <h3>Contents</h3>
    <ul>
      <li><a href="#section-buy">BUY (6)</a><ul>...one per ASIN...</ul></li>
      <li><a href="#section-source-only">SOURCE_ONLY (12)</a><ul>...</ul></li>
      ...
    </ul>
  </nav>

  <main>
    <section id="section-buy" class="verdict-section">
      <h2>BUY (6)</h2>
      <article id="asin-B0B636ZKZQ" class="card verdict-buy">...</article>
      ...
    </section>
    <section id="section-source-only" class="verdict-section">...</section>
    <section id="section-negotiate" class="verdict-section">...</section>
    <section id="section-watch" class="verdict-section">...</section>
  </main>

  <footer>
    <div>Generated by 08_buy_plan + 09_buy_plan_html · {generated_at}</div>
  </footer>
</body>
</html>
```

### 5.2 Per-card structure

```html
<article id="asin-B0B636ZKZQ" class="card verdict-buy">
  <header class="card-header">
    <span class="verdict-badge">BUY · HIGH</span>
    <span class="card-score">Score: 85/100</span>
  </header>

  <div class="card-identity-economics">
    <a class="card-image" href="https://www.amazon.co.uk/dp/B0B636ZKZQ" target="_blank" rel="noopener">
      <img src="https://m.media-amazon.com/images/I/...jpg"
           onerror="this.onerror=null; this.src='https://images-na.ssl-images-amazon.com/images/P/B0B636ZKZQ.jpg'"
           alt="Casdon Morphy Richards Toaster Toy"
           loading="lazy" width="200" height="200">
    </a>
    <div class="card-summary">
      <h3 class="card-title">Casdon Morphy Richards Toaster Toy</h3>
      <div class="card-id">ASIN <a href="https://www.amazon.co.uk/dp/B0B636ZKZQ">B0B636ZKZQ</a> · Brand Casdon</div>
      <table class="economics-grid">
        <!-- rows vary by verdict — see §5.3 -->
      </table>
    </div>
  </div>

  <section class="card-prose">
    <h4>Why we should buy</h4>
    <div class="prose" data-asin="B0B636ZKZQ"><!-- prose:B0B636ZKZQ --></div>
  </section>

  <section class="card-scoring">
    <h4>Scoring</h4>
    <table class="metrics">
      <tbody>
        <tr><td class="dot dot-green"></td><td class="metric-label">FBA Sellers</td><td class="metric-value">4</td><td class="metric-rationale">≤ 5 ceiling at volume</td></tr>
        ...
      </tbody>
    </table>
  </section>

  <footer class="card-next-action">
    <strong>Next action:</strong> Check live price, confirm stock, place test order
  </footer>
</article>
```

### 5.3 Per-verdict economics grid variants

| Verdict       | economics-grid contents (rows × cols)                                                    |
|---------------|------------------------------------------------------------------------------------------|
| `BUY`         | Buy cost · Target buy (stretch) // Order qty · Capital // Payback · 30d profit           |
| `SOURCE_ONLY` | Buy cost: not found · Target ≤ £X (stretch £Y) // Projected 30d revenue · 30d profit (at target) |
| `NEGOTIATE`   | Currently · Target ceiling // Gap (£ + %) // 30d profit at current cost                  |
| `WATCH`       | Buy cost · Target buy (stretch) // Projected 30d revenue · 30d profit // (no order block) |

Per-verdict prose heading also varies: *"Why we should buy"* / *"Why source this"* / *"Why negotiate"* / *"Why watch"*.

### 5.4 Style rules

- Single embedded `<style>` block. No external CSS, no `<link>`, no JS.
- System font stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`.
- Verdict colours: BUY green (`#27AE60`), SOURCE_ONLY blue (`#2980B9`), NEGOTIATE amber (`#E67E22`), WATCH yellow (`#F1C40F`). Border-left 4px on cards uses the verdict colour.
- Traffic-light dot CSS: `<span class="dot dot-green"></span>` renders as a 12px circle via `border-radius: 50%; background: <colour>`.
- Card max-width 900px, centred. TOC sticky on desktop (`position: sticky` at sidebar widths ≥ 1280px), collapsed to top of body on narrow viewports.
- `@media print`: hide `<nav class="toc">`, page-break-inside: avoid on cards, drop background colours to white, keep verdict-badge colour for visual identity.

---

## 6. Cowork orchestration — LLM prose injection

The engine writes the JSON + HTML skeleton with placeholder markers. A separate Cowork agent step generates the prose and walks the HTML to inject it.

### 6.1 Orchestration task definition

New file: `orchestration/runs/buyer_report_prose.yaml`. Generic (reusable across strategies). Inputs:

- `json_path`: absolute path to `buyer_report_<ts>.json`
- `html_path`: absolute path to `buyer_report_<ts>.html`

Behaviour:

1. Load JSON.
2. For each `rows[]` entry: dispatch a Claude agent with the row's verdict + structured metrics + rationales + risk flags. Prompt asks for a 2-3 sentence buyer-perspective paragraph (what to do, why, what to watch).
3. After all paragraphs returned: read HTML, replace each `<!-- prose:{asin} -->` marker with `<p class="prose-text">{paragraph}</p>`. Atomic-write back to `html_path`.
4. Cache responses keyed on `hash(asin + verdict + sorted(metric values + economics))`. Cache lives at `.cache/buyer_report_prose/<hash>.txt`. Re-running on unchanged engine output costs zero LLM calls.

### 6.2 Strategy orchestration wiring

Each existing strategy orchestration YAML in `orchestration/runs/` (e.g. `keepa_finder.yaml`, `single_asin.yaml`) gets a new task entry that runs after the engine task:

```yaml
- name: buyer_report_prose
  uses: orchestration/runs/buyer_report_prose.yaml
  inputs:
    json_path: "{output_dir}/buyer_report_{timestamp}.json"
    html_path: "{output_dir}/buyer_report_{timestamp}.html"
  if: file_exists("{output_dir}/buyer_report_{timestamp}.json")
```

The `if: file_exists(...)` guard handles the case where the engine ran with `--no-html` and the JSON wasn't produced.

### 6.3 LLM prompt contract (pinned)

The prose-generation agent receives the row JSON as input and must:

- Return exactly one paragraph, 2-3 sentences, ~50-90 words.
- Reference the verdict explicitly (BUY / SOURCE / NEGOTIATE / WATCH) by what it implies, not the bare label.
- Cite at least one specific metric value (e.g. *"sales running at 250/mo"*).
- Mention any red traffic-light metric or risk flag if present. Skip if all-green.
- Tone: terse, operator-to-operator. No marketing fluff.
- No HTML tags in the response (renderer wraps in `<p>`).

The prompt template lives at `orchestration/runs/buyer_report_prose_prompt.md`. Pinned at v1; future tone changes ship as v2 with a config knob.

---

## 7. Configuration

New optional block in `shared/config/decision_thresholds.yaml`:

```yaml
buy_plan_html:
  enabled: true                          # always produce JSON+HTML by default
  metrics_traffic_light:
    predicted_velocity_amber_pct: 0.25   # below 25th-percentile share → amber
    bsr_drops_floor: 20                  # < 20/mo always amber regardless of sales
```

Values are conservative defaults; ops can override per-run via `--context`.

Per-run override:
- `--no-html` CLI flag on `run.py` (skips both JSON and HTML)
- `html_enabled` runner-context value (default `"true"`)

`min_roi_buy`, `min_profit_absolute_buy`, `target_monthly_sales`, etc. are reused from `OpportunityValidation` — do not duplicate. The traffic-light judgments use the same thresholds the verdict layer used.

Loaded via a new `BuyPlanHtml` dataclass in `fba_config_loader.py` (mirror `BuyPlan`). Permissive defaults so existing yaml files without the block still load.

---

## 8. Edge cases

The step must never crash the pipeline. All of the following degrade gracefully, never raise:

1. **Empty DataFrame** — write a JSON with `rows: []` and an HTML carrying the run header + an empty `<main>` with a "no actionable rows" notice. No cards.
2. **All rows are KILL** — same as empty DataFrame. The buyer report is for actionable verdicts; KILL doesn't count.
3. **Per-row exception in the payload builder** — log via `logger.exception`, omit the row from the JSON, continue. The HTML simply has fewer cards than the run produced. Counts in the header reflect what landed in the JSON, not what the pipeline saw.
4. **Per-row exception in the renderer** — log, render a minimal `<article class="card render-error">` with the ASIN + a "render failed; see logs" notice, continue.
5. **Missing image URLs** — `<img>` falls back to the public URL pattern; if that also fails, browser shows the alt text. No JS retry beyond the one `onerror` swap.
6. **Missing prose marker after Cowork run** — log warn ("orchestration agent didn't return prose for ASIN X"); the template-prose fallback that engine already wrote stays in place.
7. **Cowork agent returns malformed prose** (HTML tags, multiple paragraphs, etc.) — strip tags + collapse whitespace before injection. Hard cap at 500 chars.
8. **Cowork run uses `--no-html`** — engine wrote nothing; orchestration's `if: file_exists(...)` guard short-circuits cleanly.
9. **Re-running orchestration on unchanged engine output** — cache hits all rows, zero LLM calls, HTML re-injected idempotently.

---

## 9. Tests

Mirror the patterns in `fba_engine/steps/tests/test_validate_opportunity.py` and `shared/lib/python/sourcing_engine/tests/test_buy_plan.py`. Aim for ~45 new tests across:

**Unit tests (`shared/lib/python/sourcing_engine/buy_plan_html/tests/`):**
- `test_payload.py` — per-verdict happy path; per-metric traffic-light judgment parametrised across thresholds (green/amber/red boundary cases); missing-data → `verdict: grey`; image_url fallback always populated; row dict mutation invariant pinned.
- `test_template_prose.py` — each verdict produces non-empty deterministic text; same row twice → byte-identical prose; missing-data row degrades to a short fallback; no template references undefined fields.
- `test_renderer.py` — 4-verdict synthetic frame produces valid HTML (BeautifulSoup parses); each card has a `<!-- prose:{asin} -->` marker; verdict-section headings present with correct counts; CSS class `verdict-buy` etc. on cards; image-rail anchor wraps image and points to `amazon_url`; TOC contains one `<a>` per row.
- `test_prose_injector.py` — pure function `inject_prose(html, {asin: prose}) -> html`; all markers replaced; missing prose for an ASIN leaves marker untouched (log warn, no crash); markers idempotent across re-runs; ASIN in dict but no marker → log + ignore.

**Step tests (`fba_engine/steps/tests/test_buy_plan_html.py`):**
- Empty df → empty JSON + minimal HTML written, df returned unchanged.
- 4-verdict df → JSON + HTML files land at run dir; JSON parses; HTML structurally valid.
- `--no-html` / `enabled=false` config → no files written, df unchanged.
- Per-row exception → row absent from JSON, HTML carries an error card or skips; run continues.
- File paths interpolated correctly from runner context (e.g. `{output_dir}/{timestamp}`).

**Strategy smoke tests (`fba_engine/strategies/tests/test_runner.py` — extend existing):**
- Each of the 6 wired strategies, after end-to-end run, has `buyer_report_*.html` and `.json` at the expected path. One row's HTML body grep-checked for the verdict label.

**Snapshot test (`shared/lib/python/sourcing_engine/buy_plan_html/tests/test_html_snapshot.py`):**
- Known fixture (4 rows, one per verdict) → write HTML with all template prose populated → diff against stored snapshot. Updated by `--snapshot-update`. Snapshots structural HTML, not prose itself, so prose-only changes don't break the snapshot.

**Cowork orchestration tests (separate):**
- Mock-Claude unit test: canned-response patch → assert each marker replaced, idempotency, malformed-prose stripping.
- Live LLM smoke test (skipped in CI when `ANTHROPIC_API_KEY` missing): 4-row fixture → real agent run → assert each output is 2-3 sentences, mentions verdict and at least one metric. No exact-wording assertion.

**Manual verification (one-off, not in CI):**
- Real `python run.py --supplier abgee --market-data ... --no-preflight` run produces a `buyer_report_*.html` that:
  - Parses cleanly in Chrome / Safari / Firefox.
  - Renders the image rail (or alt text fallback gracefully).
  - Prints to PDF cleanly (no broken card splits).
  - Forwards to email (Gmail / Outlook) without losing layout.
  - Renders in Notion when pasted as HTML.

---

## 10. Acceptance criteria

A real `python run.py --supplier abgee` (or `--strategy keepa_finder ...`) run produces, alongside the existing CSV / XLSX / MD outputs, a `buyer_report_<ts>.html` and `buyer_report_<ts>.json` where:

1. JSON validates against the schema in §4 (parseable, required fields present, `schema_version: 1`).
2. HTML parses cleanly via BeautifulSoup (no malformed tags, all `<section>` and `<article>` elements close).
3. One card per non-KILL row, grouped under verdict-section headings. Counts in `<header>` and TOC match the row counts.
4. All four verdicts (BUY / SOURCE_ONLY / NEGOTIATE / WATCH) render their correct economics-grid variant (per §5.3).
5. Every card has a `<!-- prose:{asin} -->` marker AND a `<div class="prose" data-asin="...">` wrapper. With Cowork orchestration: prose paragraphs land in every marker. Without: template prose is in every marker (no marker left empty).
6. Prints to PDF cleanly — no card split across pages where `page-break-inside: avoid` should hold.
7. All 1357 existing Python tests still pass. ~45 new tests added per §9, all passing.
8. MCP suite untouched.

---

## 11. Versioning + handoff to SPEC.md

After ship and sign-off:

1. Fold this PRD into `docs/SPEC.md` as section **§8e — Buyer report**, mirroring the §8c / §8d style.
2. Move this file to `docs/archive/PRD-buyer-report.md`.
3. Update `CLAUDE.md` Current State block.
4. Add a row to SPEC §9 (signal availability) noting which fields the buyer report consumes (none new — all reuse existing engine signals).

---

## 12. Non-objectives, restated

This step is **not** the operator's full buyer cockpit. It is a single-purpose share-friendly artefact that turns the engine's existing structured output into a per-product narrative card view. Sortable tables, live Amazon scraping, email send-out, multi-language prose, A/B prose styles, and PDF rendering are **separately specced and shipped**. Anything that requires reading external services at render time (live Amazon, live SP-API) or interactive UI affordances (sort, filter, slice) is out of scope for `09_buy_plan_html` by design — keep the step pure-transformation + LLM prose, and let the data-acquisition / interactivity layers be their own modules with their own PRDs.
