import type { SpApiService } from "../services/sp-api.js";
import type { DiskCache } from "../services/disk-cache.js";
import {
  UK_MARKETPLACE_ID,
  type ListingRestrictionsResult,
  type RestrictionReason,
  type RestrictionStatus,
} from "../types.js";

export interface CheckListingRestrictionsInput {
  asin: string;
  seller_id: string;
  marketplace_id?: string;
  condition_type?: string;
  refresh_cache?: boolean;
}

interface SpApiRestrictionLink {
  resource?: string;
  title?: string;
  type?: string;
  verb?: string;
}

interface SpApiRestrictionReason {
  message?: string;
  reasonCode?: string;
  links?: SpApiRestrictionLink[];
}

interface SpApiRestriction {
  asin?: string;
  conditionType?: string;
  reasons?: SpApiRestrictionReason[];
}

interface SpApiRestrictionsResponse {
  restrictions?: SpApiRestriction[];
}

const BRAND_HINTS = /\bbrand\b/i;
const CATEGORY_HINTS = /\bcateg(ory|ories)\b|\bsubcategory\b/i;

// Classify a single SP-API reason. Prefers structured `reasonCode` over
// free-text matching. CATEGORY checked before BRAND in the message
// fallback because if both keywords appear in one reason, category
// framing typically dominates ("approval required for brand X in
// category Y" describes a category gate the seller must clear).
function classifyReason(r: RestrictionReason): RestrictionStatus | "UNKNOWN" {
  if (r.reasonCode === "ASIN_NOT_IN_PRODUCT_GROUP") return "CATEGORY_GATED";
  const msg = r.message ?? "";
  if (CATEGORY_HINTS.test(msg)) return "CATEGORY_GATED";
  if (BRAND_HINTS.test(msg)) return "BRAND_GATED";
  return "UNKNOWN";
}

function classify(reasons: RestrictionReason[]): RestrictionStatus {
  if (reasons.length === 0) return "UNRESTRICTED";
  // Per-reason classification, then aggregate. Each SP-API reason
  // typically describes ONE gate type — the previous blob-join approach
  // let a "brand" word in one reason mis-tag a category-gated ASIN.
  // Aggregation rule: any CATEGORY signal wins; otherwise BRAND if
  // any reason flagged it; otherwise generic RESTRICTED.
  let result: RestrictionStatus = "RESTRICTED";
  for (const r of reasons) {
    const c = classifyReason(r);
    if (c === "CATEGORY_GATED") return "CATEGORY_GATED";
    if (c === "BRAND_GATED") result = "BRAND_GATED";
  }
  return result;
}

function normalise(
  asin: string,
  marketplaceId: string,
  raw: SpApiRestrictionsResponse
): { result: ListingRestrictionsResult; wellFormed: boolean } {
  const wellFormed = !!raw && Array.isArray(raw.restrictions);
  const restrictions = raw?.restrictions ?? [];
  const reasons: RestrictionReason[] = restrictions.flatMap((r) =>
    (r.reasons ?? []).map((reason) => ({
      message: reason.message ?? "",
      reasonCode: reason.reasonCode,
      link: reason.links?.[0]?.resource,
    }))
  );
  const status = classify(reasons);
  const approval_required =
    reasons.length > 0 &&
    reasons.some(
      (r) =>
        r.reasonCode === "APPROVAL_REQUIRED" || r.reasonCode === undefined
    );
  return {
    result: {
      asin,
      status,
      reasons,
      approval_required:
        status === "UNRESTRICTED" ? false : approval_required,
      marketplace_id: marketplaceId,
      raw,
    },
    wellFormed,
  };
}

export async function checkListingRestrictions(
  input: CheckListingRestrictionsInput,
  spApi: SpApiService,
  cache?: DiskCache<ListingRestrictionsResult>
): Promise<ListingRestrictionsResult> {
  const marketplaceId = input.marketplace_id ?? UK_MARKETPLACE_ID;
  const conditionType = input.condition_type ?? "new_new";
  const cacheKey = [
    input.seller_id,
    marketplaceId,
    conditionType,
    input.asin,
  ];

  if (cache && !input.refresh_cache) {
    const hit = cache.get(...cacheKey);
    if (hit.hit && hit.data) return hit.data;
  }

  try {
    const raw = (await spApi.getListingsRestrictions({
      asin: input.asin,
      sellerId: input.seller_id,
      marketplaceId,
      conditionType,
    })) as SpApiRestrictionsResponse;
    const { result, wellFormed } = normalise(input.asin, marketplaceId, raw);
    // Skip cache write if SP-API returned a malformed/empty response, so a
    // transient upstream issue isn't pinned at UNRESTRICTED for 7 days.
    if (wellFormed) cache?.set(cacheKey, { data: result });
    return result;
  } catch (err) {
    if (cache) {
      const stale = cache.get(...cacheKey);
      if (stale.stale && stale.data) {
        return { ...stale.data, raw: { ...stale.data.raw as object, stale: true } };
      }
    }
    throw err;
  }
}
