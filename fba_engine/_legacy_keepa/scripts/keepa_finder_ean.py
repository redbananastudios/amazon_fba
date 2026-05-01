"""
Keepa Product Finder EAN lookup via browser.

Uses the Product Finder's "Product Codes" > "EAN" field.
The field is in a collapsible section that needs to be expanded first.
EANs separated by ### (Keepa's multi-value separator), max 50 per search.
"""
import asyncio
import csv
import json
import os
import glob
import sys
import time

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from playwright.async_api import async_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EAN_FILE = os.path.join(SCRIPT_DIR, "..", "raw", "eans_for_keepa.txt")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "raw")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "keepa_connect_beauty_latest.csv")
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "..", "downloads")
BATCH_SIZE = 50


def load_eans(path):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


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
    """Navigate to Product Finder for Amazon.co.uk."""
    await page.evaluate("window.location.href = 'https://keepa.com/#!tracking'")
    await asyncio.sleep(2)
    await page.evaluate("window.location.href = 'https://keepa.com/#!finder/6'")
    await asyncio.sleep(6)
    print("[OK] Product Finder open")


async def find_and_expand_product_codes(page):
    """Find the Product Codes checkbox, enable it, then return the EAN text field."""

    # Step 1: Scroll down to make the section visible
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(1)

    # Step 2: Find and click the Product Codes checkbox to enable the filter
    checkbox_result = await page.evaluate("""
        () => {
            // Find checkbox whose ID starts with 'productCodes'
            const inputs = document.querySelectorAll('input[type="checkbox"]');
            for (const cb of inputs) {
                if (cb.id && cb.id.startsWith('productCodes')) {
                    // Check it if not already checked
                    if (!cb.checked) {
                        cb.click();
                    }
                    // Also scroll it into view
                    cb.scrollIntoView({block: 'center'});
                    return {id: cb.id, checked: cb.checked};
                }
            }

            // Try by label
            const labels = document.querySelectorAll('label');
            for (const label of labels) {
                const forAttr = label.getAttribute('for') || '';
                if (forAttr.startsWith('productCodes')) {
                    label.click();
                    const cb = document.getElementById(forAttr);
                    return {id: forAttr, checked: cb ? cb.checked : 'unknown', clickedLabel: true};
                }
                if (label.textContent.trim() === 'Product Codes:') {
                    label.click();
                    return {clickedLabel: label.textContent.trim()};
                }
            }

            return null;
        }
    """)
    print(f"[*] Product Codes checkbox: {checkbox_result}")
    await asyncio.sleep(2)

    await page.screenshot(path=os.path.join(DOWNLOAD_DIR, "after_checkbox.png"))

    # Step 3: Now find the EAN text input that should have appeared
    ean_field = await page.evaluate("""
        () => {
            // Look for eanList text input
            const inputs = document.querySelectorAll('input[type="text"], textarea');
            for (const input of inputs) {
                const id = input.id || '';
                if (id.startsWith('eanList')) {
                    input.scrollIntoView({block: 'center'});
                    return {id: id, tag: input.tagName, type: input.type, visible: input.offsetParent !== null};
                }
            }

            // Check all inputs and list them for debugging
            const all = [];
            document.querySelectorAll('input, textarea').forEach(el => {
                const id = el.id || '';
                if (id && (id.includes('ean') || id.includes('EAN') || id.includes('upc')
                    || id.includes('asin') || id.includes('productCode'))) {
                    all.push({id: id, type: el.type, visible: el.offsetParent !== null});
                }
            });
            return {notFound: true, relatedFields: all};
        }
    """)
    print(f"[*] EAN field: {ean_field}")

    await page.screenshot(path=os.path.join(DOWNLOAD_DIR, "ean_field_state.png"))
    return ean_field


async def fill_eans_and_search(page, eans, ean_field_info):
    """Fill the EAN field and click Find Products."""
    ean_str = "###".join(eans)  # Keepa multi-value separator
    field_id = ean_field_info.get("id", "")

    if not field_id:
        print("[!] No EAN field ID found")
        return False

    # Make field visible if hidden and fill it
    filled = await page.evaluate(f"""
        () => {{
            const el = document.getElementById('{field_id}');
            if (!el) return 'element not found';

            // Make visible if hidden
            if (el.offsetParent === null) {{
                el.style.display = 'block';
                el.style.visibility = 'visible';
                // Also show parent containers
                let parent = el.parentElement;
                for (let i = 0; i < 10 && parent; i++) {{
                    parent.style.display = 'block';
                    parent.style.visibility = 'visible';
                    parent = parent.parentElement;
                }}
            }}

            // Focus and fill
            el.focus();
            el.value = '{ean_str}';
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            el.dispatchEvent(new Event('blur', {{bubbles: true}}));

            // Also try jQuery trigger if available
            if (typeof jQuery !== 'undefined') {{
                jQuery(el).val('{ean_str}').trigger('input').trigger('change');
            }}

            return 'filled: ' + el.value.substring(0, 50) + '...';
        }}
    """)
    print(f"[*] Fill result: {filled}")

    await asyncio.sleep(1)

    # Switch the operator to "Is X of" if needed (for multi-value)
    await page.evaluate("""
        () => {
            // Look for operator dropdowns near the EAN field
            const selects = document.querySelectorAll('select');
            for (const sel of selects) {
                const name = sel.name || sel.id || '';
                if (name.includes('eanList') || name.includes('productCodes')) {
                    // Set to "Is X of" (value might be 'isXOf' or similar)
                    for (const opt of sel.options) {
                        if (opt.text.includes('Is X of') || opt.text.includes('is X of')
                            || opt.text.includes('Is one of') || opt.value === 'isXOf') {
                            sel.value = opt.value;
                            sel.dispatchEvent(new Event('change', {bubbles: true}));
                            break;
                        }
                    }
                }
            }
        }
    """)

    # Click Find Products
    await page.evaluate("""
        () => {
            const btns = document.querySelectorAll('button, a, div, span');
            for (const btn of btns) {
                const text = btn.textContent.trim().toUpperCase();
                if (text === 'FIND PRODUCTS' || text.startsWith('FIND PRODUCTS')) {
                    btn.click();
                    return true;
                }
            }
            // Fallback: try ID patterns
            const fb = document.getElementById('findProducts') ||
                       document.getElementById('submitFinder') ||
                       document.getElementById('findProductsButton');
            if (fb) { fb.click(); return true; }
            return false;
        }
    """)
    print("[*] Clicked Find Products")

    # Wait for results
    await asyncio.sleep(10)
    await page.screenshot(path=os.path.join(DOWNLOAD_DIR, "after_search.png"))

    # Check for results
    result_info = await page.evaluate("""
        () => {
            const body = document.body.innerText;
            // Look for result count
            const match = body.match(/(\\d+)\\s*(?:result|product)/i);
            // Look for ag-grid
            const hasGrid = !!document.querySelector('.ag-body-viewport .ag-row');
            return {
                resultText: match ? match[0] : 'unknown',
                hasGrid: hasGrid,
                bodySnippet: body.substring(0, 500)
            };
        }
    """)
    print(f"[*] Results: {result_info.get('resultText')}, grid: {result_info.get('hasGrid')}")

    return result_info.get("hasGrid", False)


async def export_results(page, batch_num):
    """Click export and download CSV."""
    # Find and click export button
    await page.evaluate("""
        () => {
            const btns = document.querySelectorAll('button, a, span, div');
            for (const btn of btns) {
                const text = btn.textContent.trim().toLowerCase();
                const id = (btn.id || '').toLowerCase();
                if (text === 'export' || id.includes('export')) {
                    btn.click();
                    return;
                }
            }
        }
    """)

    try:
        async with page.expect_download(timeout=30000) as dl_info:
            pass  # Export was already clicked
        download = await dl_info.value
        dest = os.path.join(DOWNLOAD_DIR, f"keepa_batch_{batch_num}.csv")
        await download.save_as(dest)
        print(f"[OK] Exported batch {batch_num}")
        return dest
    except Exception as e:
        print(f"[!] Export failed: {e}")
        # Try clicking export again with download listener
        try:
            async with page.expect_download(timeout=30000) as dl_info:
                await page.evaluate("""
                    () => {
                        const btns = document.querySelectorAll('button, a, span, div');
                        for (const btn of btns) {
                            const text = btn.textContent.trim().toLowerCase();
                            if (text === 'export') { btn.click(); return; }
                        }
                    }
                """)
            download = await dl_info.value
            dest = os.path.join(DOWNLOAD_DIR, f"keepa_batch_{batch_num}.csv")
            await download.save_as(dest)
            print(f"[OK] Exported batch {batch_num} (retry)")
            return dest
        except Exception as e2:
            print(f"[!] Export retry failed: {e2}")
            return None


def merge_csvs():
    """Merge batch CSVs into one output file."""
    files = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, "keepa_batch_*.csv")))
    if not files:
        print("[!] No batch files found")
        return 0

    all_rows = []
    headers = None
    for f in files:
        with open(f, "r", encoding="utf-8-sig") as csvf:
            reader = csv.DictReader(csvf)
            if headers is None:
                headers = reader.fieldnames
            for row in reader:
                all_rows.append(row)

    if all_rows and headers:
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"[OK] Merged {len(all_rows)} rows -> {OUTPUT_FILE}")
        return len(all_rows)
    return 0


async def main():
    eans = load_eans(EAN_FILE)
    print(f"[*] {len(eans)} EANs to look up")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # Clear old batches
    for old in glob.glob(os.path.join(DOWNLOAD_DIR, "keepa_batch_*.csv")):
        os.remove(old)

    batches = [eans[i:i + BATCH_SIZE] for i in range(0, len(eans), BATCH_SIZE)]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080}, accept_downloads=True)
        page = await ctx.new_page()

        await login(page)
        await open_finder(page)

        # Find the Product Codes / EAN field
        ean_field = await find_and_expand_product_codes(page)

        if not ean_field:
            print("[!] Could not find EAN field. Taking diagnostic screenshots...")
            # Scroll through entire page taking screenshots
            for i in range(30):
                await page.evaluate("window.scrollBy(0, 400)")
                await asyncio.sleep(0.3)
            await page.screenshot(path=os.path.join(DOWNLOAD_DIR, "finder_full_scroll.png"), full_page=True)
            await browser.close()
            return

        # Process first batch to test
        print("\n--- Testing with first batch ---")
        has_results = await fill_eans_and_search(page, batches[0], ean_field)

        if has_results:
            exported = await export_results(page, 1)
            if exported:
                print("[OK] First batch successful! Processing remaining...")
                for i, batch in enumerate(batches[1:], 2):
                    await open_finder(page)
                    await asyncio.sleep(2)
                    ean_field = await find_and_expand_product_codes(page)
                    if ean_field:
                        has_results = await fill_eans_and_search(page, batch, ean_field)
                        if has_results:
                            await export_results(page, i)
                    await asyncio.sleep(2)
        else:
            print("[!] No results from first batch. Check screenshots.")

        await asyncio.sleep(3)
        await browser.close()

    total = merge_csvs()
    print(f"\n[*] Done. {total} products found.")


if __name__ == "__main__":
    asyncio.run(main())
