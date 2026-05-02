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

// ────────────────────────────────────────────────────────────────────────
// Sourcing-tools shared types (Tier 1 / 2 / 3)
// ────────────────────────────────────────────────────────────────────────

export type RestrictionStatus =
  | "UNRESTRICTED"
  | "RESTRICTED"
  | "BRAND_GATED"
  | "CATEGORY_GATED";

export interface RestrictionReason {
  message: string;
  reasonCode?: string;
  link?: string;
}

export interface ListingRestrictionsResult {
  asin: string;
  status: RestrictionStatus;
  reasons: RestrictionReason[];
  approval_required: boolean;
  marketplace_id: string;
  raw?: unknown;
}

export interface FbaEligibilityIneligibilityReason {
  code: string;
  description: string;
}

export interface FbaEligibilityResult {
  asin: string;
  eligible: boolean;
  ineligibility_reasons: FbaEligibilityIneligibilityReason[];
  marketplace_id: string;
  program: string;
  raw?: unknown;
}

export interface BatchFeeItem {
  asin: string;
  selling_price: number;
  marketplace_id?: string;
  identifier?: string;
}

export interface BatchFeeResultEntry {
  asin: string;
  identifier: string;
  ok: boolean;
  fees?: FeeEstimate;
  error?: string;
}

export interface CatalogItemDimensions {
  length?: number;
  width?: number;
  height?: number;
  weight?: number;
  unit?: string;
}

export interface CatalogItemImage {
  link: string;
  height?: number;
  width?: number;
}

export interface CatalogItemClassification {
  classificationId: string;
  displayName: string;
}

export interface CatalogItemResult {
  asin: string;
  title?: string;
  brand?: string;
  manufacturer?: string;
  dimensions?: CatalogItemDimensions;
  hazmat?: boolean;
  classifications?: CatalogItemClassification[];
  images?: CatalogItemImage[];
  // Listing-quality signals (added for the operator-validator-fidelity
  // sweep). All optional — populated when the SP-API summary block
  // carries them. image_count derived from `images` length so it's
  // always populatable even on summary-light responses.
  image_count?: number;
  has_aplus_content?: boolean;
  release_date?: string; // ISO 8601 if present
  marketplace_id: string;
  raw?: unknown;
}

export interface LivePricingResult {
  asin: string;
  buy_box_price?: number;
  buy_box_seller?: "AMZN" | "FBA" | "FBM" | string;
  listing_price?: number;
  shipping?: number;
  offer_count_new?: number;
  offer_count_fba?: number;
  marketplace_id: string;
  raw?: unknown;
}

export interface PreflightItem {
  asin: string;
  selling_price: number;
  cost_price: number;
}

export interface PreflightResult {
  asin: string;
  restrictions?: ListingRestrictionsResult;
  fba?: FbaEligibilityResult;
  fees?: FeeEstimate;
  catalog?: CatalogItemResult;
  pricing?: LivePricingResult;
  profitability?: ProfitabilityResult;
  cached: Record<string, boolean>;
  errors: Array<{ source: string; message: string }>;
}
