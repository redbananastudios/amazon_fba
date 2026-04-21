# Amazon FBA Product Sourcing System

## Current State
**Last updated:** 2026-04-18
**Currently working on:** Testing supplier pipelines on connect-beauty niche, building repeatable product discovery and profit analysis
**Next steps:** Find profitable products with consistent sales (target 2-3 GBP profit per item), get SP-API credentials for Amazon FBA Fees MCP
**Blockers:** SP-API credentials awaited (code complete); need to find higher velocity products meeting ROI targets

## Session Protocol
- At the end of each session, update the "Current State" section above
- If you learned something about how this project works that would help next time, add it to this file
- Commit CLAUDE.md changes as part of your work

This workspace contains two independent pipelines for Amazon FBA product sourcing. Both are designed to be portable — all paths are relative to their project root. Clone or install them anywhere.

---

## Projects

### 1. Keepa Niche Finder (`keepa_niche_finder/`)

A Node.js pipeline that uses Keepa and SellerAmp to find profitable products within a given niche.

**6-Phase Pipeline:**

| Phase | Purpose | Tool |
|-------|---------|------|
| 1 — Keepa Finder | Export ~1000 products per niche from Keepa | Browser / Keepa API |
| 2 — SellerAmp Enrichment | Confirm gating, hazmat, FBA fees, seller counts | Browser (sas.selleramp.com) |
| 3 — Scoring & Shortlist | Composite scoring, cut to 50-100 products | Logic only |
| 4 — IP Risk Analysis | Brand control, seller structure, compliance flags | Logic only |
| 5 — Build Final Output | Styled Excel workbook + Google Sheets upload | Node.js (ExcelJS) |
| 6 — Decision Engine | BUY / NEGOTIATE / WATCH / KILL verdicts | Logic only |

**Stack:** Node.js, ExcelJS, Google Sheets API, Playwright (browser automation)

**Run:** `cd keepa_niche_finder && claude`

See `keepa_niche_finder/CLAUDE.md` for credentials, niche configs, and full pipeline docs.

---

### 2. Supplier Pricelist Finder (`supplier_pricelist_finder/`)

A Python pipeline that processes supplier price lists and identifies profitable products to sell on Amazon via FBA or FBM.

**Pipeline Stages:** Ingest → Normalise → Case Detection → Match (EAN→ASIN) → Market Data (Keepa) → Fees → Conservative Price → Profit → Decision

**Decisions:** SHORTLIST (profitable, act on it) / REVIEW (flagged, needs human eyes) / REJECT (below thresholds)

**Stack:** Python, pandas, pytest

**Run:** `cd supplier_pricelist_finder/pricelists/abgee && python -m sourcing_engine.main --input ./raw/ --output ./results/`

See `supplier_pricelist_finder/CLAUDE.md` for domain rules, thresholds, and testing.

---

## Portability

All paths in both projects are relative to their own root directory. No hardcoded drive letters or absolute paths. Users can clone this repo anywhere and it will work.

- **JS scripts** resolve paths via `path.resolve(__dirname, ...)` relative to the script location
- **Python pipeline** uses `--input` and `--output` CLI arguments
- **SKILL.md files** reference paths as `./data/{niche}/...`

---

## Credentials

Credentials are stored in `keepa_niche_finder/CLAUDE.md` (Keepa, SellerAmp logins). These should **not** be committed to a public repo. Add them to `.gitignore` or use environment variables before sharing.

---

## Key Business Rules

These rules apply across both pipelines. They protect real money.

- **Sell price = Buy Box price.** Never use `lowest_fba_price`.
- **Price range:** GBP 20–70
- **Min FBA sellers:** 2 (single seller = private label risk)
- **Max FBA sellers:** 20
- **Velocity floor:** 100/month (50 for sports-goods)
- **ROI floor:** 20% target
- **Conservative price:** 15th percentile of 90-day FBA history (never use floored value in profit calc)
- **FBA and FBM are separate fee paths.** Never mix them.
- **Never crash on a single bad row.** Log, flag as REVIEW, continue.

---

## Shared Niche Configs

Configured niches (in `keepa_niche_finder/config/niche-configs/`):

- afro-hair
- kids-toys
- educational-toys
- stationery
- sports-goods
- pet-care

## Available Services
This project has access to the following MCP servers:
- Amazon FBA Fees (custom SP-API fee calculator — awaiting SP-API credentials)
- Chrome DevTools (browser inspection)
- Playwright (browser automation)
- NotebookLM (research)

Global services also available: Notion, Context7, Google Workspace, Zapier

Credentials are centralized at: F:\My Drive\workspace\credentials.env
Never hardcode keys — use ${VAR_NAME} references in .mcp.json
