# Amazon FBA Fees MCP — Sourcing Tools Expansion

**Status:** Draft (awaiting sign-off)
**Owner:** Peter Farrell
**Author:** Claude
**Branch:** `feat/mcp-sourcing-tools`
**Targets:** `services/amazon-fba-fees-mcp/` + light pipeline integration

---

## Goal

Expand the MCP server beyond fees + profitability so the Python sourcing pipeline can answer the full sourcing-decision question with a single round-trip per ASIN:

> *Can I list this? Can I FBA it? What does it cost? Who are the competitors? What's it worth right now?*

Today the MCP only answers the third question (fees) and the fifth in part (profitability math, but no live BB).

## Non-goals

- Auto-reject on ungating. **Restriction status is informational only.** A gated ASIN with strong economics may be worth applying for — that's the user's call, not the engine's.
- Real-time pricing data ingestion at scale (Keepa stays the source of truth for historical/aggregate; the new live-pricing tool is for decision-time validation).
- New SP-API auth or env vars. Reuses existing `SP_API_CLIENT_ID` / `SP_API_CLIENT_SECRET` / `SP_API_REFRESH_TOKEN`.
- Replacing SellerAmp's role in the legacy Keepa pipeline. That pipeline keeps using SellerAmp until step 4 dismantles it.

---

## Architecture summary

```
sourcing_engine (Python)            MCP server (Node/TS)            SP-API
─────────────────                   ─────────────────────           ──────
                                                                    
preflight_asin(asins[]) ────────►  preflight composite ──┬──► Listings Restrictions
                                                          ├──► FBA Eligibility
                                                          ├──► Fees (batch ≤ 20)
                                                          └──► Catalog Items
                                                          
                                   ◄─── unified record per ASIN ◄────
                                          (persistent cache: ~/.cache/fba-mcp/)
```

Pipeline calls `preflight_asin` once per supplier batch. MCP fans out to 4 SP-API endpoints, normalises responses, caches by ASIN, returns a structured matrix.

---

## Tools

### Tier 1 — Decision quality

#### 1. `check_listing_restrictions`

```
Input:
  asin: string
  marketplace_id?: string  (default: A1F83G8C2ARO7P / UK)
  condition_type?: "new_new" | "new_open_box" | ...  (default: new_new)
  seller_id: string  (required — restrictions are per-seller)

Output:
  asin: string
  status: "UNRESTRICTED" | "RESTRICTED" | "BRAND_GATED" | "CATEGORY_GATED"
  reasons: Array<{ message: string, reasonCode: string, link?: string }>
  approval_required: boolean
  raw: <SP-API response>
```

**SP-API:** `GET /listings/2021-08-01/restrictions`
**Rate limit:** 5 req/sec, burst 10 (per seller)
**Notes:** `RESTRICTED` is the umbrella. `BRAND_GATED` and `CATEGORY_GATED` are derived from `reasonCode` patterns for downstream readability.

#### 2. `check_fba_eligibility`

```
Input:
  asin: string
  marketplace_id?: string
  program?: "INBOUND" | "COMMINGLED" | ...  (default: INBOUND)

Output:
  asin: string
  eligible: boolean
  ineligibility_reasons: Array<{ code: string, description: string }>
  raw: <SP-API response>
```

**SP-API:** `GET /fba/inbound/v1/eligibility/itemPreview`
**Rate limit:** 1 req/sec, burst 1
**Notes:** Distinct from listing restrictions. An ASIN can be listable but FBA-ineligible (hazmat, oversized, prohibited).

#### 3. `estimate_fees_batch`

```
Input:
  items: Array<{ asin: string, selling_price: number, marketplace_id?: string }>  (max 20)

Output:
  results: Array<FeeEstimate | FeeError>  (same length as input)
```

**SP-API:** `POST /products/fees/v0/feesEstimate`
**Rate limit:** 0.5 req/sec, burst 1
**Notes:** Replaces caller-side looping over single-ASIN fees. ~20× speedup for long supplier lists.

### Tier 2 — First-party data

#### 4. `get_catalog_item`

```
Input:
  asin: string
  marketplace_id?: string
  included_data?: Array<"attributes" | "dimensions" | "images" | "identifiers" | "relationships" | "salesRanks" | "summaries" | "vendorDetails">

Output:
  asin: string
  title: string
  brand?: string
  manufacturer?: string
  dimensions?: { length, width, height, weight, unit }
  hazmat?: boolean
  classifications?: Array<{ classificationId, displayName }>
  images?: Array<{ link, height, width }>
  raw: <SP-API response>
```

**SP-API:** `GET /catalog/2022-04-01/items/{asin}`
**Rate limit:** 2 req/sec, burst 2
**Notes:** Brand → IP risk gate input. Hazmat flag → cross-validates FBA eligibility reasons.

#### 5. `get_live_pricing`

```
Input:
  asins: Array<string>  (max 20)
  marketplace_id?: string
  item_condition?: "New" | "Used"  (default: New)

Output:
  results: Array<{
    asin: string
    buy_box_price?: number    (featured-offer landed price, GBP)
    buy_box_seller?: "AMZN" | "FBA" | "FBM" | string
    listing_price?: number
    shipping?: number
    offer_count_new?: number
    offer_count_fba?: number
  }>
```

**SP-API:** `GET /products/pricing/v0/itemOffers/batch` (multi-ASIN)
**Rate limit:** 0.5 req/sec, burst 1
**Notes:** Real-time Buy Box vs Keepa's 90-day history. Used for decision-time validation, not bulk ingest.

### Tier 3 — Pipeline ergonomics

#### 6. `preflight_asin` (composite)

```
Input:
  items: Array<{ asin: string, selling_price: number, cost_price: number }>  (max 20)
  marketplace_id?: string
  seller_id: string
  include?: Array<"restrictions" | "fba" | "fees" | "catalog" | "pricing" | "profitability">  (default: all)
  refresh_cache?: boolean  (default: false)

Output:
  results: Array<{
    asin: string
    restrictions?: <output of check_listing_restrictions>
    fba?: <output of check_fba_eligibility>
    fees?: <output of estimate_fees>
    catalog?: <output of get_catalog_item>
    pricing?: <output of get_live_pricing>
    profitability?: <output of calculate_profitability>
    cached: { restrictions: boolean, fba: boolean, ... }
    errors: Array<{ source: string, message: string }>
  }>
```

**Behaviour:**
- Fans out to all selected sub-tools in parallel (with rate-limit-aware queueing).
- Per-ASIN errors don't fail the batch. Returns partial results with `errors[]`.
- Honours cache by default; `refresh_cache: true` forces revalidation.
- TTLs (see cache section): restrictions 7d, FBA eligibility 7d, catalog 30d, fees 24h, pricing 5min.

#### 7. Persistent disk cache

**Location:** `<repo>/.cache/fba-mcp/<resource>/<asin>.json` (gitignored)

**Layout:**
```
.cache/fba-mcp/
├── restrictions/<seller_id>/<marketplace_id>/<asin>.json
├── fba_eligibility/<marketplace_id>/<asin>.json
├── catalog/<marketplace_id>/<asin>.json
├── fees/<marketplace_id>/<asin>__<price_bucket>.json
└── pricing/<marketplace_id>/<asin>.json
```

**Entry shape:**
```json
{
  "fetched_at": "2026-04-28T12:34:56Z",
  "ttl_seconds": 604800,
  "data": { ...tool output... }
}
```

**Behaviour:**
- On read: if `now < fetched_at + ttl_seconds` and `refresh_cache` is false, return cached.
- On miss / expired / refresh: call SP-API, write fresh entry.
- On SP-API error: serve stale cache if available with `stale: true` flag, else propagate error.

**Why disk over the existing in-memory `cache.ts`:** in-memory dies with the process. The pipeline runs as separate Python invocations; disk cache survives across runs and across `claude` sessions.

---

## Pipeline integration (informational, no auto-reject)

In `shared/lib/python/sourcing_engine/pipeline/`:

1. After EAN→ASIN match, before profit/decision: **add a `preflight` step** that calls the MCP's `preflight_asin` tool in batches of 20.
2. Annotate each row with new columns:
   - `restriction_status` — UNRESTRICTED / RESTRICTED / BRAND_GATED / CATEGORY_GATED
   - `restriction_reasons` — comma-joined reason codes
   - `fba_eligible` — true / false
   - `fba_ineligibility` — comma-joined codes
   - `live_buy_box` — current BB if pulled (else null)
   - `catalog_brand` — first-party brand string (alongside the Keepa-derived one)
3. **Decision gate (`decision.py`) is NOT changed.** Existing SHORTLIST/REVIEW/REJECT logic stays exactly as it is.
4. CSV/Excel output gets the new columns appended at the end. Existing column positions unchanged.
5. Markdown report shows a "🚫 Restriction notes" section listing any RESTRICTED rows in SHORTLIST, so the user can see — at glance — which profitable items need ungating action.

**Result:** SHORTLIST may contain restricted items. They surface visibly so the user can decide whether to apply for ungating. No engine-side rejection.

---

## SP-API auth & rate limits

All endpoints reuse the existing `SP_API_CLIENT_ID` / `SP_API_CLIENT_SECRET` / `SP_API_REFRESH_TOKEN` LWA exchange in `services/sp-api.ts`.

Rate-limit handling: per-endpoint token-bucket using `Bottleneck` (already in deps via `@modelcontextprotocol/sdk` indirectly, or add as direct dep). One bucket per endpoint × marketplace.

Backoff strategy on 429: respect `x-amzn-RateLimit-Limit` headers, exponential retry with jitter (max 3 retries, then surface error to caller).

---

## Test strategy

For each new tool:
- **Unit:** mock SP-API client, assert request shape and response normalisation.
- **Cache:** assert TTL respected, `refresh_cache` bypasses, stale-on-error behaviour.
- **Rate-limit:** assert queueing under burst, 429 retry path.
- **Integration:** one happy-path end-to-end test per tool with a recorded SP-API response (vitest `__fixtures__`).

For pipeline integration:
- New unit tests in `shared/lib/python/sourcing_engine/tests/` mocking the MCP call.
- Regression: run the connect-beauty pipeline before/after, confirm SHORTLIST/REVIEW/REJECT counts unchanged (since decision gate untouched). New columns populated.

Total expected test additions: **~30-40 vitest tests** + **~10 pytest tests**.

---

## Commit shape

1. `feat(mcp): add SP-API rate limiter + persistent disk cache scaffold`
2. `feat(mcp): add check_listing_restrictions tool`
3. `feat(mcp): add check_fba_eligibility tool`
4. `feat(mcp): add estimate_fees_batch tool`
5. `feat(mcp): add get_catalog_item tool`
6. `feat(mcp): add get_live_pricing tool`
7. `feat(mcp): add preflight_asin composite tool`
8. `feat(pipeline): annotate rows with MCP preflight data (informational only)`
9. `docs: update README + AGENTS.md for new MCP tools`

One commit per feature, per the existing repo convention.

---

## Open questions for sign-off

1. **`seller_id` source.** Listing restrictions need a seller ID. Options:
   - (a) Pass via env var `SP_API_SELLER_ID` — simplest
   - (b) Derive from a `getMyAccountInfo` call at MCP startup — auto, but adds an extra call
   - **Recommended:** (a)

2. **Cache directory location.** `.cache/fba-mcp/` at the repo root, or `~/.cache/fba-mcp/` (user-scoped, shared across repos)?
   - **Recommended:** repo root — easier to clear, easier to inspect, gitignored.

3. **Tier 3 cache TTLs OK?**
   - restrictions 7d, FBA 7d, catalog 30d, fees 24h, pricing 5min
   - Or override per-deployment via env?

4. **"Brand" cross-check.** When catalog brand differs from Keepa brand, what wins?
   - Recommended: prefer SP-API catalog brand (first-party); log Keepa brand as `keepa_brand` for diff-tracking.

5. **MCP-as-stdin-MCP vs HTTP service.** Currently it's stdin-MCP (spawned by Claude Code). The Python pipeline is a separate process. To call the MCP from Python, options:
   - (a) Spawn the MCP as a subprocess from Python and speak MCP protocol over stdin (heavyweight)
   - (b) Add a CLI mode to the MCP: `node dist/cli.js preflight --asins ABC123,DEF456` — Python shells out
   - (c) Extract the SP-API logic into a shared TypeScript library and add a small Python-facing HTTP endpoint
   - **Recommended:** (b). Minimal new surface, reuses all the same code, easy to test.

6. **Test against real SP-API, or only mocks?** Real-API integration tests need credentials (still awaited per CLAUDE.md). Plan: mocks only for now; add `npm run test:integration` later when creds arrive.

---

## Out of scope (future)

- Inventory / orders / sales reports (your-own-data SP-API)
- Multi-marketplace support beyond UK (foundation supports it; not exercised)
- AI-based brand-risk classifier
- Webhook-driven cache invalidation

---

**Sign off Q1-Q6 and I'll start Tier 1.**
