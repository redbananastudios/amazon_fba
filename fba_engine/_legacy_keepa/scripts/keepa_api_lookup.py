"""
Keepa API EAN lookup — uses raw HTTP API to avoid keepa library token bugs.

Batch lookups of EANs to get ASINs and market data.
Saves progress for resume capability.
"""
import csv
import gzip
import json
import os
import sys
import time
import urllib.request
import urllib.parse

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Config
KEEPA_API_KEY = "bnt1d9nrggbogt6v30e1lq3994p1fl1s4uuoiu42qcea12o2qie2itmugf1q0a5q"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EAN_FILE = os.path.join(SCRIPT_DIR, "..", "raw", "eans_for_keepa.txt")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "raw")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "keepa_connect_beauty_latest.csv")
PROGRESS_FILE = os.path.join(SCRIPT_DIR, "..", "downloads", "api_progress.json")
BATCH_SIZE = 50  # EANs per API call (max 100)
DOMAIN_ID = 2    # Amazon.co.uk = domain 2 in Keepa API
API_BASE = "https://api.keepa.com"


def load_eans(path):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed_eans": [], "results": []}


def save_progress(progress):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, default=str)


def api_request(endpoint, params):
    """Make a Keepa API request, handling gzip response."""
    params["key"] = KEEPA_API_KEY
    url = f"{API_BASE}/{endpoint}?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url)
    req.add_header('Accept-Encoding', 'gzip')
    resp = urllib.request.urlopen(req, timeout=60)
    raw = resp.read()
    try:
        data = json.loads(gzip.decompress(raw))
    except Exception:
        data = json.loads(raw)
    return data


def get_token_status():
    """Check available tokens."""
    data = api_request("token", {})
    return data.get("tokensLeft", 0), data.get("refillRate", 1)


def query_products_by_ean(eans):
    """Query Keepa product API by EAN codes."""
    params = {
        "domain": DOMAIN_ID,
        "code": ",".join(eans),
        "stats": 90,
    }
    data = api_request("product", params)
    return data


def product_to_csv_row(product):
    """Convert a Keepa API product object to a CSV row."""
    if not product:
        return None

    eans = product.get("eanList", []) or []
    upcs = product.get("upcList", []) or []

    row = {
        "ASIN": product.get("asin", ""),
        "Title": product.get("title", ""),
        "Brand": product.get("brand", ""),
        "Sales Rank: Current": "",
        "Sales Rank: 90 days avg.": "",
        "Bought in past month": "",
        "Reviews: Rating": "",
        "Reviews: Rating Count": "",
        "Buy Box: Current": "",
        "Buy Box: 90 days avg.": "",
        "Buy Box: Is FBA": "",
        "Amazon: Current": "",
        "New, 3rd Party FBA: Current": "",
        "New Offer Count: Current": "",
        "FBA Pick&Pack Fee": "",
        "Referral Fee %": "",
        "Referral Fee based on current Buy Box price": "",
        "Product Codes: EAN": ",".join(eans) if eans else "",
        "Product Codes: UPC": ",".join(upcs) if upcs else "",
        "Categories: Root": str(product.get("rootCategory", "")),
        "Parent ASIN": product.get("parentAsin", ""),
    }

    # Stats from 90-day period
    stats = product.get("stats") or {}
    if stats:
        current = stats.get("current", [])
        avg90 = stats.get("avg90", [])

        # current[0]=Amazon, [1]=New, [7]=NewFBA, [11]=CountNew, [18]=BuyBox
        if current and len(current) > 18:
            if current[0] and current[0] > 0:
                row["Amazon: Current"] = f"{current[0] / 100.0:.2f}"
            if current[1] and current[1] > 0:
                row["New, 3rd Party FBA: Current"] = f"{current[1] / 100.0:.2f}"
            if current[18] and current[18] > 0:
                row["Buy Box: Current"] = f"{current[18] / 100.0:.2f}"
            if current[11] and current[11] > 0:
                row["New Offer Count: Current"] = str(current[11])
            if current[3] and current[3] > 0:
                row["Sales Rank: Current"] = str(current[3])
            # New FBA price (index 7)
            if len(current) > 7 and current[7] and current[7] > 0:
                row["New, 3rd Party FBA: Current"] = f"{current[7] / 100.0:.2f}"

        if avg90 and len(avg90) > 18:
            if avg90[18] and avg90[18] > 0:
                row["Buy Box: 90 days avg."] = f"{avg90[18] / 100.0:.2f}"
            if avg90[3] and avg90[3] > 0:
                row["Sales Rank: 90 days avg."] = str(avg90[3])

    # Monthly sold
    monthly = product.get("monthlySold")
    if monthly is not None and monthly >= 0:
        row["Bought in past month"] = str(monthly)

    # Sales rank from product directly
    sr = product.get("salesRank")
    if sr and sr > 0 and not row["Sales Rank: Current"]:
        row["Sales Rank: Current"] = str(sr)

    # FBA fees
    fba_fees = product.get("fbaFees") or {}
    if fba_fees:
        pick_pack = fba_fees.get("pickAndPackFee", 0)
        if pick_pack and pick_pack > 0:
            row["FBA Pick&Pack Fee"] = f"{pick_pack / 100.0:.2f}"

    # Referral fee
    ref_fee = product.get("referralFeePercent")
    if ref_fee and ref_fee > 0:
        row["Referral Fee %"] = f"{ref_fee}%"

    # Buy Box is FBA
    bb_is_fba = product.get("buyBoxIsFBA")
    if bb_is_fba is not None:
        row["Buy Box: Is FBA"] = "Yes" if bb_is_fba else "No"

    # Rating and reviews from stats
    if stats:
        current = stats.get("current", [])
        if current and len(current) > 17:
            if current[16] and current[16] > 0:
                row["Reviews: Rating"] = f"{current[16] / 10.0:.1f}"
            if current[17] and current[17] > 0:
                row["Reviews: Rating Count"] = str(current[17])

    return row


def write_csv(rows):
    if not rows:
        return
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    headers = list(rows[0].keys())
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[*] CSV: {len(rows)} rows -> {OUTPUT_FILE}")


def main():
    eans = load_eans(EAN_FILE)
    print(f"[*] Loaded {len(eans)} EANs")

    # Load progress
    progress = load_progress()
    completed = set(progress.get("completed_eans", []))
    all_rows = progress.get("results", [])

    remaining = [e for e in eans if e not in completed]
    print(f"[*] Already completed: {len(completed)}, remaining: {len(remaining)}")

    if not remaining:
        print("[*] All EANs already processed!")
        write_csv(all_rows)
        return

    # Check tokens
    tokens, refill_rate = get_token_status()
    print(f"[*] Tokens available: {tokens}, refill rate: {refill_rate}/min")

    # Process in batches
    batches = [remaining[i:i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
    total_matched = 0
    total_not_found = 0

    for batch_idx, batch in enumerate(batches):
        print(f"\n[{batch_idx + 1}/{len(batches)}] {len(batch)} EANs...", end=" ", flush=True)

        # Check tokens before each batch
        tokens, _ = get_token_status()
        needed = len(batch)  # 1 token per product

        while tokens < needed:
            wait = max(65, (needed - tokens) * 60 + 10)
            print(f"\n    Need {needed} tokens, have {tokens}. Waiting {wait//60}min...", end="", flush=True)
            time.sleep(wait)
            tokens, _ = get_token_status()
            print(f" now {tokens}", end="", flush=True)

        try:
            result = query_products_by_ean(batch)
            products = result.get("products", [])
            tokens_after = result.get("tokensLeft", "?")

            if products:
                batch_rows = []
                for p in products:
                    row = product_to_csv_row(p)
                    if row and row["ASIN"]:
                        batch_rows.append(row)

                all_rows.extend(batch_rows)
                total_matched += len(batch_rows)

                # Show first few
                for r in batch_rows[:2]:
                    title = r['Title'][:40] if r.get('Title') else '?'
                    print(f"\n    {r['ASIN']} {title} BB:{r.get('Buy Box: Current','?')}", end="")

                print(f"\n    -> {len(batch_rows)} matched, {tokens_after} tokens left")
            else:
                total_not_found += len(batch)
                print(f"0 results, {tokens_after} tokens left")

        except Exception as e:
            err_str = str(e)
            print(f"\n    ERROR: {err_str}")
            if "429" in err_str or "token" in err_str.lower():
                print("    Rate limited, waiting 120s...")
                time.sleep(120)
                continue

        # Mark completed
        for ean in batch:
            completed.add(ean)

        # Save progress
        progress["completed_eans"] = list(completed)
        progress["results"] = all_rows
        save_progress(progress)

        # Write interim CSV
        if all_rows:
            write_csv(all_rows)

        # Brief pause
        time.sleep(1)

    # Final summary
    write_csv(all_rows)
    print(f"\n{'='*60}")
    print(f"DONE: {total_matched} matched, {total_not_found} not found out of {len(eans)} EANs")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
