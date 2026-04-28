export const UK_MARKETPLACE_ID = "A1F83G8C2ARO7P";
export const DEFAULT_VAT_RATE = 0.20;

export interface FeeEstimate {
  asin: string;
  product_title: string;
  selling_price: number;
  referral_fee: number;
  fba_fulfillment_fee: number;
  closing_fee: number;
  total_fees: number;
  currency: string;
}

export interface ProfitabilityResult extends FeeEstimate {
  revenue_ex_vat: number;
  vat_amount: number;
  cost_price: number;
  shipping_cost: number;
  profit: number;
  margin_pct: number;
  roi_pct: number;
  vat_registered: boolean;
}

export interface SheetRow {
  date: string;
  asin: string;
  product_title: string;
  selling_price: number;
  revenue_ex_vat?: number;
  cost_price?: number;
  shipping_cost?: number;
  referral_fee: number;
  fba_fulfillment_fee: number;
  closing_fee: number;
  total_fees: number;
  profit?: number;
  margin_pct?: number;
  roi_pct?: number;
}
