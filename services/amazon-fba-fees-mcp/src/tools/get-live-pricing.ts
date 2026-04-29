import type { SpApiService } from "../services/sp-api.js";
import type { DiskCache } from "../services/disk-cache.js";
import { UK_MARKETPLACE_ID, type LivePricingResult } from "../types.js";

export interface GetLivePricingInput {
  asins: string[];
  marketplace_id?: string;
  item_condition?: string;
  refresh_cache?: boolean;
}

interface SpApiMoney {
  Amount?: number;
  CurrencyCode?: string;
}

interface SpApiBuyBoxPrice {
  condition?: string;
  LandedPrice?: SpApiMoney;
  ListingPrice?: SpApiMoney;
  Shipping?: SpApiMoney;
}

interface SpApiOfferCount {
  condition?: string;
  fulfillmentChannel?: string;
  OfferCount?: number;
}

interface SpApiSummary {
  TotalOfferCount?: number;
  NumberOfOffers?: SpApiOfferCount[];
  BuyBoxPrices?: SpApiBuyBoxPrice[];
}

interface SpApiOffer {
  SellerId?: string;
  IsBuyBoxWinner?: boolean;
  IsFeaturedMerchant?: boolean;
  IsFulfilledByAmazon?: boolean;
  ListingPrice?: SpApiMoney;
  Shipping?: SpApiMoney;
}

interface SpApiPayload {
  ASIN?: string;
  MarketplaceID?: string;
  Status?: string;
  Summary?: SpApiSummary;
  Offers?: SpApiOffer[];
}

interface SpApiResponseEntry {
  status?: { statusCode?: number; reasonPhrase?: string };
  body?: { payload?: SpApiPayload };
  request?: { ASIN?: string; uri?: string };
}

interface SpApiBatchResponse {
  responses?: SpApiResponseEntry[];
}

function asinFromUri(uri?: string): string | undefined {
  if (!uri) return undefined;
  const m = uri.match(/items\/([^/]+)\/offers/);
  return m ? m[1] : undefined;
}

function classifyBuyBoxSeller(payload: SpApiPayload): string | undefined {
  // Only classify when there's an actual Buy Box winner. Falling back to
  // offers[0] would silently mislabel any ASIN where no offer holds the
  // Buy Box (suppressed listing, no qualifying offer, etc.) — the
  // buy_box_price stays undefined in those cases, so seller class must
  // also be undefined to stay consistent.
  const offers = payload.Offers ?? [];
  const winner = offers.find((o) => o.IsBuyBoxWinner);
  if (!winner) return undefined;
  if (winner.IsFulfilledByAmazon) return "FBA";
  return "FBM";
}

function buildResult(
  asin: string,
  marketplaceId: string,
  payload: SpApiPayload | undefined
): LivePricingResult {
  if (!payload) {
    return { asin, marketplace_id: marketplaceId, raw: payload };
  }
  const buyBox = payload.Summary?.BuyBoxPrices?.[0];
  const newOffers = (payload.Summary?.NumberOfOffers ?? []).filter(
    (o) => o.condition?.toLowerCase() === "new"
  );
  const offer_count_new = newOffers.reduce(
    (sum, o) => sum + (o.OfferCount ?? 0),
    0
  );
  const offer_count_fba = newOffers
    .filter((o) => o.fulfillmentChannel === "Amazon")
    .reduce((sum, o) => sum + (o.OfferCount ?? 0), 0);
  return {
    asin,
    buy_box_price: buyBox?.LandedPrice?.Amount,
    buy_box_seller: classifyBuyBoxSeller(payload),
    listing_price: buyBox?.ListingPrice?.Amount,
    shipping: buyBox?.Shipping?.Amount,
    offer_count_new: newOffers.length > 0 ? offer_count_new : undefined,
    offer_count_fba: newOffers.length > 0 ? offer_count_fba : undefined,
    marketplace_id: marketplaceId,
    raw: payload,
  };
}

export async function getLivePricing(
  input: GetLivePricingInput,
  spApi: SpApiService,
  cache?: DiskCache<LivePricingResult>
): Promise<LivePricingResult[]> {
  if (input.asins.length === 0) return [];
  if (input.asins.length > 20) {
    throw new Error(
      `get_live_pricing: max 20 ASINs per call, got ${input.asins.length}`
    );
  }
  const marketplaceId = input.marketplace_id ?? UK_MARKETPLACE_ID;
  const condition = input.item_condition ?? "New";

  const results: LivePricingResult[] = new Array(input.asins.length);
  const toFetch: Array<{ idx: number; asin: string }> = [];

  // Cache pass.
  input.asins.forEach((asin, idx) => {
    if (cache && !input.refresh_cache) {
      const hit = cache.get(marketplaceId, condition, asin);
      if (hit.hit && hit.data) {
        results[idx] = hit.data;
        return;
      }
    }
    toFetch.push({ idx, asin });
  });

  if (toFetch.length === 0) return results;

  let raw: unknown;
  try {
    raw = await spApi.getItemOffersBatch({
      asins: toFetch.map((t) => t.asin),
      marketplaceId,
      itemCondition: condition,
    });
  } catch (err) {
    if (cache) {
      let staleCount = 0;
      for (const { idx, asin } of toFetch) {
        const stale = cache.get(marketplaceId, condition, asin);
        if (stale.stale && stale.data) {
          results[idx] = {
            ...stale.data,
            raw: { ...(stale.data.raw as object), stale: true },
          };
          staleCount++;
        } else {
          results[idx] = {
            asin,
            marketplace_id: marketplaceId,
            raw: { error: (err as Error).message },
          };
        }
      }
      if (staleCount > 0) return results;
    }
    throw err;
  }

  const responses: SpApiResponseEntry[] = (raw as SpApiBatchResponse)?.responses ?? [];
  // Match responses to requests by ASIN (parsed from request.uri) when available,
  // fall back to positional matching.
  const byAsin = new Map<string, SpApiResponseEntry>();
  for (const entry of responses) {
    const asin = entry.body?.payload?.ASIN ?? asinFromUri(entry.request?.uri);
    if (asin) byAsin.set(asin, entry);
  }

  toFetch.forEach(({ idx, asin }, posInFetch) => {
    const entry = byAsin.get(asin) ?? responses[posInFetch];
    const payload = entry?.body?.payload;
    const result = buildResult(asin, marketplaceId, payload);
    // Skip cache write when SP-API returned no payload for this ASIN —
    // pinning a stub-with-no-Buy-Box for 5 minutes would mask transient
    // upstream issues. Only cache when the payload was structurally present.
    if (payload) cache?.set([marketplaceId, condition, asin], result);
    results[idx] = result;
  });

  return results;
}
