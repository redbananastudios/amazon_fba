"""Test if Keepa API is accessible with PRO subscription.
Keepa API page shows the API key for PRO subscribers."""
import asyncio
import os
from playwright.async_api import async_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "..", "downloads")

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

        # Navigate to Keepa API page
        await page.evaluate("window.location.href = 'https://keepa.com/#!api'")
        await asyncio.sleep(5)
        await page.screenshot(path=os.path.join(DOWNLOAD_DIR, "keepa_api_page.png"))

        # Try to find API key
        api_info = await page.evaluate("""
            () => {
                const body = document.body.innerText;
                // Look for API key pattern
                const keyMatch = body.match(/[a-zA-Z0-9]{30,}/);
                return {
                    bodyText: body.substring(0, 3000),
                    possibleKey: keyMatch ? keyMatch[0] : null
                };
            }
        """)
        print(f"\n[*] API page text:\n{api_info.get('bodyText', '')[:2000]}")
        if api_info.get('possibleKey'):
            print(f"\n[*] Possible API key: {api_info['possibleKey']}")

        # Also check Product Finder page - scroll to find Product Codes section
        print("\n\n[*] Now checking Product Finder for Product Codes field...")
        await page.evaluate("window.location.href = 'https://keepa.com/#!tracking'")
        await asyncio.sleep(2)
        await page.evaluate("window.location.href = 'https://keepa.com/#!finder/6'")
        await asyncio.sleep(8)

        # Scroll all the way down and look for product codes
        for i in range(20):
            await page.evaluate("window.scrollBy(0, 500)")
            await asyncio.sleep(0.3)

        await page.screenshot(path=os.path.join(DOWNLOAD_DIR, "finder_bottom.png"))

        # Find the product codes section
        product_codes_info = await page.evaluate("""
            () => {
                const all = document.querySelectorAll('*');
                const matches = [];
                for (const el of all) {
                    const text = el.textContent.trim();
                    const id = el.id || '';
                    if ((text.toLowerCase().includes('product code') || id.toLowerCase().includes('productcode'))
                        && text.length < 200) {
                        matches.push({
                            tag: el.tagName,
                            id: el.id,
                            text: text.substring(0, 100),
                            class: (el.className || '').substring(0, 60),
                            y: Math.round(el.getBoundingClientRect().top),
                            visible: el.offsetParent !== null
                        });
                    }
                }
                return matches;
            }
        """)
        print("\n[*] Product Codes elements:")
        for m in product_codes_info:
            print(f"  {m}")

        await asyncio.sleep(2)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
