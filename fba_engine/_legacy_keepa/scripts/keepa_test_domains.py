"""Test which Keepa domain ID corresponds to Amazon.co.uk for Product Viewer."""
import asyncio
import os
from playwright.async_api import async_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "..", "downloads")
# Known UK ASIN for Maybelline product
TEST_ASIN = "B08FCS1PJG"  # A common UK beauty ASIN
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

        # Test domains 1-10 with EAN
        for domain in range(1, 11):
            url = f"https://keepa.com/#!product/{domain}-{TEST_EAN}"
            await page.evaluate(f"window.location.href = '{url}'")
            await asyncio.sleep(4)
            body = await page.evaluate("() => document.body.innerText.substring(0, 500)")
            has_data = "does not provide" not in body and "not found" not in body.lower()
            title = ""
            if has_data:
                title = await page.evaluate("""
                    () => {
                        const h2 = document.querySelector('h2');
                        return h2 ? h2.textContent.trim().substring(0, 60) : '';
                    }
                """)
            status = "HAS DATA" if has_data else "no data"
            print(f"Domain {domain}: {status} {title}")
            if has_data:
                await page.screenshot(path=os.path.join(DOWNLOAD_DIR, f"domain_{domain}_ean.png"))

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
