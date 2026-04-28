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
import type { FeeEstimate, ListingRestrictionsResult } from "./types.js";

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
