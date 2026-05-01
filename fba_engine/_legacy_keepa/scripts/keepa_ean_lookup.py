"""
Keepa EAN→ASIN lookup via browser automation.

Strategy:
1. Log into Keepa
2. Use Keepa's search bar to look up each EAN
3. Scrape product data from the product page
4. Build a market data CSV matching the Keepa export format

Uses Keepa's search which resolves EAN→ASIN and shows the product page.
"""
import asyncio
import csv
import json
import os
import re
import glob
import time

from playwright.async_api import async_playwright

# Config
KEEPA_EMAIL = "JustThis"
KEEPA_PASSWORD = "Polopolo121"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EAN_FILE = os.path.join(SCRIPT_DIR, "..", "raw", "eans_for_keepa.txt")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "raw")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "keepa_connect_beauty_latest.csv")
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "..", "downloads")
PROGRESS_FILE = os.path.join(DOWNLOAD_DIR, "progress.json")
KEEPA_DOMAIN = "6"  # Amazon.co.uk


def load_eans(path):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def load_progress():
    """Load previously scraped data for resume capability."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": [], "data": []}


def save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)


async def login_to_keepa(page):
    """Log into Keepa."""
    print("[*] Navigating to Keepa...")
    await page.goto("https://keepa.com/", wait_until="networkidle", timeout=30000)
    await asyncio.sleep(3)

    # Check if already logged in
    header = await page.evaluate("() => document.querySelector('#panelUserName')?.textContent || ''")
    if header.strip():
        print(f"[*] Already logged in as: {header.strip()}")
        # Switch to .co.uk if needed
        await ensure_uk_domain(page)
        return True

    # Login via JS
    await page.evaluate("window.location.href = 'https://keepa.com/#!login'")
    await asyncio.sleep(3)
    await page.evaluate(f"""
        () => {{
            const u = document.getElementById('username');
            const p = document.getElementById('password');
            if (u) {{ u.value = '{KEEPA_EMAIL}'; u.dispatchEvent(new Event('input', {{bubbles:true}})); }}
            if (p) {{ p.value = '{KEEPA_PASSWORD}'; p.dispatchEvent(new Event('input', {{bubbles:true}})); }}
            const btn = document.getElementById('submitLogin');
            if (btn) btn.click();
        }}
    """)
    await asyncio.sleep(5)

    # Verify
    header = await page.evaluate("() => document.querySelector('#panelUserName')?.textContent || ''")
    if header.strip():
        print(f"[*] Logged in as: {header.strip()}")
    else:
        print("[*] Login submitted (assuming success)")

    await ensure_uk_domain(page)
    return True


async def ensure_uk_domain(page):
    """Make sure we're on Amazon.co.uk domain."""
    domain_text = await page.evaluate("""
        () => {
            const el = document.querySelector('#domainSelector, .domainSelector');
            return el ? el.textContent.trim() : '';
        }
    """)
    if '.co.uk' not in domain_text:
        print("[*] Switching to Amazon.co.uk...")
        # Click the domain selector area and pick UK
        await page.evaluate("""
            () => {
                const flags = document.querySelectorAll('.domainSelection img, .flag');
                for (const f of flags) {
                    if (f.title && f.title.includes('United Kingdom')) {
                        f.click();
                        return;
                    }
                    if (f.alt && f.alt.includes('UK')) {
                        f.click();
                        return;
                    }
                }
            }
        """)
        await asyncio.sleep(2)


async def search_ean(page, ean):
    """Search for an EAN using Keepa's search and extract product data."""
    # Navigate to product viewer with EAN search
    url = f"https://keepa.com/#!product/2-{ean}"
    await page.evaluate(f"window.location.href = '{url}'")
    await asyncio.sleep(4)

    # Check if product was found — look for product title
    product_data = await page.evaluate("""
        () => {
            const data = {};

            // Check for "not found" state
            const body = document.body.innerText;
            if (body.includes('could not find') || body.includes('no product found') ||
                body.includes('Product not found') || body.includes('not in our database')) {
                return null;
            }

            // ASIN — from URL or page content
            const urlMatch = window.location.hash.match(/product\\/\\d-([A-Z0-9]{10})/);
            if (urlMatch) data.asin = urlMatch[1];

            // Try to get data from Keepa's internal product object
            // Keepa stores product data in window variables
            if (typeof keepaProduct !== 'undefined' && keepaProduct) {
                data.title = keepaProduct.title || '';
                data.asin = keepaProduct.asin || data.asin || '';
            }

            // Product title — usually in h2 or specific element
            const titleEl = document.querySelector('#productTitle, .productTitle, h2');
            if (titleEl) data.title = titleEl.textContent.trim();

            // If no title found, try the page title
            if (!data.title) {
                const pageTitle = document.title;
                if (pageTitle && !pageTitle.includes('Keepa')) {
                    data.title = pageTitle;
                }
            }

            return data;
        }
    """)

    if not product_data or not product_data.get("asin"):
        # Try waiting a bit longer — page might still be loading
        await asyncio.sleep(3)

        # Try to extract ASIN from the current URL hash
        current_url = await page.evaluate("() => window.location.hash")
        asin_match = re.search(r'product/\d-([A-Z0-9]{10})', current_url) if current_url else None

        if asin_match:
            if not product_data:
                product_data = {}
            product_data["asin"] = asin_match.group(1)
        else:
            return None

    # Now scrape detailed data from the product page tables
    details = await page.evaluate("""
        () => {
            const data = {};

            // Get all text content with labels
            const rows = document.querySelectorAll('tr, .dataTableRow, .productInfoRow');
            for (const row of rows) {
                const cells = row.querySelectorAll('td, th, .label, .value');
                if (cells.length >= 2) {
                    const label = cells[0].textContent.trim().toLowerCase();
                    const value = cells[1].textContent.trim();

                    if (label.includes('buy box')) data.buyBox = value;
                    if (label.includes('amazon') && !label.includes('warehouse')) data.amazonPrice = value;
                    if (label.includes('new') && label.includes('fba')) data.newFba = value;
                    if (label.includes('sales rank') && label.includes('current')) data.salesRank = value;
                    if (label.includes('bought') || label.includes('monthly sold')) data.monthlySold = value;
                    if (label.includes('rating')) data.rating = value;
                    if (label.includes('review') && label.includes('count')) data.reviewCount = value;
                    if (label.includes('brand')) data.brand = value;
                    if (label.includes('seller') && label.includes('count')) data.sellerCount = value;
                    if (label.includes('fba') && label.includes('fee')) data.fbaFee = value;
                    if (label.includes('referral') && label.includes('fee')) data.referralFee = value;
                    if (label.includes('ean')) data.ean = value;
                    if (label.includes('upc')) data.upc = value;
                    if (label.includes('category')) data.category = value;
                    if (label.includes('title')) data.title = value;
                }
            }

            // Also try to get info from the product stats panel
            const statsTexts = document.querySelectorAll('.productStatValue, .stat-value, span[data-key]');
            for (const el of statsTexts) {
                const key = el.getAttribute('data-key') || el.className;
                const val = el.textContent.trim();
                if (key) data['stat_' + key] = val;
            }

            // Get the page body text for regex extraction
            data._bodyText = document.body.innerText.substring(0, 10000);

            return data;
        }
    """)

    if details:
        product_data.update(details)

    # Extract specific values from body text using regex
    body = product_data.get("_bodyText", "")
    if body:
        # Buy Box price
        bb_match = re.search(r'Buy Box[:\s]*£([\d.]+)', body)
        if bb_match and "buyBox" not in product_data:
            product_data["buyBox"] = bb_match.group(1)

        # Sales rank
        sr_match = re.search(r'Sales Rank[:\s]*#?([\d,]+)', body)
        if sr_match:
            product_data["salesRank"] = sr_match.group(1)

        # Monthly sold / Bought in past month
        sold_match = re.search(r'(\d+[\d,]*)\+?\s*bought\s+in\s+past\s+month', body, re.I)
        if sold_match:
            product_data["monthlySold"] = sold_match.group(1)

        # New offer count
        offer_match = re.search(r'New.*?(\d+)\s*(?:offer|seller)', body, re.I)
        if offer_match:
            product_data["sellerCount"] = offer_match.group(1)

        # Rating
        rating_match = re.search(r'Rating[:\s]*([\d.]+)', body)
        if rating_match:
            product_data["rating"] = rating_match.group(1)

        # Reviews
        review_match = re.search(r'([\d,]+)\s*(?:review|rating)', body, re.I)
        if review_match:
            product_data["reviewCount"] = review_match.group(1)

        # Title from page
        title_match = re.search(r'Amazon\.co\.uk.*?:\s*(.+?)(?:\n|$)', body)
        if title_match and not product_data.get("title"):
            product_data["title"] = title_match.group(1).strip()

    # Clean up
    product_data.pop("_bodyText", None)

    return product_data


def build_keepa_csv_row(ean, product_data):
    """Convert scraped product data into a row matching Keepa CSV export format."""
    if not product_data:
        return None

    def parse_price(val):
        if not val:
            return ""
        val = str(val).replace("£", "").replace(",", "").replace(" ", "").strip()
        try:
            return f"£ {float(val):.2f}"
        except (ValueError, TypeError):
            return val

    def parse_int(val):
        if not val:
            return ""
        val = str(val).replace(",", "").strip()
        try:
            return str(int(float(val)))
        except (ValueError, TypeError):
            return val

    return {
        "ASIN": product_data.get("asin", ""),
        "Title": product_data.get("title", ""),
        "Brand": product_data.get("brand", ""),
        "Buy Box: Current": parse_price(product_data.get("buyBox")),
        "Amazon: Current": parse_price(product_data.get("amazonPrice")),
        "New Offer Count: Current": parse_int(product_data.get("sellerCount")),
        "Sales Rank: Current": parse_int(product_data.get("salesRank")),
        "Bought in past month": parse_int(product_data.get("monthlySold")),
        "Buy Box: 90 days avg.": "",
        "Buy Box: Is FBA": "",
        "New, 3rd Party FBA: Current": parse_price(product_data.get("newFba")),
        "FBA Pick&Pack Fee": parse_price(product_data.get("fbaFee")),
        "Referral Fee %": product_data.get("referralFee", ""),
        "Referral Fee based on current Buy Box price": "",
        "Product Codes: EAN": ean,
        "Product Codes: UPC": product_data.get("upc", ""),
        "Reviews: Rating": product_data.get("rating", ""),
        "Reviews: Rating Count": parse_int(product_data.get("reviewCount")),
        "Categories: Root": product_data.get("category", ""),
        "Parent ASIN": "",
    }


def write_output_csv(data_rows, output_path):
    """Write market data CSV."""
    if not data_rows:
        print("[!] No data to write")
        return

    headers = list(data_rows[0].keys())
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(data_rows)
    print(f"[+] Wrote {len(data_rows)} rows to {output_path}")


async def main():
    eans = load_eans(EAN_FILE)
    print(f"[*] Loaded {len(eans)} EANs")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # Load progress for resume
    progress = load_progress()
    completed_eans = set(progress.get("completed", []))
    data_rows = progress.get("data", [])

    remaining = [e for e in eans if e not in completed_eans]
    print(f"[*] Already completed: {len(completed_eans)}, remaining: {len(remaining)}")

    if not remaining:
        print("[*] All EANs already processed!")
        write_output_csv(data_rows, OUTPUT_FILE)
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        # Login
        await login_to_keepa(page)

        matched = 0
        no_match = 0
        errors = 0
        batch_save_interval = 10  # Save progress every N EANs

        for i, ean in enumerate(remaining):
            try:
                print(f"\r[{i+1}/{len(remaining)}] EAN: {ean}", end="", flush=True)

                product_data = await search_ean(page, ean)

                if product_data and product_data.get("asin"):
                    row = build_keepa_csv_row(ean, product_data)
                    if row:
                        data_rows.append(row)
                        matched += 1
                        print(f" -> {product_data['asin']} OK")
                    else:
                        no_match += 1
                        print(f" -> no data")
                else:
                    no_match += 1
                    print(f" -> not found")

                completed_eans.add(ean)

                # Save progress periodically
                if (i + 1) % batch_save_interval == 0:
                    progress["completed"] = list(completed_eans)
                    progress["data"] = data_rows
                    save_progress(progress)
                    # Also write interim CSV
                    write_output_csv(data_rows, OUTPUT_FILE)

                # Small delay to avoid rate limiting
                await asyncio.sleep(1.5)

            except Exception as e:
                errors += 1
                print(f" -> ERROR: {e}")
                completed_eans.add(ean)
                await asyncio.sleep(3)

        await browser.close()

    # Final save
    progress["completed"] = list(completed_eans)
    progress["data"] = data_rows
    save_progress(progress)
    write_output_csv(data_rows, OUTPUT_FILE)

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total EANs: {len(eans)}")
    print(f"Matched: {matched}")
    print(f"Not found: {no_match}")
    print(f"Errors: {errors}")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
