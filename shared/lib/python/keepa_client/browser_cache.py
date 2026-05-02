"""Keepa Browser scrape cache — chart-quality signals per ASIN.

The Keepa Pro Browser carries data that the API doesn't expose
cleanly (per-seller historical Buy Box share, real per-seller
sold-30d, the precomputed 365-day price/rank averages). For
single-ASIN deep dives, the operator's verdict quality is bounded
by whether the engine can read those signals.

Architecture: the engine doesn't drive the browser itself. A
separate scraping process (Claude-in-Chrome MCP today, Playwright
service tomorrow) writes a JSON cache file per ASIN. The engine's
``keepa_browser_enrich`` step reads the cache and merges richer
fields into the row before the validator runs.

Cache layout:
    .cache/keepa_browser/<asin>.json

TTL default: 24h. Stale entries are still readable (caller decides
whether to use stale data vs trigger a re-scrape).

Why not auto-scrape from the engine? The MCP-based browser drive
runs in Claude's environment, not the engine's. The engine code
can't call MCP tools. Splitting at the JSON cache boundary keeps
the engine self-contained and lets the scraper evolve
independently (today: Claude+MCP; tomorrow: Playwright headless;
later: a one-shot CLI).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ────────────────────────────────────────────────────────────────────────
# Schema
# ────────────────────────────────────────────────────────────────────────


class BrowserSellerStat(BaseModel):
    """One row from the Keepa Browser "Buy Box Statistics" tab.

    Mirrors what the Browser's BB Statistics table shows per seller.
    The validator's share-aware velocity predictor uses these to
    replace the equal-split + API-buyBoxStats assumptions with real
    per-seller distribution.
    """

    model_config = ConfigDict(extra="ignore")

    seller_id: str       # human-readable name (Browser shows the display name); could be Amazon merchant ID if available
    pct_won: float       # raw percent (0.0–100.0). Browser shows "59 %" → store as 59. Sub-1% values (e.g. 0.5) are real and stored literally — DO NOT treat <=1 as already-fraction.
    avg_price: Optional[float] = None
    avg_offer_count: Optional[int] = None
    stock: Optional[int] = None
    is_fba: Optional[bool] = None
    last_won_at: Optional[str] = None     # ISO 8601 if scraped


class BrowserActiveOffer(BaseModel):
    """One row from the Keepa Browser "Offers" tab — currently
    listed sellers (filtered to live, not historical)."""

    model_config = ConfigDict(extra="ignore")

    seller_id: str
    stock: Optional[int] = None
    sold_lifetime: Optional[int] = None
    sold_30d: Optional[int] = None
    is_fba: Optional[bool] = None
    is_prime: Optional[bool] = None
    current_price: Optional[float] = None


class BrowserProductDetails(BaseModel):
    """Precomputed columns from the Keepa Browser "Product Details"
    tab. Most are also derivable from the API's stats lanes, but
    Browser's precomputed values are typically more accurate (e.g.
    365-day Buy Box low) than what we'd compute from the API's
    csv arrays."""

    model_config = ConfigDict(extra="ignore")

    title: Optional[str] = None
    brand: Optional[str] = None
    tracking_since: Optional[str] = None       # YYYY-MM-DD
    listed_since: Optional[str] = None
    buy_box_current: Optional[float] = None
    buy_box_avg_30d: Optional[float] = None
    buy_box_avg_90d: Optional[float] = None
    buy_box_avg_180d: Optional[float] = None
    buy_box_avg_365d: Optional[float] = None
    buy_box_lowest_365d: Optional[float] = None
    buy_box_lowest_ever: Optional[float] = None
    buy_box_highest: Optional[float] = None
    buy_box_oos_pct_90d: Optional[float] = None    # 0.0–1.0
    buy_box_stock: Optional[int] = None
    sales_rank_current: Optional[int] = None
    sales_rank_avg_30d: Optional[int] = None
    sales_rank_avg_90d: Optional[int] = None
    sales_rank_avg_365d: Optional[int] = None
    sales_rank_drops_30d: Optional[int] = None
    sales_rank_drops_90d: Optional[int] = None
    rating_current: Optional[float] = None
    review_count_current: Optional[int] = None
    review_count_avg_90d: Optional[int] = None
    review_count_avg_365d: Optional[int] = None
    total_offer_count: Optional[int] = None
    fba_pickpack_fee: Optional[float] = None
    referral_fee_pct: Optional[float] = None       # as fraction (0.15)
    suggested_lower_price: Optional[float] = None  # Keepa's
                                                    # "competitive undercut"
                                                    # hint
    bb_eligible_fba_count: Optional[int] = None
    bb_eligible_fbm_count: Optional[int] = None


class BrowserScrape(BaseModel):
    """The full Keepa Browser scrape for one ASIN. Written to
    .cache/keepa_browser/<asin>.json by the scraper process and
    consumed by the keepa_browser_enrich step."""

    model_config = ConfigDict(extra="ignore")

    asin: str
    scraped_at: str   # ISO 8601 with Z
    scraped_via: str = "claude_in_chrome_mcp"
    product_details: BrowserProductDetails = Field(default_factory=BrowserProductDetails)
    buy_box_seller_stats: list[BrowserSellerStat] = Field(default_factory=list)
    active_offers: list[BrowserActiveOffer] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────────
# Cache I/O
# ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BrowserCacheEntry:
    """A loaded cache entry plus freshness metadata."""

    scrape: BrowserScrape
    age_seconds: float
    is_stale: bool


def cache_root(repo_root: Path | None = None) -> Path:
    """Return the canonical cache directory (creates if missing)."""
    if repo_root is None:
        # Walk up from this file's directory to find the repo (root has
        # both `fba_engine/` and `shared/` as immediate children).
        for ancestor in Path(__file__).resolve().parents:
            if (ancestor / "fba_engine").is_dir() and (ancestor / "shared").is_dir():
                repo_root = ancestor
                break
    if repo_root is None:
        repo_root = Path.cwd()
    root = repo_root / ".cache" / "keepa_browser"
    root.mkdir(parents=True, exist_ok=True)
    return root


_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


def cache_path_for(asin: str, repo_root: Path | None = None) -> Path:
    """Return the cache file path for a given ASIN.

    Raises ``ValueError`` if the ASIN doesn't match Amazon's
    canonical 10-char alphanumeric format. This guards against
    path traversal via crafted ASIN inputs (e.g. ``../../etc``).
    """
    canonical = str(asin).upper().strip()
    if not _ASIN_RE.match(canonical):
        raise ValueError(f"Invalid ASIN: {asin!r}")
    return cache_root(repo_root) / f"{canonical}.json"


def write(scrape: BrowserScrape, repo_root: Path | None = None) -> Path:
    """Write a scrape to cache. Returns the path written."""
    path = cache_path_for(scrape.asin, repo_root)
    path.write_text(
        scrape.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )
    return path


def read(
    asin: str,
    *,
    ttl_seconds: int = 24 * 60 * 60,
    allow_stale: bool = True,
    repo_root: Path | None = None,
) -> Optional[BrowserCacheEntry]:
    """Read a scrape from cache.

    Returns None when the cache file is missing OR malformed.
    Returns the entry when fresh, OR (when ``allow_stale=True``)
    when stale. Caller checks ``entry.is_stale`` to decide whether
    to use stale data or trigger a re-scrape.
    """
    path = cache_path_for(asin, repo_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        scrape = BrowserScrape.model_validate(data)
    except Exception:
        # Malformed cache → treat as missing; caller can re-scrape.
        return None

    try:
        scraped_at = datetime.fromisoformat(scrape.scraped_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    age = (datetime.now(timezone.utc) - scraped_at).total_seconds()
    is_stale = age > ttl_seconds

    if is_stale and not allow_stale:
        return None
    return BrowserCacheEntry(scrape=scrape, age_seconds=age, is_stale=is_stale)


def now_iso() -> str:
    """ISO 8601 timestamp with Z suffix (Keepa-friendly)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
