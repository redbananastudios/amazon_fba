import { describe, it, expect, vi } from "vitest";
import { resolveProductCodes } from "./resolve-product-codes.js";
import type { SpApiService } from "../services/sp-api.js";

const UK = "A1F83G8C2ARO7P";

function mockSpApi(response: unknown): SpApiService {
  return {
    searchCatalogItems: vi.fn().mockResolvedValue(response),
  } as unknown as SpApiService;
}

describe("resolveProductCodes", () => {
  it("resolves a single UPC result to an ASIN", async () => {
    const spApi = mockSpApi({
      items: [
        {
          asin: "B0UPC001",
          summaries: [{ marketplaceId: UK, itemName: "UPC Widget", brandName: "Acme" }],
          identifiers: {
            identifiers: [{ identifierType: "UPC", identifier: "856739001159" }],
          },
        },
      ],
    });

    const out = await resolveProductCodes({ codes: ["856739001159"] }, spApi);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({
      code: "856739001159",
      status: "FOUND",
      asin: "B0UPC001",
      title: "UPC Widget",
      brand: "Acme",
    });
    expect(spApi.searchCatalogItems).toHaveBeenCalledWith({
      keywords: ["856739001159"],
      marketplaceId: UK,
      includedData: ["summaries", "identifiers"],
      pageSize: 10,
    });
  });

  it("matches UPC against EAN leading-zero aliases", async () => {
    const spApi = mockSpApi({
      items: [
        {
          asin: "B0EAN001",
          identifiers: {
            marketplaceASIN: {
              marketplaceId: UK,
              identifiers: [{ identifierType: "EAN", identifier: "0856739001159" }],
            },
          },
        },
      ],
    });

    const out = await resolveProductCodes({ codes: ["856739001159"] }, spApi);
    expect(out[0].status).toBe("FOUND");
    expect(out[0].asin).toBe("B0EAN001");
  });

  it("does not auto-select when multiple ASINs match", async () => {
    const spApi = mockSpApi({
      items: [
        { asin: "B0ONE", identifiers: { x: [{ identifier: "856739001159" }] } },
        { asin: "B0TWO", identifiers: { x: [{ identifier: "856739001159" }] } },
      ],
    });

    const out = await resolveProductCodes({ codes: ["856739001159"] }, spApi);
    expect(out[0].status).toBe("MULTIPLE_MATCHES");
    expect(out[0].asins).toEqual(["B0ONE", "B0TWO"]);
    expect(out[0].asin).toBeUndefined();
  });

  it("returns NO_MATCH when no catalog items are found", async () => {
    const spApi = mockSpApi({ items: [] });
    const out = await resolveProductCodes({ codes: ["856739001159"] }, spApi);
    expect(out[0].status).toBe("NO_MATCH");
  });
});
