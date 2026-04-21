# config.py — All thresholds and constants for the sourcing engine.
# Never hardcode these values in pipeline logic. Always import from here.

# Profit thresholds
MIN_PROFIT = 2.50           # GBP — minimum profit at conservative price
MIN_MARGIN = 0.10           # 10% — minimum margin at conservative price
MIN_SALES_SHORTLIST = 20    # units/month — auto-shortlist threshold
MIN_SALES_REVIEW = 10       # units/month — minimum to appear in REVIEW

# Capital exposure
CAPITAL_EXPOSURE_LIMIT = 200.00  # GBP — MOQ x buy_cost above this -> HIGH_MOQ

# History
HISTORY_MINIMUM_DAYS = 30   # minimum qualifying days of FBA price history
HISTORY_WINDOW_DAYS = 90    # lookback window for conservative price calculation
LOWER_BAND_PERCENTILE = 15  # 15th percentile for conservative pricing

# Size tier
SIZE_TIER_BOUNDARY_PCT = 0.10           # 10% of next tier boundary
FBA_FEE_CONSERVATIVE_FALLBACK = 4.50    # GBP — used when size_tier is UNKNOWN

# Storage
STORAGE_RISK_THRESHOLD = 20  # sales/month below which storage fee risk is flagged

# FBM fulfilment estimates — SET THESE TO YOUR REAL COSTS
FBM_SHIPPING_ESTIMATE = 3.50   # GBP — default Royal Mail 2nd class up to 1kg
FBM_PACKAGING_ESTIMATE = 0.50  # GBP — default poly bag / small box

# VAT
VAT_RATE = 0.20                 # fixed UK standard rate — non-VAT registered seller
VAT_MISMATCH_TOLERANCE = 0.02   # GBP — rounding tolerance for VAT field validation

# Case/unit detection
MIN_PLAUSIBLE_UNIT_PRICE = 0.50  # GBP — implied unit price below this -> assume price is per case

# Referral fee — default rate when category is unknown
DEFAULT_REFERRAL_FEE_PCT = 0.15  # 15%
