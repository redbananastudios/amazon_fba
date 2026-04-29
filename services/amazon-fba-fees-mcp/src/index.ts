#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { SpApiService } from "./services/sp-api.js";
import { SheetsService } from "./services/sheets.js";
import { Cache } from "./services/cache.js";
import { DiskCache, loadTtls } from "./services/disk-cache.js";
import { estimateFees } from "./tools/estimate-fees.js";
import { calculateProfitability } from "./tools/profitability.js";
import { saveToSheet } from "./tools/save-to-sheet.js";
import { checkListingRestrictions } from "./tools/check-listing-restrictions.js";
import { checkFbaEligibility } from "./tools/check-fba-eligibility.js";
import { estimateFeesBatch } from "./tools/estimate-fees-batch.js";
import { getCatalogItem } from "./tools/get-catalog-item.js";
import { getLivePricing } from "./tools/get-live-pricing.js";
import { preflightAsin } from "./tools/preflight-asin.js";
import type {
  CatalogItemResult,
  FeeEstimate,
  FbaEligibilityResult,
  ListingRestrictionsResult,
  LivePricingResult,
} from "./types.js";

const TWENTY_FOUR_HOURS = 24 * 60 * 60 * 1000;

function getEnvOrThrow(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

process.on("unhandledRejection", (reason) => {
  console.error("Unhandled promise rejection:", reason);
  process.exit(1);
});

let spApi: SpApiService;
let sheets: SheetsService | null;
let cache: Cache<FeeEstimate>;
let sellerId: string | undefined;
let restrictionsCache: DiskCache<ListingRestrictionsResult>;
let fbaEligibilityCache: DiskCache<FbaEligibilityResult>;
let feesCache: DiskCache<FeeEstimate>;
let catalogCache: DiskCache<CatalogItemResult>;
let pricingCache: DiskCache<LivePricingResult>;

try {
  spApi = new SpApiService({
    clientId: getEnvOrThrow("SP_API_CLIENT_ID"),
    clientSecret: getEnvOrThrow("SP_API_CLIENT_SECRET"),
    refreshToken: getEnvOrThrow("SP_API_REFRESH_TOKEN"),
  });

  const sheetsCredentials = process.env.GOOGLE_SHEETS_CREDENTIALS;
  const sheetId = process.env.GOOGLE_SHEET_ID;
  sheets =
    sheetsCredentials && sheetId
      ? new SheetsService(sheetId, sheetsCredentials)
      : null;

  cache = new Cache<FeeEstimate>(TWENTY_FOUR_HOURS);

  sellerId = process.env.SP_API_SELLER_ID;
  const ttls = loadTtls();
  restrictionsCache = new DiskCache<ListingRestrictionsResult>({
    resource: "restrictions",
    defaultTtlSeconds: ttls.restrictions,
  });
  fbaEligibilityCache = new DiskCache<FbaEligibilityResult>({
    resource: "fba_eligibility",
    defaultTtlSeconds: ttls.fbaEligibility,
  });
  feesCache = new DiskCache<FeeEstimate>({
    resource: "fees",
    defaultTtlSeconds: ttls.fees,
  });
  catalogCache = new DiskCache<CatalogItemResult>({
    resource: "catalog",
    defaultTtlSeconds: ttls.catalog,
  });
  pricingCache = new DiskCache<LivePricingResult>({
    resource: "pricing",
    defaultTtlSeconds: ttls.pricing,
  });
} catch (error: any) {
  console.error(`Startup failed: ${error.message}`);
  process.exit(1);
}

const server = new McpServer({
  name: "amazon-fba-fees",
  version: "1.0.0",
});

// Register estimate_fees tool
server.tool(
  "estimate_fees",
  "Get Amazon FBA fee breakdown for an ASIN at a given selling price (UK marketplace)",
  {
    asin: z.string().describe("Amazon ASIN"),
    selling_price: z
      .number()
      .positive()
      .describe("Selling price in GBP (VAT-inclusive, as shown on Amazon)"),
    marketplace_id: z
      .string()
      .optional()
      .describe("Marketplace ID (default: A1F83G8C2ARO7P for UK)"),
  },
  async ({ asin, selling_price, marketplace_id }) => {
    try {
      const result = await estimateFees(
        { asin, selling_price, marketplace_id },
        spApi,
        cache
      );
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error: any) {
      return {
        content: [{ type: "text" as const, text: `Error: ${error.message}` }],
        isError: true,
      };
    }
  }
);

// Register calculate_profitability tool
server.tool(
  "calculate_profitability",
  "Calculate full profitability for an ASIN including fees, VAT, and ROI",
  {
    asin: z.string().describe("Amazon ASIN"),
    selling_price: z
      .number()
      .positive()
      .describe("Selling price in GBP (VAT-inclusive)"),
    cost_price: z
      .number()
      .nonnegative()
      .describe("Product cost in GBP (ex-VAT)"),
    shipping_cost: z
      .number()
      .nonnegative()
      .optional()
      .describe("Shipping/sourcing cost in GBP (default 0)"),
    vat_registered: z
      .boolean()
      .optional()
      .describe("Whether seller is VAT-registered (default true)"),
    vat_rate: z
      .number()
      .min(0)
      .max(1)
      .optional()
      .describe("VAT rate as decimal (default 0.20)"),
    marketplace_id: z
      .string()
      .optional()
      .describe("Marketplace ID (default: UK)"),
  },
  async (args) => {
    try {
      const result = await calculateProfitability(args, spApi, cache);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error: any) {
      return {
        content: [{ type: "text" as const, text: `Error: ${error.message}` }],
        isError: true,
      };
    }
  }
);

// Register check_listing_restrictions tool
server.tool(
  "check_listing_restrictions",
  "Check whether the configured seller can list an ASIN, and surface any brand/category gating reasons. Restriction status is INFORMATIONAL ONLY — does not auto-reject candidates.",
  {
    asin: z.string().describe("Amazon ASIN"),
    marketplace_id: z
      .string()
      .optional()
      .describe("Marketplace ID (default: A1F83G8C2ARO7P for UK)"),
    condition_type: z
      .string()
      .optional()
      .describe("Listing condition (default: new_new)"),
    seller_id: z
      .string()
      .optional()
      .describe(
        "Seller ID to check restrictions against. Falls back to SP_API_SELLER_ID env var."
      ),
    refresh_cache: z
      .boolean()
      .optional()
      .describe("Force a fresh SP-API call, bypassing the disk cache"),
  },
  async ({ asin, marketplace_id, condition_type, seller_id, refresh_cache }) => {
    const effectiveSellerId = seller_id ?? sellerId;
    if (!effectiveSellerId) {
      return {
        content: [
          {
            type: "text" as const,
            text:
              "Error: seller_id is required. Pass it as an argument or set SP_API_SELLER_ID in the environment.",
          },
        ],
        isError: true,
      };
    }
    try {
      const result = await checkListingRestrictions(
        {
          asin,
          seller_id: effectiveSellerId,
          marketplace_id,
          condition_type,
          refresh_cache,
        },
        spApi,
        restrictionsCache
      );
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error: any) {
      return {
        content: [{ type: "text" as const, text: `Error: ${error.message}` }],
        isError: true,
      };
    }
  }
);

// Register check_fba_eligibility tool
server.tool(
  "check_fba_eligibility",
  "Check whether an ASIN is eligible for FBA inbound (or another program). Distinct from listing restrictions: an ASIN can be listable but FBA-ineligible (hazmat, oversized, missing dimensions). Informational only.",
  {
    asin: z.string().describe("Amazon ASIN"),
    marketplace_id: z
      .string()
      .optional()
      .describe("Marketplace ID (default: A1F83G8C2ARO7P for UK)"),
    program: z
      .string()
      .optional()
      .describe("FBA program: INBOUND or COMMINGLED (default: INBOUND)"),
    refresh_cache: z
      .boolean()
      .optional()
      .describe("Force a fresh SP-API call, bypassing the disk cache"),
  },
  async ({ asin, marketplace_id, program, refresh_cache }) => {
    try {
      const result = await checkFbaEligibility(
        { asin, marketplace_id, program, refresh_cache },
        spApi,
        fbaEligibilityCache
      );
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error: any) {
      return {
        content: [{ type: "text" as const, text: `Error: ${error.message}` }],
        isError: true,
      };
    }
  }
);

// Register estimate_fees_batch tool
server.tool(
  "estimate_fees_batch",
  "Get FBA fees for up to 20 ASINs in a single SP-API call (~20× faster than looping estimate_fees). Per-item errors don't fail the batch — each entry has ok:true/false.",
  {
    items: z
      .array(
        z.object({
          asin: z.string(),
          selling_price: z.number().positive(),
          marketplace_id: z.string().optional(),
          identifier: z
            .string()
            .optional()
            .describe(
              "Caller-supplied identifier echoed back in the response (default: derived from asin+price)"
            ),
        })
      )
      .min(0)
      .max(20)
      .describe("Up to 20 items to fee-estimate"),
    refresh_cache: z
      .boolean()
      .optional()
      .describe("Force fresh SP-API calls, bypassing the disk cache"),
  },
  async ({ items, refresh_cache }) => {
    try {
      const result = await estimateFeesBatch(
        { items, refresh_cache },
        spApi,
        feesCache
      );
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ results: result }, null, 2),
          },
        ],
      };
    } catch (error: any) {
      return {
        content: [{ type: "text" as const, text: `Error: ${error.message}` }],
        isError: true,
      };
    }
  }
);

// Register get_catalog_item tool
server.tool(
  "get_catalog_item",
  "Fetch first-party Amazon catalog data for an ASIN: title, brand (SP-API wins over Keepa per spec), manufacturer, dimensions, hazmat hint, classifications, images.",
  {
    asin: z.string().describe("Amazon ASIN"),
    marketplace_id: z
      .string()
      .optional()
      .describe("Marketplace ID (default: A1F83G8C2ARO7P for UK)"),
    refresh_cache: z
      .boolean()
      .optional()
      .describe("Force a fresh SP-API call, bypassing the disk cache"),
  },
  async ({ asin, marketplace_id, refresh_cache }) => {
    try {
      const result = await getCatalogItem(
        { asin, marketplace_id, refresh_cache },
        spApi,
        catalogCache
      );
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error: any) {
      return {
        content: [{ type: "text" as const, text: `Error: ${error.message}` }],
        isError: true,
      };
    }
  }
);

// Register get_live_pricing tool
server.tool(
  "get_live_pricing",
  "Fetch real-time Buy Box price + offer summary for up to 20 ASINs (SP-API getItemOffersBatch). Returns landed price, FBA/FBM seller class, total new offers, FBA offer count. For decision-time validation only — Keepa stays the source of truth for historical/aggregate data.",
  {
    asins: z.array(z.string()).min(0).max(20).describe("Up to 20 ASINs"),
    marketplace_id: z
      .string()
      .optional()
      .describe("Marketplace ID (default: A1F83G8C2ARO7P for UK)"),
    item_condition: z
      .string()
      .optional()
      .describe("New | Used (default: New)"),
    refresh_cache: z
      .boolean()
      .optional()
      .describe("Force a fresh SP-API call, bypassing the disk cache"),
  },
  async ({ asins, marketplace_id, item_condition, refresh_cache }) => {
    try {
      const results = await getLivePricing(
        { asins, marketplace_id, item_condition, refresh_cache },
        spApi,
        pricingCache
      );
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ results }, null, 2),
          },
        ],
      };
    } catch (error: any) {
      return {
        content: [{ type: "text" as const, text: `Error: ${error.message}` }],
        isError: true,
      };
    }
  }
);

// Register preflight_asin tool
server.tool(
  "preflight_asin",
  "Composite sourcing-decision check: fans out to listing restrictions, FBA eligibility, fee estimate, catalog lookup, live pricing and profitability for up to 20 ASINs in parallel. Per-source errors are isolated in errors[] — they don't fail the batch. cached[source] reports whether each source was served from disk cache. Restriction/eligibility data is INFORMATIONAL ONLY — does not change SHORTLIST/REVIEW/REJECT logic.",
  {
    items: z
      .array(
        z.object({
          asin: z.string(),
          selling_price: z.number().positive(),
          cost_price: z.number().nonnegative(),
        })
      )
      .min(0)
      .max(20)
      .describe("Up to 20 items, each with asin + selling_price + cost_price"),
    marketplace_id: z
      .string()
      .optional()
      .describe("Marketplace ID (default: A1F83G8C2ARO7P for UK)"),
    seller_id: z
      .string()
      .optional()
      .describe(
        "Seller ID for restrictions (falls back to SP_API_SELLER_ID env var)"
      ),
    include: z
      .array(
        z.enum([
          "restrictions",
          "fba",
          "fees",
          "catalog",
          "pricing",
          "profitability",
        ])
      )
      .optional()
      .describe(
        "Which sources to fetch (default: all six). Pass a subset to skip slower calls."
      ),
    refresh_cache: z
      .boolean()
      .optional()
      .describe("Force fresh SP-API calls across every source"),
    vat_registered: z
      .boolean()
      .optional()
      .describe("VAT-registered seller? (default: true)"),
    vat_rate: z
      .number()
      .min(0)
      .max(1)
      .optional()
      .describe("VAT rate as decimal (default: 0.20)"),
    shipping_cost: z
      .number()
      .nonnegative()
      .optional()
      .describe("Shipping/sourcing cost in GBP per item (default: 0)"),
    include_raw: z
      .boolean()
      .optional()
      .describe(
        "Include full SP-API response payloads in each sub-result (default: false). Adds ~250KB/ASIN — only enable for debugging."
      ),
  },
  async (args) => {
    try {
      const results = await preflightAsin(args, {
        spApi,
        caches: {
          restrictions: restrictionsCache,
          fbaEligibility: fbaEligibilityCache,
          fees: feesCache,
          catalog: catalogCache,
          pricing: pricingCache,
        },
        defaultSellerId: sellerId,
      });
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ results }, null, 2),
          },
        ],
      };
    } catch (error: any) {
      return {
        content: [{ type: "text" as const, text: `Error: ${error.message}` }],
        isError: true,
      };
    }
  }
);

// Register save_to_sheet tool
server.tool(
  "save_to_sheet",
  "Save fee estimate or profitability calculation to Google Sheets",
  {
    data: z
      .record(z.string(), z.any())
      .describe(
        "Output from estimate_fees or calculate_profitability"
      ),
  },
  async ({ data }) => {
    if (!sheets) {
      return {
        content: [
          {
            type: "text" as const,
            text: "Error: Google Sheets not configured. Set GOOGLE_SHEETS_CREDENTIALS and GOOGLE_SHEET_ID environment variables.",
          },
        ],
        isError: true,
      };
    }
    try {
      const result = await saveToSheet(data, sheets);
      return {
        content: [{ type: "text" as const, text: result }],
      };
    } catch (error: any) {
      return {
        content: [{ type: "text" as const, text: `Error: ${error.message}` }],
        isError: true,
      };
    }
  }
);

// Start server
try {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // Log to stderr (stdout is reserved for MCP JSON-RPC)
  console.error("amazon-fba-fees MCP server started");
} catch (error: any) {
  console.error(`Failed to start MCP transport: ${error.message}`);
  process.exit(1);
}
