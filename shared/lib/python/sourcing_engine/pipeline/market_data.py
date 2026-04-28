"""Keepa data extraction — loads Keepa Product Finder CSV exports.

Maps Keepa column names to the pipeline's internal schema.
Market data is keyed by EAN for lookup during matching.
"""
import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)

# Keepa column → pipeline field mapping
_KEEPA_COLUMN_MAP = {
    "ASIN": "asin",
    "Title": "title",
    "Brand": "brand",
    "Buy Box: Current": "buy_box_price",
    "Amazon: Current": "amazon_price",
    "New Offer Count: Current": "fba_seller_count",
    "Sales Rank: Current": "sales_rank",
    "Bought in past month": "monthly_sales_estimate",
    "Buy Box: 90 days avg.": "buy_box_avg90",
    "Buy Box: Is FBA": "buy_box_is_fba",
    "New, 3rd Party FBA: Current": "new_fba_price",
    "FBA Pick&Pack Fee": "fba_pick_pack_fee",
    "Referral Fee %": "referral_fee_pct",
    "Referral Fee based on current Buy Box price": "referral_fee_amount",
    "Product Codes: EAN": "ean",
    "Product Codes: UPC": "upc",
    "Sales Rank: 90 days avg.": "sales_rank_avg90",
    "Sales Rank: Drops last 90 days": "sales_rank_drops_90",
    "Buy Box: 90 days drop %": "buy_box_drop_pct_90",
    "Buy Box: Lowest": "buy_box_lowest",
    "Buy Box: Highest": "buy_box_highest",
    "Buy Box: 90 days OOS": "buy_box_oos_pct_90",
    "Buy Box: % Amazon 90 days": "amazon_bb_pct_90",
    "Reviews: Rating": "rating",
    "Reviews: Rating Count": "review_count",
    "Package: Dimension (cm³)": "package_volume_cm3",
    "Package: Weight (g)": "package_weight_g",
    "Categories: Root": "category_root",
    "Categories: Tree": "category_tree",
    "Parent ASIN": "parent_asin",
}


def load_market_data(source=None) -> dict:
    """Load market data from a Keepa CSV export, keyed by EAN.

    Args:
        source: Path to Keepa CSV, or a pre-loaded DataFrame, or None.

    Returns: dict[ean] -> {asin, title, buy_box_price, amazon_price,
        fba_seller_count, monthly_sales_estimate, size_tier, gated, ...}
    """
    if source is None:
        return {}

    if isinstance(source, str):
        try:
            df = pd.read_csv(
                source,
                encoding="utf-8-sig",
                dtype={"Product Codes: EAN": str, "Product Codes: UPC": str, "ASIN": str},
                low_memory=False,
            )
        except Exception:
            logger.exception("Failed to load market data from %s", source)
            return {}
    elif isinstance(source, pd.DataFrame):
        df = source
    else:
        return {}

    logger.info("Loaded %d rows from market data", len(df))

    # Rename columns to pipeline schema
    rename_map = {k: v for k, v in _KEEPA_COLUMN_MAP.items() if k in df.columns}
    df = df.rename(columns=rename_map)

    # Parse numeric fields that may have currency symbols or percentage signs
    for col in ["buy_box_price", "amazon_price", "new_fba_price", "buy_box_avg90",
                 "buy_box_lowest", "buy_box_highest", "fba_pick_pack_fee",
                 "referral_fee_amount"]:
        if col in df.columns:
            df[col] = df[col].apply(_parse_numeric)

    for col in ["fba_seller_count", "monthly_sales_estimate", "sales_rank",
                 "sales_rank_avg90", "sales_rank_drops_90", "review_count"]:
        if col in df.columns:
            df[col] = df[col].apply(_parse_numeric)

    if "referral_fee_pct" in df.columns:
        df["referral_fee_pct"] = df["referral_fee_pct"].apply(_parse_pct)

    if "buy_box_drop_pct_90" in df.columns:
        df["buy_box_drop_pct_90"] = df["buy_box_drop_pct_90"].apply(_parse_pct)

    if "amazon_bb_pct_90" in df.columns:
        df["amazon_bb_pct_90"] = df["amazon_bb_pct_90"].apply(_parse_pct)

    if "buy_box_oos_pct_90" in df.columns:
        df["buy_box_oos_pct_90"] = df["buy_box_oos_pct_90"].apply(_parse_pct)

    # Derive amazon_status from Amazon price presence and amazon_bb_pct
    if "amazon_price" in df.columns:
        df["amazon_status"] = df.apply(_derive_amazon_status, axis=1)
    else:
        df["amazon_status"] = "UNKNOWN"

    # Size tier: Keepa doesn't provide this directly — set to UNKNOWN
    df["size_tier"] = "UNKNOWN"

    # Gated: not available from Keepa — set to UNKNOWN
    df["gated"] = "UNKNOWN"

    # History days: approximate from sales rank drops
    df["history_days"] = 90  # Keepa data covers 90 days

    # No price history series in CSV export — set to None
    df["price_history"] = None

    # Build lookup dict keyed by EAN
    # Keepa sometimes has multiple EANs per product (comma-separated)
    data = {}
    for _, row in df.iterrows():
        ean_field = str(row.get("ean", "")).strip()
        if not ean_field or ean_field == "nan":
            continue
        row_dict = row.to_dict()
        for ean in ean_field.split(","):
            ean = ean.strip()
            if ean and len(ean) >= 8:
                if ean not in data:
                    data[ean] = row_dict

    logger.info("Market data indexed: %d unique EANs", len(data))
    return data


def _derive_amazon_status(row) -> str:
    """Derive Amazon seller status from Keepa data."""
    amazon_price = row.get("amazon_price")
    amazon_bb_pct = row.get("amazon_bb_pct_90")

    if pd.notna(amazon_price) and amazon_price > 0:
        return "ON_LISTING"
    if pd.notna(amazon_bb_pct) and amazon_bb_pct > 0:
        return "ON_LISTING"
    return "NOT_ON_LISTING"


def _parse_numeric(val):
    """Parse a numeric value, stripping currency symbols and commas."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    s = re.sub(r"[£$€,\s]", "", s)
    s = s.replace("\u00a3", "")  # £ sign
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_pct(val):
    """Parse a percentage string like '6.95 %' → 0.0695."""
    if pd.isna(val):
        return None
    s = str(val).strip().replace("%", "").strip()
    if not s or s == "-":
        return None
    try:
        return float(s) / 100.0
    except ValueError:
        return None
