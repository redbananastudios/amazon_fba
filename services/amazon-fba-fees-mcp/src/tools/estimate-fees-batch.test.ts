import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { estimateFeesBatch } from "./estimate-fees-batch.js";
import { DiskCache } from "../services/disk-cache.js";
import type { SpApiService } from "../services/sp-api.js";
import type { FeeEstimate } from "../types.js";

function feesEntry(identifier: string, total: string, status = "Success") {
  return {
    FeesEstimateResult: {
      FeesEstimateIdentifier: { SellerInputIdentifier: identifier },
      FeesEstimate: {
        TotalFeesEstimate: { Amount: total, CurrencyCode: "GBP" },
        FeeDetailList: [
          { FeeType: "ReferralFee", FeeAmount: { Amount: "1.00", CurrencyCode: "GBP" } },
          { FeeType: "FBAFees", FeeAmount: { Amount: "2.00", CurrencyCode: "GBP" } },
          { FeeType: "ClosingFee", FeeAmount: { Amount: "0.00", CurrencyCode: "GBP" } },
        ],
      },
      Status: status,
    },
  };
}

function mockSpApi(response: unknown): SpApiService {
  return {
    getMyFeesEstimates: vi.fn().mockResolvedValue(response),
  } as unknown as SpApiService;
}

describe("estimateFeesBatch", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "fees-batch-test-"));
  });

  afterEach(() => {
    if (existsSync(tmp)) rmSync(tmp, { recursive: true, force: true });
  });

  const cacheFor = () =>
    new DiskCache<FeeEstimate>({
      resource: "fees",
      defaultTtlSeconds: 3600,
      cacheRoot: tmp,
    });

  it("returns empty array for empty input", async () => {
    const spApi = mockSpApi([]);
    const result = await estimateFeesBatch({ items: [] }, spApi);
    expect(result).toEqual([]);
    expect(spApi.getMyFeesEstimates).not.toHaveBeenCalled();
  });

  it("rejects batches >20 items", async () => {
    const spApi = mockSpApi([]);
    const items = Array.from({ length: 21 }, (_, i) => ({
      asin: `B0${i}`,
      selling_price: 9.99,
    }));
    await expect(estimateFeesBatch({ items }, spApi)).rejects.toThrow("max 20");
  });

  it("returns ok=true entries with parsed fees on success", async () => {
    const spApi = mockSpApi([feesEntry("B001-9.99-0", "3.00")]);
    const result = await estimateFeesBatch(
      { items: [{ asin: "B001", selling_price: 9.99 }] },
      spApi
    );
    expect(result).toHaveLength(1);
    expect(result[0].ok).toBe(true);
    expect(result[0].asin).toBe("B001");
    expect(result[0].fees?.total_fees).toBe(3.0);
    expect(result[0].fees?.referral_fee).toBe(1.0);
    expect(result[0].fees?.fba_fulfillment_fee).toBe(2.0);
  });

  it("preserves request order via identifier match", async () => {
    // Response returned out of order — tool should still align by identifier.
    const spApi = mockSpApi([
      feesEntry("B002-19.99-1", "4.00"),
      feesEntry("B001-9.99-0", "3.00"),
    ]);
    const result = await estimateFeesBatch(
      {
        items: [
          { asin: "B001", selling_price: 9.99 },
          { asin: "B002", selling_price: 19.99 },
        ],
      },
      spApi
    );
    expect(result[0].asin).toBe("B001");
    expect(result[0].fees?.total_fees).toBe(3.0);
    expect(result[1].asin).toBe("B002");
    expect(result[1].fees?.total_fees).toBe(4.0);
  });

  it("marks per-item errors without failing the whole batch", async () => {
    const spApi = mockSpApi([
      feesEntry("B0OK-9.99-0", "3.00"),
      {
        FeesEstimateResult: {
          FeesEstimateIdentifier: { SellerInputIdentifier: "B0BAD-9.99-1" },
          Status: "ClientError",
          Error: { Type: "ClientError", Code: "INVALID", Message: "Bad ASIN" },
        },
      },
    ]);
    const result = await estimateFeesBatch(
      {
        items: [
          { asin: "B0OK", selling_price: 9.99 },
          { asin: "B0BAD", selling_price: 9.99 },
        ],
      },
      spApi
    );
    expect(result[0].ok).toBe(true);
    expect(result[1].ok).toBe(false);
    expect(result[1].error).toBe("Bad ASIN");
  });

  it("marks ALL items as errored when whole call throws", async () => {
    const spApi = {
      getMyFeesEstimates: vi.fn().mockRejectedValue(new Error("SP-API 503")),
    } as unknown as SpApiService;
    const result = await estimateFeesBatch(
      {
        items: [
          { asin: "B001", selling_price: 9.99 },
          { asin: "B002", selling_price: 19.99 },
        ],
      },
      spApi
    );
    expect(result.every((r) => r.ok === false)).toBe(true);
    expect(result.every((r) => r.error === "SP-API 503")).toBe(true);
  });

  it("uses cache for repeat calls (same asin + price)", async () => {
    const spApi = mockSpApi([feesEntry("B001-9.99-0", "3.00")]);
    const cache = cacheFor();
    await estimateFeesBatch(
      { items: [{ asin: "B001", selling_price: 9.99 }] },
      spApi,
      cache
    );
    await estimateFeesBatch(
      { items: [{ asin: "B001", selling_price: 9.99 }] },
      spApi,
      cache
    );
    expect(spApi.getMyFeesEstimates).toHaveBeenCalledTimes(1);
  });

  it("buckets cache by price (different price = miss)", async () => {
    let lastIds: string[] = [];
    const spApi = {
      getMyFeesEstimates: vi.fn().mockImplementation(async (items: any[]) => {
        lastIds = items.map((i) => i.identifier);
        return items.map((it) => feesEntry(it.identifier, "3.00"));
      }),
    } as unknown as SpApiService;
    const cache = cacheFor();
    await estimateFeesBatch(
      { items: [{ asin: "B001", selling_price: 9.99 }] },
      spApi,
      cache
    );
    await estimateFeesBatch(
      { items: [{ asin: "B001", selling_price: 14.99 }] },
      spApi,
      cache
    );
    expect(spApi.getMyFeesEstimates).toHaveBeenCalledTimes(2);
    expect(lastIds[0]).toBe("B001-14.99-0");
  });

  it("only fetches uncached items in mixed batch", async () => {
    let fetchedItems: any[] = [];
    const spApi = {
      getMyFeesEstimates: vi.fn().mockImplementation(async (items: any[]) => {
        fetchedItems = items;
        return items.map((it) => feesEntry(it.identifier, "5.00"));
      }),
    } as unknown as SpApiService;
    const cache = cacheFor();
    // Prime cache with B001
    await estimateFeesBatch(
      { items: [{ asin: "B001", selling_price: 9.99 }] },
      spApi,
      cache
    );
    fetchedItems = [];
    // Mixed batch: B001 cached, B002 fresh
    const result = await estimateFeesBatch(
      {
        items: [
          { asin: "B001", selling_price: 9.99 },
          { asin: "B002", selling_price: 9.99 },
        ],
      },
      spApi,
      cache
    );
    // Only B002 should hit SP-API on the second call
    expect(fetchedItems).toHaveLength(1);
    expect(fetchedItems[0].asin).toBe("B002");
    expect(result[0].asin).toBe("B001");
    expect(result[0].ok).toBe(true);
    expect(result[1].asin).toBe("B002");
    expect(result[1].ok).toBe(true);
  });

  it("bypasses cache when refresh_cache is true", async () => {
    const spApi = {
      getMyFeesEstimates: vi.fn().mockImplementation(async (items: any[]) =>
        items.map((it) => feesEntry(it.identifier, "3.00"))
      ),
    } as unknown as SpApiService;
    const cache = cacheFor();
    await estimateFeesBatch(
      { items: [{ asin: "B001", selling_price: 9.99 }] },
      spApi,
      cache
    );
    await estimateFeesBatch(
      { items: [{ asin: "B001", selling_price: 9.99 }], refresh_cache: true },
      spApi,
      cache
    );
    expect(spApi.getMyFeesEstimates).toHaveBeenCalledTimes(2);
  });

  it("handles missing FeesEstimate in entry as a per-item error", async () => {
    const spApi = mockSpApi([
      {
        FeesEstimateResult: {
          FeesEstimateIdentifier: { SellerInputIdentifier: "B0EMPTY-9.99-0" },
          Status: "Success",
          // FeesEstimate intentionally omitted
        },
      },
    ]);
    const result = await estimateFeesBatch(
      { items: [{ asin: "B0EMPTY", selling_price: 9.99 }] },
      spApi
    );
    expect(result[0].ok).toBe(false);
    expect(result[0].error).toContain("Empty FeesEstimate");
  });
});
