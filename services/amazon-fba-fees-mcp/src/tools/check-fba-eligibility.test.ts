import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { checkFbaEligibility } from "./check-fba-eligibility.js";
import { DiskCache } from "../services/disk-cache.js";
import type { SpApiService } from "../services/sp-api.js";
import type { FbaEligibilityResult } from "../types.js";

function mockSpApi(response: unknown, opts: { failAfter?: number } = {}): SpApiService {
  let calls = 0;
  return {
    getItemEligibilityPreview: vi.fn().mockImplementation(async () => {
      calls++;
      if (opts.failAfter !== undefined && calls > opts.failAfter) {
        throw new Error("SP-API 503");
      }
      return response;
    }),
  } as unknown as SpApiService;
}

describe("checkFbaEligibility", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "fba-elig-test-"));
  });

  afterEach(() => {
    if (existsSync(tmp)) rmSync(tmp, { recursive: true, force: true });
  });

  const cacheFor = () =>
    new DiskCache<FbaEligibilityResult>({
      resource: "fba_eligibility",
      defaultTtlSeconds: 3600,
      cacheRoot: tmp,
    });

  it("returns eligible=true with empty reasons for an OK ASIN", async () => {
    const spApi = mockSpApi({
      payload: {
        asin: "B0OK",
        marketplaceId: "A1F83G8C2ARO7P",
        program: "INBOUND",
        isEligibleForProgram: true,
        ineligibilityReasonList: [],
      },
    });
    const result = await checkFbaEligibility({ asin: "B0OK" }, spApi);
    expect(result.eligible).toBe(true);
    expect(result.ineligibility_reasons).toEqual([]);
    expect(result.marketplace_id).toBe("A1F83G8C2ARO7P");
    expect(result.program).toBe("INBOUND");
  });

  it("maps ineligibility codes to human descriptions", async () => {
    const spApi = mockSpApi({
      payload: {
        isEligibleForProgram: false,
        ineligibilityReasonList: ["FBA_INB_0019", "FBA_INB_0010"],
      },
    });
    const result = await checkFbaEligibility({ asin: "B0HAZ" }, spApi);
    expect(result.eligible).toBe(false);
    expect(result.ineligibility_reasons).toEqual([
      { code: "FBA_INB_0019", description: "ASIN is hazmat — review/approval required" },
      { code: "FBA_INB_0010", description: "ASIN is missing required dimensions or weight" },
    ]);
  });

  it("falls back to code as description for unknown codes", async () => {
    const spApi = mockSpApi({
      payload: {
        isEligibleForProgram: false,
        ineligibilityReasonList: ["FBA_INB_9999"],
      },
    });
    const result = await checkFbaEligibility({ asin: "B0NEW" }, spApi);
    expect(result.ineligibility_reasons[0]).toEqual({
      code: "FBA_INB_9999",
      description: "FBA_INB_9999",
    });
  });

  it("handles unwrapped response (no payload wrapper)", async () => {
    const spApi = mockSpApi({
      isEligibleForProgram: true,
      ineligibilityReasonList: [],
    });
    const result = await checkFbaEligibility({ asin: "B0FLAT" }, spApi);
    expect(result.eligible).toBe(true);
    expect(result.ineligibility_reasons).toEqual([]);
  });

  it("respects program override and passes it to SP-API", async () => {
    const spApi = mockSpApi({ payload: { isEligibleForProgram: true } });
    await checkFbaEligibility({ asin: "B0COMM", program: "COMMINGLED" }, spApi);
    expect(spApi.getItemEligibilityPreview).toHaveBeenCalledWith(
      expect.objectContaining({ program: "COMMINGLED" })
    );
  });

  it("uses cache on second call", async () => {
    const spApi = mockSpApi({ payload: { isEligibleForProgram: true } });
    const cache = cacheFor();
    await checkFbaEligibility({ asin: "B0CACHE" }, spApi, cache);
    await checkFbaEligibility({ asin: "B0CACHE" }, spApi, cache);
    expect(spApi.getItemEligibilityPreview).toHaveBeenCalledTimes(1);
  });

  it("bypasses cache when refresh_cache is true", async () => {
    const spApi = mockSpApi({ payload: { isEligibleForProgram: true } });
    const cache = cacheFor();
    await checkFbaEligibility({ asin: "B0R" }, spApi, cache);
    await checkFbaEligibility({ asin: "B0R", refresh_cache: true }, spApi, cache);
    expect(spApi.getItemEligibilityPreview).toHaveBeenCalledTimes(2);
  });

  it("scopes cache by program (different program misses)", async () => {
    const spApi = mockSpApi({ payload: { isEligibleForProgram: true } });
    const cache = cacheFor();
    await checkFbaEligibility({ asin: "B0P", program: "INBOUND" }, spApi, cache);
    await checkFbaEligibility({ asin: "B0P", program: "COMMINGLED" }, spApi, cache);
    expect(spApi.getItemEligibilityPreview).toHaveBeenCalledTimes(2);
  });

  it("serves stale cache when SP-API errors", async () => {
    const spApi = mockSpApi(
      { payload: { isEligibleForProgram: true, ineligibilityReasonList: [] } },
      { failAfter: 1 }
    );
    const cache = cacheFor();
    await checkFbaEligibility({ asin: "B0STL" }, spApi, cache);
    const path = join(tmp, "fba_eligibility", "A1F83G8C2ARO7P__INBOUND__B0STL.json");
    const fs = await import("fs");
    const entry = JSON.parse(fs.readFileSync(path, "utf8"));
    entry.fetched_at = new Date(Date.now() - 999_999_999).toISOString();
    fs.writeFileSync(path, JSON.stringify(entry));
    const result = await checkFbaEligibility({ asin: "B0STL" }, spApi, cache);
    expect(result.eligible).toBe(true);
    expect((result.raw as { stale?: boolean }).stale).toBe(true);
  });

  it("propagates error when no stale cache available", async () => {
    const spApi = mockSpApi({}, { failAfter: 0 });
    await expect(
      checkFbaEligibility({ asin: "B0FAIL" }, spApi, cacheFor())
    ).rejects.toThrow("SP-API 503");
  });
});
