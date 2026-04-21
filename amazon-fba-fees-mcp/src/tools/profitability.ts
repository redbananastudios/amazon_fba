import { estimateFees } from "./estimate-fees.js";
import type { SpApiService } from "../services/sp-api.js";
import type { Cache } from "../services/cache.js";
import { DEFAULT_VAT_RATE, type FeeEstimate, type ProfitabilityResult } from "../types.js";

interface ProfitabilityInput {
  asin: string;
  selling_price: number;
  cost_price: number;
  shipping_cost?: number;
  vat_registered?: boolean;
  vat_rate?: number;
  marketplace_id?: string;
}

export async function calculateProfitability(
  input: ProfitabilityInput,
  spApi: SpApiService,
  cache: Cache<FeeEstimate>
): Promise<ProfitabilityResult> {
  const shippingCost = input.shipping_cost ?? 0;
  const vatRegistered = input.vat_registered ?? true;
  const vatRate = input.vat_rate ?? DEFAULT_VAT_RATE;

  const fees = await estimateFees(
    {
      asin: input.asin,
      selling_price: input.selling_price,
      marketplace_id: input.marketplace_id,
    },
    spApi,
    cache
  );

  let revenueExVat: number;
  let vatAmount: number;

  if (vatRegistered) {
    revenueExVat = input.selling_price / (1 + vatRate);
    vatAmount = input.selling_price - revenueExVat;
  } else {
    revenueExVat = input.selling_price;
    vatAmount = 0;
  }

  const profit = revenueExVat - input.cost_price - shippingCost - fees.total_fees;
  const totalInvestment = input.cost_price + shippingCost;
  const marginPct = (profit / revenueExVat) * 100;
  const roiPct = totalInvestment > 0 ? (profit / totalInvestment) * 100 : 0;

  return {
    ...fees,
    revenue_ex_vat: Math.round(revenueExVat * 100) / 100,
    vat_amount: Math.round(vatAmount * 100) / 100,
    cost_price: input.cost_price,
    shipping_cost: shippingCost,
    profit: Math.round(profit * 100) / 100,
    margin_pct: Math.round(marginPct * 100) / 100,
    roi_pct: Math.round(roiPct * 100) / 100,
    vat_registered: vatRegistered,
  };
}
