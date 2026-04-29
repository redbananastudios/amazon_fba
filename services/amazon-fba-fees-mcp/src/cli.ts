#!/usr/bin/env node
/**
 * CLI entry point for the Amazon FBA Fees MCP. Lets the Python sourcing
 * pipeline shell out to the same TypeScript code paths the MCP server uses,
 * without speaking MCP protocol over stdin.
 *
 * Usage:
 *   node dist/cli.js <subcommand> [flags]
 *
 * Subcommands:
 *   preflight       Composite preflight on a batch of items (primary use case)
 *   restrictions    Check listing restrictions for one or more ASINs
 *   fba             Check FBA eligibility for one or more ASINs
 *   fees            Batch fee estimate
 *   catalog         Fetch catalog item for one ASIN
 *   pricing         Get live pricing for one or more ASINs
 *
 * Output is always JSON on stdout. Logs/errors go to stderr.
 */

import { readFileSync } from "fs";
import { SpApiService } from "./services/sp-api.js";
import { DiskCache, loadTtls } from "./services/disk-cache.js";
import { checkListingRestrictions } from "./tools/check-listing-restrictions.js";
import { checkFbaEligibility } from "./tools/check-fba-eligibility.js";
import { estimateFeesBatch } from "./tools/estimate-fees-batch.js";
import { getCatalogItem } from "./tools/get-catalog-item.js";
import { getLivePricing } from "./tools/get-live-pricing.js";
import {
  preflightAsin,
  type PreflightInput,
  type PreflightSource,
} from "./tools/preflight-asin.js";
import type {
  CatalogItemResult,
  FbaEligibilityResult,
  FeeEstimate,
  ListingRestrictionsResult,
  LivePricingResult,
} from "./types.js";

interface ParsedArgs {
  positional: string[];
  flags: Record<string, string | boolean>;
}

function parseArgs(argv: string[]): ParsedArgs {
  const positional: string[] = [];
  const flags: Record<string, string | boolean> = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg.startsWith("--")) {
      const key = arg.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("--")) {
        flags[key] = true;
      } else {
        flags[key] = next;
        i++;
      }
    } else {
      positional.push(arg);
    }
  }
  return { positional, flags };
}

function getEnvOrThrow(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

function readInput(path: string): unknown {
  const raw = path === "-" ? readFileSync(0, "utf8") : readFileSync(path, "utf8");
  try {
    return JSON.parse(raw);
  } catch (err) {
    throw new Error(
      `Failed to parse JSON from ${path === "-" ? "stdin" : path}: ${(err as Error).message}`
    );
  }
}

function writeOutput(data: unknown, pretty: boolean): void {
  process.stdout.write(JSON.stringify(data, null, pretty ? 2 : undefined) + "\n");
}

function splitList(v: string | boolean | undefined): string[] {
  if (typeof v !== "string" || v.length === 0) return [];
  return v.split(",").map((s) => s.trim()).filter(Boolean);
}

function bool(v: string | boolean | undefined): boolean {
  return v === true || v === "true" || v === "1";
}

function num(v: string | boolean | undefined): number | undefined {
  if (typeof v !== "string") return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

function buildSpApi(): SpApiService {
  return new SpApiService({
    clientId: getEnvOrThrow("SP_API_CLIENT_ID"),
    clientSecret: getEnvOrThrow("SP_API_CLIENT_SECRET"),
    refreshToken: getEnvOrThrow("SP_API_REFRESH_TOKEN"),
  });
}

function buildCaches() {
  const ttls = loadTtls();
  return {
    restrictions: new DiskCache<ListingRestrictionsResult>({
      resource: "restrictions",
      defaultTtlSeconds: ttls.restrictions,
    }),
    fbaEligibility: new DiskCache<FbaEligibilityResult>({
      resource: "fba_eligibility",
      defaultTtlSeconds: ttls.fbaEligibility,
    }),
    fees: new DiskCache<FeeEstimate>({
      resource: "fees",
      defaultTtlSeconds: ttls.fees,
    }),
    catalog: new DiskCache<CatalogItemResult>({
      resource: "catalog",
      defaultTtlSeconds: ttls.catalog,
    }),
    pricing: new DiskCache<LivePricingResult>({
      resource: "pricing",
      defaultTtlSeconds: ttls.pricing,
    }),
  };
}

function printUsage(): void {
  process.stderr.write(
    [
      "Usage: node dist/cli.js <subcommand> [flags]",
      "",
      "Subcommands:",
      "  preflight       Composite preflight on a batch of items",
      "  restrictions    Check listing restrictions for one or more ASINs",
      "  fba             Check FBA eligibility for one or more ASINs",
      "  fees            Batch fee estimate",
      "  catalog         Fetch catalog item for one ASIN",
      "  pricing         Get live pricing for one or more ASINs",
      "",
      "Common flags:",
      "  --marketplace-id <id>   Default A1F83G8C2ARO7P (UK)",
      "  --refresh-cache         Force fresh SP-API calls",
      "  --pretty                Pretty-print JSON output",
      "  --help                  Print usage",
      "",
      "preflight flags:",
      "  --input <path|->        JSON file with { items, seller_id?, ... } (or - for stdin)",
      "  --include <list>        Comma-separated subset (restrictions,fba,fees,catalog,pricing,profitability)",
      "  --seller-id <id>        Overrides SP_API_SELLER_ID",
      "",
      "restrictions/fba/pricing flags:",
      "  --asins <list>          Comma-separated ASINs",
      "  --seller-id <id>        For restrictions only (or set SP_API_SELLER_ID)",
      "",
      "catalog flags:",
      "  --asin <id>             Single ASIN",
      "",
      "fees flags:",
      "  --input <path|->        JSON file with { items: [{ asin, selling_price }, ...] }",
      "",
    ].join("\n")
  );
}

async function runPreflight(flags: Record<string, string | boolean>): Promise<unknown> {
  const inputPath = flags["input"];
  if (typeof inputPath !== "string") {
    throw new Error("--input <path|-> is required for preflight");
  }
  const payload = readInput(inputPath) as Partial<PreflightInput> & {
    items?: Array<{ asin: string; selling_price: number; cost_price: number }>;
  };
  if (!Array.isArray(payload.items)) {
    throw new Error("preflight input must contain an items[] array");
  }
  const include =
    typeof flags["include"] === "string"
      ? (splitList(flags["include"]) as PreflightSource[])
      : payload.include;
  const sellerId =
    (typeof flags["seller-id"] === "string" ? flags["seller-id"] : undefined) ??
    payload.seller_id ??
    process.env.SP_API_SELLER_ID;
  const marketplaceId =
    (typeof flags["marketplace-id"] === "string"
      ? flags["marketplace-id"]
      : undefined) ?? payload.marketplace_id;
  const refreshCache = bool(flags["refresh-cache"]) || payload.refresh_cache;
  const includeRaw = bool(flags["include-raw"]) || payload.include_raw;

  const spApi = buildSpApi();
  const caches = buildCaches();
  const results = await preflightAsin(
    {
      ...payload,
      items: payload.items,
      seller_id: sellerId,
      marketplace_id: marketplaceId,
      include,
      refresh_cache: refreshCache,
      include_raw: includeRaw,
    } as PreflightInput,
    { spApi, caches, defaultSellerId: sellerId }
  );
  return { results };
}

async function runRestrictions(flags: Record<string, string | boolean>): Promise<unknown> {
  const asins = splitList(flags["asins"]);
  if (asins.length === 0) throw new Error("--asins required");
  const sellerId =
    (typeof flags["seller-id"] === "string" ? flags["seller-id"] : undefined) ??
    process.env.SP_API_SELLER_ID;
  if (!sellerId) {
    throw new Error("--seller-id (or SP_API_SELLER_ID env) required for restrictions");
  }
  const marketplaceId =
    typeof flags["marketplace-id"] === "string"
      ? flags["marketplace-id"]
      : undefined;
  const refresh = bool(flags["refresh-cache"]);
  const spApi = buildSpApi();
  const cache = buildCaches().restrictions;
  const results = await Promise.all(
    asins.map((asin) =>
      checkListingRestrictions(
        {
          asin,
          seller_id: sellerId,
          marketplace_id: marketplaceId,
          refresh_cache: refresh,
        },
        spApi,
        cache
      ).catch((err: Error) => ({ asin, error: err.message }))
    )
  );
  return { results };
}

async function runFba(flags: Record<string, string | boolean>): Promise<unknown> {
  const asins = splitList(flags["asins"]);
  if (asins.length === 0) throw new Error("--asins required");
  const marketplaceId =
    typeof flags["marketplace-id"] === "string"
      ? flags["marketplace-id"]
      : undefined;
  const refresh = bool(flags["refresh-cache"]);
  const spApi = buildSpApi();
  const cache = buildCaches().fbaEligibility;
  const results = await Promise.all(
    asins.map((asin) =>
      checkFbaEligibility(
        { asin, marketplace_id: marketplaceId, refresh_cache: refresh },
        spApi,
        cache
      ).catch((err: Error) => ({ asin, error: err.message }))
    )
  );
  return { results };
}

async function runFees(flags: Record<string, string | boolean>): Promise<unknown> {
  const inputPath = flags["input"];
  if (typeof inputPath !== "string") {
    throw new Error("--input <path|-> is required for fees");
  }
  const payload = readInput(inputPath) as {
    items?: Array<{ asin: string; selling_price: number; marketplace_id?: string }>;
  };
  if (!Array.isArray(payload.items)) {
    throw new Error("fees input must contain an items[] array");
  }
  const refresh = bool(flags["refresh-cache"]);
  const spApi = buildSpApi();
  const cache = buildCaches().fees;
  const results = await estimateFeesBatch(
    { items: payload.items, refresh_cache: refresh },
    spApi,
    cache
  );
  return { results };
}

async function runCatalog(flags: Record<string, string | boolean>): Promise<unknown> {
  const asin = typeof flags["asin"] === "string" ? flags["asin"] : undefined;
  if (!asin) throw new Error("--asin required");
  const marketplaceId =
    typeof flags["marketplace-id"] === "string"
      ? flags["marketplace-id"]
      : undefined;
  const refresh = bool(flags["refresh-cache"]);
  const spApi = buildSpApi();
  const cache = buildCaches().catalog;
  return await getCatalogItem(
    { asin, marketplace_id: marketplaceId, refresh_cache: refresh },
    spApi,
    cache
  );
}

async function runPricing(flags: Record<string, string | boolean>): Promise<unknown> {
  const asins = splitList(flags["asins"]);
  if (asins.length === 0) throw new Error("--asins required");
  const marketplaceId =
    typeof flags["marketplace-id"] === "string"
      ? flags["marketplace-id"]
      : undefined;
  const refresh = bool(flags["refresh-cache"]);
  const condition =
    typeof flags["condition"] === "string" ? flags["condition"] : undefined;
  const spApi = buildSpApi();
  const cache = buildCaches().pricing;
  const results = await getLivePricing(
    {
      asins,
      marketplace_id: marketplaceId,
      item_condition: condition,
      refresh_cache: refresh,
    },
    spApi,
    cache
  );
  return { results };
}

async function main(): Promise<number> {
  const args = parseArgs(process.argv.slice(2));
  if (args.flags["help"] || args.positional.length === 0) {
    printUsage();
    return args.flags["help"] ? 0 : 1;
  }
  const sub = args.positional[0];
  const pretty = bool(args.flags["pretty"]);

  let result: unknown;
  try {
    switch (sub) {
      case "preflight":
        result = await runPreflight(args.flags);
        break;
      case "restrictions":
        result = await runRestrictions(args.flags);
        break;
      case "fba":
        result = await runFba(args.flags);
        break;
      case "fees":
        result = await runFees(args.flags);
        break;
      case "catalog":
        result = await runCatalog(args.flags);
        break;
      case "pricing":
        result = await runPricing(args.flags);
        break;
      default:
        process.stderr.write(`Unknown subcommand: ${sub}\n\n`);
        printUsage();
        return 1;
    }
  } catch (err) {
    process.stderr.write(`Error: ${(err as Error).message}\n`);
    return 1;
  }

  writeOutput(result, pretty);
  return 0;
}

// Run only when executed directly (not when imported).
const isDirectRun =
  typeof process !== "undefined" &&
  process.argv[1] &&
  /[\\/]cli\.js$/.test(process.argv[1]);

if (isDirectRun) {
  main()
    .then((code) => process.exit(code))
    .catch((err) => {
      process.stderr.write(`Fatal: ${(err as Error).message}\n`);
      process.exit(1);
    });
}

export { main, parseArgs };
