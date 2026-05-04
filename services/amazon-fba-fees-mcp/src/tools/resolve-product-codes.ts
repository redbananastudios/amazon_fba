import type { SpApiService } from "../services/sp-api.js";
import type { DiskCache } from "../services/disk-cache.js";
import {
  UK_MARKETPLACE_ID,
  type ProductCodeResolveResult,
} from "../types.js";

export interface ResolveProductCodesInput {
  codes: string[];
  marketplace_id?: string;
  refresh_cache?: boolean;
}

interface SpApiSummary {
  marketplaceId?: string;
  itemName?: string;
  brandName?: string;
  brand?: string;
}

interface SpApiCatalogSearchItem {
  asin?: string;
  summaries?: SpApiSummary[];
  identifiers?: unknown;
}

interface SpApiCatalogSearchResponse {
  items?: SpApiCatalogSearchItem[];
}

function normaliseCode(code: string): string {
  return String(code).replace(/\D/g, "");
}

function codeAliases(code: string): Set<string> {
  const clean = normaliseCode(code);
  const aliases = new Set<string>();
  if (clean.length < 8) return aliases;
  aliases.add(clean);
  const stripped = clean.replace(/^0+/, "");
  if (stripped) aliases.add(stripped);
  if (clean.length === 12) aliases.add(`0${clean}`);
  if (clean.length === 13 && clean.startsWith("0")) aliases.add(clean.slice(1));
  return aliases;
}

function pickSummary(
  item: SpApiCatalogSearchItem,
  marketplaceId: string
): SpApiSummary | undefined {
  const summaries = item.summaries ?? [];
  return summaries.find((s) => s.marketplaceId === marketplaceId) ?? summaries[0];
}

function collectIdentifierValues(value: unknown, out: Set<string>): void {
  if (value === null || value === undefined) return;
  if (typeof value === "string" || typeof value === "number") {
    const clean = normaliseCode(String(value));
    if (clean.length >= 8) out.add(clean);
    return;
  }
  if (Array.isArray(value)) {
    for (const entry of value) collectIdentifierValues(entry, out);
    return;
  }
  if (typeof value === "object") {
    for (const v of Object.values(value as Record<string, unknown>)) {
      collectIdentifierValues(v, out);
    }
  }
}

function itemCarriesCode(
  item: SpApiCatalogSearchItem,
  aliases: Set<string>
): boolean {
  // SP-API identifier shapes vary by product type and marketplace. Walk the
  // returned identifiers tree and compare digit-only aliases rather than
  // binding to one brittle schema path.
  const found = new Set<string>();
  collectIdentifierValues(item.identifiers, found);
  for (const value of found) {
    if (aliases.has(value)) return true;
    for (const alias of codeAliases(value)) {
      if (aliases.has(alias)) return true;
    }
  }
  return false;
}

function normaliseResult(
  code: string,
  marketplaceId: string,
  raw: SpApiCatalogSearchResponse
): ProductCodeResolveResult {
  const aliases = codeAliases(code);
  const items = (raw.items ?? []).filter((item) => item.asin);
  const exact = items.filter((item) => itemCarriesCode(item, aliases));
  const candidates = exact.length > 0 ? exact : items;
  const uniqueByAsin = new Map<string, SpApiCatalogSearchItem>();
  for (const item of candidates) {
    if (item.asin) uniqueByAsin.set(item.asin, item);
  }
  const unique = [...uniqueByAsin.values()];

  if (unique.length === 0) {
    return { code, marketplace_id: marketplaceId, status: "NO_MATCH", raw };
  }
  if (unique.length > 1) {
    return {
      code,
      marketplace_id: marketplaceId,
      status: "MULTIPLE_MATCHES",
      asins: unique.map((item) => item.asin!),
      raw,
    };
  }

  const item = unique[0];
  const summary = pickSummary(item, marketplaceId);
  return {
    code,
    marketplace_id: marketplaceId,
    status: "FOUND",
    asin: item.asin,
    title: summary?.itemName,
    brand: summary?.brandName ?? summary?.brand,
    raw,
  };
}

export async function resolveProductCodes(
  input: ResolveProductCodesInput,
  spApi: SpApiService,
  cache?: DiskCache<ProductCodeResolveResult>
): Promise<ProductCodeResolveResult[]> {
  const marketplaceId = input.marketplace_id ?? UK_MARKETPLACE_ID;
  const codes = [...new Set(input.codes.map(normaliseCode).filter((c) => c.length >= 8))];
  const results: ProductCodeResolveResult[] = [];

  for (const code of codes) {
    const cacheKey = [marketplaceId, code];
    if (cache && !input.refresh_cache) {
      const hit = cache.get(...cacheKey);
      if (hit.hit && hit.data) {
        results.push(hit.data);
        continue;
      }
    }

    try {
      const raw = (await spApi.searchCatalogItems({
        keywords: [code],
        marketplaceId,
        includedData: ["summaries", "identifiers"],
        pageSize: 10,
      })) as SpApiCatalogSearchResponse;
      const result = normaliseResult(code, marketplaceId, raw);
      cache?.set(cacheKey, { data: result });
      results.push(result);
    } catch (err) {
      if (cache) {
        const stale = cache.get(...cacheKey);
        if (stale.stale && stale.data) {
          results.push({
            ...stale.data,
            raw: { ...(stale.data.raw as object), stale: true },
          });
          continue;
        }
      }
      results.push({
        code,
        marketplace_id: marketplaceId,
        status: "ERROR",
        error: (err as Error).message,
      });
    }
  }

  return results;
}
