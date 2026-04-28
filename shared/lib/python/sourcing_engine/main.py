"""Entry point — orchestrates the full sourcing pipeline.

Usage (recommended):
    python -m sourcing_engine.main --supplier abgee

Auto-derives input/output paths from the supplier name. Override with --input
and --output if you need to point elsewhere.

Full form:
    python -m sourcing_engine.main \\
        --supplier abgee \\
        --input  supplier_pricelist_finder/pricelists/abgee/raw/ \\
        --output supplier_pricelist_finder/pricelists/abgee/results/ \\
        --market-data supplier_pricelist_finder/pricelists/abgee/raw/keepa_combined_2026-03-25.csv

The PYTHONPATH must include shared/lib/python/. Use the launcher at the repo
root (`run.py`) to handle this automatically:
    python run.py --supplier abgee
"""
import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from sourcing_engine.adapters.loader import load_supplier_adapter, AdapterNotFoundError
from sourcing_engine.pipeline.case_detection import derive_costs
from sourcing_engine.pipeline.match import match_product
from sourcing_engine.pipeline.market_data import load_market_data
from sourcing_engine.pipeline.fees import calculate_fees_fba, calculate_fees_fbm
from sourcing_engine.pipeline.conservative_price import calculate_conservative_price
from sourcing_engine.pipeline.profit import calculate_profit
from sourcing_engine.pipeline.decision import decide
from sourcing_engine.config import CAPITAL_EXPOSURE_LIMIT
from sourcing_engine.utils.flags import (
    AMAZON_ON_LISTING, AMAZON_STATUS_UNKNOWN, SINGLE_FBA_SELLER,
    FBM_ONLY, HIGH_MOQ, INSUFFICIENT_HISTORY, PRICE_MISMATCH_RRP,
)
from sourcing_engine.utils.ean_validator import validate_ean

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sourcing_engine")


def _find_repo_root() -> Path:
    """Walk up from this file looking for the repo root (has supplier_pricelist_finder/)."""
    here = Path(__file__).resolve()
    for ancestor in [here, *here.parents]:
        if (ancestor / "supplier_pricelist_finder").is_dir():
            return ancestor
    raise RuntimeError(
        "Could not locate repo root (no supplier_pricelist_finder/ ancestor)."
    )


def _default_paths(supplier: str, repo_root: Path) -> tuple[Path, Path]:
    """Default input and output paths for a supplier."""
    base = repo_root / "supplier_pricelist_finder" / "pricelists" / supplier
    return base / "raw", base / "results"


def run_pipeline(
    supplier: str,
    input_path: str,
    output_dir: str,
    market_data_path: str | None = None,
):
    """Run the full pipeline for one supplier.

    Args:
        supplier: supplier name matching the adapter folder under
            supplier_pricelist_finder/pricelists/<supplier>/adapters/
        input_path: directory or single file to ingest
        output_dir: where to write the timestamped run folder
        market_data_path: optional Keepa CSV
    """
    # Load the supplier-specific adapter (ingest + normalise)
    try:
        ingest_mod, normalise_mod = load_supplier_adapter(supplier)
    except AdapterNotFoundError as e:
        logger.error("Adapter load failed: %s", e)
        sys.exit(2)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(output_dir, timestamp)
    os.makedirs(run_dir, exist_ok=True)

    # Step 1: Ingest (supplier-specific)
    logger.info("Ingesting supplier files for %s from %s", supplier, input_path)
    if os.path.isdir(input_path):
        raw_df = ingest_mod.ingest_directory(input_path)
    else:
        raw_df = ingest_mod.ingest_file(input_path)
    if raw_df.empty:
        logger.error("No data ingested — exiting")
        return
    logger.info(
        "Ingested %d raw rows from %d files",
        len(raw_df), raw_df["source_file"].nunique(),
    )

    # Step 2: Normalise (supplier-specific)
    norm_df = normalise_mod.normalise(raw_df)
    logger.info("Normalised %d rows", len(norm_df))

    # Step 3: Case detection + cost derivation
    for idx, row in norm_df.iterrows():
        costs = derive_costs(
            supplier_price_ex_vat=row["supplier_price_ex_vat"],
            supplier_price_basis=row["supplier_price_basis"],
            case_qty=row["case_qty"],
            rrp_inc_vat=row.get("rrp_inc_vat"),
        )
        for key in ("unit_cost_ex_vat", "unit_cost_inc_vat", "case_cost_ex_vat",
                    "case_cost_inc_vat", "case_qty"):
            norm_df.at[idx, key] = costs[key]
        existing = norm_df.at[idx, "risk_flags"]
        if not isinstance(existing, list):
            existing = []
        norm_df.at[idx, "risk_flags"] = existing + costs["flags"]

    # Step 4: EAN validation + matching
    market_data = load_market_data(market_data_path)
    output_rows = []
    stats = {"matched": 0, "no_match": 0, "invalid_ean": 0, "errors": 0}

    for idx, row in norm_df.iterrows():
        try:
            row_dict = row.to_dict()
            if not row_dict.get("ean") or not validate_ean(row_dict["ean"]):
                output_rows.append({
                    **row_dict, "decision": "REJECT",
                    "decision_reason": "Invalid or missing EAN", "match_type": "UNIT",
                })
                stats["invalid_ean"] += 1
                continue

            matches = match_product(row_dict, market_data)
            if not matches:
                output_rows.append({
                    **row_dict, "decision": "REJECT",
                    "decision_reason": "No Amazon match found", "match_type": "UNIT",
                })
                stats["no_match"] += 1
                continue

            for match in matches:
                try:
                    processed = _process_match(match)
                    output_rows.append(processed)
                    stats["matched"] += 1
                except Exception:
                    logger.exception(
                        "[%s] [ROW_%s] [%s] — match processing error",
                        row_dict.get("supplier"), idx, row_dict.get("ean"),
                    )
                    match["decision"] = "REVIEW"
                    match["decision_reason"] = "Processing error — manual review required"
                    output_rows.append(match)
                    stats["errors"] += 1
        except Exception:
            logger.exception(
                "[%s] [ROW_%s] [%s] — pipeline error",
                row.get("supplier"), idx, row.get("ean"),
            )
            stats["errors"] += 1

    # Step 5: Output
    output_df = pd.DataFrame(output_rows)
    if not output_df.empty:
        from sourcing_engine.output.csv_writer import write_csv
        from sourcing_engine.output.excel_writer import write_excel
        from sourcing_engine.output.markdown_report import write_report

        csv_path = os.path.join(run_dir, f"shortlist_{timestamp}.csv")
        xlsx_path = os.path.join(run_dir, f"shortlist_{timestamp}.xlsx")
        md_path = os.path.join(run_dir, f"report_{timestamp}.md")
        write_csv(output_df, csv_path)
        # Pass supplier label so the Excel title bar names this supplier specifically
        write_excel(
            output_df, xlsx_path, market_data=market_data,
            supplier_label=_friendly_supplier_label(supplier),
        )
        write_report(output_df, md_path)

    _print_summary(output_df, stats, norm_df)
    return run_dir


def _friendly_supplier_label(supplier: str) -> str:
    """Turn 'connect-beauty' into 'Connect Beauty' for display."""
    return " ".join(part.capitalize() for part in supplier.replace("_", "-").split("-"))


def _process_match(match):
    risk_flags = list(match.get("risk_flags", []))
    fba_seller_count = match.get("fba_seller_count", 0) or 0
    amazon_status = match.get("amazon_status")
    buy_box_price = match.get("buy_box_price")
    amazon_price = match.get("amazon_price")
    lowest_fba_price = match.get("new_fba_price")

    def _pick_market_price(bb, fba):
        candidates = [p for p in (bb, fba) if p is not None and p > 0]
        return min(candidates) if candidates else None

    if fba_seller_count > 0:
        price_basis = "FBA"
        market_price = _pick_market_price(buy_box_price, lowest_fba_price)
        if amazon_status == "ON_LISTING":
            risk_flags.append(AMAZON_ON_LISTING)
        elif amazon_status == "UNKNOWN":
            risk_flags.append(AMAZON_STATUS_UNKNOWN)
        if fba_seller_count == 1:
            risk_flags.append(SINGLE_FBA_SELLER)
    else:
        price_basis = "FBM"
        market_price = buy_box_price
        risk_flags.append(FBM_ONLY)

    if market_price is None or market_price <= 0:
        match["decision"] = "REJECT"
        match["decision_reason"] = "No valid market price"
        return match

    rrp = match.get("rrp_inc_vat")
    if rrp and rrp > 0 and market_price > 0:
        price_ratio = market_price / rrp
        if price_ratio > 2.0 or price_ratio < 0.3:
            risk_flags.append(PRICE_MISMATCH_RRP)

    buy_cost = match["buy_cost"]
    size_tier = match.get("size_tier")
    sales_estimate = match.get("sales_estimate")
    keepa_fba_fee = match.get("fba_pick_pack_fee")
    keepa_referral_pct = match.get("referral_fee_pct")

    if price_basis == "FBA":
        fees_current = calculate_fees_fba(
            market_price, size_tier, sales_estimate=sales_estimate,
            keepa_fba_fee=keepa_fba_fee, keepa_referral_fee_pct=keepa_referral_pct,
        )
    else:
        fees_current = calculate_fees_fbm(market_price)

    price_history = match.get("price_history")
    if price_history and isinstance(price_history, list):
        raw_cp, _, _ = calculate_conservative_price(price_history, market_price, buy_cost, 0)
        if price_basis == "FBA":
            fees_conservative = calculate_fees_fba(
                raw_cp, size_tier, sales_estimate=sales_estimate,
                keepa_fba_fee=keepa_fba_fee, keepa_referral_fee_pct=keepa_referral_pct,
            )
        else:
            fees_conservative = calculate_fees_fbm(raw_cp)
        raw_cp, floored_cp, cp_flag = calculate_conservative_price(
            price_history, market_price, buy_cost, fees_conservative["total"],
        )
    else:
        raw_cp = market_price
        floored_cp = market_price
        cp_flag = INSUFFICIENT_HISTORY
        if price_basis == "FBA":
            fees_conservative = calculate_fees_fba(
                raw_cp, size_tier, sales_estimate=sales_estimate,
                keepa_fba_fee=keepa_fba_fee, keepa_referral_fee_pct=keepa_referral_pct,
            )
        else:
            fees_conservative = calculate_fees_fbm(raw_cp)

    if cp_flag:
        risk_flags.append(cp_flag)

    risk_flags.extend(fees_current.get("flags", []))
    risk_flags.extend(fees_conservative.get("flags", []))

    profit = calculate_profit(market_price, raw_cp, fees_current, fees_conservative, buy_cost)

    moq = match.get("moq", 1) or 1
    capital_exposure = moq * buy_cost
    if capital_exposure > CAPITAL_EXPOSURE_LIMIT:
        risk_flags.append(HIGH_MOQ)

    risk_flags = list(dict.fromkeys(risk_flags))

    decision_input = {
        **profit, "sales_estimate": sales_estimate,
        "gated": match.get("gated", "UNKNOWN"), "risk_flags": risk_flags,
        "price_basis": price_basis, "buy_cost": buy_cost,
    }
    decision, reason = decide(decision_input)

    match.update({
        "market_price": market_price, "raw_conservative_price": raw_cp,
        "floored_conservative_price": floored_cp, "price_basis": price_basis,
        "fees_current": fees_current["total"], "fees_conservative": fees_conservative["total"],
        **profit, "capital_exposure": capital_exposure,
        "decision": decision, "decision_reason": reason, "risk_flags": risk_flags,
    })
    return match


def _print_summary(output_df, stats, norm_df):
    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    logger.info("=" * 60)
    logger.info(
        "Suppliers processed: %d",
        norm_df["supplier"].nunique() if not norm_df.empty else 0,
    )
    logger.info("Source rows processed: %d", len(norm_df))
    logger.info("Matched: %d", stats["matched"])
    logger.info("Invalid EAN: %d", stats["invalid_ean"])
    logger.info("No match: %d", stats["no_match"])
    logger.info("Errors: %d", stats["errors"])
    if not output_df.empty and "decision" in output_df.columns:
        for d in ["SHORTLIST", "REVIEW", "REJECT"]:
            logger.info("%s: %d", d, (output_df["decision"] == d).sum())


def main():
    parser = argparse.ArgumentParser(description="Amazon Supplier Shortlist Engine")
    parser.add_argument(
        "--supplier", required=True,
        help="Supplier name (matches the folder name under supplier_pricelist_finder/pricelists/)",
    )
    parser.add_argument(
        "--input", default=None,
        help="Supplier file or directory. Defaults to "
             "supplier_pricelist_finder/pricelists/<supplier>/raw/",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output directory. Defaults to "
             "supplier_pricelist_finder/pricelists/<supplier>/results/",
    )
    parser.add_argument("--market-data", default=None, help="Market data CSV path")
    args = parser.parse_args()

    repo_root = _find_repo_root()
    default_in, default_out = _default_paths(args.supplier, repo_root)
    input_path = args.input if args.input else str(default_in)
    output_dir = args.output if args.output else str(default_out)

    run_pipeline(args.supplier, input_path, output_dir, args.market_data)


if __name__ == "__main__":
    main()
