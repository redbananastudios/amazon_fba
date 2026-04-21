import SellingPartner from "amazon-sp-api";

interface SpApiCredentials {
  clientId: string;
  clientSecret: string;
  refreshToken: string;
}

interface FeeAndTitleResult {
  product_title: string;
  referral_fee: number;
  fba_fulfillment_fee: number;
  closing_fee: number;
  total_fees: number;
  currency: string;
}

export class SpApiService {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private client: any;
  private semaphore: Promise<void> = Promise.resolve();

  constructor(credentials: SpApiCredentials) {
    // auto_request_throttled: the amazon-sp-api library automatically retries
    // throttled requests (429) with exponential backoff. This satisfies the spec's
    // "retry with exponential backoff" requirement without custom retry logic.
    // The semaphore below provides additional protection by serializing requests.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    this.client = new (SellingPartner as any)({
      region: "eu",
      refresh_token: credentials.refreshToken,
      credentials: {
        SELLING_PARTNER_APP_CLIENT_ID: credentials.clientId,
        SELLING_PARTNER_APP_CLIENT_SECRET: credentials.clientSecret,
      },
      options: {
        auto_request_tokens: true,
        auto_request_throttled: true,
      },
    });
  }

  private async withSemaphore<T>(fn: () => Promise<T>): Promise<T> {
    const prev = this.semaphore;
    let resolve: () => void;
    this.semaphore = new Promise<void>((r) => (resolve = r));
    await prev;
    try {
      return await fn();
    } finally {
      resolve!();
    }
  }

  async getFeesAndTitle(
    asin: string,
    sellingPrice: number,
    marketplaceId: string
  ): Promise<FeeAndTitleResult> {
    // Fee estimate call
    const feesResponse = await this.withSemaphore(() =>
      this.client.callAPI({
        operation: "getMyFeesEstimateForASIN",
        endpoint: "productFees",
        path: { Asin: asin },
        body: {
          FeesEstimateRequest: {
            MarketplaceId: marketplaceId,
            IsAmazonFulfilled: true,
            PriceToEstimateFees: {
              ListingPrice: {
                CurrencyCode: "GBP",
                Amount: sellingPrice,
              },
              Shipping: {
                CurrencyCode: "GBP",
                Amount: 0,
              },
            },
            Identifier: `${asin}-${Date.now()}`,
          },
        },
      })
    );

    // Catalog item call for product title
    const catalogResponse = await this.withSemaphore(() =>
      this.client.callAPI({
        operation: "getCatalogItem",
        endpoint: "catalogItems",
        path: { asin },
        query: {
          marketplaceIds: [marketplaceId],
          includedData: ["summaries"],
        },
        options: { version: "2022-04-01" },
      })
    );

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const feesAny = feesResponse as any;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const catalogAny = catalogResponse as any;

    const feeDetails =
      feesAny?.FeesEstimateResult?.FeesEstimate?.FeeDetailList ?? [];
    const totalFees =
      feesAny?.FeesEstimateResult?.FeesEstimate?.TotalFeesEstimate;

    const findFee = (type: string): number => {
      const fee = feeDetails.find(
        (f: any) => f.FeeType === type
      );
      return fee ? parseFloat(fee.FeeAmount.Amount) : 0;
    };

    const productTitle =
      catalogAny?.summaries?.[0]?.itemName ?? "Unknown Product";

    return {
      product_title: productTitle,
      referral_fee: findFee("ReferralFee"),
      fba_fulfillment_fee: findFee("FBAFees"),
      closing_fee: findFee("ClosingFee"),
      total_fees: totalFees ? parseFloat(totalFees.Amount) : 0,
      currency: totalFees?.CurrencyCode ?? "GBP",
    };
  }
}
