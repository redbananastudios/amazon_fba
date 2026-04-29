"""Preflight annotation step — calls the Amazon FBA Fees MCP CLI in batches
to add SP-API-derived informational columns to matched rows.

Per spec, this is INFORMATIONAL ONLY — the decision gate (decision.py) does
NOT consider any of these fields. SHORTLIST/REVIEW/REJECT counts are
unchanged before vs after this step. Restricted items still surface in
SHORTLIST when their economics warrant it; the markdown report adds a
"Restriction notes" section so the user can see at a glance which
profitable items need ungating action.

New columns added per row (None if missing/error):

    restriction_status      UNRESTRICTED | RESTRICTED | BRAND_GATED | CATEGORY_GATED
    restriction_reasons     Comma-joined reason codes
    fba_eligible            True / False
    fba_ineligibility       Comma-joined ineligibility codes
    live_buy_box            Real-time Buy Box landed price (GBP)
    live_buy_box_seller     "FBA" | "FBM"
    live_offer_count_new    Total new-condition offers
    live_offer_count_fba    FBA offers (subset)
    catalog_brand           SP-API catalog brand (canonical, may differ from Keepa)
    keepa_brand             Original Keepa brand string (for diff tracking)
    catalog_hazmat          True if hazmat hint detected, None otherwise
    preflight_errors        Comma-joined list of source-level errors
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
PREFLIGHT_COLUMNS = [
    "restriction_status",
    "restriction_reasons",
    "fba_eligible",
    "fba_ineligibility",
    "live_buy_box",
    "live_buy_box_seller",
    "live_offer_count_new",
    "live_offer_count_fba",
    "catalog_brand",
    "keepa_brand",
    "catalog_hazmat",
    "preflight_errors",
]


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


def _node_executable() -> str | None:
    return shutil.which("node")


def _check_runtime_ready() -> tuple[bool, str]:
    """Independent of CLI location: just checks node + SP-API creds."""
    if _node_executable() is None:
        return False, "node executable not found on PATH"
    if not os.environ.get("SP_API_CLIENT_ID"):
        return False, "SP_API_CLIENT_ID env var not set"
    return True, "ready"


def is_preflight_available(repo_root: Path | None = None) -> tuple[bool, str]:
    """Returns (available, reason). reason explains why it's NOT available
    when False, and is empty/'ready' when True. Auto-detects the CLI."""
    repo = repo_root or _find_repo_root()
    if repo is None:
        return False, "could not locate repo root"
    cli = _find_cli(repo)
    if cli is None:
        return False, (
            f"MCP CLI not found at {repo}/services/amazon-fba-fees-mcp/dist/cli.js — "
            "run `npm run build` in that folder"
        )
    return _check_runtime_ready()


def _row_to_item(row: dict) -> dict | None:
    asin = row.get("asin")
    selling_price = row.get("market_price") or row.get("raw_conservative_price")
    cost_price = row.get("buy_cost")
    if not asin or not selling_price or selling_price <= 0:
        return None
    if cost_price is None or cost_price < 0:
        cost_price = 0.0
    return {
        "asin": asin,
        "selling_price": float(selling_price),
        "cost_price": float(cost_price),
    }


def _chunk(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _call_cli(
    cli_path: Path,
    payload: dict,
    timeout_seconds: int = 90,
) -> dict | None:
    """Invoke the MCP CLI and parse its stdout as JSON. Returns None on error."""
    node = _node_executable()
    if node is None:
        return None
    try:
        proc = subprocess.run(
            [node, str(cli_path), "preflight", "--input", "-"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("preflight: CLI timed out after %ss", timeout_seconds)
        return None
    except Exception:
        logger.exception("preflight: CLI invocation failed")
        return None
    if proc.returncode != 0:
        stderr = proc.stderr.strip().splitlines()[-1] if proc.stderr else ""
        logger.warning("preflight: CLI exit %s — %s", proc.returncode, stderr)
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.warning("preflight: failed to parse CLI stdout as JSON")
        return None


def _coerce_result(result: dict, original_row: dict) -> dict:
    """Translate a single preflight result envelope into the flat column set
    we attach to the row. Missing/error sources resolve to None."""
    out: dict[str, Any] = {col: None for col in PREFLIGHT_COLUMNS}
    # Prefer an existing keepa_brand column if a prior pass set it — that's
    # the original Keepa value. Falling back to original_row.get("brand")
    # only on first-pass rows means re-running preflight (e.g. with refresh
    # cache) doesn't clobber the historical brand if some upstream step
    # has since merged the SP-API canonical brand into row["brand"].
    out["keepa_brand"] = (
        original_row.get("keepa_brand") or original_row.get("brand")
    )

    restrictions = result.get("restrictions") or {}
    if restrictions:
        out["restriction_status"] = restrictions.get("status")
        reasons = restrictions.get("reasons") or []
        codes = [r.get("reasonCode") for r in reasons if r.get("reasonCode")]
        if codes:
            out["restriction_reasons"] = ", ".join(codes)

    fba = result.get("fba") or {}
    if fba:
        out["fba_eligible"] = fba.get("eligible")
        ineligibility = fba.get("ineligibility_reasons") or []
        codes = [r.get("code") for r in ineligibility if r.get("code")]
        if codes:
            out["fba_ineligibility"] = ", ".join(codes)

    pricing = result.get("pricing") or {}
    if pricing:
        out["live_buy_box"] = pricing.get("buy_box_price")
        out["live_buy_box_seller"] = pricing.get("buy_box_seller")
        out["live_offer_count_new"] = pricing.get("offer_count_new")
        out["live_offer_count_fba"] = pricing.get("offer_count_fba")

    catalog = result.get("catalog") or {}
    if catalog:
        out["catalog_brand"] = catalog.get("brand")
        out["catalog_hazmat"] = catalog.get("hazmat")

    errors = result.get("errors") or []
    if errors:
        out["preflight_errors"] = ", ".join(
            f"{e.get('source')}:{e.get('message')}" for e in errors
        )

    return out


def _seed_row(row: dict) -> None:
    """Populate the preflight columns with None and keepa_brand on a row that
    we couldn't or didn't preflight, so downstream output writers see a
    consistent schema."""
    for col in PREFLIGHT_COLUMNS:
        if col == "keepa_brand":
            row.setdefault(col, row.get("brand"))
        else:
            row.setdefault(col, None)


def annotate_with_preflight(
    rows: list[dict],
    cli_path: Path | None = None,
    seller_id: str | None = None,
    marketplace_id: str | None = None,
) -> list[dict]:
    """Annotate `rows` (in place) with informational SP-API columns.

    Args:
        rows: list of dicts as built by main._process_match.
        cli_path: explicit path to dist/cli.js. If None, auto-detect.
        seller_id: override SP_API_SELLER_ID for the restrictions check.
        marketplace_id: override default UK.

    Returns the same list (with rows mutated). Always returns successfully —
    individual failures are logged and rows are seeded with None columns so
    the output schema stays consistent.
    """
    if not rows:
        return rows

    repo_root = _find_repo_root()
    cli = cli_path or _find_cli(repo_root)
    if cli is None:
        logger.info(
            "preflight: CLI not found, skipping informational annotation"
        )
        for row in rows:
            _seed_row(row)
        return rows

    # Runtime check (node + creds) — independent of CLI auto-detection so
    # callers passing an explicit cli_path still get gated by env state.
    ready, reason = _check_runtime_ready()
    if not ready:
        logger.info("preflight: skipping (%s)", reason)
        for row in rows:
            _seed_row(row)
        return rows

    # Build a list of (row_idx, item) tuples for rows we can preflight,
    # then send them in chunks of 20.
    candidates: list[tuple[int, dict]] = []
    for idx, row in enumerate(rows):
        item = _row_to_item(row)
        if item is not None:
            candidates.append((idx, item))
        else:
            _seed_row(row)

    if not candidates:
        logger.info("preflight: no candidate rows (need asin + market_price)")
        return rows

    logger.info(
        "preflight: annotating %d/%d rows in batches of %d",
        len(candidates), len(rows), BATCH_SIZE,
    )

    annotated_count = 0
    for batch in _chunk(candidates, BATCH_SIZE):
        payload: dict[str, Any] = {"items": [item for _, item in batch]}
        if seller_id:
            payload["seller_id"] = seller_id
        if marketplace_id:
            payload["marketplace_id"] = marketplace_id
        response = _call_cli(cli, payload)
        if not response or not isinstance(response.get("results"), list):
            logger.warning(
                "preflight: batch failed, seeding %d rows with None",
                len(batch),
            )
            for idx, _ in batch:
                _seed_row(rows[idx])
            continue
        results = response["results"]
        for (idx, _item), result in zip(batch, results):
            cols = _coerce_result(result, rows[idx])
            rows[idx].update(cols)
            annotated_count += 1
        # Any rows in batch that didn't get a corresponding result entry
        # (length mismatch) get seeded.
        if len(results) < len(batch):
            for idx, _ in batch[len(results):]:
                _seed_row(rows[idx])

    logger.info("preflight: %d rows annotated", annotated_count)
    return rows


def restriction_notes_for_shortlist(rows: list[dict]) -> list[dict]:
    """Pull rows in SHORTLIST that have a non-UNRESTRICTED status, for the
    markdown report's restriction-notes section."""
    out = []
    for row in rows:
        if row.get("decision") != "SHORTLIST":
            continue
        status = row.get("restriction_status")
        if not status or status == "UNRESTRICTED":
            continue
        out.append(row)
    return out
