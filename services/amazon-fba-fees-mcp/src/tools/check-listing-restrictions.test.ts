import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { checkListingRestrictions } from "./check-listing-restrictions.js";
import { DiskCache } from "../services/disk-cache.js";
import type { SpApiService } from "../services/sp-api.js";
import type { ListingRestrictionsResult } from "../types.js";

function mockSpApi(response: unknown, opts: { failAfter?: number } = {}): SpApiService {
  let calls = 0;
  return {
    getListingsRestrictions: vi.fn().mockImplementation(async () => {
      calls++;
      if (opts.failAfter !== undefined && calls > opts.failAfter) {
        throw new Error("SP-API 503");
      }
      return response;
    }),
  } as unknown as SpApiService;
}

describe("checkListingRestrictions", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "restrictions-test-"));
  });

  afterEach(() => {
    if (existsSync(tmp)) rmSync(tmp, { recursive: true, force: true });
  });

  const cacheFor = () =>
    new DiskCache<ListingRestrictionsResult>({
      resource: "restrictions",
      defaultTtlSeconds: 3600,
      cacheRoot: tmp,
    });

  it("returns UNRESTRICTED when SP-API returns empty restrictions", async () => {
    const spApi = mockSpApi({ restrictions: [] });
    const result = await checkListingRestrictions(
      { asin: "B0FREE", seller_id: "S1" },
      spApi
    );
    expect(result.status).toBe("UNRESTRICTED");
    expect(result.approval_required).toBe(false);
    expect(result.reasons).toEqual([]);
    expect(result.asin).toBe("B0FREE");
    expect(result.marketplace_id).toBe("A1F83G8C2ARO7P");
  });

  it("classifies BRAND_GATED when message mentions brand", async () => {
    const spApi = mockSpApi({
      restrictions: [
        {
          asin: "B0BRAND",
          conditionType: "new_new",
          reasons: [
            {
              message: "You need approval to list this brand.",
              reasonCode: "APPROVAL_REQUIRED",
              links: [{ resource: "/help/brand-approval", title: "Apply", verb: "GET" }],
            },
          ],
        },
      ],
    });
    const result = await checkListingRestrictions(
      { asin: "B0BRAND", seller_id: "S1" },
      spApi
    );
    expect(result.status).toBe("BRAND_GATED");
    expect(result.approval_required).toBe(true);
    expect(result.reasons).toHaveLength(1);
    expect(result.reasons[0].link).toBe("/help/brand-approval");
  });

  it("classifies CATEGORY_GATED when message mentions category", async () => {
    const spApi = mockSpApi({
      restrictions: [
        {
          reasons: [
            {
              message: "This category requires approval.",
              reasonCode: "APPROVAL_REQUIRED",
            },
          ],
        },
      ],
    });
    const result = await checkListingRestrictions(
      { asin: "B0CAT", seller_id: "S1" },
      spApi
    );
    expect(result.status).toBe("CATEGORY_GATED");
    expect(result.approval_required).toBe(true);
  });

  it("falls back to RESTRICTED for generic approval-required reasons", async () => {
    const spApi = mockSpApi({
      restrictions: [
        {
          reasons: [
            { message: "Approval required.", reasonCode: "APPROVAL_REQUIRED" },
          ],
        },
      ],
    });
    const result = await checkListingRestrictions(
      { asin: "B0GEN", seller_id: "S1" },
      spApi
    );
    expect(result.status).toBe("RESTRICTED");
    expect(result.approval_required).toBe(true);
  });

  it("respects condition_type override and passes it to SP-API", async () => {
    const spApi = mockSpApi({ restrictions: [] });
    await checkListingRestrictions(
      {
        asin: "B0COND",
        seller_id: "S1",
        condition_type: "used_good",
      },
      spApi
    );
    expect(spApi.getListingsRestrictions).toHaveBeenCalledWith(
      expect.objectContaining({ conditionType: "used_good" })
    );
  });

  it("uses cache on second call (no extra SP-API hit)", async () => {
    const spApi = mockSpApi({ restrictions: [] });
    const cache = cacheFor();
    await checkListingRestrictions({ asin: "B0CACHE", seller_id: "S1" }, spApi, cache);
    await checkListingRestrictions({ asin: "B0CACHE", seller_id: "S1" }, spApi, cache);
    expect(spApi.getListingsRestrictions).toHaveBeenCalledTimes(1);
  });

  it("bypasses cache when refresh_cache is true", async () => {
    const spApi = mockSpApi({ restrictions: [] });
    const cache = cacheFor();
    await checkListingRestrictions({ asin: "B0R", seller_id: "S1" }, spApi, cache);
    await checkListingRestrictions(
      { asin: "B0R", seller_id: "S1", refresh_cache: true },
      spApi,
      cache
    );
    expect(spApi.getListingsRestrictions).toHaveBeenCalledTimes(2);
  });

  it("scopes cache by seller_id (different seller misses)", async () => {
    const spApi = mockSpApi({ restrictions: [] });
    const cache = cacheFor();
    await checkListingRestrictions({ asin: "B0S", seller_id: "S1" }, spApi, cache);
    await checkListingRestrictions({ asin: "B0S", seller_id: "S2" }, spApi, cache);
    expect(spApi.getListingsRestrictions).toHaveBeenCalledTimes(2);
  });

  it("serves stale cache when SP-API errors", async () => {
    const spApi = mockSpApi({ restrictions: [] }, { failAfter: 1 });
    const cache = cacheFor();
    // First call: success, populates cache
    await checkListingRestrictions({ asin: "B0STL", seller_id: "S1" }, spApi, cache);
    // Backdate the cache file so it's stale
    const path = join(tmp, "restrictions", "S1__A1F83G8C2ARO7P__new_new__B0STL.json");
    const fs = await import("fs");
    const entry = JSON.parse(fs.readFileSync(path, "utf8"));
    entry.fetched_at = new Date(Date.now() - 999_999_999).toISOString();
    fs.writeFileSync(path, JSON.stringify(entry));
    // Second call: SP-API throws; should serve stale
    const result = await checkListingRestrictions(
      { asin: "B0STL", seller_id: "S1" },
      spApi,
      cache
    );
    expect(result.status).toBe("UNRESTRICTED");
    expect((result.raw as { stale?: boolean }).stale).toBe(true);
  });

  it("propagates error when no stale cache available", async () => {
    const spApi = mockSpApi({ restrictions: [] }, { failAfter: 0 });
    await expect(
      checkListingRestrictions({ asin: "B0FAIL", seller_id: "S1" }, spApi, cacheFor())
    ).rejects.toThrow("SP-API 503");
  });
});
