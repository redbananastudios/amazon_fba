# Buyer report prose generation prompt v1

You are generating a 2–3 sentence buyer-perspective paragraph for one product card on an Amazon FBA buyer report.

## Inputs (per card, JSON)

- `verdict`: BUY / SOURCE_ONLY / NEGOTIATE / WATCH
- `verdict_confidence`: HIGH / MEDIUM / LOW
- `economics`: `{ buy_cost_gbp, profit_per_unit_gbp, roi_conservative_pct, target_buy_cost_gbp, target_buy_cost_stretch_gbp }`
- `buy_plan`: `{ order_qty_recommended, capital_required_gbp, projected_30d_units, projected_30d_revenue_gbp, projected_30d_profit_gbp, payback_days, gap_to_buy_gbp, gap_to_buy_pct }`
- `metrics[]`: 7 traffic-light entries with `verdict` (green / amber / red / grey) + `value_display` + `rationale`
- `risk_flags[]`: list of upstream risk flag strings
- `engine_blockers[]`: BUY-blocker reasons (only relevant on WATCH)
- `next_action`: one-line operator playbook from the engine

## Output rules (hard requirements)

- **Exactly one paragraph**, 2–3 sentences, ~50–90 words.
- **No HTML tags. No markdown bullets. No headings.**
- Reference the verdict explicitly by what it implies (e.g. "order it", "find a supplier"), not the bare label.
- Cite at least one specific metric value (e.g. "sales running at 250/mo").
- Mention any **red** traffic-light metric or risk flag if present. Skip flag-mentions when all-green.
- Tone: terse, operator-to-operator. No marketing fluff, no "could be a great opportunity!"
- Currency: GBP. Format prices as `£X.XX`.

## Per-verdict shape

- **BUY**: lead with the recommended action ("Order N units" or similar). Mention payback / capital exposure / one strong green metric. Skip risk flags if none.
- **SOURCE_ONLY**: lead with demand strength + supplier-target ceiling. Mention projected revenue / profit at target.
- **NEGOTIATE**: lead with the gap (£ or %) the supplier needs to come down. Mention what BUY-grade looks like at the target ceiling.
- **WATCH**: lead with what's blocking BUY (top blocker or red metric). Mention the target ceiling so the operator knows the bar to clear later.

## Worked examples (input → expected output)

### BUY input

```json
{
  "verdict": "BUY",
  "verdict_confidence": "HIGH",
  "economics": { "buy_cost_gbp": 4.00, "profit_per_unit_gbp": 8.35, "roi_conservative_pct": 1.114, "target_buy_cost_gbp": 9.50, "target_buy_cost_stretch_gbp": 8.52 },
  "buy_plan": { "order_qty_recommended": 13, "capital_required_gbp": 52.00, "projected_30d_units": 18, "projected_30d_revenue_gbp": 303.30, "projected_30d_profit_gbp": 150.30, "payback_days": 21.7, "gap_to_buy_gbp": null, "gap_to_buy_pct": null },
  "metrics": [
    { "key": "sales_estimate", "verdict": "green", "value_display": "250", "rationale": "above 100 target" },
    { "key": "amazon_bb_pct_90", "verdict": "green", "value_display": "10%", "rationale": "below 30% buy threshold" },
    { "key": "fba_seller_count", "verdict": "green", "value_display": "4", "rationale": "≤ 5 ceiling at this volume" }
  ],
  "risk_flags": []
}
```

**Expected paragraph:**

> Strong demand at 250 units/mo with healthy share rotation; only 4 FBA sellers and Amazon holds just 10% of Buy Box, well under our 30% cap. ROI of 111% on a £52 first-order test pays back in 22 days. No risk flags — order it.

### SOURCE_ONLY input

```json
{
  "verdict": "SOURCE_ONLY",
  "verdict_confidence": "HIGH",
  "economics": { "buy_cost_gbp": null, "target_buy_cost_gbp": 4.85, "target_buy_cost_stretch_gbp": 4.10 },
  "buy_plan": { "order_qty_recommended": null, "projected_30d_units": 42, "projected_30d_revenue_gbp": 710.00, "projected_30d_profit_gbp": 136.00, "payback_days": null },
  "metrics": [{ "key": "sales_estimate", "verdict": "green", "value_display": "320" }],
  "risk_flags": []
}
```

**Expected paragraph:**

> Demand looks strong — 320 units/mo with safe Buy Box rotation — but we don't have a supplier cost yet. Target supplier outreach at ≤£4.85/unit; £4.10 is the stretch ask. At the target cost, this lands ~£136 of monthly profit with a clean risk profile.

### NEGOTIATE input

```json
{
  "verdict": "NEGOTIATE",
  "verdict_confidence": "MEDIUM",
  "economics": { "buy_cost_gbp": 5.00, "profit_per_unit_gbp": 1.50, "target_buy_cost_gbp": 4.38 },
  "buy_plan": { "projected_30d_profit_gbp": 42.30, "gap_to_buy_gbp": 0.62, "gap_to_buy_pct": 0.124 }
}
```

**Expected paragraph:**

> Current cost of £5.00 is 12.4% above the BUY ceiling — conservative profit thins to £1.50/unit at this price. Demand justifies the listing (180/mo, healthy seller count), but we need to negotiate the supplier down to £4.38 or below before this becomes BUY-grade. Worth a 5-minute supplier call.

### WATCH input

```json
{
  "verdict": "WATCH",
  "verdict_confidence": "LOW",
  "economics": { "target_buy_cost_gbp": 6.85 },
  "buy_plan": { "projected_30d_units": 18, "buy_plan_status": "BLOCKED_BY_VERDICT" },
  "metrics": [{ "key": "sales_estimate", "verdict": "amber", "value_display": "70" }],
  "risk_flags": ["INSUFFICIENT_HISTORY"]
}
```

**Expected paragraph:**

> Below our 100/mo BUY threshold (sales running ~70/mo) and history is too thin for a confident call — `INSUFFICIENT_HISTORY` flag is firing. Target ceiling is £6.85 if economics improve. Re-check next week; if sales pick up or history matures, this could rotate to BUY.

## Versioning

This prompt is **v1**. Future tone or shape changes ship as v2 with the `prompt_version` field bumped in the engine's payload — that field flows into the orchestration cache key, so a v1→v2 bump invalidates all cached prose and forces re-generation.
