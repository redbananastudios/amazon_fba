import type { SpApiService } from "../services/sp-api.js";
import type { DiskCache } from "../services/disk-cache.js";
import {
  UK_MARKETPLACE_ID,
  type FbaEligibilityIneligibilityReason,
  type FbaEligibilityResult,
} from "../types.js";

export interface CheckFbaEligibilityInput {
  asin: string;
  marketplace_id?: string;
  program?: string;
  refresh_cache?: boolean;
}

interface SpApiEligibilityResponse {
  payload?: {
    asin?: string;
    marketplaceId?: string;
    program?: string;
    isEligibleForProgram?: boolean;
    ineligibilityReasonList?: string[];
  };
  // amazon-sp-api sometimes unwraps payload — handle both shapes.
  isEligibleForProgram?: boolean;
  ineligibilityReasonList?: string[];
}

// Most-common SP-API FBA inbound ineligibility codes mapped to human
// descriptions. Unknown codes fall back to the code itself so callers
// always get a non-empty description.
const REASON_DESCRIPTIONS: Record<string, string> = {
  FBA_INB_0004: "ASIN not found in catalog",
  FBA_INB_0006: "ASIN is not eligible for this program",
  FBA_INB_0007: "ASIN is restricted (gated)",
  FBA_INB_0008: "ASIN is restricted from this program",
  FBA_INB_0009: "Storage type unavailable for this ASIN",
  FBA_INB_0010: "ASIN is missing required dimensions or weight",
  FBA_INB_0011: "ASIN is missing required attributes",
  FBA_INB_0019: "ASIN is hazmat — review/approval required",
  FBA_INB_0034: "Country of origin / country eligibility issue",
  FBA_INB_0050: "Generic eligibility issue",
  FBA_INB_0051: "Cannot fulfil — ASIN ineligible for FBA",
  FBA_INB_0053: "ASIN size/weight outside FBA limits",
  FBA_INB_0055: "Hazmat classification pending",
  APPLY_FOR_HAZMAT_REVIEW: "Hazmat review required before listing FBA",
  HAZMAT: "Hazmat — FBA ineligible",
  FBA_INB_0065: "Temperature-sensitive item — FBA ineligible",
};

function describe(code: string): FbaEligibilityIneligibilityReason {
  return {
    code,
    description: REASON_DESCRIPTIONS[code] ?? code,
  };
}

function normalise(
  asin: string,
  marketplaceId: string,
  program: string,
  raw: SpApiEligibilityResponse
): { result: FbaEligibilityResult; wellFormed: boolean } {
  const payload = raw?.payload ?? raw;
  const wellFormed =
    !!payload && typeof payload.isEligibleForProgram === "boolean";
  const eligible = payload?.isEligibleForProgram ?? false;
  const codes = payload?.ineligibilityReasonList ?? [];
  return {
    result: {
      asin,
      eligible,
      ineligibility_reasons: codes.map(describe),
      marketplace_id: marketplaceId,
      program,
      raw,
    },
    wellFormed,
  };
}

export async function checkFbaEligibility(
  input: CheckFbaEligibilityInput,
  spApi: SpApiService,
  cache?: DiskCache<FbaEligibilityResult>
): Promise<FbaEligibilityResult> {
  const marketplaceId = input.marketplace_id ?? UK_MARKETPLACE_ID;
  const program = input.program ?? "INBOUND";
  const cacheKey = [marketplaceId, program, input.asin];

  if (cache && !input.refresh_cache) {
    const hit = cache.get(...cacheKey);
    if (hit.hit && hit.data) return hit.data;
  }

  try {
    const raw = (await spApi.getItemEligibilityPreview({
      asin: input.asin,
      marketplaceId,
      program,
    })) as SpApiEligibilityResponse;
    const { result, wellFormed } = normalise(
      input.asin,
      marketplaceId,
      program,
      raw
    );
    // Only persist a "trusted" answer. A malformed/empty SP-API response
    // would otherwise be cached as eligible=false for 7 days, masking a
    // transient upstream issue. Skipping the write lets the next call retry.
    if (wellFormed) cache?.set(cacheKey, { data: result });
    return result;
  } catch (err) {
    if (cache) {
      const stale = cache.get(...cacheKey);
      if (stale.stale && stale.data) {
        return {
          ...stale.data,
          raw: { ...(stale.data.raw as object), stale: true },
        };
      }
    }
    throw err;
  }
}
