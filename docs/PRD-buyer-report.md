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
- **Extending SP-API `enrich` to capture image URLs.** Today the catalog response provides `image_count` (int) but no URL; capturing the URL would require changes to the MCP + the preflight step. This PRD uses only the deterministic public URL pattern (§4.4) — extending enrich for first-party image URLs is a separate workstream.
- **Per-strategy orchestration YAMLs for the 5 strategies that don't yet have one** (only `orchestration/runs/keepa_finder.yaml` exists today). This PRD creates the generic `buyer_report_prose.yaml` task; wiring it into per-strategy orchestrations is deferred to those strategies' own orchestration work.
- **Uploading `buyer_report_<ts>.json` to Google Drive alongside the XLSX.** The JSON is a Cowork-internal contract; operators inspect via the rendered HTML.
- **Changes to `csv_writer.py`, `excel_writer.py`, or `markdown_report.py`.** All three are unchanged by this work — `09_buy_plan_html` only adds new artefacts.

---

## 3. Pipeline placement

```
01_discover → 02_resolve → 03_enrich → 04_calculate
            → 04.5_candidate_score → 05_decide
            → 07_validate_opportunity → 08_buy_plan
            → 06_output (CSV / XLSX / MD)
            → 09_buy_plan_html
```

`09_buy_plan_html` runs **after** `06_output`. It must run last so any prior writer failure surfaces first, and so the buyer report is the freshest deliverable.

**Test-count baseline:** branch `feat/buyer-report` cuts off `main` at the head of PR #78 (08_buy_plan, merged). The Python test count at that point is the post-merge baseline; this PRD's acceptance criterion is that the existing tests still pass at whatever count `main` shows when implementation starts, not a magic number pinned in the PRD.

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
  "prompt_version": 1,
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

`schema_version` and `prompt_version` both feed the orchestration cache key (§6.1). Bumping either invalidates cached prose so a schema or prompt change forces re-generation.

`supplier` is `null` when the strategy has no supplier dimension (`keepa_finder`, `single_asin`). The HTML title falls back to `Buyer Report — {strategy} — {date}`.

### 4.2 Per-row entry

```json
{
  "asin": "B0B636ZKZQ",
  "title": "Casdon Morphy Richards Toaster Toy",
  "brand": "Casdon",
  "supplier": "Casdon",
  "supplier_sku": "12345",
  "amazon_url": "https://www.amazon.co.uk/dp/B0B636ZKZQ",
  "image_url": "https://images-na.ssl-images-amazon.com/images/P/B0B636ZKZQ.jpg",

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

Exactly these seven, in this order, with explicit thresholds for all three verdicts. Each row in this table is the contract; `payload.py::_judge_metric` implements it directly with no additional logic. Tests parametrise every boundary value.

| key                    | label                  | source column            | green                                   | amber                                          | red                                |
|------------------------|------------------------|--------------------------|-----------------------------------------|------------------------------------------------|------------------------------------|
| `fba_seller_count`     | FBA Sellers            | `fba_seller_count`       | passes `_is_seller_count_healthy()` per OV | within 50% over the OV ceiling for sales tier | beyond 150% of the OV ceiling      |
| `amazon_on_listing`    | Amazon on Listing      | `amazon_on_listing`      | `"N"` or absent                         | `"UNKNOWN"`                                    | `"Y"`                              |
| `amazon_bb_pct_90`     | Amazon BB Share 90d    | `amazon_bb_pct_90`       | `< 0.30` (`OV.max_amazon_bb_share_buy`) | `0.30 ≤ x < 0.70` (`OV.max_amazon_bb_share_watch`) | `≥ 0.70`                       |
| `price_volatility`     | Price Consistency      | `price_volatility_90d`   | `< 0.20` (`OV.max_price_volatility_buy`)| `0.20 ≤ x < 0.40` (`OV.kill_price_volatility`) | `≥ 0.40`                           |
| `sales_estimate`       | Volume (units/mo)      | `sales_estimate`         | `≥ 100` (`OV.target_monthly_sales`)     | `20 ≤ x < 100`                                 | `< 20` (`OV.kill_min_sales`)       |
| `predicted_velocity`   | Your Expected Sales    | `predicted_velocity_mid` | `≥ 0.5 × non_amazon_share`              | `0.25 × non_amazon_share ≤ x < 0.5 ×`          | `< 0.25 × non_amazon_share`        |
| `bsr_drops_30d`        | Stock Replenishments   | `bsr_drops_30d`          | `≥ max(20, sales_estimate × 0.5)`       | `≥ max(10, sales_estimate × 0.25)`             | below the amber floor              |

`non_amazon_share` (predicted_velocity row) is a **per-row deterministic value**: `non_amazon_share = sales_estimate × (1 - amazon_bb_pct_90)` when both are present. When either is None, predicted_velocity falls through to `verdict: grey`. **No percentile / dataset-wide statistics** — every threshold is row-local.

When any source column is missing/None, `verdict = "grey"` and `value_display = "—"`, `rationale = "signal missing"`. The HTML renders grey as a hollow circle (○̇) so the operator can spot signal gaps.

### 4.4 Field-level rules

- `image_url` is always populated as `https://images-na.ssl-images-amazon.com/images/P/{asin}.jpg`. This is an **empirical Amazon URL pattern** — not part of any documented API. It returns a real product image for the majority of UK ASINs but breaks on some (especially recent listings or private label). The HTML renderer uses `<img onerror="this.style.display='none'">` so a broken image hides cleanly instead of showing a broken-image icon next to a card the operator is supposed to act on. Acceptable miss rate: ≤20% of cards on a typical run; if higher in practice, the follow-up workstream that extends SP-API enrich (out of scope here per §2) will provide a higher-quality primary URL.
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
      <img src="https://images-na.ssl-images-amazon.com/images/P/B0B636ZKZQ.jpg"
           onerror="this.style.display='none'"
           alt="Casdon Morphy Richards Toaster Toy"
           loading="lazy">
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

Each variant is a `<table class="economics-grid">` with three two-column rows. Cells use `<td class="econ-label">` for the label and `<td class="econ-value">` for the value, with verdict-specific contents.

**`BUY` — full order plan:**

```html
<table class="economics-grid">
  <tr><td class="econ-label">Buy cost</td><td class="econ-value">£4.00</td>
      <td class="econ-label">Target buy</td><td class="econ-value">£9.50 (stretch £8.52)</td></tr>
  <tr><td class="econ-label">Order qty</td><td class="econ-value">13</td>
      <td class="econ-label">Capital</td><td class="econ-value">£52.00</td></tr>
  <tr><td class="econ-label">Payback</td><td class="econ-value">22 days</td>
      <td class="econ-label">30d profit</td><td class="econ-value">£150.30</td></tr>
</table>
```

**`SOURCE_ONLY` — no buy_cost, supplier outreach target:**

```html
<table class="economics-grid">
  <tr><td class="econ-label">Buy cost</td><td class="econ-value">— (no supplier yet)</td>
      <td class="econ-label">Target buy</td><td class="econ-value">≤ £4.85 (stretch £4.10)</td></tr>
  <tr><td class="econ-label">Projected 30d revenue</td><td class="econ-value">£710.00</td>
      <td class="econ-label">30d profit at target</td><td class="econ-value">£136.00</td></tr>
</table>
```
(Two rows, not three — no order block.)

**`NEGOTIATE` — current cost over the BUY ceiling:**

```html
<table class="economics-grid">
  <tr><td class="econ-label">Currently</td><td class="econ-value">£5.00</td>
      <td class="econ-label">Target ceiling</td><td class="econ-value">£4.38 (stretch £3.50)</td></tr>
  <tr><td class="econ-label">Gap to BUY</td><td class="econ-value gap-positive">£0.62 (12.4%)</td>
      <td class="econ-label">30d profit (current cost)</td><td class="econ-value">£42.30</td></tr>
</table>
```
The `.gap-positive` class on the gap cell renders red so the buyer eye-jumps to it.

**`WATCH` — re-evaluable, no sizing:**

```html
<table class="economics-grid">
  <tr><td class="econ-label">Buy cost</td><td class="econ-value">£4.00</td>
      <td class="econ-label">Target buy</td><td class="econ-value">£9.50 (stretch £8.52)</td></tr>
  <tr><td class="econ-label">Projected 30d revenue</td><td class="econ-value">£303.30</td>
      <td class="econ-label">30d profit</td><td class="econ-value">£150.30</td></tr>
</table>
```

**Per-verdict prose heading varies:**

| Verdict       | Prose heading       |
|---------------|---------------------|
| `BUY`         | Why we should buy   |
| `SOURCE_ONLY` | Why source this     |
| `NEGOTIATE`   | Why negotiate       |
| `WATCH`       | Why watch           |

### 5.4 Style rules

- Single embedded `<style>` block. No external CSS, no `<link>`, no JS (other than the `<img onerror>` inline attribute for image fallback).
- System font stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`.
- Verdict colours: BUY green (`#27AE60`), SOURCE_ONLY blue (`#2980B9`), NEGOTIATE amber (`#E67E22`), WATCH yellow (`#F1C40F`). Border-left 4px on cards uses the verdict colour.
- Traffic-light dot CSS: `<span class="dot dot-green"></span>` renders as a 12px circle via `border-radius: 50%; background: <colour>`. Grey state (signal missing) renders as a hollow circle via `border: 1.5px solid #B0B0B0; background: transparent`.
- Card image: CSS `width: 200px; height: auto; max-width: 100%; object-fit: contain` — no `width`/`height` HTML attributes. Lets the image scale on retina + narrow viewports without distortion.
- Card max-width 900px, centred.
- **Within-verdict sort order:** BUY rows sorted by `projected_30d_profit` desc (matches the XLSX sort introduced in PR #78); other verdicts sorted by `opportunity_score` desc. Stable sort so equal values keep their input ordering.
- **TOC behaviour:** the `<nav class="toc">` is rendered when the run has more than 3 actionable rows (BUY + SOURCE_ONLY + NEGOTIATE + WATCH). For runs with ≤3 rows (e.g. single-ASIN), the TOC is omitted — the cards themselves are the navigation.
- TOC stickiness deferred to a follow-up PR — v1 ships with a top-of-body TOC. Sticky-on-desktop is a 5-line CSS addition but fights with print rendering and adds `@media` complexity that's not worth the v1 footprint.
- `@media print`: hide `<nav class="toc">` (when present), `page-break-inside: avoid` on cards, drop background colours to white, keep verdict-badge colour for visual identity.

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
4. Cache responses keyed on `hash(schema_version + prompt_version + asin + verdict + sorted(metric values + economics))`. Cache lives at `.cache/buyer_report_prose/<hash>.txt`. Re-running on unchanged engine output costs zero LLM calls. Bumping `schema_version` (engine-side) or `prompt_version` (orchestration-side) invalidates the cache so a schema or prompt change forces re-generation.

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

The prompt template lives at `orchestration/runs/buyer_report_prose_prompt.md`. Pinned at v1; future tone changes ship as v2 with a config knob (and the `prompt_version: 2` field in the JSON forces a cache miss on rerun).

### 6.3.1 Worked examples (input → expected paragraph)

These examples are the contract — the v1 prompt + sample inputs must reproduce paragraphs of equivalent shape and length. Tests assert structural properties (sentence count, word count, mentions of verdict-implication + at least one metric) rather than exact wording.

**BUY input** (from §4.2 example): verdict `BUY`/HIGH, sales 250/mo (green), Amazon BB 10% (green), 4 FBA sellers (green), 13-unit order at £4 cost = £52 capital, 22-day payback, all-green metrics.

> Strong demand at 250 units/mo with healthy share rotation; only 4 FBA sellers, Amazon holds 10% of Buy Box well under our 30% cap, and price has been stable. ROI of 111% on a £52 first-order test pays back in 22 days. No risk flags — order it.

**SOURCE_ONLY input:** verdict `SOURCE_ONLY`/HIGH, sales 320/mo (green), no buy_cost, target ceiling £4.85 (stretch £4.10), projected revenue £710/mo at target.

> Demand looks strong — 320 units/mo with safe Buy Box rotation — but we don't have a supplier cost yet. Target supplier outreach at ≤£4.85/unit; £4.10 is the stretch ask. At target cost, this lands ~£136 of monthly profit with a clean risk profile.

**NEGOTIATE input:** verdict `NEGOTIATE`/MEDIUM, sales 180/mo (green), current buy_cost £5.00, target ceiling £4.38, 12.4% gap, profit_conservative thin at £1.50/unit.

> Current cost of £5.00 is 12.4% above the BUY ceiling — conservative profit thins to £1.50/unit at this price. Demand justifies the listing (180/mo, healthy seller count), but we need to negotiate the supplier down to £4.38 or below before this becomes BUY-grade. Worth a 5-minute supplier call.

**WATCH input:** verdict `WATCH`/LOW, sales 70/mo (amber), `INSUFFICIENT_HISTORY` flag, target ceiling £6.85, no immediate action.

> Below our 100/mo BUY threshold (sales running ~70/mo) and history is too thin for a confident call — `INSUFFICIENT_HISTORY` flag is firing. Target ceiling is £6.85 if economics improve. Re-check next week; if sales pick up or history matures, this could rotate to BUY.

---

## 7. Configuration

New optional block in `shared/config/decision_thresholds.yaml`:

```yaml
buy_plan_html:
  enabled: true                          # always produce JSON+HTML by default
```

Currently the only knob is the on/off switch. Traffic-light thresholds are **derived from existing config** (`OpportunityValidation` and `BuyPlan` blocks) per the §4.3 table — no duplicate threshold knobs in this block. If a future tuning pass needs per-buyer-report-only thresholds, add them here as a v2 schema bump.

Per-run override:
- `--no-html` CLI flag on `run.py` (skips both JSON and HTML)
- `html_enabled` runner-context value (default `"true"`)

`min_roi_buy`, `min_profit_absolute_buy`, `target_monthly_sales`, `max_amazon_bb_share_buy`, `max_price_volatility_buy`, `kill_min_sales`, `kill_price_volatility`, `kill_amazon_bb_share` are all reused from `OpportunityValidation` — the traffic-light logic uses the same thresholds the verdict layer used.

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
10. **Anthropic API rate-limit / quota error** during orchestration — log the error per affected row, leave that row's marker in place (engine template prose persists), continue with remaining rows. Orchestration exits successfully so the run doesn't fail on a transient API blip. Operator can re-run orchestration to pick up the missing rows from the cache or live API.
11. **Crash mid-orchestration after partial injection** — orchestration writes the cache file BEFORE mutating the HTML for that row, so on retry already-cached rows hit the cache and re-injection is idempotent (the same `<!-- prose:{asin} -->` regex finds and replaces correctly even if the marker was already replaced). HTML is atomically written via tmp+rename per row, so a crash mid-write leaves either the prior HTML or the fully-replaced HTML — never a corrupt mix.
12. **Single-ASIN runs with ≤3 rows** — TOC is omitted entirely (per §5.4); cards alone are the navigation. Verdict-section headings still render.
13. **Strategy with `null` supplier** (`keepa_finder`, `single_asin`) — JSON `supplier: null`; HTML title falls back to `Buyer Report — {strategy} — {date}`.

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

**Integration regression (parallel to buy_plan PRD §9):**
- Re-run the existing strategy fixtures (the abgee + connect-beauty supplier_pricelist tests, the keepa_finder synthetic-CSV test, the oa_csv test). Assert `buyer_report_*.html` and `buyer_report_*.json` land at the expected output path and have ≥ 1 BUY card on the runs that produce SHORTLIST rows. This catches strategy-wiring drift the unit + step tests can't.

**Manual verification (one-off, not in CI):**
- Real `python run.py --supplier abgee --market-data ... --no-preflight` run produces a `buyer_report_*.html` that:
  - Parses cleanly in Chrome / Safari / Firefox.
  - Renders the image rail (or hides broken images cleanly via the `onerror` rule).
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
7. All Python tests on `main` still pass after this branch's work (the test count at branch-cut is the baseline — no magic number pinned in the PRD). ~45 new tests added per §9, all passing.
8. MCP suite untouched.

---

## 11. Versioning + handoff to SPEC.md

After ship and sign-off:

1. Fold this PRD into `docs/SPEC.md` as section **§8e — Buyer report**, mirroring the §8c / §8d style.
2. Move this file to `docs/archive/PRD-buyer-report.md`.
3. Update `CLAUDE.md` Current State block.
4. **Signal availability:** the buyer report consumes only existing engine signals (verdict, score, confidence, predicted velocity, candidate score, validate_opportunity outputs, the eleven buy_plan columns, and the calculate-step economics). It introduces no new engine signals — `image_url` is derived deterministically from `asin` alone via the public URL pattern. SPEC §9 needs no row addition unless/until a follow-up workstream extends `enrich` to capture a first-party image URL.

---

## 12. Non-objectives, restated

This step is **not** the operator's full buyer cockpit. It is a single-purpose share-friendly artefact that turns the engine's existing structured output into a per-product narrative card view. Sortable tables, live Amazon scraping, email send-out, multi-language prose, A/B prose styles, and PDF rendering are **separately specced and shipped**. Anything that requires reading external services at render time (live Amazon, live SP-API) or interactive UI affordances (sort, filter, slice) is out of scope for `09_buy_plan_html` by design — keep the step pure-transformation + LLM prose, and let the data-acquisition / interactivity layers be their own modules with their own PRDs.
