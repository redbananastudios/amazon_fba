import type { SpApiService } from "../services/sp-api.js";
import type { DiskCache } from "../services/disk-cache.js";
import {
  UK_MARKETPLACE_ID,
  type CatalogItemClassification,
  type CatalogItemDimensions,
  type CatalogItemImage,
  type CatalogItemResult,
} from "../types.js";

export interface GetCatalogItemInput {
  asin: string;
  marketplace_id?: string;
  refresh_cache?: boolean;
}

interface SpApiSummary {
  marketplaceId?: string;
  brandName?: string;
  brand?: string;
  manufacturer?: string;
  itemName?: string;
}

interface SpApiDimensionValue {
  unit?: string;
  value?: number;
}

interface SpApiDimensions {
  marketplaceId?: string;
  item?: {
    height?: SpApiDimensionValue;
    length?: SpApiDimensionValue;
    width?: SpApiDimensionValue;
    weight?: SpApiDimensionValue;
  };
  package?: {
    height?: SpApiDimensionValue;
    length?: SpApiDimensionValue;
    width?: SpApiDimensionValue;
    weight?: SpApiDimensionValue;
  };
}

interface SpApiClassification {
  classificationId?: string;
  displayName?: string;
}

interface SpApiClassificationsBlock {
  marketplaceId?: string;
  classifications?: SpApiClassification[];
}

interface SpApiImage {
  variant?: string;
  link?: string;
  height?: number;
  width?: number;
}

interface SpApiImagesBlock {
  marketplaceId?: string;
  images?: SpApiImage[];
}

interface SpApiCatalogResponse {
  asin?: string;
  summaries?: SpApiSummary[];
  attributes?: Record<string, unknown>;
  dimensions?: SpApiDimensions[];
  classifications?: SpApiClassificationsBlock[];
  images?: SpApiImagesBlock[];
  salesRanks?: unknown;
  identifiers?: unknown;
}

const HAZMAT_HINT_KEYS = [
  "supplier_declared_dg_hz_regulation",
  "is_dangerous_goods",
  "hazmat_un_number",
  "hazmat",
  "dangerous_goods",
  "dangerous_goods_regulations",
  "regulatory_dangerous_goods",
];

function pickForMarketplace<T extends { marketplaceId?: string }>(
  blocks: T[] | undefined,
  marketplaceId: string
): T | undefined {
  if (!blocks || blocks.length === 0) return undefined;
  return blocks.find((b) => b.marketplaceId === marketplaceId) ?? blocks[0];
}

function extractDimensions(
  block: SpApiDimensions | undefined
): CatalogItemDimensions | undefined {
  if (!block) return undefined;
  // Prefer item dimensions, fall back to package.
  const src = block.item ?? block.package;
  if (!src) return undefined;
  const unit = src.length?.unit ?? src.height?.unit ?? src.width?.unit;
  const weightUnit = src.weight?.unit;
  const out: CatalogItemDimensions = {};
  if (src.length?.value !== undefined) out.length = src.length.value;
  if (src.width?.value !== undefined) out.width = src.width.value;
  if (src.height?.value !== undefined) out.height = src.height.value;
  if (src.weight?.value !== undefined) out.weight = src.weight.value;
  if (unit) out.unit = weightUnit && weightUnit !== unit ? `${unit}/${weightUnit}` : unit;
  else if (weightUnit) out.unit = weightUnit;
  return Object.keys(out).length > 0 ? out : undefined;
}

function detectHazmat(
  attributes: Record<string, unknown> | undefined
): boolean | undefined {
  if (!attributes) return undefined;
  for (const key of Object.keys(attributes)) {
    const lower = key.toLowerCase();
    const matches =
      HAZMAT_HINT_KEYS.includes(lower) ||
      /hazmat|dangerous_goods|hazardous/.test(lower);
    if (!matches) continue;
    const v = attributes[key];
    if (v === true) return true;
    if (typeof v === "string") {
      const norm = v.toLowerCase().trim();
      if (
        norm &&
        norm !== "no" &&
        norm !== "false" &&
        norm !== "n" &&
        norm !== "not_applicable" &&
        norm !== "storage_non_dangerous_goods"
      ) {
        return true;
      }
    }
    if (Array.isArray(v) && v.length > 0) {
      // attributes are usually arrays of {value, marketplace_id, language_tag}.
      const hasMeaningfulValue = v.some((entry) => {
        if (typeof entry === "string") return entry.trim().length > 0;
        if (entry && typeof entry === "object") {
          const value = (entry as { value?: unknown }).value;
          if (typeof value === "string") {
            const lv = value.toLowerCase().trim();
            return (
              lv.length > 0 &&
              lv !== "no" &&
              lv !== "false" &&
              lv !== "not_applicable" &&
              lv !== "storage_non_dangerous_goods"
            );
          }
          return value !== undefined && value !== null && value !== false;
        }
        return false;
      });
      if (hasMeaningfulValue) return true;
    }
  }
  return undefined;
}

function extractClassifications(
  blocks: SpApiClassificationsBlock[] | undefined,
  marketplaceId: string
): CatalogItemClassification[] | undefined {
  const block = pickForMarketplace(blocks, marketplaceId);
  if (!block?.classifications) return undefined;
  const out = block.classifications
    .filter((c) => c.classificationId && c.displayName)
    .map((c) => ({
      classificationId: c.classificationId!,
      displayName: c.displayName!,
    }));
  return out.length > 0 ? out : undefined;
}

function extractImages(
  blocks: SpApiImagesBlock[] | undefined,
  marketplaceId: string
): CatalogItemImage[] | undefined {
  const block = pickForMarketplace(blocks, marketplaceId);
  if (!block?.images) return undefined;
  const out = block.images
    .filter((i) => i.link)
    .map((i) => ({ link: i.link!, height: i.height, width: i.width }));
  return out.length > 0 ? out : undefined;
}

function normalise(
  asin: string,
  marketplaceId: string,
  raw: SpApiCatalogResponse
): CatalogItemResult {
  const summary = pickForMarketplace(raw.summaries, marketplaceId);
  const dims = pickForMarketplace(raw.dimensions, marketplaceId);
  return {
    asin,
    title: summary?.itemName,
    brand: summary?.brandName ?? summary?.brand,
    manufacturer: summary?.manufacturer,
    dimensions: extractDimensions(dims),
    hazmat: detectHazmat(raw.attributes),
    classifications: extractClassifications(raw.classifications, marketplaceId),
    images: extractImages(raw.images, marketplaceId),
    marketplace_id: marketplaceId,
    raw,
  };
}

export async function getCatalogItem(
  input: GetCatalogItemInput,
  spApi: SpApiService,
  cache?: DiskCache<CatalogItemResult>
): Promise<CatalogItemResult> {
  const marketplaceId = input.marketplace_id ?? UK_MARKETPLACE_ID;
  const cacheKey = [marketplaceId, input.asin];

  if (cache && !input.refresh_cache) {
    const hit = cache.get(...cacheKey);
    if (hit.hit && hit.data) return hit.data;
  }

  try {
    const raw = (await spApi.getCatalogItemFull({
      asin: input.asin,
      marketplaceId,
    })) as SpApiCatalogResponse;
    const result = normalise(input.asin, marketplaceId, raw);
    cache?.set(cacheKey, result);
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
