import { describe, it, expect, vi, beforeEach } from "vitest";
import { SheetsService } from "./sheets.js";

const EXPECTED_HEADERS = vi.hoisted(() => [
  "Date", "ASIN", "Product Title", "Selling Price", "Revenue (ex-VAT)",
  "Cost Price", "Shipping", "Referral Fee", "FBA Fee", "Closing Fee",
  "Total Fees", "Profit", "Margin %", "ROI %",
]);

vi.mock("googleapis", () => {
  const appendMock = vi.fn().mockResolvedValue({
    data: { updates: { updatedRows: 1, updatedRange: "Sheet1!A2:N2" } },
  });
  const getMock = vi.fn().mockResolvedValue({
    data: { values: [EXPECTED_HEADERS] },
  });
  return {
    google: {
      auth: {
        GoogleAuth: class MockGoogleAuth {
          constructor(_opts: unknown) {}
        },
      },
      sheets: vi.fn().mockReturnValue({
        spreadsheets: {
          values: {
            append: appendMock,
            get: getMock,
          },
        },
      }),
    },
  };
});

describe("SheetsService", () => {
  let service: SheetsService;

  beforeEach(() => {
    service = new SheetsService("fake-sheet-id", "/fake/credentials.json");
  });

  it("appends a row with fee-only data", async () => {
    const result = await service.appendRow({
      date: "2026-03-20",
      asin: "B0CX23VBGT",
      product_title: "Test Product",
      selling_price: 24.99,
      referral_fee: 3.75,
      fba_fulfillment_fee: 3.07,
      closing_fee: 0,
      total_fees: 6.82,
    });

    expect(result).toContain("Saved");
  });

  it("appends a row with full profitability data", async () => {
    const result = await service.appendRow({
      date: "2026-03-20",
      asin: "B0CX23VBGT",
      product_title: "Test Product",
      selling_price: 24.99,
      revenue_ex_vat: 20.83,
      cost_price: 8.0,
      shipping_cost: 2.0,
      referral_fee: 3.75,
      fba_fulfillment_fee: 3.07,
      closing_fee: 0,
      total_fees: 6.82,
      profit: 4.01,
      margin_pct: 19.25,
      roi_pct: 40.1,
    });

    expect(result).toContain("Saved");
  });
});
