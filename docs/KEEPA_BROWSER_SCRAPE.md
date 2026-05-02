# Keepa Browser Scrape — Operator Protocol

When you want a chart-quality verdict on a single ASIN, the engine
can read **Keepa Browser data** (per-seller Buy Box share with
seller names, precomputed 365-day signals, currently-active offers).

This data isn't in the Keepa API at our 1-token/min tier — it
comes from scraping the logged-in Pro Browser session. The engine
is split so it doesn't need browser access itself: a separate
**scraping process** writes a JSON cache, the engine reads the
cache.

## Today's workflow

1. **Ask Claude** (in Claude Code): *"scrape Keepa Browser for B001Y54F88"*
2. Claude uses the Chrome MCP to navigate, read, and parse the BB
   Statistics + Offers + Product Details tabs
3. Claude writes `.cache/keepa_browser/B001Y54F88.json` with the
   structured scrape
4. Run the engine:
   ```bash
   python run.py --strategy single_asin --asin B001Y54F88 --buy-cost 35.00
   ```
5. The `keepa_browser_enrich` step picks up the cache automatically.
   You'll see `Buy Box dominance (Keepa Browser scrape):` in the
   stdout block + the share-aware velocity prediction uses the
   real per-seller distribution.

## What the cache carries

`.cache/keepa_browser/<asin>.json` — schema in
`shared/lib/python/keepa_client/browser_cache.py::BrowserScrape`:

- **`product_details`** — Browser's precomputed columns (365-day
  averages, lifetime lows, OOS%, BSR drops 30d/90d, total offer
  count). Most are derivable from API csv arrays but Browser's
  versions are typically more accurate.
- **`buy_box_seller_stats`** — per-seller %BB-won, avg price, FBA
  flag, current stock. Replaces the API's `buyBoxStats` (which
  uses anonymous merchant IDs without seller names).
- **`active_offers`** — currently-listing sellers (vs the
  buy_box_seller_stats which is lifetime). Carries stock + sold-
  30d per seller.

## TTL + freshness

- Default TTL: **24 hours**. Stale entries are still readable
  (caller decides to use stale or trigger re-scrape).
- The engine prints `browser_scrape_at` in the verdict block so
  the operator sees scrape age.
- Re-scrape any ASIN by asking Claude to scrape it again — the new
  JSON overwrites the old.

## When to scrape vs not

**Scrape when:**
- You're considering a real test order on the ASIN
- The API verdict feels wrong (validator says WATCH but you suspect
  BUY, or vice versa)
- The listing has lots of sellers (Browser's per-seller share
  matters most when there are many competitors)

**Skip when:**
- Bulk discovery runs (200+ rows — too many to scrape one-by-one)
- The verdict is obviously KILL or obviously BUY from the API alone
- The listing has 1-2 sellers (the share split is straightforward)

## Architecture (for future work)

The engine reads from `.cache/keepa_browser/<asin>.json`. The
**scraper** today is Claude+MCP. Tomorrow it could be:

- A standalone Playwright script (`scripts/scrape_keepa.py`) that
  uses persistent cookies to scrape unattended
- A sister Node service `services/keepa-browser-scraper/` invoked
  via subprocess
- A Cowork orchestrator that batches scrapes for top-N candidates
  after a `keepa_finder` run

Whichever scraper writes the JSON cache in the schema defined in
`browser_cache.py::BrowserScrape`, the engine reads it the same
way. The cache is the contract.

## Cache file location

Auto-detected via repo root walk-up. Typically
`<repo>/.cache/keepa_browser/<asin>.json`. The directory is
gitignored — scrape data is local-only by design.

## What if the cache is missing?

`keepa_browser_enrich` is a **silent no-op** when the cache file
is absent. The row passes through unchanged and the validator
falls back to:
- API-derived `amazon_bb_pct_90` (still works)
- API's `buy_box_seller_stats` (anonymous IDs but workable)
- Engine-computed velocity prediction (less accurate without
  per-seller share data)

So the engine never breaks if you forget to scrape — you just get
the API-quality verdict instead of the chart-quality one.
