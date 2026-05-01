"""Debug script: discover Keepa Product Finder form elements."""
import asyncio
import json
import os
from urllib.parse import quote as url_quote
from playwright.async_api import async_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "..", "downloads")
KEEPA_DOMAIN = "6"

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        # Login
        await page.goto("https://keepa.com/", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        await page.evaluate("""
            () => {
                window.location.href = 'https://keepa.com/#!login';
            }
        """)
        await asyncio.sleep(3)
        await page.evaluate("""
            () => {
                const u = document.getElementById('username');
                const p = document.getElementById('password');
                if (u) { u.value = 'JustThis'; u.dispatchEvent(new Event('input', {bubbles:true})); }
                if (p) { p.value = 'Polopolo121'; p.dispatchEvent(new Event('input', {bubbles:true})); }
                const btn = document.getElementById('submitLogin');
                if (btn) btn.click();
            }
        """)
        await asyncio.sleep(5)
        print("[*] Logged in")

        # Navigate to Product Finder
        minimal = {"f": {}, "s": [], "t": "f"}
        encoded = url_quote(json.dumps(minimal, separators=(",", ":")))
        await page.evaluate("window.location.href = 'https://keepa.com/#!tracking'")
        await asyncio.sleep(3)
        await page.evaluate(f"window.location.href = 'https://keepa.com/#!finder/{KEEPA_DOMAIN}/{encoded}'")
        await asyncio.sleep(8)

        # Dump all form elements
        elements = await page.evaluate("""
            () => {
                const results = [];

                // All inputs
                document.querySelectorAll('input, textarea, select').forEach(el => {
                    const rect = el.getBoundingClientRect();
                    results.push({
                        tag: el.tagName,
                        type: el.type,
                        id: el.id,
                        name: el.name,
                        placeholder: el.placeholder,
                        class: (el.className || '').substring(0, 60),
                        visible: el.offsetParent !== null,
                        y: Math.round(rect.top),
                        parentText: (el.parentElement?.textContent || '').substring(0, 80).trim()
                    });
                });

                // All buttons
                document.querySelectorAll('button, [role="button"]').forEach(el => {
                    results.push({
                        tag: el.tagName,
                        type: 'button',
                        id: el.id,
                        text: el.textContent.trim().substring(0, 60),
                        class: (el.className || '').substring(0, 60),
                        visible: el.offsetParent !== null,
                        y: Math.round(el.getBoundingClientRect().top),
                    });
                });

                return results;
            }
        """)

        # Filter for interesting elements
        print("\n=== INPUTS with 'code', 'ean', 'asin', 'product' in id/name/placeholder ===")
        for el in elements:
            if el.get('tag') in ('INPUT', 'TEXTAREA', 'SELECT'):
                searchable = f"{el.get('id','')} {el.get('name','')} {el.get('placeholder','')} {el.get('parentText','')}".lower()
                if any(k in searchable for k in ['code', 'ean', 'asin', 'product', 'upc', 'gtin']):
                    print(f"  {json.dumps(el, indent=2)}")

        print("\n=== ALL AUTOCOMPLETE INPUTS ===")
        for el in elements:
            if el.get('tag') in ('INPUT',) and 'autocomplete' in (el.get('id','') + el.get('class','')).lower():
                print(f"  {json.dumps(el, indent=2)}")

        print("\n=== BUTTONS ===")
        for el in elements:
            if el.get('type') == 'button':
                text = el.get('text', '').lower()
                if any(k in text for k in ['find', 'search', 'export', 'submit', 'clear']):
                    print(f"  {json.dumps(el, indent=2)}")

        # Also scroll down and take screenshots
        for i in range(5):
            await page.evaluate(f"window.scrollBy(0, 600)")
            await asyncio.sleep(0.5)
            await page.screenshot(path=os.path.join(DOWNLOAD_DIR, f"finder_scroll_{i}.png"))

        # Look for "Product Code" label/section
        print("\n=== LABELS containing 'code', 'ean', 'asin' ===")
        labels = await page.evaluate("""
            () => {
                const results = [];
                document.querySelectorAll('label, h3, h4, span, div').forEach(el => {
                    const text = el.textContent.trim().toLowerCase();
                    if ((text.includes('product code') || text.includes('ean') || text.includes('asin') || text.includes('upc'))
                        && text.length < 100) {
                        results.push({
                            tag: el.tagName,
                            id: el.id,
                            for: el.getAttribute('for'),
                            text: el.textContent.trim().substring(0, 80),
                            class: (el.className || '').substring(0, 60),
                            y: Math.round(el.getBoundingClientRect().top)
                        });
                    }
                });
                return results;
            }
        """)
        for l in labels:
            print(f"  {json.dumps(l, indent=2)}")

        await asyncio.sleep(2)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
