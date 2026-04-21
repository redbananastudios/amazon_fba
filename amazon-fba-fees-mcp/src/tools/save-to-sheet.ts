import type { SheetsService } from "../services/sheets.js";
import type { SheetRow } from "../types.js";

interface ValidationResult {
  valid: boolean;
  error?: string;
}

export function validateSheetData(data: Record<string, any>): ValidationResult {
  const required = ["asin", "selling_price", "total_fees"];
  const missing = required.filter((key) => data[key] === undefined || data[key] === null);

  if (missing.length > 0) {
    return { valid: false, error: `Missing required fields: ${missing.join(", ")}` };
  }
  return { valid: true };
}

export async function saveToSheet(
  data: Record<string, any>,
  sheets: SheetsService
): Promise<string> {
  const validation = validateSheetData(data);
  if (!validation.valid) {
    throw new Error(validation.error);
  }

  const row: SheetRow = {
    date: new Date().toISOString().split("T")[0],
    asin: data.asin,
    product_title: data.product_title ?? "",
    selling_price: data.selling_price,
    revenue_ex_vat: data.revenue_ex_vat,
    cost_price: data.cost_price,
    shipping_cost: data.shipping_cost,
    referral_fee: data.referral_fee ?? 0,
    fba_fulfillment_fee: data.fba_fulfillment_fee ?? 0,
    closing_fee: data.closing_fee ?? 0,
    total_fees: data.total_fees,
    profit: data.profit,
    margin_pct: data.margin_pct,
    roi_pct: data.roi_pct,
  };

  return sheets.appendRow(row);
}
