"""SP-API barcode -> ASIN resolver for supplier-pricelist matching.

This is a best-effort second pass after local Keepa CSV matching. It only
runs when the MCP CLI is built, Node is available, and SP-API credentials are
present in the environment. Failures never stop the supplier pipeline; rows
fall back to the existing "No Amazon match found" path.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

UK_MARKETPLACE_ID = "A1F83G8C2ARO7P"
PRICING_BATCH_SIZE = 20


def _find_repo_root(start: Path | None = None) -> Path | None:
    here = (start or Path(__file__)).resolve()
    for ancestor in [here, *here.parents]:
        if (ancestor / "fba_engine").is_dir() and (ancestor / "services").is_dir():
            return ancestor
    return None


def _find_cli(repo_root: Path | None) -> Path | None:
    if repo_root is None:
        return None
    cli = repo_root / "services" / "amazon-fba-fees-mcp" / "dist" / "cli.js"
    return cli if cli.is_file() else None


def _runtime_ready() -> tuple[bool, str]:
    if shutil.which("node") is None:
        return False, "node executable not found on PATH"
    for name in ("SP_API_CLIENT_ID", "SP_API_CLIENT_SECRET", "SP_API_REFRESH_TOKEN"):
        if not os.environ.get(name):
            return False, f"{name} env var not set"
    return True, "ready"


def is_sp_api_code_resolver_available(repo_root: Path | None = None) -> tuple[bool, str]:
    repo = repo_root or _find_repo_root()
    if repo is None:
        return False, "could not locate repo root"
    if _find_cli(repo) is None:
        return False, (
            f"MCP CLI not found at {repo}/services/amazon-fba-fees-mcp/dist/cli.js - "
            "run `npm run build` in that folder"
        )
    return _runtime_ready()


def _call_cli(
    cli_path: Path,
    subcommand: str,
    payload: dict[str, Any] | None = None,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any] | None:
    node = shutil.which("node")
    if node is None:
        return None

    args = [node, str(cli_path), subcommand]
    input_text = None
    if payload is not None:
        args.extend(["--input", "-"])
        input_text = json.dumps(payload, allow_nan=False)
    if extra_args:
        args.extend(extra_args)

    try:
        proc = subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("sp_api_code_resolver: %s timed out", subcommand)
        return None
    except Exception:
        logger.exception("sp_api_code_resolver: CLI invocation failed")
        return None

    if proc.returncode != 0:
        stderr = proc.stderr.strip().splitlines()[-1] if proc.stderr else ""
        logger.warning(
            "sp_api_code_resolver: %s exit %s - %s",
            subcommand,
            proc.returncode,
            stderr,
        )
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.warning("sp_api_code_resolver: failed to parse %s stdout", subcommand)
        return None


def _chunk(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def _pricing_by_asin(
    cli_path: Path,
    asins: list[str],
    marketplace_id: str,
) -> dict[str, dict[str, Any]]:
    pricing: dict[str, dict[str, Any]] = {}
    for batch in _chunk(asins, PRICING_BATCH_SIZE):
        response = _call_cli(
            cli_path,
            "pricing",
            extra_args=[
                "--asins",
                ",".join(batch),
                "--marketplace-id",
                marketplace_id,
            ],
            timeout_seconds=180,
        )
        results = response.get("results") if isinstance(response, dict) else None
        if not isinstance(results, list):
            continue
        for result in results:
            if isinstance(result, dict) and result.get("asin"):
                pricing[str(result["asin"])] = result
    return pricing


def resolve_codes_to_market_data(
    codes: list[str],
    *,
    marketplace_id: str = UK_MARKETPLACE_ID,
    include_pricing: bool = True,
) -> dict[str, dict[str, Any]]:
    """Return market-data-shaped rows keyed by supplier barcode.

    Only unique SP-API matches are returned. No-match, multiple-match, and
    error results are deliberately omitted so the caller can preserve the
    existing no-match behaviour for unsafe/ambiguous rows.
    """
    if not codes:
        return {}

    repo = _find_repo_root()
    cli = _find_cli(repo)
    if cli is None:
        logger.info("sp_api_code_resolver: CLI not found, skipping")
        return {}
    ready, reason = _runtime_ready()
    if not ready:
        logger.info("sp_api_code_resolver: skipping (%s)", reason)
        return {}

    clean_codes = list(dict.fromkeys(str(c).strip() for c in codes if str(c).strip()))
    response = _call_cli(
        cli,
        "resolve-codes",
        payload={"codes": clean_codes, "marketplace_id": marketplace_id},
        timeout_seconds=max(180, len(clean_codes) * 8),
    )
    results = response.get("results") if isinstance(response, dict) else None
    if not isinstance(results, list):
        return {}

    found = [
        r for r in results
        if isinstance(r, dict) and r.get("status") == "FOUND" and r.get("asin")
    ]
    if not found:
        return {}

    asins = list(dict.fromkeys(str(r["asin"]) for r in found))
    pricing = _pricing_by_asin(cli, asins, marketplace_id) if include_pricing else {}

    out: dict[str, dict[str, Any]] = {}
    for result in found:
        code = str(result["code"])
        asin = str(result["asin"])
        price = pricing.get(asin, {})
        offer_count_fba = price.get("offer_count_fba")
        offer_count_new = price.get("offer_count_new")
        fba_seller_count = (
            offer_count_fba
            if offer_count_fba is not None
            else offer_count_new
        )
        out[code] = {
            "asin": asin,
            "title": result.get("title"),
            "brand": result.get("brand"),
            "buy_box_price": price.get("buy_box_price"),
            "amazon_price": None,
            "new_fba_price": None,
            "amazon_status": "UNKNOWN",
            "fba_seller_count": fba_seller_count,
            "monthly_sales_estimate": None,
            "size_tier": "UNKNOWN",
            "gated": "UNKNOWN",
            "history_days": 0,
            "price_history": None,
            "sp_api_code_resolved": True,
            "sp_api_code": code,
            "live_buy_box_seller": price.get("buy_box_seller"),
            "live_offer_count_new": offer_count_new,
            "live_offer_count_fba": offer_count_fba,
        }

    logger.info(
        "sp_api_code_resolver: resolved %d/%d unmatched product codes",
        len(out),
        len(clean_codes),
    )
    return out
