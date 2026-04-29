# amazon-fba-fees-mcp

MCP server exposing Amazon SP-API endpoints for sourcing-decision support: fee
estimation, profitability calculation, listing-restrictions / FBA-eligibility
checks, catalog lookups, live Buy Box pricing, and a composite preflight tool
that fans out to all of the above for batches of up to 20 ASINs.

Also ships a CLI mode so the Python sourcing pipeline can shell out instead of
speaking MCP protocol over stdin.

---

## Tools

### Original (already in use)

- **`estimate_fees`** — FBA fee breakdown for one ASIN at one selling price
- **`calculate_profitability`** — fees + VAT + ROI, given cost and optional shipping
- **`save_to_sheet`** — append a fee/profitability result to a configured Google Sheet

### Sourcing-decision tools (this expansion)

| Tool | What it does | SP-API |
|---|---|---|
| **`check_listing_restrictions`** | Per-ASIN per-seller gating status: `UNRESTRICTED` / `BRAND_GATED` / `CATEGORY_GATED` / `RESTRICTED` plus reason codes | `getListingsRestrictions` |
| **`check_fba_eligibility`** | Whether an ASIN is eligible for FBA inbound, with human-readable ineligibility reasons (hazmat, oversized, missing dims, etc.) | `getItemEligibilityPreview` |
| **`estimate_fees_batch`** | Batch fee estimate for up to 20 ASINs in one round-trip (~20× faster than looping) | `getMyFeesEstimates` |
| **`get_catalog_item`** | First-party Amazon catalog: title, brand, manufacturer, dimensions, hazmat hint, classifications, images | `getCatalogItem` (2022-04-01) |
| **`get_live_pricing`** | Real-time Buy Box price + offer summary for up to 20 ASINs (FBA/FBM seller class, offer counts) | `getItemOffersBatch` |
| **`preflight_asin`** | **Composite** — fans out to all 5 sub-tools above plus locally-derived profitability for up to 20 items in parallel. Per-source errors isolated; per-source `cached` flag reports cache state | (all of the above) |

> **Important:** restriction status and FBA eligibility are **informational
> only**. They do NOT auto-reject candidates. The Python pipeline's
> `decision.py` logic is unchanged — restricted items can still SHORTLIST
> if their economics warrant it. The markdown report adds a "🚫 Restriction
> notes" section so the user can see, at a glance, which profitable items
> need ungating action.

---

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `SP_API_CLIENT_ID` | yes | SP-API LWA client id |
| `SP_API_CLIENT_SECRET` | yes | SP-API LWA client secret |
| `SP_API_REFRESH_TOKEN` | yes | SP-API refresh token for the seller |
| `SP_API_SELLER_ID` | for restrictions | Seller ID used by `check_listing_restrictions` and the `preflight_asin` composite. Restrictions are scoped per seller. |
| `GOOGLE_SHEETS_CREDENTIALS` | no | Path to Google service-account JSON; enables `save_to_sheet` |
| `GOOGLE_SHEET_ID` | no | Target sheet id; enables `save_to_sheet` |
| `MCP_CACHE_TTL_RESTRICTIONS_S` | no | Override default 7d TTL for the restrictions disk cache |
| `MCP_CACHE_TTL_FBA_S` | no | Override default 7d TTL for the FBA eligibility disk cache |
| `MCP_CACHE_TTL_CATALOG_S` | no | Override default 30d TTL for the catalog disk cache |
| `MCP_CACHE_TTL_FEES_S` | no | Override default 24h TTL for the fees disk cache |
| `MCP_CACHE_TTL_PRICING_S` | no | Override default 5min TTL for the live-pricing disk cache |

Defaults: marketplace is UK (`A1F83G8C2ARO7P`) unless overridden per-call via
`marketplace_id`.

For this workspace, all SP-API values live in
`F:\My Drive\workspace\credentials.env` and are synced into
`~/.claude/settings.json` by `sync-credentials.ps1`. Never hardcode.

---

## Persistent disk cache

Sourcing-decision data lives in `<repo>/.cache/fba-mcp/` (gitignored). Layout:

```
.cache/fba-mcp/
├── restrictions/<sellerId>__<marketplaceId>__<conditionType>__<asin>.json
├── fba_eligibility/<marketplaceId>__<program>__<asin>.json
├── catalog/<marketplaceId>__<asin>.json
├── fees/<marketplaceId>__<asin>__<priceBucket>.json
└── pricing/<marketplaceId>__<condition>__<asin>.json
```

Each entry stores `{ fetched_at, ttl_seconds, data }` so TTL can vary per call.
On SP-API error, stale entries (past TTL) are served as a fallback when
available; the `raw.stale=true` flag identifies them. To force a fresh call,
pass `refresh_cache: true` to any tool. To wipe the cache entirely:

```
rm -rf .cache/fba-mcp/
```

---

## Build & run

```
npm install
npm run build       # tsc → dist/
npm start           # MCP server on stdio (for Claude Code)
```

### Tests

```
npm test                  # 99 unit tests (mocks SP-API; no creds needed)
npm run test:integration  # 5 live SP-API smoke tests (requires creds)
```

The integration tests are read-only (catalog/fees/eligibility/pricing/restrictions
on a known stable ASIN) and auto-skip when `SP_API_CLIENT_ID` is unset.
Override the test ASIN via `INTEGRATION_TEST_ASIN`.

---

## CLI mode (for the Python pipeline)

Each of the seven tools is also reachable via a non-MCP CLI:

```bash
node dist/cli.js preflight    --input items.json
node dist/cli.js preflight    --input -          # JSON from stdin
node dist/cli.js restrictions --asins B001,B002  --seller-id S1
node dist/cli.js fba          --asins B001,B002
node dist/cli.js fees         --input items.json
node dist/cli.js catalog      --asin  B001
node dist/cli.js pricing      --asins B001,B002
node dist/cli.js --help
```

Output is JSON on stdout, errors and the help text are on stderr. Exit code
is `0` on success and `1` on error. The Python pipeline auto-detects
`dist/cli.js` at the repo root and shells out in batches of 20 ASINs.

---

## Registering with Claude Code

Add to `.mcp.json` at the workspace root:

```json
{
  "mcpServers": {
    "amazon-fba-fees": {
      "command": "node",
      "args": ["<absolute path to this folder>/dist/index.js"],
      "env": {
        "SP_API_CLIENT_ID": "${SP_API_CLIENT_ID}",
        "SP_API_CLIENT_SECRET": "${SP_API_CLIENT_SECRET}",
        "SP_API_REFRESH_TOKEN": "${SP_API_REFRESH_TOKEN}",
        "SP_API_SELLER_ID": "${SP_API_SELLER_ID}"
      }
    }
  }
}
```

---

## Spec

See [SPEC.md](./SPEC.md) for the full design, sign-off questions, and
non-goals. Headline non-goals:

- **No auto-reject on ungating.** Restriction status is informational only.
- **No real-time pricing at scale.** `get_live_pricing` is for decision-time
  validation; Keepa stays the source of truth for historical/aggregate.
- **No new SP-API auth or env vars** beyond `SP_API_SELLER_ID` for restrictions.
