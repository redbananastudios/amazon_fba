import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { preflightAsin } from "./preflight-asin.js";
import { DiskCache } from "../services/disk-cache.js";
import type { SpApiService } from "../services/sp-api.js";
import type {
  CatalogItemResult,
  FbaEligibilityResult,
  FeeEstimate,
  ListingRestrictionsResult,
  LivePricingResult,
} from "../types.js";

const UK = "A1F83G8C2ARO7P";

function mkCaches(root: string) {
  return {
    restrictions: new DiskCache<ListingRestrictionsResult>({
      resource: "restrictions",
      defaultTtlSeconds: 3600,
      cacheRoot: root,
    }),
    fbaEligibility: new DiskCache<FbaEligibilityResult>({
      resource: "fba_eligibility",
      defaultTtlSeconds: 3600,
      cacheRoot: root,
    }),
    fees: new DiskCache<FeeEstimate>({
      resource: "fees",
      defaultTtlSeconds: 3600,
      cacheRoot: root,
    }),
    catalog: new DiskCache<CatalogItemResult>({
      resource: "catalog",
      defaultTtlSeconds: 3600,
      cacheRoot: root,
    }),
    pricing: new DiskCache<LivePricingResult>({
      resource: "pricing",
      defaultTtlSeconds: 3600,
      cacheRoot: root,
    }),
  };
}

function feesEntryFor(asin: string, idx: number, total = "3.00") {
  return {
    FeesEstimateResult: {
      FeesEstimateIdentifier: {
        SellerInputIdentifier: `${asin}-${9.99}-${idx}`,
      },
      FeesEstimate: {
        TotalFeesEstimate: { Amount: total, CurrencyCode: "GBP" },
        FeeDetailList: [
          { FeeType: "ReferralFee", FeeAmount: { Amount: "1.00", CurrencyCode: "GBP" } },
          { FeeType: "FBAFees", FeeAmount: { Amount: "2.00", CurrencyCode: "GBP" } },
          { FeeType: "ClosingFee", FeeAmount: { Amount: "0.00", CurrencyCode: "GBP" } },
        ],
      },
      Status: "Success",
    },
  };
}

function pricingEntryFor(asin: string, buyBox = 12.99) {
  return {
    body: {
      payload: {
        ASIN: asin,
        Status: "Success",
        Summary: {
          NumberOfOffers: [
            { condition: "new", fulfillmentChannel: "Amazon", OfferCount: 1 },
          ],
          BuyBoxPrices: [
            {
              condition: "New",
              LandedPrice: { Amount: buyBox, CurrencyCode: "GBP" },
              ListingPrice: { Amount: buyBox, CurrencyCode: "GBP" },
              Shipping: { Amount: 0, CurrencyCode: "GBP" },
            },
          ],
        },
        Offers: [{ SellerId: "S1", IsBuyBoxWinner: true, IsFulfilledByAmazon: true }],
      },
    },
    request: { uri: `/products/pricing/v0/items/${asin}/offers` },
  };
}

function makeSpApi(overrides: Partial<Record<string, any>> = {}): SpApiService {
  return {
    getListingsRestrictions: vi.fn().mockResolvedValue({ restrictions: [] }),
    getItemEligibilityPreview: vi.fn().mockResolvedValue({
      payload: { isEligibleForProgram: true, ineligibilityReasonList: [] },
    }),
    getMyFeesEstimates: vi.fn().mockImplementation(async (items: any[]) =>
      items.map((it: any, i: number) => feesEntryFor(it.asin, i))
    ),
    getCatalogItemFull: vi.fn().mockResolvedValue({
      summaries: [{ marketplaceId: UK, itemName: "X", brandName: "Acme" }],
    }),
    getItemOffersBatch: vi.fn().mockImplementation(async (params: any) => ({
      responses: params.asins.map((a: string) => pricingEntryFor(a)),
    })),
    ...overrides,
  } as unknown as SpApiService;
}

describe("preflightAsin", () => {
  let tmp: string;
  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "preflight-test-"));
  });
  afterEach(() => {
    if (existsSync(tmp)) rmSync(tmp, { recursive: true, force: true });
  });

  it("returns one result per item, in input order", async () => {
    const spApi = makeSpApi();
    const result = await preflightAsin(
      {
        items: [
          { asin: "B001", selling_price: 9.99, cost_price: 3 },
          { asin: "B002", selling_price: 9.99, cost_price: 4 },
        ],
        seller_id: "S1",
      },
      { spApi, caches: mkCaches(tmp) }
    );
    expect(result).toHaveLength(2);
    expect(result[0].asin).toBe("B001");
    expect(result[1].asin).toBe("B002");
  });

  it("populates all six sources for a happy-path ASIN", async () => {
    const spApi = makeSpApi();
    const [r] = await preflightAsin(
      {
        items: [{ asin: "B0HAPPY", selling_price: 9.99, cost_price: 3 }],
        seller_id: "S1",
      },
      { spApi, caches: mkCaches(tmp) }
    );
    expect(r.restrictions?.status).toBe("UNRESTRICTED");
    expect(r.fba?.eligible).toBe(true);
    expect(r.fees?.total_fees).toBe(3.0);
    expect(r.catalog?.brand).toBe("Acme");
    expect(r.pricing?.buy_box_price).toBe(12.99);
    expect(r.profitability?.profit).toBeCloseTo(9.99 / 1.2 - 3 - 3, 2);
    expect(r.errors).toEqual([]);
  });

  it("respects include[] to fetch only selected sources", async () => {
    const spApi = makeSpApi();
    const [r] = await preflightAsin(
      {
        items: [{ asin: "B0SUB", selling_price: 9.99, cost_price: 3 }],
        seller_id: "S1",
        include: ["restrictions", "fees"],
      },
      { spApi, caches: mkCaches(tmp) }
    );
    expect(r.restrictions).toBeDefined();
    expect(r.fees).toBeDefined();
    expect(r.fba).toBeUndefined();
    expect(r.catalog).toBeUndefined();
    expect(r.pricing).toBeUndefined();
    expect(r.profitability).toBeUndefined();
    expect(spApi.getItemEligibilityPreview).not.toHaveBeenCalled();
    expect(spApi.getCatalogItemFull).not.toHaveBeenCalled();
    expect(spApi.getItemOffersBatch).not.toHaveBeenCalled();
  });

  it("isolates per-source errors without failing the batch", async () => {
    const spApi = makeSpApi({
      getItemEligibilityPreview: vi.fn().mockRejectedValue(new Error("FBA down")),
    });
    const [r] = await preflightAsin(
      {
        items: [{ asin: "B0PARTIAL", selling_price: 9.99, cost_price: 3 }],
        seller_id: "S1",
      },
      { spApi, caches: mkCaches(tmp) }
    );
    expect(r.fba).toBeUndefined();
    expect(r.errors).toContainEqual({ source: "fba", message: "FBA down" });
    // Other sources should still succeed.
    expect(r.restrictions?.status).toBe("UNRESTRICTED");
    expect(r.fees?.total_fees).toBe(3);
    expect(r.catalog?.brand).toBe("Acme");
    expect(r.pricing?.buy_box_price).toBe(12.99);
  });

  it("records seller_id error on restrictions when not provided", async () => {
    const spApi = makeSpApi();
    const [r] = await preflightAsin(
      {
        items: [{ asin: "B0NOSELL", selling_price: 9.99, cost_price: 3 }],
      },
      { spApi, caches: mkCaches(tmp) }
    );
    expect(r.restrictions).toBeUndefined();
    expect(r.errors).toContainEqual(
      expect.objectContaining({ source: "restrictions" })
    );
    expect(spApi.getListingsRestrictions).not.toHaveBeenCalled();
  });

  it("falls back to deps.defaultSellerId when seller_id not in input", async () => {
    const spApi = makeSpApi();
    const [r] = await preflightAsin(
      {
        items: [{ asin: "B0DEF", selling_price: 9.99, cost_price: 3 }],
      },
      { spApi, caches: mkCaches(tmp), defaultSellerId: "DEFAULT_SELLER" }
    );
    expect(r.restrictions?.status).toBe("UNRESTRICTED");
    expect(spApi.getListingsRestrictions).toHaveBeenCalledWith(
      expect.objectContaining({ sellerId: "DEFAULT_SELLER" })
    );
  });

  it("populates cached[source]=false on first call, true on second", async () => {
    const spApi = makeSpApi();
    const caches = mkCaches(tmp);
    const item = { asin: "B0CCH", selling_price: 9.99, cost_price: 3 };
    const first = await preflightAsin(
      { items: [item], seller_id: "S1" },
      { spApi, caches }
    );
    expect(first[0].cached.restrictions).toBe(false);
    expect(first[0].cached.fba).toBe(false);
    expect(first[0].cached.fees).toBe(false);
    expect(first[0].cached.catalog).toBe(false);
    expect(first[0].cached.pricing).toBe(false);

    const second = await preflightAsin(
      { items: [item], seller_id: "S1" },
      { spApi, caches }
    );
    expect(second[0].cached.restrictions).toBe(true);
    expect(second[0].cached.fba).toBe(true);
    expect(second[0].cached.fees).toBe(true);
    expect(second[0].cached.catalog).toBe(true);
    expect(second[0].cached.pricing).toBe(true);
    // No new SP-API hits second time.
    expect(spApi.getListingsRestrictions).toHaveBeenCalledTimes(1);
    expect(spApi.getMyFeesEstimates).toHaveBeenCalledTimes(1);
  });

  it("rejects batches >20 items", async () => {
    const spApi = makeSpApi();
    const items = Array.from({ length: 21 }, (_, i) => ({
      asin: `B0${i}`,
      selling_price: 9.99,
      cost_price: 3,
    }));
    await expect(
      preflightAsin({ items, seller_id: "S1" }, { spApi, caches: mkCaches(tmp) })
    ).rejects.toThrow("max 20");
  });

  it("returns empty array for empty input without calling any SP-API", async () => {
    const spApi = makeSpApi();
    const result = await preflightAsin(
      { items: [], seller_id: "S1" },
      { spApi, caches: mkCaches(tmp) }
    );
    expect(result).toEqual([]);
    expect(spApi.getListingsRestrictions).not.toHaveBeenCalled();
    expect(spApi.getMyFeesEstimates).not.toHaveBeenCalled();
  });

  it("computes profitability from fees output (no extra SP-API call)", async () => {
    const spApi = makeSpApi();
    const [r] = await preflightAsin(
      {
        items: [{ asin: "B0PROF", selling_price: 12.0, cost_price: 4 }],
        seller_id: "S1",
        include: ["fees", "profitability"],
        vat_registered: true,
        vat_rate: 0.2,
      },
      { spApi, caches: mkCaches(tmp) }
    );
    // selling_price 12.00 ex-VAT = 10.00; profit = 10 - 4 - 0 - 3 = 3.00
    expect(r.profitability?.revenue_ex_vat).toBe(10.0);
    expect(r.profitability?.profit).toBe(3.0);
    expect(r.profitability?.roi_pct).toBe(75.0); // 3/4 * 100
    // Profitability must NOT trigger a second fees call.
    expect(spApi.getMyFeesEstimates).toHaveBeenCalledTimes(1);
  });

  it("propagates per-item fees error without breaking other sources", async () => {
    const spApi = makeSpApi({
      getMyFeesEstimates: vi.fn().mockResolvedValue([
        feesEntryFor("B0OK", 0),
        {
          FeesEstimateResult: {
            FeesEstimateIdentifier: { SellerInputIdentifier: "B0BAD-9.99-1" },
            Status: "ClientError",
            Error: { Message: "Bad ASIN" },
          },
        },
      ]),
    });
    const result = await preflightAsin(
      {
        items: [
          { asin: "B0OK", selling_price: 9.99, cost_price: 3 },
          { asin: "B0BAD", selling_price: 9.99, cost_price: 3 },
        ],
        seller_id: "S1",
      },
      { spApi, caches: mkCaches(tmp) }
    );
    expect(result[0].fees).toBeDefined();
    expect(result[0].profitability).toBeDefined();
    expect(result[1].fees).toBeUndefined();
    expect(result[1].errors).toContainEqual(
      expect.objectContaining({ source: "fees", message: "Bad ASIN" })
    );
  });
});
