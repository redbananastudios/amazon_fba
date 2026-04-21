# ean_validator.py — EAN-8, EAN-13, and UPC-A checksum validation.
# Preserves leading zeros. Returns False for invalid codes.


def validate_ean(ean_str):
    """
    Validate an EAN/UPC barcode string.

    Accepts:
        - EAN-13 (13 digits)
        - EAN-8 (8 digits)
        - UPC-A (12 digits)

    Returns True if the checksum is valid, False otherwise.
    Leading/trailing whitespace is stripped. Leading zeros are preserved.
    """
    if ean_str is None:
        return False

    ean = str(ean_str).strip()

    if not ean.isdigit():
        return False

    if len(ean) not in (8, 12, 13):
        return False

    return _check_digit_valid(ean)


def _check_digit_valid(ean):
    """
    Verify the check digit for EAN-8, UPC-A (12), or EAN-13 barcodes.

    The algorithm is the same for all three lengths:
    - Starting from the rightmost digit (check digit), alternate weights of 1 and 3
      moving left.
    - The weighted sum of all digits (including the check digit) must be divisible by 10.
    """
    digits = [int(d) for d in ean]
    length = len(digits)

    # Weights alternate 1, 3 from the right (check digit has weight 1)
    total = 0
    for i, digit in enumerate(digits):
        # Position from the right: length - 1 - i
        # Check digit (rightmost) gets weight 1, next gets 3, etc.
        position_from_right = length - 1 - i
        weight = 1 if position_from_right % 2 == 0 else 3
        total += digit * weight

    return total % 10 == 0


def sanitise_ean(ean_str):
    """
    Clean and zero-pad an EAN string for lookup.

    - Strips whitespace
    - Removes common non-digit prefixes (e.g. leading apostrophes from Excel)
    - Zero-pads to 13 digits if 12 or fewer digits (UPC-A -> EAN-13)
    - Returns None if the input is empty or not numeric after cleaning
    """
    if ean_str is None:
        return None

    ean = str(ean_str).strip().lstrip("'\"")

    if not ean.isdigit():
        return None

    if len(ean) == 0:
        return None

    # Zero-pad 12-digit UPC-A to 13-digit EAN-13 for Keepa lookup
    if len(ean) == 12:
        ean = "0" + ean

    # Zero-pad short codes (EAN-8 stays as-is for validation but pad for lookup)
    # EAN-8 is valid but Keepa typically indexes by EAN-13
    # Leave as-is — caller decides whether to pad

    return ean
