"""
Keepa Product Finder — browser export by brand.

Uses the autocomplete brand field in Product Finder to search for
beauty brand products on Amazon UK, then exports as CSV.
Each brand search is a separate export, merged at the end.
"""
import asyncio
import csv
import json
import os
import glob
import sys
import time
from urllib.parse import quote as url_quote

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from playwright.async_api import async_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "..", "downloads", "browser_exports")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "..", "raw", "keepa_browser_export.csv")

# Brands from the supplier price list
BRANDS = [
    "Maybelline",
    "L'Oreal Paris",
    "Rimmel",
    "Max Factor",
    "Essie",
    "Bourjois",
    "NYX",
    "Sally Hansen",
]


async def login(page):
    await page.goto("https://keepa.com/", wait_until="networkidle", timeout=30000)
    await asyncio.sleep(3)
    await page.evaluate("window.location.href = 'https://keepa.com/#!login'")
    await asyncio.sleep(3)
    await page.evaluate("""
        () => {
            const u = document.getElementById('username');
            const p = document.getElementById('password');
            if (u) { u.value = 'JustThis'; u.dispatchEvent(new Event('input', {bubbles:true})); }
            if (p) { p.value = 'Polopolo121'; p.dispatchEvent(new Event('input', {bubbles:true})); }
            document.getElementById('submitLogin')?.click();
        }
    """)
    await asyncio.sleep(5)
    print("[OK] Logged in")


async def open_finder(page):
    """Navigate to Product Finder for Amazon.co.uk with empty filter."""
    minimal = {"f": {}, "s": [], "t": "f"}
    encoded = url_quote(json.dumps(minimal, separators=(",", ":")))
    await page.evaluate("window.location.href = 'https://keepa.com/#!tracking'")
    await asyncio.sleep(2)
    await page.evaluate(f"window.location.href = 'https://keepa.com/#!finder/6/{encoded}'")
    await asyncio.sleep(6)


async def set_brand_and_search(page, brand):
    """Type brand into the autocomplete brand field and click Find Products."""
    print(f"[*] Setting brand: {brand}")

    # Scroll down to make the brand field visible
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(1)

    # Use Playwright's type() for proper keystroke simulation (handles apostrophes)
    brand_input = await page.query_selector("#autocomplete-brand")
    if brand_input:
        await brand_input.click()
        await brand_input.fill("")
        await brand_input.type(brand, delay=50)
        await asyncio.sleep(2)

        # Click the matching dropdown item
        brand_lower = brand.lower().split("'")[0]  # Use text before apostrophe for matching
        clicked = await page.evaluate("""
            (searchText) => {
                const items = document.querySelectorAll('.ui-menu-item, .ui-menu-item-wrapper, li');
                for (const item of items) {
                    const text = item.textContent.trim().toLowerCase();
                    if (text.includes(searchText)) {
                        item.click();
                        return 'clicked: ' + item.textContent.trim();
                    }
                }
                // Try the autocomplete list specifically
                const acItems = document.querySelectorAll('.ui-autocomplete li');
                if (acItems.length > 0) {
                    acItems[0].click();
                    return 'clicked first autocomplete: ' + acItems[0].textContent.trim();
                }
                return 'no dropdown items';
            }
        """, brand_lower)
        print(f"    Dropdown: {clicked}")
    else:
        print("    [!] Brand input not found")
        return

    await asyncio.sleep(2)

    # Take screenshot to verify brand is set
    await page.screenshot(path=os.path.join(DOWNLOAD_DIR, f"brand_set_{brand.split(chr(39))[0]}.png"))

    # Click Find Products using Playwright's click (more reliable than JS)
    find_btn = await page.query_selector("text=FIND PRODUCTS")
    if find_btn:
        await find_btn.scroll_into_view_if_needed()
        await asyncio.sleep(1)
        await find_btn.click()
        print(f"[*] Clicked Find Products for {brand}")
    else:
        # Fallback JS click
        await page.evaluate("""
            () => {
                const btns = document.querySelectorAll('button, a, div, span');
                for (const btn of btns) {
                    if (btn.textContent.trim().toUpperCase().startsWith('FIND PRODUCTS')) {
                        btn.scrollIntoView();
                        btn.click();
                        return;
                    }
                }
            }
        """)
        print(f"[*] JS-clicked Find Products for {brand}")

    # Wait longer for results to load (large result sets take time)
    await asyncio.sleep(15)


async def wait_for_results(page):
    """Wait for results grid to load."""
    for _ in range(10):
        has_grid = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('.ag-row');
                return rows.length;
            }
        """)
        if has_grid > 0:
            print(f"[*] Grid has {has_grid} rows")
            return True
        await asyncio.sleep(2)

    # Check for "no results"
    body = await page.evaluate("() => document.body.innerText.substring(0, 1000)")
    if "no results" in body.lower() or "0 results" in body.lower():
        print("[*] No results for this brand")
        return False

    print("[*] Grid didn't load, continuing anyway")
    return False


async def set_rows_per_page(page, target=5000):
    """Set rows per page to maximum for export."""
    await page.evaluate(f"""
        () => {{
            // Find the rows-per-page selector
            const selects = document.querySelectorAll('select');
            for (const sel of selects) {{
                // Look for page size selector (usually has options like 10, 50, 100, 500, 1000, 5000)
                const options = Array.from(sel.options).map(o => o.value);
                if (options.some(v => parseInt(v) >= 100)) {{
                    // Set to highest value
                    const maxOpt = options.reduce((a, b) => parseInt(a) > parseInt(b) ? a : b);
                    sel.value = maxOpt;
                    sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return 'set to ' + maxOpt;
                }}
            }}
            return 'no selector found';
        }}
    """)
    await asyncio.sleep(3)


async def export_csv(page, brand_name):
    """Click export and save CSV. Export button is span.tool__export in the toolbar."""
    safe_name = brand_name.replace("'", "").replace(" ", "_").lower()
    dest = os.path.join(DOWNLOAD_DIR, f"keepa_{safe_name}.csv")

    # First set rows to max — click the "100 rows" dropdown and pick highest
    rows_set = await page.evaluate("""
        () => {
            const el = document.querySelector('.tool__rows .trigger, .tool__rows select');
            if (el) { el.click(); return 'clicked rows trigger'; }
            return 'not found';
        }
    """)
    print(f"    Rows selector: {rows_set}")
    await asyncio.sleep(1)

    # Pick the highest option from the dropdown that appears
    await page.evaluate("""
        () => {
            // Look for dropdown menu items with numbers
            const items = document.querySelectorAll('.dropdown-menu li, .menu-item, option, .popover li, li');
            let maxVal = 0, maxEl = null;
            for (const item of items) {
                const num = parseInt(item.textContent.trim());
                if (num > maxVal && item.offsetParent !== null) {
                    maxVal = num;
                    maxEl = item;
                }
            }
            if (maxEl) { maxEl.click(); return 'set to ' + maxVal; }
            return 'no options';
        }
    """)
    await asyncio.sleep(5)

    # Scroll to top to find export button
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(1)

    # Screenshot the results page
    await page.screenshot(path=os.path.join(DOWNLOAD_DIR, f"results_{safe_name}.png"))

    # Find export-related elements for debugging
    export_info = await page.evaluate("""
        () => {
            const found = [];
            document.querySelectorAll('button, a, span, div, input').forEach(el => {
                const text = el.textContent.trim().toLowerCase();
                const id = (el.id || '').toLowerCase();
                const cls = (el.className || '').toLowerCase();
                if ((text.includes('export') || id.includes('export') || cls.includes('export'))
                    && text.length < 50) {
                    found.push({
                        tag: el.tagName, id: el.id,
                        text: text.substring(0, 30),
                        cls: cls.substring(0, 40),
                        visible: el.offsetParent !== null,
                        y: Math.round(el.getBoundingClientRect().top)
                    });
                }
            });
            return found;
        }
    """)
    print(f"    Export elements: {export_info}")

    # Step 1: Click Export in toolbar to open the modal
    try:
        export_el = page.locator("span.tool__export").first
        await export_el.click()
        await asyncio.sleep(2)

        # Step 2: In the modal, select CSV radio and click EXPORT button
        # CSV radio might already be selected, but click it to be sure
        csv_radio = page.locator("text=CSV").first
        if await csv_radio.is_visible(timeout=3000):
            await csv_radio.click()
            await asyncio.sleep(0.5)

        # Step 3: Click the blue EXPORT button in the modal
        async with page.expect_download(timeout=60000) as dl_info:
            export_btn = page.locator("button:has-text('EXPORT'), a:has-text('EXPORT')").first
            if await export_btn.is_visible(timeout=3000):
                await export_btn.click()
            else:
                # Fallback: click by JS
                await page.evaluate("""
                    () => {
                        const btns = document.querySelectorAll('button, a');
                        for (const btn of btns) {
                            if (btn.textContent.trim() === 'EXPORT') {
                                btn.click();
                                return;
                            }
                        }
                    }
                """)

        download = await dl_info.value
        await download.save_as(dest)
        print(f"[OK] Exported to {dest}")
        return dest

    except Exception as e:
        print(f"[!] Export failed: {e}")
        await page.screenshot(path=os.path.join(DOWNLOAD_DIR, f"export_fail_{safe_name}.png"))
        return None


def merge_csvs():
    """Merge all brand export CSVs + existing API CSV into one."""
    files = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, "keepa_*.csv")))

    # Also include the API results if they exist
    api_file = os.path.join(SCRIPT_DIR, "..", "raw", "keepa_connect_beauty_latest.csv")
    if os.path.exists(api_file):
        files.append(api_file)

    if not files:
        print("[!] No CSV files found")
        return 0

    all_rows = []
    headers = None
    seen_asins = set()

    for f in files:
        try:
            with open(f, "r", encoding="utf-8-sig") as csvf:
                reader = csv.DictReader(csvf)
                if headers is None:
                    headers = reader.fieldnames
                for row in reader:
                    asin = row.get("ASIN", "")
                    if asin and asin not in seen_asins:
                        seen_asins.add(asin)
                        all_rows.append(row)
        except Exception as e:
            print(f"[!] Error reading {f}: {e}")

    if all_rows and headers:
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"\n[OK] Merged {len(all_rows)} unique products -> {OUTPUT_FILE}")
        return len(all_rows)
    return 0


async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            accept_downloads=True,
        )
        page = await ctx.new_page()

        await login(page)

        for brand in BRANDS:
            print(f"\n{'='*40}")
            print(f"Brand: {brand}")
            print(f"{'='*40}")

            await open_finder(page)
            await set_brand_and_search(page, brand)

            has_results = await wait_for_results(page)
            if has_results:
                await set_rows_per_page(page)
                await asyncio.sleep(3)
                await export_csv(page, brand)
            else:
                await page.screenshot(path=os.path.join(DOWNLOAD_DIR, f"no_results_{brand}.png"))

            await asyncio.sleep(2)

        await browser.close()

    total = merge_csvs()
    print(f"\nDone. {total} total unique products in {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
