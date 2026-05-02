import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { getCatalogItem } from "./get-catalog-item.js";
import { DiskCache } from "../services/disk-cache.js";
import type { SpApiService } from "../services/sp-api.js";
import type { CatalogItemResult } from "../types.js";

const UK = "A1F83G8C2ARO7P";

function mockSpApi(response: unknown, opts: { failAfter?: number } = {}): SpApiService {
  let calls = 0;
  return {
    getCatalogItemFull: vi.fn().mockImplementation(async () => {
      calls++;
      if (opts.failAfter !== undefined && calls > opts.failAfter) {
        throw new Error("SP-API 503");
      }
      return response;
    }),
  } as unknown as SpApiService;
}

describe("getCatalogItem", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "catalog-test-"));
  });

  afterEach(() => {
    if (existsSync(tmp)) rmSync(tmp, { recursive: true, force: true });
  });

  const cacheFor = () =>
    new DiskCache<CatalogItemResult>({
      resource: "catalog",
      defaultTtlSeconds: 3600,
      cacheRoot: tmp,
    });

  it("extracts title, brand, manufacturer from summaries", async () => {
    const spApi = mockSpApi({
      summaries: [
        {
          marketplaceId: UK,
          itemName: "Acme Hair Dryer 2000",
          brandName: "Acme",
          manufacturer: "Acme Industries",
        },
      ],
    });
    const result = await getCatalogItem({ asin: "B0AC" }, spApi);
    expect(result.title).toBe("Acme Hair Dryer 2000");
    expect(result.brand).toBe("Acme");
    expect(result.manufacturer).toBe("Acme Industries");
    expect(result.marketplace_id).toBe(UK);
  });

  it("picks the summary matching marketplace_id when multiple are returned", async () => {
    const spApi = mockSpApi({
      summaries: [
        { marketplaceId: "ATVPDKIKX0DER", itemName: "US Title", brandName: "USBrand" },
        { marketplaceId: UK, itemName: "UK Title", brandName: "UKBrand" },
      ],
    });
    const result = await getCatalogItem({ asin: "B0MM" }, spApi);
    expect(result.title).toBe("UK Title");
    expect(result.brand).toBe("UKBrand");
  });

  it("extracts item dimensions and unit", async () => {
    const spApi = mockSpApi({
      summaries: [{ marketplaceId: UK, itemName: "X" }],
      dimensions: [
        {
          marketplaceId: UK,
          item: {
            length: { unit: "centimeters", value: 30 },
            width: { unit: "centimeters", value: 20 },
            height: { unit: "centimeters", value: 10 },
            weight: { unit: "kilograms", value: 1.5 },
          },
        },
      ],
    });
    const result = await getCatalogItem({ asin: "B0DIM" }, spApi);
    expect(result.dimensions).toEqual({
      length: 30,
      width: 20,
      height: 10,
      weight: 1.5,
      unit: "centimeters/kilograms",
    });
  });

  it("falls back to package dimensions if no item dimensions", async () => {
    const spApi = mockSpApi({
      dimensions: [
        {
          marketplaceId: UK,
          package: {
            length: { unit: "inches", value: 5 },
            weight: { unit: "pounds", value: 0.5 },
          },
        },
      ],
    });
    const result = await getCatalogItem({ asin: "B0PKG" }, spApi);
    expect(result.dimensions?.length).toBe(5);
    expect(result.dimensions?.weight).toBe(0.5);
  });

  it("detects hazmat from supplier_declared_dg_hz_regulation attribute", async () => {
    const spApi = mockSpApi({
      attributes: {
        supplier_declared_dg_hz_regulation: [
          { value: "ghs", marketplace_id: UK, language_tag: "en_GB" },
        ],
      },
    });
    const result = await getCatalogItem({ asin: "B0HAZ" }, spApi);
    expect(result.hazmat).toBe(true);
  });

  it("treats storage_non_dangerous_goods as NOT hazmat", async () => {
    const spApi = mockSpApi({
      attributes: {
        supplier_declared_dg_hz_regulation: [
          { value: "storage_non_dangerous_goods", marketplace_id: UK },
        ],
      },
    });
    const result = await getCatalogItem({ asin: "B0SAFE" }, spApi);
    expect(result.hazmat).toBeUndefined();
  });

  it("treats 'none' / 'non_dangerous' / 'non_dangerous_goods' as NOT hazmat", async () => {
    // Regression: previously the deny-list missed these tokens and
    // any non-empty value (including these explicit negatives) flagged
    // as hazmat — a false positive that steers the seller away from
    // safe products.
    for (const negative of ["none", "non_dangerous", "non_dangerous_goods"]) {
      const spApi = mockSpApi({
        attributes: {
          supplier_declared_dg_hz_regulation: [
            { value: negative, marketplace_id: UK },
          ],
        },
      });
      const result = await getCatalogItem({ asin: `B0${negative}` }, spApi);
      expect(result.hazmat, `value=${negative}`).toBeUndefined();
    }
  });

  it("hazmat is undefined (not false) when no relevant attributes are present", async () => {
    const spApi = mockSpApi({
      attributes: { color: [{ value: "red" }] },
    });
    const result = await getCatalogItem({ asin: "B0NA" }, spApi);
    expect(result.hazmat).toBeUndefined();
  });

  it("extracts classifications and images for the requested marketplace", async () => {
    const spApi = mockSpApi({
      classifications: [
        {
          marketplaceId: UK,
          classifications: [
            { classificationId: "1234", displayName: "Beauty" },
            { classificationId: "5678", displayName: "Hair Care" },
          ],
        },
      ],
      images: [
        {
          marketplaceId: UK,
          images: [
            { variant: "MAIN", link: "https://m.media-amazon.com/main.jpg", height: 500, width: 500 },
            { variant: "PT01", link: "https://m.media-amazon.com/alt.jpg" },
          ],
        },
      ],
    });
    const result = await getCatalogItem({ asin: "B0IMG" }, spApi);
    expect(result.classifications).toEqual([
      { classificationId: "1234", displayName: "Beauty" },
      { classificationId: "5678", displayName: "Hair Care" },
    ]);
    expect(result.images).toHaveLength(2);
    expect(result.images?.[0].link).toBe("https://m.media-amazon.com/main.jpg");
  });

  it("returns undefined for missing optional fields", async () => {
    const spApi = mockSpApi({}); // empty response
    const result = await getCatalogItem({ asin: "B0EMP" }, spApi);
    expect(result.asin).toBe("B0EMP");
    expect(result.title).toBeUndefined();
    expect(result.brand).toBeUndefined();
    expect(result.dimensions).toBeUndefined();
    expect(result.hazmat).toBeUndefined();
    expect(result.classifications).toBeUndefined();
    expect(result.images).toBeUndefined();
  });

  it("uses cache on second call", async () => {
    const spApi = mockSpApi({ summaries: [{ marketplaceId: UK, itemName: "X" }] });
    const cache = cacheFor();
    await getCatalogItem({ asin: "B0CACHE" }, spApi, cache);
    await getCatalogItem({ asin: "B0CACHE" }, spApi, cache);
    expect(spApi.getCatalogItemFull).toHaveBeenCalledTimes(1);
  });

  it("bypasses cache when refresh_cache is true", async () => {
    const spApi = mockSpApi({ summaries: [] });
    const cache = cacheFor();
    await getCatalogItem({ asin: "B0R" }, spApi, cache);
    await getCatalogItem({ asin: "B0R", refresh_cache: true }, spApi, cache);
    expect(spApi.getCatalogItemFull).toHaveBeenCalledTimes(2);
  });

  it("populates listing-quality signals (image_count, has_aplus_content, release_date)", async () => {
    // Listing-quality signals (PR D — operator-validator-fidelity sweep).
    // image_count derived from images array length; has_aplus_content
    // detected from attributes; release_date copied from summary.
    const spApi = mockSpApi({
      summaries: [
        {
          marketplaceId: UK,
          itemName: "Quality Listing",
          releaseDate: "2022-06-15T00:00:00Z",
        },
      ],
      images: [
        {
          marketplaceId: UK,
          images: [
            { variant: "MAIN", link: "https://m.media-amazon.com/i1.jpg" },
            { variant: "PT01", link: "https://m.media-amazon.com/i2.jpg" },
            { variant: "PT02", link: "https://m.media-amazon.com/i3.jpg" },
          ],
        },
      ],
      attributes: {
        a_plus_content: [
          { value: "<p>Brand story</p>", marketplace_id: UK },
        ],
      },
    });
    const result = await getCatalogItem({ asin: "B0QUAL" }, spApi);
    expect(result.image_count).toBe(3);
    expect(result.has_aplus_content).toBe(true);
    expect(result.release_date).toBe("2022-06-15T00:00:00Z");
  });

  it("listing-quality signals stay undefined when not in SP-API response", async () => {
    // Bare summary (common for older listings) — fields stay undefined
    // rather than crashing. The Python preflight reader propagates None
    // and the validator treats absence as "signal missing", not "bad".
    const spApi = mockSpApi({
      summaries: [{ marketplaceId: UK, itemName: "Bare" }],
    });
    const result = await getCatalogItem({ asin: "B0BARE" }, spApi);
    expect(result.image_count).toBeUndefined();
    expect(result.has_aplus_content).toBeUndefined();
    expect(result.release_date).toBeUndefined();
  });

  it("serves stale cache when SP-API errors", async () => {
    const spApi = mockSpApi(
      { summaries: [{ marketplaceId: UK, itemName: "Stale Title" }] },
      { failAfter: 1 }
    );
    const cache = cacheFor();
    await getCatalogItem({ asin: "B0STL" }, spApi, cache);
    const path = join(tmp, "catalog", `${UK}__B0STL.json`);
    const fs = await import("fs");
    const entry = JSON.parse(fs.readFileSync(path, "utf8"));
    entry.fetched_at = new Date(Date.now() - 999_999_999).toISOString();
    fs.writeFileSync(path, JSON.stringify(entry));
    const result = await getCatalogItem({ asin: "B0STL" }, spApi, cache);
    expect(result.title).toBe("Stale Title");
    expect((result.raw as { stale?: boolean }).stale).toBe(true);
  });
});
