"""`open` subcommand — one-shot browser launches during manual validation.

Used after a strategy run to inspect specific shortlisted ASINs without
copying URLs by hand.

    python run.py open --asin B0XXXXXXX --target keepa
    python run.py open --asin B0XXXXXXX --target amazon
    python run.py open --asin B0XXXXXXX --target supplier
    python run.py open --seller A1B2C3D4E5 --target storefront

Per `docs/PRD-sourcing-strategies.md` §9: pure URL launches via
`webbrowser.open()` — no browser automation, no scraping. Supplier-search
URLs are constructed from the same templates Skill 99 uses (loaded from
`shared/config/supplier_leads.yaml`) so the manual flow matches what gets
written to `supplier_leads.md`.
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from typing import Callable
from urllib.parse import quote_plus

from fba_engine.steps.supplier_leads import (
    DEFAULT_SUPPLIER_LEADS_CONFIG_PATH,
    load_supplier_leads_config,
)

# Marketplace / domain identifiers — UK only per PRD §2 ("Multi-marketplace
# (UK only) is out of scope").
KEEPA_MARKETPLACE_CODE: int = 2
AMAZON_DOMAIN: str = "amazon.co.uk"


# ────────────────────────────────────────────────────────────────────────
# URL builders (pure — testable without launching a browser).
# ────────────────────────────────────────────────────────────────────────


def keepa_url(asin: str) -> str:
    """Keepa chart URL for `asin` in the UK marketplace."""
    return f"https://keepa.com/#!product/{KEEPA_MARKETPLACE_CODE}-{asin}"


def amazon_url(asin: str) -> str:
    """Amazon UK product detail page for `asin`."""
    return f"https://www.{AMAZON_DOMAIN}/dp/{asin}"


def storefront_url(seller_id: str) -> str:
    """Amazon UK seller storefront page."""
    return f"https://www.{AMAZON_DOMAIN}/sp?seller={seller_id}"


def supplier_search_urls(
    *,
    brand: str = "",
    product_name: str = "",
    config_path=None,
) -> list[tuple[str, str]]:
    """Render supplier-search URLs from the shared templates.

    Returns a list of `(label, url)` pairs, in template order, with
    templates that would produce empty queries (missing brand for
    skip_if_brand_missing) filtered out.
    """
    config = load_supplier_leads_config(
        config_path or DEFAULT_SUPPLIER_LEADS_CONFIG_PATH
    )
    pairs: list[tuple[str, str]] = []
    substitutions = {"brand": brand.strip(), "product_name": product_name.strip()}
    for template in config.search_templates:
        if template.skip_if_brand_missing and not substitutions["brand"]:
            continue
        try:
            rendered = template.template.format(**substitutions)
        except KeyError:
            continue
        if not rendered.strip():
            continue
        pairs.append((template.label, config.search_engine_url + quote_plus(rendered)))
    return pairs


# ────────────────────────────────────────────────────────────────────────
# Entry point.
# ────────────────────────────────────────────────────────────────────────


VALID_TARGETS: tuple[str, ...] = ("keepa", "amazon", "supplier", "storefront")


def resolve_urls(
    *,
    target: str,
    asin: str = "",
    seller: str = "",
    brand: str = "",
    product_name: str = "",
    config_path=None,
) -> list[tuple[str, str]]:
    """Resolve `--target` + identifiers into a list of (label, url) pairs.

    Pure — does not call `webbrowser.open`. Tests mock at the dispatch
    layer (`launch_urls`) and call `resolve_urls` directly to verify the
    URL construction.
    """
    if target not in VALID_TARGETS:
        raise ValueError(
            f"Unknown --target '{target}'. Must be one of: {', '.join(VALID_TARGETS)}"
        )

    if target == "keepa":
        if not asin:
            raise ValueError("--target keepa requires --asin")
        return [("Keepa chart", keepa_url(asin))]
    if target == "amazon":
        if not asin:
            raise ValueError("--target amazon requires --asin")
        return [("Amazon listing", amazon_url(asin))]
    if target == "storefront":
        if not seller:
            raise ValueError("--target storefront requires --seller")
        return [("Seller storefront", storefront_url(seller))]
    if target == "supplier":
        if not (brand or product_name):
            raise ValueError(
                "--target supplier requires --brand and/or --product-name "
                "(supplier search templates need brand/product context)"
            )
        return supplier_search_urls(
            brand=brand, product_name=product_name, config_path=config_path
        )
    # Defensive — VALID_TARGETS is exhaustive above.
    raise ValueError(f"Unhandled target: {target}")


def launch_urls(
    urls: list[tuple[str, str]],
    *,
    open_fn: Callable[[str], bool] | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> int:
    """Open each URL in the user's default browser.

    Returns the number of URLs opened. Pure dependency injection on
    `open_fn` / `log_fn` for testability — production uses
    `webbrowser.open` and `print`.
    """
    open_fn = open_fn or webbrowser.open
    log_fn = log_fn or print
    count = 0
    for label, url in urls:
        log_fn(f"Opening {label}: {url}")
        open_fn(url)
        count += 1
    return count


# ────────────────────────────────────────────────────────────────────────
# Argparse + main.
# ────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py open",
        description=(
            "Open a Keepa chart, Amazon listing, supplier search, or seller "
            "storefront in the default browser."
        ),
    )
    parser.add_argument(
        "--target", required=True, choices=VALID_TARGETS,
        help="What to open. supplier opens all 3 supplier searches.",
    )
    parser.add_argument("--asin", default="")
    parser.add_argument("--seller", default="")
    parser.add_argument("--brand", default="")
    parser.add_argument("--product-name", default="", dest="product_name")
    parser.add_argument(
        "--config", default=None,
        help="Optional path to a non-canonical supplier_leads.yaml.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        urls = resolve_urls(
            target=args.target,
            asin=args.asin,
            seller=args.seller,
            brand=args.brand,
            product_name=args.product_name,
            config_path=args.config,
        )
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    launch_urls(urls)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
