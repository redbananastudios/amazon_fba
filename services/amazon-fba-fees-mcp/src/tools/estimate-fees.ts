import type { SpApiService } from "../services/sp-api.js";
import type { Cache } from "../services/cache.js";
import { UK_MARKETPLACE_ID, type FeeEstimate } from "../types.js";

interface EstimateFeesInput {
  asin: string;
  selling_price: number;
  marketplace_id?: string;
}

export async function estimateFees(
  input: EstimateFeesInput,
  spApi: SpApiService,
  cache: Cache<FeeEstimate>
): Promise<FeeEstimate> {
  const marketplaceId = input.marketplace_id ?? UK_MARKETPLACE_ID;
  const cacheKey = cache.makeKey(input.asin, input.selling_price, marketplaceId);

  const cached = cache.get(cacheKey);
  if (cached) return cached;

  const fees = await spApi.getFeesAndTitle(
    input.asin,
    input.selling_price,
    marketplaceId
  );

  const result: FeeEstimate = {
    asin: input.asin,
    product_title: fees.product_title,
    selling_price: input.selling_price,
    referral_fee: fees.referral_fee,
    fba_fulfillment_fee: fees.fba_fulfillment_fee,
    closing_fee: fees.closing_fee,
    total_fees: fees.total_fees,
    currency: fees.currency,
  };

  cache.set(cacheKey, result);
  return result;
}
