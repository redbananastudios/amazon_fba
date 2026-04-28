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

function classify(reasons: RestrictionReason[]): RestrictionStatus {
  if (reasons.length === 0) return "UNRESTRICTED";
  const blob = reasons
    .map((r) => `${r.message ?? ""} ${r.reasonCode ?? ""}`)
    .join(" ");
  if (BRAND_HINTS.test(blob)) return "BRAND_GATED";
  if (CATEGORY_HINTS.test(blob)) return "CATEGORY_GATED";
  return "RESTRICTED";
}

function normalise(
  asin: string,
  marketplaceId: string,
  raw: SpApiRestrictionsResponse
): ListingRestrictionsResult {
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
    asin,
    status,
    reasons,
    approval_required: status === "UNRESTRICTED" ? false : approval_required,
    marketplace_id: marketplaceId,
    raw,
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
    const result = normalise(input.asin, marketplaceId, raw);
    cache?.set(cacheKey, result);
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
