import { describe, it, expect, vi } from "vitest";
import { estimateFees } from "./estimate-fees.js";
import type { SpApiService } from "../services/sp-api.js";
import { Cache } from "../services/cache.js";
import type { FeeEstimate } from "../types.js";

describe("estimateFees", () => {
  const mockSpApi = {
    getFeesAndTitle: vi.fn().mockResolvedValue({
      product_title: "Test Product",
      referral_fee: 3.75,
      fba_fulfillment_fee: 3.07,
      closing_fee: 0,
      total_fees: 6.82,
      currency: "GBP",
    }),
  } as unknown as SpApiService;

  const cache = new Cache<FeeEstimate>(86400000);

  it("returns fee breakdown for an ASIN", async () => {
    const result = await estimateFees(
      { asin: "B0CX23VBGT", selling_price: 24.99 },
      mockSpApi,
      cache
    );

    expect(result.asin).toBe("B0CX23VBGT");
    expect(result.total_fees).toBe(6.82);
    expect(result.product_title).toBe("Test Product");
  });

  it("returns cached result on second call", async () => {
    vi.clearAllMocks();
    await estimateFees(
      { asin: "B0CACHED", selling_price: 19.99 },
      mockSpApi,
      cache
    );
    await estimateFees(
      { asin: "B0CACHED", selling_price: 19.99 },
      mockSpApi,
      cache
    );

    expect(mockSpApi.getFeesAndTitle).toHaveBeenCalledTimes(1);
  });
});
