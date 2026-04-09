---
name: skill-6-decision-engine
description: Build and run Phase 6 Decision Engine after the Phase 5 final workbook exists. Use when the user asks for final sourcing decisions, BUY/NEGOTIATE/WATCH/KILL actions, decision scoring, max buy price and target buy price, or a separate shortlist output.
---

# Skill 6 -- Decision Engine (Phase 6)

This skill adds the final operator decision layer on top of the existing sourcing pipeline.

It does not replace any earlier phase. It consumes the latest final results produced by Phase 5 and converts them into practical next actions:

- `BUY`
- `NEGOTIATE`
- `WATCH`
- `KILL`

It also generates a separate shortlist workbook containing all `BUY` and `NEGOTIATE` products.

## When to use this skill

Use this skill when the user asks to:

- run Phase 6
- build a decision engine
- assign BUY / NEGOTIATE / WATCH / KILL
- create a sourcing shortlist
- calculate max buy price or target buy price
- turn scored products into operator decisions

## Inputs

Preferred input:

- `data/{niche}/{niche}_final_results.csv`

Fallback input:

- `data/{niche}/working/{niche}_final_results.csv`

The Phase 6 script expects the current final Phase 5 output schema and appends the decision-layer fields without replacing any existing columns.

## Outputs

The script writes:

- `data/{niche}/working/{niche}_phase6_decisions.csv`
- `data/{niche}/working/{niche}_phase6_stats.txt`
- `data/{niche}/working/{niche}_phase6_handoff.md`
- `data/{niche}/{niche}_phase6_shortlist.xlsx`

The shortlist workbook contains:

- `Shortlist` sheet
- `Summary` sheet

`Shortlist` must contain all `BUY` and `NEGOTIATE` products.

## Fields appended by Phase 6

Append these 11 fields to every product row:

- `Decision`
- `Decision Score`
- `Decision Reason`
- `Joinability Status`
- `Buy Readiness`
- `Max Buy Price`
- `Target Buy Price`
- `Cost Gap`
- `Margin Status`
- `Action Note`
- `Shortlist Flag`

## Decision model

Phase 6 combines four layers:

1. Commercial attractiveness
2. Supplier / cost feasibility
3. Private label risk
4. IP / listing safety risk

In plain language:

- can it make money?
- can we source it?
- can we safely sell it?
- what should we do next?

## Decision rules

### BUY

Use `BUY` when:

- the product is commercially strong
- the lane is `BALANCED` or a strong `CASH FLOW`
- IP risk is low
- private label risk is acceptable
- price stability is acceptable
- competition is acceptable
- supplier cost is confirmed and within target or max buy price
- there is no major joinability issue

### NEGOTIATE

Use `NEGOTIATE` when:

- the product is commercially attractive
- but supplier cost is too high right now
- or supplier cost is missing but the product is otherwise promising
- or the cost gap is only slightly negative and could be improved through negotiation

### WATCH

Use `WATCH` when:

- the product is interesting
- but there is caution around IP, PL, price trend, competition, or supplier clarity
- it is not ready to buy now

### KILL

Use `KILL` when:

- IP risk is high
- joinability is unsafe
- margin fails badly
- supplier economics are clearly impossible
- listing control risk is too high
- the commercial case is weak

## Scoring model

Decision Score is a practical `0-100` actionability score built from:

- `CommercialScore * 0.35`
- `FeasibilityScore * 0.25`
- `SafetyScore * 0.25`
- `MarginSafetyScore * 0.15`

Banding:

- `80+` = `BUY`
- `60-79` = `NEGOTIATE`
- `40-59` = `WATCH`
- `<40` = `KILL`

Hard overrides apply before the band mapping.

## Important behaviour

- Do not replace the earlier commercial scores.
- Do not invent supplier prices.
- If supplier cost is missing, prefer `NEGOTIATE` or `WATCH` over `BUY` unless the row is explicitly buy-ready.
- Reuse existing fields such as `Max Cost 20% ROI`, `Trade Price`, `Real ROI %`, `IP Risk Band`, and `Private Label Risk`.
- Keep the logic commercially practical rather than overengineered.

## How to run

1. Preferred repo flow:

   use the existing niche runner:

   `data/{niche}\working/phase6_decision.js`

   This launcher already injects the niche-specific `NICHE` and `BASE`
   values into the shared template.

2. If you need to create a new niche runner manually, copy the template:

   `skills/skill-6-decision-engine\phase6_decision.js`

   to:

   `data/{niche}\working/phase6_decision.js`

3. Replace the placeholders at the top:

   - `const NICHE = '__NICHE__';`
   - `const BASE = '__BASE__';`

4. Run:

   `node data/{niche}\working/phase6_decision.js`

## What good output looks like

- every row gets a clear final decision
- `BUY` rows are safe, commercially strong, and buy-ready
- `NEGOTIATE` rows are attractive but blocked by cost
- `WATCH` rows have meaningful caution
- `KILL` rows clearly fail on safety, margin, or feasibility
- the shortlist workbook contains all `BUY` and `NEGOTIATE` rows
- `Decision Reason` and `Action Note` are short, commercial, and operator-friendly

## Validation checklist

After running Phase 6, confirm:

- decision counts exist for `BUY`, `NEGOTIATE`, `WATCH`, `KILL`
- top products by `Decision Score` look commercially sensible
- the shortlist workbook was created successfully
- sample `BUY`, `NEGOTIATE`, and `WATCH` or `KILL` rows have believable reasons
