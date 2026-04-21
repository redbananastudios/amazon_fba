import { describe, it, expect, vi, beforeEach } from "vitest";
import { SpApiService } from "./sp-api.js";

// Mock amazon-sp-api module
vi.mock("amazon-sp-api", () => {
  return {
    default: vi.fn().mockImplementation(function (this: any) {
      this.callAPI = vi.fn();
    }),
  };
});

describe("SpApiService", () => {
  let service: SpApiService;

  beforeEach(() => {
    service = new SpApiService({
      clientId: "test-id",
      clientSecret: "test-secret",
      refreshToken: "test-token",
    });
  });

  it("returns fee estimate for a valid ASIN", async () => {
    const mockCallAPI = vi.fn()
      // First call: getMyFeesEstimateForASIN
      .mockResolvedValueOnce({
        FeesEstimateResult: {
          FeesEstimate: {
            FeeDetailList: [
              { FeeType: "ReferralFee", FeeAmount: { Amount: "3.75", CurrencyCode: "GBP" } },
              { FeeType: "FBAFees", FeeAmount: { Amount: "3.07", CurrencyCode: "GBP" } },
              { FeeType: "ClosingFee", FeeAmount: { Amount: "0", CurrencyCode: "GBP" } },
            ],
            TotalFeesEstimate: { Amount: "6.82", CurrencyCode: "GBP" },
          },
        },
      })
      // Second call: getCatalogItem
      .mockResolvedValueOnce({
        summaries: [{ itemName: "Test Product" }],
      });

    // Replace the client's callAPI with our mock
    (service as any).client.callAPI = mockCallAPI;

    const result = await service.getFeesAndTitle("B0CX23VBGT", 24.99, "A1F83G8C2ARO7P");

    expect(result.referral_fee).toBe(3.75);
    expect(result.fba_fulfillment_fee).toBe(3.07);
    expect(result.total_fees).toBe(6.82);
    expect(result.product_title).toBe("Test Product");
  });
});
