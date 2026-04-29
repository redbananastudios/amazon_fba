/**
 * Read-only smoke tests against the live SP-API. These verify the wrapper
 * actually hits the right endpoint and gets back a non-empty response.
 *
 * They DO consume real rate-limit budget. Keep them light — one ASIN per
 * endpoint, no assertions on specific values that change over time.
 *
 * SKIPPED automatically when SP_API_CLIENT_ID is unset, so a fresh clone or
 * CI without secrets doesn't fail.
 *
 * Test ASIN: B07XJ8C8F5 (Kindle Paperwhite, has been on UK marketplace for
 * years). Override via the INTEGRATION_TEST_ASIN env var.
 */
import { describe, it, expect } from "vitest";
import { SpApiService } from "../services/sp-api.js";
import { checkListingRestrictions } from "../tools/check-listing-restrictions.js";
import { checkFbaEligibility } from "../tools/check-fba-eligibility.js";
import { estimateFeesBatch } from "../tools/estimate-fees-batch.js";
import { getCatalogItem } from "../tools/get-catalog-item.js";
import { getLivePricing } from "../tools/get-live-pricing.js";

const HAVE_CREDS = Boolean(process.env.SP_API_CLIENT_ID);
// Restrictions tests need both credentials AND a seller_id.
const HAVE_SELLER = HAVE_CREDS && Boolean(process.env.SP_API_SELLER_ID);
const TEST_ASIN = process.env.INTEGRATION_TEST_ASIN ?? "B07XJ8C8F5";
const UK = "A1F83G8C2ARO7P";

const skipIfNoCreds = HAVE_CREDS ? describe : describe.skip;
const skipIfNoSeller = HAVE_SELLER ? describe : describe.skip;

function makeService(): SpApiService {
  return new SpApiService({
    clientId: process.env.SP_API_CLIENT_ID!,
    clientSecret: process.env.SP_API_CLIENT_SECRET!,
    refreshToken: process.env.SP_API_REFRESH_TOKEN!,
  });
}

skipIfNoCreds("SP-API live (catalog)", () => {
  it("returns a non-empty catalog payload for a known ASIN", async () => {
    const result = await getCatalogItem(
      { asin: TEST_ASIN, marketplace_id: UK },
      makeService()
    );
    expect(result.asin).toBe(TEST_ASIN);
    // Title should be set for any ASIN that exists.
    expect(typeof result.title).toBe("string");
    expect((result.title ?? "").length).toBeGreaterThan(0);
  });
});

skipIfNoCreds("SP-API live (fees batch)", () => {
  it("returns a fee estimate for a known ASIN", async () => {
    const result = await estimateFeesBatch(
      { items: [{ asin: TEST_ASIN, selling_price: 99.99 }] },
      makeService()
    );
    expect(result).toHaveLength(1);
    if (result[0].ok) {
      expect(result[0].fees?.total_fees).toBeGreaterThan(0);
      expect(result[0].fees?.referral_fee).toBeGreaterThan(0);
    } else {
      // SP-API may legitimately reject some ASINs (e.g. restricted from fees).
      // Don't fail the test — just surface the error shape is correct.
      expect(result[0].error).toBeTruthy();
    }
  });
});

skipIfNoCreds("SP-API live (FBA eligibility)", () => {
  it("returns eligibility (true or false) for a known ASIN", async () => {
    const result = await checkFbaEligibility(
      { asin: TEST_ASIN, marketplace_id: UK },
      makeService()
    );
    expect(result.asin).toBe(TEST_ASIN);
    expect(typeof result.eligible).toBe("boolean");
  });
});

skipIfNoCreds("SP-API live (live pricing)", () => {
  it("returns offers data for a known ASIN", async () => {
    const result = await getLivePricing(
      { asins: [TEST_ASIN], marketplace_id: UK },
      makeService()
    );
    expect(result).toHaveLength(1);
    expect(result[0].asin).toBe(TEST_ASIN);
    // Either we got Buy Box data or a structured empty result —
    // both are acceptable. We're verifying the wrapper round-trips.
    expect(result[0].marketplace_id).toBe(UK);
  });
});

skipIfNoSeller("SP-API live (listing restrictions)", () => {
  it("returns restriction status for a known ASIN", async () => {
    const result = await checkListingRestrictions(
      {
        asin: TEST_ASIN,
        seller_id: process.env.SP_API_SELLER_ID!,
        marketplace_id: UK,
      },
      makeService()
    );
    expect(result.asin).toBe(TEST_ASIN);
    expect([
      "UNRESTRICTED",
      "RESTRICTED",
      "BRAND_GATED",
      "CATEGORY_GATED",
    ]).toContain(result.status);
  });
});
