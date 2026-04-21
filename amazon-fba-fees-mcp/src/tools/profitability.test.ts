import { describe, it, expect, vi } from "vitest";
import { calculateProfitability } from "./profitability.js";
import type { SpApiService } from "../services/sp-api.js";
import { Cache } from "../services/cache.js";
import type { FeeEstimate } from "../types.js";

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

describe("calculateProfitability", () => {
  it("calculates profit with VAT for registered seller", async () => {
    const result = await calculateProfitability(
      {
        asin: "B0CX23VBGT",
        selling_price: 24.99,
        cost_price: 8.0,
        shipping_cost: 2.0,
        vat_registered: true,
        vat_rate: 0.2,
      },
      mockSpApi,
      cache
    );

    expect(result.revenue_ex_vat).toBeCloseTo(20.83, 1);
    expect(result.vat_amount).toBeCloseTo(4.16, 1);
    expect(result.profit).toBeCloseTo(4.01, 0);
    expect(result.vat_registered).toBe(true);
  });

  it("calculates profit without VAT for non-registered seller", async () => {
    const result = await calculateProfitability(
      {
        asin: "B0CX23VBGT",
        selling_price: 24.99,
        cost_price: 8.0,
        shipping_cost: 2.0,
        vat_registered: false,
      },
      mockSpApi,
      cache
    );

    expect(result.profit).toBeCloseTo(8.17, 1);
    expect(result.revenue_ex_vat).toBe(24.99);
    expect(result.vat_amount).toBe(0);
    expect(result.vat_registered).toBe(false);
  });

  it("defaults shipping_cost to 0", async () => {
    const result = await calculateProfitability(
      {
        asin: "B0CX23VBGT",
        selling_price: 24.99,
        cost_price: 8.0,
        vat_registered: true,
        vat_rate: 0.2,
      },
      mockSpApi,
      cache
    );

    expect(result.shipping_cost).toBe(0);
    expect(result.profit).toBeCloseTo(6.01, 0);
  });
});
