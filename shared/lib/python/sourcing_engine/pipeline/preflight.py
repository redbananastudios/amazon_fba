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
    restriction_links       Semicolon-joined Apply-to-sell URLs (one per
                            distinct SP-API reason). Click straight from
                            the output to the Seller Central application
                            page — saves looking up each ASIN by hand.
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
import math
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
    "restriction_links",
    # Y/N/UNKNOWN flag derived from restriction_status — the XLSX writer
    # reads this to colour-code rows without parsing the longer status
    # string. UNRESTRICTED → "N"; BRAND_GATED / RESTRICTED / CATEGORY_GATED
    # → "Y"; missing → "UNKNOWN".
    "gated",
    "fba_eligible",
    "fba_ineligibility",
    "live_buy_box",
    "live_buy_box_seller",
    "live_offer_count_new",
    "live_offer_count_fba",
    "catalog_brand",
    "keepa_brand",
    "catalog_hazmat",
    # Listing-quality signals (PR D — operator-validator-fidelity sweep).
    # All optional — populated when SP-API returns them on the catalog
    # response. Fed into the validator's operational-safety dimension
    # in PR E.
    "catalog_image_count",
    "catalog_has_aplus_content",
    "catalog_release_date",
    "preflight_errors",
]


# Restriction-status values that mean the seller needs Amazon's approval
# before listing. Anything else → not gated. Sourced from the SP-API
# listings-restrictions response shapes the MCP normalises into the
# `restriction_status` column.
_GATED_STATUSES = frozenset(
    {"RESTRICTED", "BRAND_GATED", "CATEGORY_GATED"}
)


def _derive_gated(restriction_status: Any) -> str:
    """Map a normalised restriction_status to the Y/N/UNKNOWN flag.

    None or empty → "UNKNOWN" (we don't know yet — preflight didn't run
    or returned nothing for this ASIN). UNRESTRICTED → "N". Anything in
    _GATED_STATUSES → "Y". Unrecognised non-empty status defaults to
    "UNKNOWN" so a future SP-API value we haven't seen doesn't get
    silently mis-classified as not-gated.
    """
    if not restriction_status:
        return "UNKNOWN"
    status = str(restriction_status).strip().upper()
    if status == "UNRESTRICTED":
        return "N"
    if status in _GATED_STATUSES:
        return "Y"
    return "UNKNOWN"

# Ungate-tracking columns — reserved schema for the ungate workflow.
# These are NOT populated by SP-API preflight. They're seeded as None
# so every row in the output has the cells, and the operator (or a
# future automated click-through bot) fills them in as ungate
# applications progress through Seller Central.
#
# Workflow today: operator clicks the URL in `restriction_links`,
# applies on Amazon, then types the outcome into these cells (e.g.
# `ungate_status = "INSTANT_APPROVED"` for the no-docs path,
# `ungate_status = "DOCS_REQUIRED"` + `ungate_required_docs = "invoice"`
# for the supplier-invoice path).
#
# Workflow when the bot lands: same columns, populated automatically.
# No schema migration — the bot just stops leaving them blank.
UNGATE_COLUMNS = [
    # INSTANT_APPROVED / DOCS_REQUIRED / IN_QUEUE / DENIED /
    # RATE_LIMITED / NOT_ATTEMPTED. Free-text accepted; canonical
    # values listed for the bot author.
    "ungate_status",
    # invoice / brand_letter / category_cert / "" — what Amazon's
    # form is asking for when ungate_status == "DOCS_REQUIRED".
    "ungate_required_docs",
    # The brand whose invoice/letter Amazon needs. Sometimes more
    # specific than the listing's `brand` field (e.g. "Hasbro Inc"
    # rather than "Transformers").
    "ungate_brand_required",
    # ISO 8601 timestamp — set by the bot or operator. Lets reruns
    # avoid re-attempting recent applications.
    "ungate_attempted_at",
    # Free-text — whatever Amazon's response page said. Useful for
    # debugging unexpected outcomes.
    "ungate_message",
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


def _is_finite_positive(v: Any) -> bool:
    """True iff v is a finite number > 0. Rejects None, NaN, inf, negatives."""
    if v is None:
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Coerce v to a finite float; falls back to default for None/NaN/inf."""
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return f


def _row_to_item(row: dict, *, allow_no_price: bool = False) -> dict | None:
    """Translate a pipeline row into the MCP preflight item shape.

    Args:
        row: pipeline row (post-calculate has market_price; ASIN-only
            sources like seller_storefront leads do not).
        allow_no_price: when True, ASIN-only rows yield an item with
            ``selling_price=0.0`` (the MCP CLI accepts this so long as
            ``include`` excludes the pricing-dependent sources —
            pricing/fees/profitability). Used by leads-mode callers
            that only need restrictions / FBA eligibility / catalog
            (gating, hazmat, brand) and don't have a market price yet.

    Returns the item dict, or None if the row can't be preflighted at
    all (no asin, or no selling_price when allow_no_price is False).
    """
    asin = row.get("asin")
    if not asin:
        return None
    # Pick the first finite, positive selling price candidate. Rows that
    # rejected early (no Amazon match, invalid EAN) carry NaN here — must
    # filter them out, otherwise json.dumps emits the literal `NaN` token,
    # which the Node CLI rejects as invalid JSON, failing the whole batch.
    candidates = [row.get("market_price"), row.get("raw_conservative_price")]
    selling_price: float | None = None
    for c in candidates:
        if _is_finite_positive(c):
            selling_price = float(c)
            break
    if selling_price is None:
        if not allow_no_price:
            return None
        # Leads-mode placeholder: the MCP types require selling_price to
        # be a finite number. 0.0 is safe ONLY if the caller's `include`
        # excludes pricing/fees/profitability sources — those would
        # compute meaningless values otherwise.
        selling_price = 0.0
    cost_price = _safe_float(row.get("buy_cost"), 0.0)
    if cost_price < 0:
        cost_price = 0.0
    return {
        "asin": asin,
        "selling_price": selling_price,
        "cost_price": cost_price,
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
            input=json.dumps(payload, allow_nan=False),
            capture_output=True,
            # Force UTF-8 with replacement so a stray non-ASCII byte in the
            # CLI's stderr doesn't crash subprocess on Windows (default
            # encoding is cp1252 there, which fails on bytes > 0x7f).
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("preflight: CLI timed out after %ss", timeout_seconds)
        return None
    except ValueError as err:
        # json.dumps with allow_nan=False raises if a NaN/inf slipped past
        # _row_to_item. Log and skip rather than crashing.
        logger.warning("preflight: payload contains non-finite values: %s", err)
        return None
    except Exception:
        logger.exception("preflight: CLI invocation failed")
        return None
    if proc.stdout is None:
        logger.warning("preflight: CLI produced no stdout")
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
    we attach to the row. Missing/error sources resolve to None.

    Ungate-tracking columns (UNGATE_COLUMNS) are also seeded as None so
    every gated row has the cells ready for the operator (or future
    bot) to fill in. Preflight itself never writes them — see the
    UNGATE_COLUMNS docstring above.
    """
    out: dict[str, Any] = {col: None for col in PREFLIGHT_COLUMNS}
    for col in UNGATE_COLUMNS:
        out[col] = None
    # Prefer an existing keepa_brand column if a prior pass set it — that's
    # the original Keepa value. Falling back to original_row.get("brand")
    # only on first-pass rows means re-running preflight (e.g. with refresh
    # cache) doesn't clobber the historical brand if some upstream step
    # has since merged the SP-API canonical brand into row["brand"].
    out["keepa_brand"] = (
        original_row.get("keepa_brand") or original_row.get("brand")
    )

    # Default `gated` for the row — overwritten below if SP-API returned
    # a restriction status. Keeps the column populated even when the
    # restrictions source is missing (e.g. a CLI partial failure) so the
    # XLSX writer never sees a None Gated cell.
    out["gated"] = "UNKNOWN"

    restrictions = result.get("restrictions") or {}
    if restrictions:
        out["restriction_status"] = restrictions.get("status")
        out["gated"] = _derive_gated(out["restriction_status"])
        reasons = restrictions.get("reasons") or []
        codes = [r.get("reasonCode") for r in reasons if r.get("reasonCode")]
        if codes:
            out["restriction_reasons"] = ", ".join(codes)
        # SP-API attaches an `Apply to sell` URL per gated reason. The
        # MCP forwards it as ``r["link"]``. We surface it so the operator
        # can click straight from the engine output to the application
        # page — preferable to the previous workflow of looking up each
        # ASIN in Seller Central by hand. Joined with `; ` so the column
        # is one cell in CSV/XLSX. Multiple reasons sometimes point at
        # the same URL — dedup while preserving order via dict.fromkeys.
        # The truthiness filter (`if r.get("link")`) drops both ``None``
        # and the empty-string SP-API edge case.
        unique_links = list(
            dict.fromkeys(r.get("link") for r in reasons if r.get("link"))
        )
        if unique_links:
            out["restriction_links"] = "; ".join(unique_links)

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
        # Listing-quality signals (PR D). image_count is derived
        # MCP-side from the images array length so it's always
        # populatable on summary-light responses; A+ content + release
        # date come straight from SP-API and may legitimately be None
        # for many listings.
        out["catalog_image_count"] = catalog.get("image_count")
        out["catalog_has_aplus_content"] = catalog.get("has_aplus_content")
        out["catalog_release_date"] = catalog.get("release_date")

    errors = result.get("errors") or []
    if errors:
        out["preflight_errors"] = ", ".join(
            f"{e.get('source')}:{e.get('message')}" for e in errors
        )

    return out


def _seed_row(row: dict) -> None:
    """Populate the preflight columns with None and keepa_brand on a row that
    we couldn't or didn't preflight, so downstream output writers see a
    consistent schema.

    Also seeds the ungate-tracking columns (UNGATE_COLUMNS) so the
    output schema is identical whether preflight ran successfully, no-
    op'd, or crashed.
    """
    for col in PREFLIGHT_COLUMNS:
        if col == "keepa_brand":
            row.setdefault(col, row.get("brand"))
        elif col == "gated":
            # "UNKNOWN" not None — the column has a domain ("Y", "N",
            # "UNKNOWN") and the XLSX writer + decision logic compare
            # against that domain. Setting None here would cause the
            # cell to render blank in the operator's spreadsheet.
            row.setdefault(col, "UNKNOWN")
        else:
            row.setdefault(col, None)
    for col in UNGATE_COLUMNS:
        row.setdefault(col, None)


def annotate_with_preflight(
    rows: list[dict],
    cli_path: Path | None = None,
    seller_id: str | None = None,
    marketplace_id: str | None = None,
    include: list[str] | None = None,
) -> list[dict]:
    """Annotate `rows` (in place) with informational SP-API columns.

    Args:
        rows: list of dicts as built by main._process_match.
        cli_path: explicit path to dist/cli.js. If None, auto-detect.
        seller_id: override SP_API_SELLER_ID for the restrictions check.
        marketplace_id: override default UK.
        include: subset of MCP preflight sources to call. The MCP
            supports {restrictions, fba, fees, catalog, pricing,
            profitability}. ``None`` calls all of them (the legacy
            supplier_pricelist contract). Leads-mode callers
            (seller_storefront, ASIN-only inputs) should pass
            ``["restrictions", "fba", "catalog"]`` to skip the pricing-
            dependent sources — that lets ASIN-only rows preflight
            without a market_price.

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

    # Leads-mode: when `include` excludes the pricing-dependent sources,
    # ASIN-only rows (no market_price) are still useful — restrictions,
    # FBA eligibility, and catalog don't need a price.
    pricing_sources = {"pricing", "fees", "profitability"}
    allow_no_price = bool(include) and not (set(include) & pricing_sources)

    # Build a list of (row_idx, item) tuples for rows we can preflight,
    # then send them in chunks of 20.
    candidates: list[tuple[int, dict]] = []
    for idx, row in enumerate(rows):
        item = _row_to_item(row, allow_no_price=allow_no_price)
        if item is not None:
            candidates.append((idx, item))
        else:
            _seed_row(row)

    if not candidates:
        logger.info(
            "preflight: no candidate rows "
            "(need asin%s)",
            "" if allow_no_price else " + market_price",
        )
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
        if include:
            payload["include"] = include
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
