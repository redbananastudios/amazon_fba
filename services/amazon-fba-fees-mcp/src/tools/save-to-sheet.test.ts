import { describe, it, expect, vi } from "vitest";
import { saveToSheet, validateSheetData } from "./save-to-sheet.js";
import type { SheetsService } from "../services/sheets.js";

describe("validateSheetData", () => {
  it("accepts valid fee-only data", () => {
    const result = validateSheetData({
      asin: "B0CX23VBGT",
      selling_price: 24.99,
      total_fees: 6.82,
    });
    expect(result.valid).toBe(true);
  });

  it("rejects data missing required fields", () => {
    const result = validateSheetData({
      asin: "B0CX23VBGT",
    });
    expect(result.valid).toBe(false);
    expect(result.error).toContain("selling_price");
  });
});

describe("saveToSheet", () => {
  it("calls appendRow on the sheets service", async () => {
    const mockSheets = {
      appendRow: vi.fn().mockResolvedValue("Saved: 1 row(s) at Sheet1!A2:N2"),
    } as unknown as SheetsService;

    const result = await saveToSheet(
      {
        asin: "B0CX23VBGT",
        product_title: "Test",
        selling_price: 24.99,
        referral_fee: 3.75,
        fba_fulfillment_fee: 3.07,
        closing_fee: 0,
        total_fees: 6.82,
      },
      mockSheets
    );

    expect(mockSheets.appendRow).toHaveBeenCalledOnce();
    expect(result).toContain("Saved");
  });
});
