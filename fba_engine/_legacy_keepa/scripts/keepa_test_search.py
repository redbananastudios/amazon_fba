"""Quick test: search a single EAN on Keepa and screenshot the result."""
import asyncio
import os
from playwright.async_api import async_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "..", "downloads")
# A known Maybelline EAN from the price list
TEST_EAN = "3600531561277"

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await ctx.new_page()

        # Login
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
        print("[*] Logged in")

        # Test 1: Direct product URL with EAN
        print(f"[*] Test 1: #!product/2-{TEST_EAN}")
        await page.evaluate(f"window.location.href = 'https://keepa.com/#!product/2-{TEST_EAN}'")
        await asyncio.sleep(6)
        await page.screenshot(path=os.path.join(DOWNLOAD_DIR, "test_product_ean.png"))
        url1 = await page.evaluate("() => window.location.hash")
        print(f"    URL hash: {url1}")

        # Test 2: Using search input
        print(f"[*] Test 2: Search bar with EAN")
        await page.evaluate("window.location.href = 'https://keepa.com/#!search'")
        await asyncio.sleep(3)

        # Try to use the search box
        search_result = await page.evaluate(f"""
            () => {{
                const input = document.getElementById('searchInput') ||
                              document.querySelector('input[name="search"]') ||
                              document.querySelector('.search-input');
                if (input) {{
                    input.value = '{TEST_EAN}';
                    input.dispatchEvent(new Event('input', {{bubbles: true}}));
                    input.dispatchEvent(new Event('keydown', {{bubbles: true, key: 'Enter', keyCode: 13}}));
                    return 'found input: ' + input.id;
                }}
                return 'no input found';
            }}
        """)
        print(f"    Search: {search_result}")
        await asyncio.sleep(5)
        await page.screenshot(path=os.path.join(DOWNLOAD_DIR, "test_search_ean.png"))
        url2 = await page.evaluate("() => window.location.hash")
        print(f"    URL hash: {url2}")

        # Test 3: Direct search URL
        print(f"[*] Test 3: #!search/{TEST_EAN}")
        await page.evaluate(f"window.location.href = 'https://keepa.com/#!search/{TEST_EAN}'")
        await asyncio.sleep(6)
        await page.screenshot(path=os.path.join(DOWNLOAD_DIR, "test_search_url.png"))
        url3 = await page.evaluate("() => window.location.hash")
        print(f"    URL hash: {url3}")

        # Dump page content for analysis
        body_text = await page.evaluate("() => document.body.innerText.substring(0, 5000)")
        print(f"\n[*] Page text (first 2000 chars):\n{body_text[:2000]}")

        await asyncio.sleep(3)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
