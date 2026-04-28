import { google } from "googleapis";
import type { SheetRow } from "../types.js";

export const SHEET_HEADERS = [
  "Date",
  "ASIN",
  "Product Title",
  "Selling Price",
  "Revenue (ex-VAT)",
  "Cost Price",
  "Shipping",
  "Referral Fee",
  "FBA Fee",
  "Closing Fee",
  "Total Fees",
  "Profit",
  "Margin %",
  "ROI %",
];

export class SheetsService {
  private sheetId: string;
  private credentialsPath: string;

  constructor(sheetId: string, credentialsPath: string) {
    this.sheetId = sheetId;
    this.credentialsPath = credentialsPath;
  }

  private getClient() {
    const auth = new google.auth.GoogleAuth({
      keyFile: this.credentialsPath,
      scopes: ["https://www.googleapis.com/auth/spreadsheets"],
    });
    return google.sheets({ version: "v4", auth });
  }

  async ensureHeaders(): Promise<void> {
    const sheets = this.getClient();
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: this.sheetId,
      range: "Sheet1!A1:N1",
    });

    if (!response.data.values || response.data.values.length === 0) {
      await sheets.spreadsheets.values.append({
        spreadsheetId: this.sheetId,
        range: "Sheet1!A1",
        valueInputOption: "RAW",
        requestBody: { values: [SHEET_HEADERS] },
      });
    }
  }

  async appendRow(row: SheetRow): Promise<string> {
    await this.ensureHeaders();
    const sheets = this.getClient();

    const values = [
      row.date,
      row.asin,
      row.product_title,
      row.selling_price,
      row.revenue_ex_vat ?? "",
      row.cost_price ?? "",
      row.shipping_cost ?? "",
      row.referral_fee,
      row.fba_fulfillment_fee,
      row.closing_fee,
      row.total_fees,
      row.profit ?? "",
      row.margin_pct ?? "",
      row.roi_pct ?? "",
    ];

    const response = await sheets.spreadsheets.values.append({
      spreadsheetId: this.sheetId,
      range: "Sheet1!A:N",
      valueInputOption: "USER_ENTERED",
      requestBody: { values: [values] },
    });

    const updatedRows = response.data.updates?.updatedRows ?? 0;
    const updatedRange = response.data.updates?.updatedRange ?? "";
    return `Saved: ${updatedRows} row(s) at ${updatedRange}. Sheet: https://docs.google.com/spreadsheets/d/${this.sheetId}`;
  }
}
