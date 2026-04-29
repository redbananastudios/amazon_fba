import type { SpApiService } from "../services/sp-api.js";
import type { DiskCache } from "../services/disk-cache.js";
import {
  DEFAULT_VAT_RATE,
  UK_MARKETPLACE_ID,
  type CatalogItemResult,
  type FbaEligibilityResult,
  type FeeEstimate,
  type ListingRestrictionsResult,
  type LivePricingResult,
  type PreflightItem,
  type PreflightResult,
  type ProfitabilityResult,
} from "../types.js";
import { checkListingRestrictions } from "./check-listing-restrictions.js";
import { checkFbaEligibility } from "./check-fba-eligibility.js";
import { estimateFeesBatch } from "./estimate-fees-batch.js";
import { getCatalogItem } from "./get-catalog-item.js";
import { getLivePricing } from "./get-live-pricing.js";

export type PreflightSource =
  | "restrictions"
  | "fba"
  | "fees"
  | "catalog"
  | "pricing"
  | "profitability";

const ALL_SOURCES: PreflightSource[] = [
  "restrictions",
  "fba",
  "fees",
  "catalog",
  "pricing",
  "profitability",
];

export interface PreflightInput {
  items: PreflightItem[];
  marketplace_id?: string;
  seller_id?: string;
  include?: PreflightSource[];
  refresh_cache?: boolean;
  vat_registered?: boolean;
  vat_rate?: number;
  shipping_cost?: number;
  // Default false. SP-API responses are large (~250KB/ASIN); a 20-ASIN batch
  // serialises ~5MB through stdout that the Python pipeline ignores entirely.
  // Opt in for direct debugging via the MCP tool surface.
  include_raw?: boolean;
}

export interface PreflightDeps {
  spApi: SpApiService;
  caches: {
    restrictions: DiskCache<ListingRestrictionsResult>;
    fbaEligibility: DiskCache<FbaEligibilityResult>;
    fees: DiskCache<FeeEstimate>;
    catalog: DiskCache<CatalogItemResult>;
    pricing: DiskCache<LivePricingResult>;
  };
  defaultSellerId?: string;
}

function emptyResult(item: PreflightItem): PreflightResult {
  return {
    asin: item.asin,
    cached: {},
    errors: [],
  };
}

function priceBucket(price: number): string {
  return Math.round(price * 100).toString();
}

function computeProfitability(
  item: PreflightItem,
  fees: FeeEstimate,
  shippingCost: number,
  vatRegistered: boolean,
  vatRate: number
): ProfitabilityResult {
  const revenueExVat = vatRegistered
    ? item.selling_price / (1 + vatRate)
    : item.selling_price;
  const vatAmount = vatRegistered ? item.selling_price - revenueExVat : 0;
  const profit =
    revenueExVat - item.cost_price - shippingCost - fees.total_fees;
  const totalInvestment = item.cost_price + shippingCost;
  const marginPct = revenueExVat > 0 ? (profit / revenueExVat) * 100 : 0;
  const roiPct = totalInvestment > 0 ? (profit / totalInvestment) * 100 : 0;
  const round2 = (n: number) => Math.round(n * 100) / 100;
  return {
    ...fees,
    revenue_ex_vat: round2(revenueExVat),
    vat_amount: round2(vatAmount),
    cost_price: item.cost_price,
    shipping_cost: shippingCost,
    profit: round2(profit),
    margin_pct: round2(marginPct),
    roi_pct: round2(roiPct),
    vat_registered: vatRegistered,
  };
}

export async function preflightAsin(
  input: PreflightInput,
  deps: PreflightDeps
): Promise<PreflightResult[]> {
  if (input.items.length === 0) return [];
  if (input.items.length > 20) {
    throw new Error(
      `preflight_asin: max 20 items per call, got ${input.items.length}`
    );
  }

  const marketplaceId = input.marketplace_id ?? UK_MARKETPLACE_ID;
  const conditionType = "new_new";
  const fbaProgram = "INBOUND";
  const pricingCondition = "New";
  const include = new Set<PreflightSource>(input.include ?? ALL_SOURCES);
  const sellerId = input.seller_id ?? deps.defaultSellerId;
  const shippingCost = input.shipping_cost ?? 0;
  const vatRegistered = input.vat_registered ?? true;
  const vatRate = input.vat_rate ?? DEFAULT_VAT_RATE;

  const results: PreflightResult[] = input.items.map(emptyResult);

  // Detect cache state up-front (before refresh-cache invalidates anything),
  // so the cached map reflects what would have been served had we honoured cache.
  const peekHit = (
    cache: DiskCache<unknown>,
    ...keyParts: string[]
  ): boolean => cache.get(...keyParts).hit;

  // ── Restrictions ───────────────────────────────────────────────────────
  const restrictionsP =
    include.has("restrictions") && sellerId
      ? Promise.all(
          input.items.map(async (item, idx) => {
            results[idx].cached.restrictions = peekHit(
              deps.caches.restrictions as DiskCache<unknown>,
              sellerId,
              marketplaceId,
              conditionType,
              item.asin
            );
            try {
              results[idx].restrictions = await checkListingRestrictions(
                {
                  asin: item.asin,
                  seller_id: sellerId,
                  marketplace_id: marketplaceId,
                  condition_type: conditionType,
                  refresh_cache: input.refresh_cache,
                },
                deps.spApi,
                deps.caches.restrictions
              );
            } catch (err) {
              results[idx].errors.push({
                source: "restrictions",
                message: (err as Error).message,
              });
            }
          })
        )
      : Promise.resolve();
  if (include.has("restrictions") && !sellerId) {
    results.forEach((r) =>
      r.errors.push({
        source: "restrictions",
        message: "seller_id missing; set SP_API_SELLER_ID or pass seller_id",
      })
    );
  }

  // ── FBA eligibility ────────────────────────────────────────────────────
  const fbaP = include.has("fba")
    ? Promise.all(
        input.items.map(async (item, idx) => {
          results[idx].cached.fba = peekHit(
            deps.caches.fbaEligibility as DiskCache<unknown>,
            marketplaceId,
            fbaProgram,
            item.asin
          );
          try {
            results[idx].fba = await checkFbaEligibility(
              {
                asin: item.asin,
                marketplace_id: marketplaceId,
                program: fbaProgram,
                refresh_cache: input.refresh_cache,
              },
              deps.spApi,
              deps.caches.fbaEligibility
            );
          } catch (err) {
            results[idx].errors.push({
              source: "fba",
              message: (err as Error).message,
            });
          }
        })
      )
    : Promise.resolve();

  // ── Catalog ────────────────────────────────────────────────────────────
  const catalogP = include.has("catalog")
    ? Promise.all(
        input.items.map(async (item, idx) => {
          results[idx].cached.catalog = peekHit(
            deps.caches.catalog as DiskCache<unknown>,
            marketplaceId,
            item.asin
          );
          try {
            results[idx].catalog = await getCatalogItem(
              {
                asin: item.asin,
                marketplace_id: marketplaceId,
                refresh_cache: input.refresh_cache,
              },
              deps.spApi,
              deps.caches.catalog
            );
          } catch (err) {
            results[idx].errors.push({
              source: "catalog",
              message: (err as Error).message,
            });
          }
        })
      )
    : Promise.resolve();

  // ── Fees (batch) ───────────────────────────────────────────────────────
  const feesP =
    include.has("fees") || include.has("profitability")
      ? (async () => {
          input.items.forEach((item, idx) => {
            results[idx].cached.fees = peekHit(
              deps.caches.fees as DiskCache<unknown>,
              marketplaceId,
              item.asin,
              priceBucket(item.selling_price)
            );
          });
          try {
            const batch = await estimateFeesBatch(
              {
                items: input.items.map((i) => ({
                  asin: i.asin,
                  selling_price: i.selling_price,
                  marketplace_id: marketplaceId,
                })),
                refresh_cache: input.refresh_cache,
              },
              deps.spApi,
              deps.caches.fees
            );
            batch.forEach((entry, idx) => {
              if (entry.ok && entry.fees) {
                if (include.has("fees")) results[idx].fees = entry.fees;
                if (include.has("profitability")) {
                  results[idx].profitability = computeProfitability(
                    input.items[idx],
                    entry.fees,
                    shippingCost,
                    vatRegistered,
                    vatRate
                  );
                }
              } else if (entry.error) {
                results[idx].errors.push({
                  source: "fees",
                  message: entry.error,
                });
              }
            });
          } catch (err) {
            // Defensive: estimateFeesBatch currently catches its own
            // SP-API errors and emits per-item error entries — its only
            // throw path is items.length > 20, which preflight pre-guards
            // above. Keeping this catch matches the pricing branch below
            // and ensures any future contract change surfaces as a
            // batch-level error rather than crashing the whole composite.
            const message = (err as Error).message;
            results.forEach((r) =>
              r.errors.push({ source: "fees", message })
            );
          }
        })()
      : Promise.resolve();

  // ── Pricing (batch) ────────────────────────────────────────────────────
  const pricingP = include.has("pricing")
    ? (async () => {
        input.items.forEach((item, idx) => {
          results[idx].cached.pricing = peekHit(
            deps.caches.pricing as DiskCache<unknown>,
            marketplaceId,
            pricingCondition,
            item.asin
          );
        });
        try {
          const pricing = await getLivePricing(
            {
              asins: input.items.map((i) => i.asin),
              marketplace_id: marketplaceId,
              item_condition: pricingCondition,
              refresh_cache: input.refresh_cache,
            },
            deps.spApi,
            deps.caches.pricing
          );
          pricing.forEach((p, idx) => {
            results[idx].pricing = p;
          });
        } catch (err) {
          const message = (err as Error).message;
          results.forEach((r) =>
            r.errors.push({ source: "pricing", message })
          );
        }
      })()
    : Promise.resolve();

  await Promise.all([restrictionsP, fbaP, catalogP, feesP, pricingP]);

  if (!input.include_raw) {
    for (const r of results) {
      if (r.restrictions) delete r.restrictions.raw;
      if (r.fba) delete r.fba.raw;
      if (r.catalog) delete r.catalog.raw;
      if (r.pricing) delete r.pricing.raw;
    }
  }

  return results;
}
