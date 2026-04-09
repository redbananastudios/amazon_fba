---
name: skill-4-ip-risk
description: >
  FBA sourcing Phase 4 listing and IP risk analysis. Use after Phase 3
  has produced a shortlist when the pipeline needs an advisory-only
  pass on brand-seller overlap, fortress listings, synthetic brands,
  A+ presence, brand-store likelihood, category IP risk, and an
  appended IP risk CSV before Phase 5 builds the final workbook.
  Triggers on: "run phase 4 ip risk", "ip risk analysis",
  "listing risk analysis", "brand seller match", "fortress listing",
  "append ip risk columns".
---

# Phase 4 -- Listing & IP Risk Analysis

Run a pure-logic advisory pass on the Phase 3 shortlist. Do not use a browser.
Do not drop or filter products. Append 9 IP-risk columns and save a new CSV,
stats file, and handoff file.

---

## Before You Start

1. Confirm the shortlist exists:
   `./data/{niche}\working/{niche}_phase3_shortlist.csv`
2. Copy the template script:
   `skills/skill-4-ip-risk\phase4_ip_risk.js`
   to:
   `data/{niche}\working/phase4_ip_risk.js`
3. Replace the template constants at the top:
   - `const NICHE = '__NICHE__';`
   - `const BASE = '__BASE__';`
4. Run:
   `node data/{niche}\working/phase4_ip_risk.js`

---

## What The Script Must Do

Read:
  `data/{niche}\working/{niche}_phase3_shortlist.csv`

Write:
  `data/{niche}\working/{niche}_phase4_ip_risk.csv`
  `data/{niche}\working/{niche}_phase4_stats.txt`
  `data/{niche}\working/{niche}_phase4_handoff.md`

Append these columns to the input schema without changing any existing columns:
  `Brand Seller Match`
  `Fortress Listing`
  `Brand Type`
  `A+ Content Present`
  `Brand Store Present`
  `Category Risk Level`
  `IP Risk Score`
  `IP Risk Band`
  `IP Reason`

---

## Scoring Rules

### Brand Seller Match

Use `Brand` and `BB Seller`.

Normalise both:
  - lowercase
  - strip `ltd`, `limited`, `inc`, `uk`
  - strip punctuation

If brand contains seller or seller contains brand:
  `YES`

Else if Levenshtein similarity > `0.7`:
  `PARTIAL`

Else:
  `NO`

Risk contribution:
  `YES = +3`
  `PARTIAL = +1`

### Fortress Listing

Use `FBA Seller Count` and `FBA Seller 90d Avg`.

If current sellers `<= 1` and 90d average `<= 1.5`:
  `YES`

Else:
  `NO`

Risk contribution:
  `YES = +3`

### Brand Type

Use `Brand`, plus `Review Count` and `Star Rating` as proxy.

`ESTABLISHED`:
  brand appears in the inline known-brands list
  OR `Review Count > 500` and `Star Rating > 3.5`

`SYNTHETIC`:
  brand looks random by regex or shape:
  - `/^[A-Z]{2,}$/`
  - `/\d{2,}/`
  - length `<= 3`

Else:
  `GENERIC`

Risk contribution:
  `ESTABLISHED = +1`

### A+ Content Present

Use existing `Has A+ Content`.

`Y` or `yes` -> `YES`
Else -> `NO`

Risk contribution:
  `YES = +1`

### Brand Store Present

Heuristic only.

If `Brand Seller Match = YES` and `A+ Content Present = YES`:
  `LIKELY`

Else:
  `UNLIKELY`

Risk contribution:
  `LIKELY = +1`

### Category Risk Level

Map from niche:
  `educational-toys -> HIGH`
  `kids-toys -> HIGH`
  `afro-hair -> MEDIUM`
  `pet-care -> MEDIUM`
  `sports-goods -> MEDIUM`
  `stationery -> LOW`
  unknown -> `MEDIUM`

Risk contribution:
  `HIGH = +1`
  `MEDIUM = +0.5`
  `LOW = +0`

### IP Risk Score / Band

Sum contributions, round the final score, then clamp `0-10`.

Bands:
  `>= 7 -> High`
  `>= 4 -> Medium`
  `< 4 -> Low`

### IP Reason

Pipe-separated explanation using only factors that added risk.

Examples:
  `Brand=Seller match (YES) | Fortress listing | A+ content | Category HIGH risk`

---

## Important Constraints

- Do not modify or replace existing columns.
- Do not filter out products.
- Do not use browser automation or external APIs.
- Keep it standalone and runnable with Node only.
- Use inline Levenshtein logic. No npm dependencies.

---

## Phase 5 Integration

After Phase 4 exists, Phase 5 build should prefer:
  `data/{niche}\working/{niche}_phase4_ip_risk.csv`

and fall back to:
  `data/{niche}\working/{niche}_phase3_shortlist.csv`

Phase 5 final output uses a `64`-column schema by inserting the 9
IP columns after `Private Label Risk` and before `Gated`.

The shared XLSX builder must:
  - handle `64` columns
  - colour `IP Risk Band`
    - `High -> red`
    - `Medium -> orange`
    - `Low -> green`

---

## Quality Check

  [ ] `phase4_ip_risk.js` uses template constants `__NICHE__` and `__BASE__`
  [ ] output CSV contains all original shortlist columns plus 9 IP columns
  [ ] stats file includes risk-band distribution, match counts, fortress count, brand-type counts, top 10 high risk, and false positives avoided
  [ ] handoff file names the three Phase 4 outputs
  [ ] Phase 5 builders prefer `phase4_ip_risk.csv` when present
  [ ] shared XLSX builder handles 64 columns and colours `IP Risk Band`
