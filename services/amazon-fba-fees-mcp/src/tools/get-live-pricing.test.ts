import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { getLivePricing } from "./get-live-pricing.js";
import { DiskCache } from "../services/disk-cache.js";
import type { SpApiService } from "../services/sp-api.js";
import type { LivePricingResult } from "../types.js";

const UK = "A1F83G8C2ARO7P";

function payload({
  asin,
  buyBox,
  listing,
  shipping,
  offers = [],
  numNewFba = 0,
  numNewMerchant = 0,
}: {
  asin: string;
  buyBox?: number;
  listing?: number;
  shipping?: number;
  offers?: Array<{ fba?: boolean; winner?: boolean }>;
  numNewFba?: number;
  numNewMerchant?: number;
}) {
  const numberOfOffers: Array<{ condition: string; fulfillmentChannel: string; OfferCount: number }> = [];
  if (numNewFba > 0)
    numberOfOffers.push({
      condition: "new",
      fulfillmentChannel: "Amazon",
      OfferCount: numNewFba,
    });
  if (numNewMerchant > 0)
    numberOfOffers.push({
      condition: "new",
      fulfillmentChannel: "Merchant",
      OfferCount: numNewMerchant,
    });
  return {
    body: {
      payload: {
        ASIN: asin,
        MarketplaceID: UK,
        Status: "Success",
        Summary: {
          NumberOfOffers: numberOfOffers,
          BuyBoxPrices:
            buyBox !== undefined
              ? [
                  {
                    condition: "New",
                    LandedPrice: { Amount: buyBox, CurrencyCode: "GBP" },
                    ListingPrice: { Amount: listing ?? buyBox, CurrencyCode: "GBP" },
                    Shipping: { Amount: shipping ?? 0, CurrencyCode: "GBP" },
                  },
                ]
              : undefined,
        },
        Offers: offers.map((o, i) => ({
          SellerId: `S${i}`,
          IsBuyBoxWinner: !!o.winner,
          IsFulfilledByAmazon: !!o.fba,
        })),
      },
    },
    request: { uri: `/products/pricing/v0/items/${asin}/offers` },
  };
}

function mockSpApi(response: unknown): SpApiService {
  return {
    getItemOffersBatch: vi.fn().mockResolvedValue(response),
  } as unknown as SpApiService;
}

describe("getLivePricing", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "live-pricing-test-"));
  });

  afterEach(() => {
    if (existsSync(tmp)) rmSync(tmp, { recursive: true, force: true });
  });

  const cacheFor = () =>
    new DiskCache<LivePricingResult>({
      resource: "pricing",
      defaultTtlSeconds: 3600,
      cacheRoot: tmp,
    });

  it("returns empty array for empty input", async () => {
    const spApi = mockSpApi({ responses: [] });
    const result = await getLivePricing({ asins: [] }, spApi);
    expect(result).toEqual([]);
    expect(spApi.getItemOffersBatch).not.toHaveBeenCalled();
  });

  it("rejects batches >20 ASINs", async () => {
    const spApi = mockSpApi({ responses: [] });
    const asins = Array.from({ length: 21 }, (_, i) => `B0${i}`);
    await expect(getLivePricing({ asins }, spApi)).rejects.toThrow("max 20");
  });

  it("extracts buy_box_price, listing_price, shipping for an ASIN", async () => {
    const spApi = mockSpApi({
      responses: [
        payload({
          asin: "B0BB",
          buyBox: 12.99,
          listing: 12.99,
          shipping: 0,
          offers: [{ winner: true, fba: true }],
          numNewFba: 1,
        }),
      ],
    });
    const [result] = await getLivePricing({ asins: ["B0BB"] }, spApi);
    expect(result.buy_box_price).toBe(12.99);
    expect(result.listing_price).toBe(12.99);
    expect(result.shipping).toBe(0);
    expect(result.buy_box_seller).toBe("FBA");
    expect(result.offer_count_new).toBe(1);
    expect(result.offer_count_fba).toBe(1);
  });

  it("classifies FBM seller when winner is not FBA", async () => {
    const spApi = mockSpApi({
      responses: [
        payload({
          asin: "B0FBM",
          buyBox: 9.99,
          offers: [{ winner: true, fba: false }],
          numNewMerchant: 1,
        }),
      ],
    });
    const [result] = await getLivePricing({ asins: ["B0FBM"] }, spApi);
    expect(result.buy_box_seller).toBe("FBM");
    expect(result.offer_count_fba).toBe(0);
    expect(result.offer_count_new).toBe(1);
  });

  it("classifies AMZN when Buy Box winner is Amazon Retail (UK seller ID)", async () => {
    // UK Amazon Retail seller ID. When this seller holds the Buy Box,
    // it materially changes sourcing math (Amazon won't share Buy Box
    // long-term), so we want to flag it distinctly from generic FBA.
    const spApi = mockSpApi({
      responses: [
        {
          body: {
            payload: {
              ASIN: "B0AMZN",
              MarketplaceID: UK,
              Status: "Success",
              Summary: {
                NumberOfOffers: [
                  { condition: "new", fulfillmentChannel: "Amazon", OfferCount: 1 },
                ],
                BuyBoxPrices: [
                  {
                    condition: "New",
                    LandedPrice: { Amount: 14.99, CurrencyCode: "GBP" },
                  },
                ],
              },
              Offers: [
                {
                  SellerId: "A3P5ROKL5A1OLE",
                  IsBuyBoxWinner: true,
                  IsFulfilledByAmazon: true,
                },
              ],
            },
          },
          request: { uri: "/products/pricing/v0/items/B0AMZN/offers" },
        },
      ],
    });
    const [result] = await getLivePricing({ asins: ["B0AMZN"] }, spApi);
    expect(result.buy_box_seller).toBe("AMZN");
  });

  it("sums NumberOfOffers across new offer entries", async () => {
    const spApi = mockSpApi({
      responses: [
        payload({
          asin: "B0SUM",
          buyBox: 5.0,
          numNewFba: 3,
          numNewMerchant: 2,
        }),
      ],
    });
    const [result] = await getLivePricing({ asins: ["B0SUM"] }, spApi);
    expect(result.offer_count_new).toBe(5);
    expect(result.offer_count_fba).toBe(3);
  });

  it("returns undefined fields when payload has no Buy Box / no offers", async () => {
    const spApi = mockSpApi({
      responses: [{ body: { payload: { ASIN: "B0NONE" } } }],
    });
    const [result] = await getLivePricing({ asins: ["B0NONE"] }, spApi);
    expect(result.buy_box_price).toBeUndefined();
    expect(result.buy_box_seller).toBeUndefined();
    expect(result.offer_count_new).toBeUndefined();
  });

  it("returns undefined buy_box_seller when offers exist but none holds the Buy Box", async () => {
    // Regression: previously classifyBuyBoxSeller fell back to offers[0]
    // and returned FBA/FBM even though buy_box_price was undefined,
    // silently misleading callers.
    const spApi = mockSpApi({
      responses: [
        {
          body: {
            payload: {
              ASIN: "B0NOWIN",
              Status: "Success",
              Summary: { NumberOfOffers: [], BuyBoxPrices: [] },
              Offers: [
                { SellerId: "S1", IsBuyBoxWinner: false, IsFulfilledByAmazon: true },
                { SellerId: "S2", IsBuyBoxWinner: false, IsFulfilledByAmazon: false },
              ],
            },
          },
          request: { uri: "/products/pricing/v0/items/B0NOWIN/offers" },
        },
      ],
    });
    const [result] = await getLivePricing({ asins: ["B0NOWIN"] }, spApi);
    expect(result.buy_box_price).toBeUndefined();
    expect(result.buy_box_seller).toBeUndefined();
  });

  it("picks BuyBoxPrice matching the requested condition (not [0])", async () => {
    // Defensive: SP-API ordering is undocumented. If a future quirk
    // lands a Used entry at index 0 when we asked for New, [0] would
    // silently return Used pricing. Filter on condition explicitly.
    const spApi = mockSpApi({
      responses: [
        {
          body: {
            payload: {
              ASIN: "B0COND",
              Status: "Success",
              Summary: {
                NumberOfOffers: [
                  { condition: "new", fulfillmentChannel: "Amazon", OfferCount: 1 },
                ],
                BuyBoxPrices: [
                  { condition: "Used", LandedPrice: { Amount: 5.0, CurrencyCode: "GBP" } },
                  { condition: "New", LandedPrice: { Amount: 19.99, CurrencyCode: "GBP" } },
                ],
              },
              Offers: [{ SellerId: "S1", IsBuyBoxWinner: true, IsFulfilledByAmazon: true }],
            },
          },
          request: { uri: "/products/pricing/v0/items/B0COND/offers" },
        },
      ],
    });
    const [result] = await getLivePricing(
      { asins: ["B0COND"], item_condition: "New" },
      spApi
    );
    expect(result.buy_box_price).toBe(19.99);
  });

  it("aligns out-of-order responses by ASIN", async () => {
    const spApi = mockSpApi({
      responses: [
        payload({ asin: "B002", buyBox: 22.22 }),
        payload({ asin: "B001", buyBox: 11.11 }),
      ],
    });
    const result = await getLivePricing({ asins: ["B001", "B002"] }, spApi);
    expect(result[0].asin).toBe("B001");
    expect(result[0].buy_box_price).toBe(11.11);
    expect(result[1].asin).toBe("B002");
    expect(result[1].buy_box_price).toBe(22.22);
  });

  it("uses cache on second call for same ASIN", async () => {
    const spApi = mockSpApi({
      responses: [payload({ asin: "B0CACHE", buyBox: 9.99 })],
    });
    const cache = cacheFor();
    await getLivePricing({ asins: ["B0CACHE"] }, spApi, cache);
    await getLivePricing({ asins: ["B0CACHE"] }, spApi, cache);
    expect(spApi.getItemOffersBatch).toHaveBeenCalledTimes(1);
  });

  it("only fetches uncached ASINs in mixed batch", async () => {
    let lastAsins: string[] = [];
    const spApi = {
      getItemOffersBatch: vi.fn().mockImplementation(async (params: any) => {
        lastAsins = params.asins;
        return { responses: params.asins.map((a: string) => payload({ asin: a, buyBox: 1 })) };
      }),
    } as unknown as SpApiService;
    const cache = cacheFor();
    await getLivePricing({ asins: ["B001"] }, spApi, cache);
    lastAsins = [];
    await getLivePricing({ asins: ["B001", "B002"] }, spApi, cache);
    expect(lastAsins).toEqual(["B002"]);
  });

  it("scopes cache by item_condition", async () => {
    const spApi = mockSpApi({
      responses: [payload({ asin: "B0COND", buyBox: 1 })],
    });
    const cache = cacheFor();
    await getLivePricing({ asins: ["B0COND"], item_condition: "New" }, spApi, cache);
    await getLivePricing({ asins: ["B0COND"], item_condition: "Used" }, spApi, cache);
    expect(spApi.getItemOffersBatch).toHaveBeenCalledTimes(2);
  });

  it("bypasses cache when refresh_cache is true", async () => {
    const spApi = mockSpApi({
      responses: [payload({ asin: "B0R", buyBox: 1 })],
    });
    const cache = cacheFor();
    await getLivePricing({ asins: ["B0R"] }, spApi, cache);
    await getLivePricing({ asins: ["B0R"], refresh_cache: true }, spApi, cache);
    expect(spApi.getItemOffersBatch).toHaveBeenCalledTimes(2);
  });

  it("serves stale cache when SP-API errors", async () => {
    let calls = 0;
    const spApi = {
      getItemOffersBatch: vi.fn().mockImplementation(async () => {
        calls++;
        if (calls > 1) throw new Error("SP-API 503");
        return { responses: [payload({ asin: "B0STL", buyBox: 7.77 })] };
      }),
    } as unknown as SpApiService;
    const cache = cacheFor();
    await getLivePricing({ asins: ["B0STL"] }, spApi, cache);
    const path = join(tmp, "pricing", `${UK}__New__B0STL.json`);
    const fs = await import("fs");
    const entry = JSON.parse(fs.readFileSync(path, "utf8"));
    entry.fetched_at = new Date(Date.now() - 999_999_999).toISOString();
    fs.writeFileSync(path, JSON.stringify(entry));
    const result = await getLivePricing({ asins: ["B0STL"] }, spApi, cache);
    expect(result[0].buy_box_price).toBe(7.77);
    expect((result[0].raw as { stale?: boolean }).stale).toBe(true);
  });
});
