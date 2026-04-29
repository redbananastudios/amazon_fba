import type { SpApiService } from "../services/sp-api.js";
import type { DiskCache } from "../services/disk-cache.js";
import {
  UK_MARKETPLACE_ID,
  type BatchFeeItem,
  type BatchFeeResultEntry,
  type FeeEstimate,
} from "../types.js";

export interface EstimateFeesBatchInput {
  items: BatchFeeItem[];
  refresh_cache?: boolean;
}

interface SpApiFeeAmount {
  Amount?: string | number;
  CurrencyCode?: string;
}

interface SpApiFeeDetail {
  FeeType?: string;
  FeeAmount?: SpApiFeeAmount;
}

interface SpApiFeesEstimate {
  TotalFeesEstimate?: SpApiFeeAmount;
  FeeDetailList?: SpApiFeeDetail[];
}

interface SpApiFeesEstimateResult {
  FeesEstimateIdentifier?: {
    SellerId?: string;
    IdType?: string;
    SellerInputIdentifier?: string;
    MarketplaceId?: string;
    IdValue?: string;
  };
  FeesEstimate?: SpApiFeesEstimate;
  Status?: string;
  Error?: { Type?: string; Code?: string; Message?: string };
}

interface SpApiBatchResponseEntry {
  FeesEstimateResult?: SpApiFeesEstimateResult;
  // amazon-sp-api occasionally returns the fields directly on the entry.
  FeesEstimateIdentifier?: SpApiFeesEstimateResult["FeesEstimateIdentifier"];
  FeesEstimate?: SpApiFeesEstimate;
  Status?: string;
  Error?: SpApiFeesEstimateResult["Error"];
}

function parseAmount(amt?: SpApiFeeAmount): number {
  if (!amt || amt.Amount === undefined || amt.Amount === null) return 0;
  const n =
    typeof amt.Amount === "string" ? parseFloat(amt.Amount) : amt.Amount;
  return Number.isFinite(n) ? n : 0;
}

function findFee(details: SpApiFeeDetail[] | undefined, type: string): number {
  if (!details) return 0;
  const fee = details.find((f) => f.FeeType === type);
  return fee ? parseAmount(fee.FeeAmount) : 0;
}

function makeIdentifier(asin: string, sellingPrice: number, idx: number): string {
  return `${asin}-${sellingPrice}-${idx}`;
}

function cacheKeyFor(item: BatchFeeItem, marketplaceId: string): string[] {
  // Bucket by 0.01 GBP — selling price is the dominant input to fees.
  const priceBucket = Math.round(item.selling_price * 100).toString();
  return [marketplaceId, item.asin, priceBucket];
}

function buildErrorEntry(
  asin: string,
  identifier: string,
  message: string
): BatchFeeResultEntry {
  return {
    asin,
    identifier,
    ok: false,
    error: message,
  };
}

function buildOkEntry(
  asin: string,
  identifier: string,
  fees: FeeEstimate
): BatchFeeResultEntry {
  return {
    asin,
    identifier,
    ok: true,
    fees,
  };
}

export async function estimateFeesBatch(
  input: EstimateFeesBatchInput,
  spApi: SpApiService,
  cache?: DiskCache<FeeEstimate>
): Promise<BatchFeeResultEntry[]> {
  if (input.items.length === 0) return [];
  if (input.items.length > 20) {
    throw new Error(
      `estimate_fees_batch: max 20 items per call, got ${input.items.length}`
    );
  }

  const results: BatchFeeResultEntry[] = new Array(input.items.length);
  const toFetch: Array<{ idx: number; identifier: string; marketplaceId: string }> = [];

  // Cache pass.
  input.items.forEach((item, idx) => {
    const marketplaceId = item.marketplace_id ?? UK_MARKETPLACE_ID;
    const identifier =
      item.identifier ?? makeIdentifier(item.asin, item.selling_price, idx);
    if (cache && !input.refresh_cache) {
      const hit = cache.get(...cacheKeyFor(item, marketplaceId));
      if (hit.hit && hit.data) {
        results[idx] = buildOkEntry(item.asin, identifier, hit.data);
        return;
      }
    }
    toFetch.push({ idx, identifier, marketplaceId });
  });

  if (toFetch.length === 0) return results;

  const fetchItems = toFetch.map(({ idx, identifier, marketplaceId }) => {
    const item = input.items[idx];
    return {
      asin: item.asin,
      sellingPrice: item.selling_price,
      marketplaceId,
      identifier,
    };
  });

  let raw: unknown;
  try {
    raw = await spApi.getMyFeesEstimates(fetchItems);
  } catch (err) {
    const message = (err as Error).message ?? "SP-API error";
    toFetch.forEach(({ idx, identifier }) => {
      const item = input.items[idx];
      results[idx] = buildErrorEntry(item.asin, identifier, message);
    });
    return results;
  }

  // Normalise: amazon-sp-api may return either an array of FeesEstimateResult
  // wrappers, or an array of unwrapped entries. Also identifiers may come back
  // either via FeesEstimateIdentifier.SellerInputIdentifier or .IdValue.
  const arr: SpApiBatchResponseEntry[] = Array.isArray(raw)
    ? (raw as SpApiBatchResponseEntry[])
    : [];

  // Build a lookup by identifier so we don't depend on response order.
  const byIdentifier = new Map<string, SpApiBatchResponseEntry>();
  for (const entry of arr) {
    const inner = entry.FeesEstimateResult ?? entry;
    const id =
      inner.FeesEstimateIdentifier?.SellerInputIdentifier ??
      inner.FeesEstimateIdentifier?.IdValue;
    if (id) byIdentifier.set(id, entry);
  }

  toFetch.forEach(({ idx, identifier, marketplaceId }, posInFetch) => {
    const item = input.items[idx];
    // SP-API normally returns entries in the same order as the request.
    // Prefer identifier match when present, fall back to positional.
    const entry = byIdentifier.get(identifier) ?? arr[posInFetch];
    if (!entry) {
      results[idx] = buildErrorEntry(
        item.asin,
        identifier,
        "No fee estimate returned for this item"
      );
      return;
    }
    const inner = entry.FeesEstimateResult ?? entry;
    if (inner.Status && inner.Status !== "Success") {
      const err =
        inner.Error?.Message ??
        inner.Error?.Type ??
        `SP-API status: ${inner.Status}`;
      results[idx] = buildErrorEntry(item.asin, identifier, err);
      return;
    }
    const fe = inner.FeesEstimate;
    if (!fe) {
      results[idx] = buildErrorEntry(
        item.asin,
        identifier,
        "Empty FeesEstimate in response"
      );
      return;
    }
    const fees: FeeEstimate = {
      asin: item.asin,
      product_title: "",
      selling_price: item.selling_price,
      referral_fee: findFee(fe.FeeDetailList, "ReferralFee"),
      fba_fulfillment_fee: findFee(fe.FeeDetailList, "FBAFees"),
      closing_fee: findFee(fe.FeeDetailList, "ClosingFee"),
      total_fees: parseAmount(fe.TotalFeesEstimate),
      currency: fe.TotalFeesEstimate?.CurrencyCode ?? "GBP",
    };
    cache?.set(cacheKeyFor(item, marketplaceId), { data: fees });
    results[idx] = buildOkEntry(item.asin, identifier, fees);
  });

  return results;
}
