"""Tests for fba_engine.steps.build_output.

Logic ported 1:1 from the per-niche `phase5_build.js` script (see
`fba_engine/data/niches/{niche}/working/phase5_build.js` — they're 99%
identical, parameterised only by the niche slug). These tests double as
a regression contract for the JS->Python port.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
import pytest

from fba_engine.steps.build_output import (
    FINAL_HEADERS,
    REJECT_HEADER_EXTRA,
    SUPPLIER_HEADERS,
    VERDICT_ORDER,
    build_handoff,
    build_stats,
    compute_phase5,
    confirmed_private_label_status,
    pl_risk,
    price_stability,
    run_step,
)


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


class TestSchema:
    def test_final_headers_count_is_67(self):
        assert len(FINAL_HEADERS) == 67

    def test_final_headers_first_three_are_asin_name_brand(self):
        assert FINAL_HEADERS[:3] == ["ASIN", "Product Name", "Brand"]

    def test_final_headers_last_three_are_product_codes(self):
        assert FINAL_HEADERS[-3:] == ["EAN", "UPC", "GTIN"]

    def test_supplier_headers_count_is_22(self):
        assert len(SUPPLIER_HEADERS) == 22

    def test_reject_extra_is_pl_exclusion_reason(self):
        assert REJECT_HEADER_EXTRA == "Private Label Exclusion Reason"


# ---------------------------------------------------------------------------
# price_stability — substring-based bucket logic
# ---------------------------------------------------------------------------


class TestPriceStability:
    def test_stable_band_is_minus_two_to_two(self):
        assert price_stability(-2.0) == "STABLE"
        assert price_stability(0) == "STABLE"
        assert price_stability(2.0) == "STABLE"

    def test_slight_dip_for_2_to_10(self):
        assert price_stability(5.0) == "SLIGHT DIP (5%)"
        assert price_stability(10.0) == "SLIGHT DIP (10%)"

    def test_dropping_for_above_10(self):
        assert price_stability(15.0) == "DROPPING (15%)"

    def test_rising_for_minus_2_to_minus_10(self):
        assert price_stability(-5.0) == "RISING (5%)"
        assert price_stability(-10.0) == "RISING (10%)"

    def test_surging_for_below_minus_10(self):
        assert price_stability(-15.0) == "SURGING (15%)"


# ---------------------------------------------------------------------------
# pl_risk — Likely / Unlikely / -
# ---------------------------------------------------------------------------


class TestPlRisk:
    def test_brand_1p_y_is_likely(self):
        assert pl_risk(fba_count=10, brand_1p="Y") == "Likely"

    def test_low_seller_count_is_unlikely(self):
        assert pl_risk(fba_count=2, brand_1p="N") == "Unlikely"
        assert pl_risk(fba_count=1, brand_1p="N") == "Unlikely"

    def test_default_is_dash(self):
        assert pl_risk(fba_count=5, brand_1p="N") == "-"

    def test_brand_1p_takes_precedence_over_seller_count(self):
        # brand_1p=Y overrides even with low seller count.
        assert pl_risk(fba_count=1, brand_1p="Y") == "Likely"


# ---------------------------------------------------------------------------
# confirmed_private_label_status — multi-signal exclusion check
# ---------------------------------------------------------------------------


class TestConfirmedPrivateLabel:
    def test_brand_1p_y_alone_is_confirmed(self):
        confirmed, reason = confirmed_private_label_status(
            brand_1p="Y",
            brand_seller_match="NO",
            fortress_listing="NO",
            brand_store_present="UNLIKELY",
            brand_type="GENERIC",
        )
        assert confirmed is True
        assert "Brand 1P" in reason

    def test_fortress_plus_partial_seller_match_is_confirmed(self):
        confirmed, reason = confirmed_private_label_status(
            brand_1p="N",
            brand_seller_match="PARTIAL",
            fortress_listing="YES",
            brand_store_present="UNLIKELY",
            brand_type="GENERIC",
        )
        assert confirmed is True

    def test_fortress_plus_yes_seller_match_is_confirmed(self):
        confirmed, _ = confirmed_private_label_status(
            "N", "YES", "YES", "UNLIKELY", "GENERIC"
        )
        assert confirmed is True

    def test_seller_match_yes_plus_store_likely_is_confirmed(self):
        confirmed, _ = confirmed_private_label_status(
            "N", "YES", "NO", "LIKELY", "GENERIC"
        )
        assert confirmed is True

    def test_three_reasons_with_strong_signal_confirms(self):
        # 3 reasons (fortress, seller match YES, established) + sellerMatch=YES.
        # sellerMatch=YES alone with fortress already confirms via strongControl,
        # so this also satisfies the multi-signal branch.
        confirmed, _ = confirmed_private_label_status(
            "N", "YES", "YES", "UNLIKELY", "ESTABLISHED"
        )
        assert confirmed is True

    def test_two_weak_signals_alone_not_confirmed(self):
        # Only fortress + established (no seller match, no brand 1p, no store).
        # 2 reasons total -> multiSignal needs 3+.
        # No strongControl (sellerMatch is "NO").
        # No brandOwned (sellerMatch is "NO").
        # Not amazonBrand. -> not confirmed.
        confirmed, _ = confirmed_private_label_status(
            "N", "NO", "YES", "UNLIKELY", "ESTABLISHED"
        )
        assert confirmed is False

    def test_clean_listing_not_confirmed(self):
        confirmed, reason = confirmed_private_label_status(
            "N", "NO", "NO", "UNLIKELY", "GENERIC"
        )
        assert confirmed is False
        assert reason == ""

    def test_reason_lists_contributing_signals(self):
        _, reason = confirmed_private_label_status(
            "Y", "YES", "YES", "LIKELY", "ESTABLISHED"
        )
        # All five signals present; reason should list them pipe-joined.
        for signal in [
            "Brand 1P",
            "Fortress listing",
            "Brand seller match",
            "Brand store likely",
            "Established brand",
        ]:
            assert signal in reason


# ---------------------------------------------------------------------------
# compute_phase5 — DataFrame transform
# ---------------------------------------------------------------------------


def _make_phase4_row(**overrides) -> dict:
    """Build a Phase-4-shaped row with defaults that survive the PL filter."""
    base = {
        "ASIN": "B0SAMPLE",
        "Title": "Sample Product",
        "Brand": "Acme",
        "Amazon URL": "https://amzn.eu/d/sample",
        "Category": "Toys",
        "Weight Flag": "OK",
        "Verdict": "YES",
        "Verdict Reason": "Strong demand and margin",
        "Composite Score": "8.5",
        "Demand Score": "8",
        "Stability Score": "8",
        "Competition Score": "7",
        "Margin Score": "9",
        "Cash Flow Score": "8",
        "Profit Score": "8",
        "Balanced Score": "8",
        "Listing Quality": "Good",
        "Opportunity Lane": "BALANCED",
        "Commercial Priority": "1",
        "Lane Reason": "Demand and margin balanced",
        "Monthly Gross Profit": "GBP500",
        "Price Compression": "Stable",
        "Current Price": "GBP25.99",
        "Buy Box 90d Avg": "GBP25.50",
        "Price Drop % 90d": "1",
        "Fulfilment Fee": "GBP3.50",
        "Amazon Fees": "GBP5.00",
        "Total Amazon Fees": "GBP8.50",
        "Est Cost 65%": "GBP10.00",
        "Est Profit": "GBP7.49",
        "Est ROI %": "32",
        "Max Cost 20% ROI": "GBP12.00",
        "Breakeven Price": "GBP18.00",
        "BSR Current": "5000",
        "BSR Drops 90d": "200",
        "Bought per Month": "150",
        "Star Rating": "4.5",
        "Review Count": "200",
        "Brand 1P": "N",
        "FBA Seller Count": "5",
        "Amazon on Listing": "N",
        "Buy Box Amazon %": "10%",
        "Brand Seller Match": "NO",
        "Fortress Listing": "NO",
        "Brand Type": "GENERIC",
        "A+ Content Present": "Y",
        "Brand Store Present": "UNLIKELY",
        "Category Risk Level": "MEDIUM",
        "IP Risk Score": "3",
        "IP Risk Band": "Low",
        "IP Reason": "A+ content",
        "Gated": "N",
        "SAS Flags": "",
        "EAN": "1234567890123",
        "UPC": "",
        "GTIN": "",
    }
    base.update(overrides)
    return base


class TestComputePhase5:
    def test_returns_three_dataframes(self):
        df = pd.DataFrame([_make_phase4_row()])
        final_df, supplier_df, rejected_df = compute_phase5(df)
        assert isinstance(final_df, pd.DataFrame)
        assert isinstance(supplier_df, pd.DataFrame)
        assert isinstance(rejected_df, pd.DataFrame)

    def test_final_has_67_columns(self):
        df = pd.DataFrame([_make_phase4_row()])
        final_df, _, _ = compute_phase5(df)
        assert len(final_df.columns) == 67
        assert list(final_df.columns) == FINAL_HEADERS

    def test_input_df_is_not_mutated(self):
        df = pd.DataFrame([_make_phase4_row()])
        before = df.copy()
        _ = compute_phase5(df)
        pd.testing.assert_frame_equal(df, before)

    def test_clean_row_lands_in_final(self):
        df = pd.DataFrame([_make_phase4_row()])
        final_df, _, rejected_df = compute_phase5(df)
        assert len(final_df) == 1
        assert len(rejected_df) == 0

    def test_brand_1p_y_row_is_rejected(self):
        df = pd.DataFrame([_make_phase4_row(**{"Brand 1P": "Y"})])
        final_df, _, rejected_df = compute_phase5(df)
        assert len(final_df) == 0
        assert len(rejected_df) == 1
        assert "Brand 1P" in rejected_df.iloc[0][REJECT_HEADER_EXTRA]

    def test_fortress_plus_seller_match_yes_row_is_rejected(self):
        df = pd.DataFrame(
            [
                _make_phase4_row(
                    **{
                        "Fortress Listing": "YES",
                        "Brand Seller Match": "YES",
                    }
                )
            ]
        )
        _, _, rejected_df = compute_phase5(df)
        assert len(rejected_df) == 1

    def test_supplier_skeleton_has_22_columns(self):
        df = pd.DataFrame([_make_phase4_row()])
        _, supplier_df, _ = compute_phase5(df)
        assert list(supplier_df.columns) == SUPPLIER_HEADERS
        assert len(supplier_df.columns) == 22

    def test_supplier_columns_are_placeholders(self):
        df = pd.DataFrame([_make_phase4_row()])
        final_df, _, _ = compute_phase5(df)
        row = final_df.iloc[0]
        # Placeholders per the JS port.
        assert row["Route Code"] == "UNCLEAR"
        assert row["Trade Price Found"] == "N"
        assert row["Supplier Notes"] == "No supplier accounts configured"

    def test_phase4_columns_passed_through(self):
        df = pd.DataFrame(
            [
                _make_phase4_row(
                    **{
                        "IP Risk Score": "7",
                        "IP Risk Band": "High",
                        "IP Reason": "Fortress listing | Brand seller match",
                    }
                )
            ]
        )
        final_df, _, _ = compute_phase5(df)
        row = final_df.iloc[0]
        assert row["IP Risk Score"] == "7"
        assert row["IP Risk Band"] == "High"
        assert "Fortress listing" in row["IP Reason"]


class TestFormatting:
    def test_composite_score_one_decimal(self):
        df = pd.DataFrame([_make_phase4_row(**{"Composite Score": "8.567"})])
        final_df, _, _ = compute_phase5(df)
        assert final_df.iloc[0]["Composite Score"] == "8.6"

    def test_currency_columns_are_gbp_two_decimals(self):
        df = pd.DataFrame([_make_phase4_row()])
        final_df, _, _ = compute_phase5(df)
        row = final_df.iloc[0]
        assert row["Current Price"] == "GBP25.99"
        assert row["Buy Box 90d Avg"] == "GBP25.50"

    def test_monthly_gross_profit_gbp_zero_decimals(self):
        df = pd.DataFrame([_make_phase4_row(**{"Monthly Gross Profit": "GBP523.45"})])
        final_df, _, _ = compute_phase5(df)
        # JS: 'GBP' + monthlyGrossProfit.toFixed(0) -> rounds to integer string
        # via banker-safe floor(x+0.5) in the port.
        assert final_df.iloc[0]["Monthly Gross Profit"] == "GBP523"

    def test_est_roi_has_percent_one_decimal(self):
        df = pd.DataFrame([_make_phase4_row(**{"Est ROI %": "32.567%"})])
        final_df, _, _ = compute_phase5(df)
        assert final_df.iloc[0]["Est ROI %"] == "32.6%"

    def test_bsr_rounded_to_int(self):
        df = pd.DataFrame([_make_phase4_row(**{"BSR Current": "5234.7"})])
        final_df, _, _ = compute_phase5(df)
        # JS Math.round(5234.7) = 5235.
        assert final_df.iloc[0]["BSR Current"] == 5235

    def test_review_count_rounded_to_int(self):
        df = pd.DataFrame([_make_phase4_row(**{"Review Count": "200.4"})])
        final_df, _, _ = compute_phase5(df)
        assert final_df.iloc[0]["Review Count"] == 200

    def test_review_count_half_rounds_up(self):
        # JS Math.round(200.5) = 201; Python round() = 200 (banker's). Use shim.
        df = pd.DataFrame([_make_phase4_row(**{"Review Count": "200.5"})])
        final_df, _, _ = compute_phase5(df)
        assert final_df.iloc[0]["Review Count"] == 201

    def test_star_rating_one_decimal(self):
        df = pd.DataFrame([_make_phase4_row(**{"Star Rating": "4.567"})])
        final_df, _, _ = compute_phase5(df)
        assert final_df.iloc[0]["Star Rating"] == "4.6"


# ---------------------------------------------------------------------------
# Sort order
# ---------------------------------------------------------------------------


class TestSortOrder:
    def test_commercial_priority_ascending_takes_precedence(self):
        rows = [
            _make_phase4_row(ASIN="B0LOW", **{"Commercial Priority": "5"}),
            _make_phase4_row(ASIN="B0HIGH", **{"Commercial Priority": "1"}),
        ]
        df = pd.DataFrame(rows)
        final_df, _, _ = compute_phase5(df)
        # Lower priority number first (1 before 5).
        assert list(final_df["ASIN"]) == ["B0HIGH", "B0LOW"]

    def test_monthly_gross_profit_descending_tiebreaker(self):
        rows = [
            _make_phase4_row(
                ASIN="B0LOW",
                **{"Commercial Priority": "1", "Monthly Gross Profit": "GBP100"},
            ),
            _make_phase4_row(
                ASIN="B0HIGH",
                **{"Commercial Priority": "1", "Monthly Gross Profit": "GBP500"},
            ),
        ]
        df = pd.DataFrame(rows)
        final_df, _, _ = compute_phase5(df)
        # Higher MGP first.
        assert list(final_df["ASIN"]) == ["B0HIGH", "B0LOW"]

    def test_verdict_order_is_yes_maybe_brand_dip_roi_gated(self):
        # Identical sort keys above this; only Verdict differs.
        rows = [
            _make_phase4_row(
                ASIN=f"B{i:03d}",
                **{
                    "Verdict": v,
                    "Commercial Priority": "1",
                    "Monthly Gross Profit": "GBP100",
                    "Bought per Month": "100",
                    "Est Profit": "GBP5",
                    "Composite Score": "8",
                },
            )
            for i, v in enumerate(
                ["GATED", "YES", "MAYBE-ROI", "MAYBE", "BUY THE DIP", "BRAND APPROACH"]
            )
        ]
        df = pd.DataFrame(rows)
        final_df, _, _ = compute_phase5(df)
        # Per VERDICT_ORDER: YES, MAYBE, BRAND APPROACH, BUY THE DIP, MAYBE-ROI, GATED.
        assert list(final_df["Verdict"]) == [
            "YES",
            "MAYBE",
            "BRAND APPROACH",
            "BUY THE DIP",
            "MAYBE-ROI",
            "GATED",
        ]

    def test_bought_per_month_descending_tiebreaker(self):
        # Identical priority + MGP; tiebreaker is Bought per Month desc.
        rows = [
            _make_phase4_row(
                ASIN="B0LOW",
                **{"Commercial Priority": "1", "Monthly Gross Profit": "GBP100",
                   "Bought per Month": "50"},
            ),
            _make_phase4_row(
                ASIN="B0HIGH",
                **{"Commercial Priority": "1", "Monthly Gross Profit": "GBP100",
                   "Bought per Month": "200"},
            ),
        ]
        df = pd.DataFrame(rows)
        final_df, _, _ = compute_phase5(df)
        assert list(final_df["ASIN"]) == ["B0HIGH", "B0LOW"]

    def test_est_profit_descending_tiebreaker(self):
        # All higher keys equal; profit decides.
        rows = [
            _make_phase4_row(
                ASIN="B0LOW",
                **{"Commercial Priority": "1", "Monthly Gross Profit": "GBP100",
                   "Bought per Month": "100", "Est Profit": "GBP3"},
            ),
            _make_phase4_row(
                ASIN="B0HIGH",
                **{"Commercial Priority": "1", "Monthly Gross Profit": "GBP100",
                   "Bought per Month": "100", "Est Profit": "GBP10"},
            ),
        ]
        df = pd.DataFrame(rows)
        final_df, _, _ = compute_phase5(df)
        assert list(final_df["ASIN"]) == ["B0HIGH", "B0LOW"]

    def test_composite_score_descending_tiebreaker(self):
        # All higher keys equal; composite decides.
        rows = [
            _make_phase4_row(
                ASIN="B0LOW",
                **{"Commercial Priority": "1", "Monthly Gross Profit": "GBP100",
                   "Bought per Month": "100", "Est Profit": "GBP5",
                   "Composite Score": "7.0"},
            ),
            _make_phase4_row(
                ASIN="B0HIGH",
                **{"Commercial Priority": "1", "Monthly Gross Profit": "GBP100",
                   "Bought per Month": "100", "Est Profit": "GBP5",
                   "Composite Score": "9.0"},
            ),
        ]
        df = pd.DataFrame(rows)
        final_df, _, _ = compute_phase5(df)
        assert list(final_df["ASIN"]) == ["B0HIGH", "B0LOW"]

    def test_commercial_priority_zero_treated_as_missing_sentinel(self):
        # Reviewer M1: JS `parseFloat(...) || 99` collapses 0 to 99.
        # A row with Commercial Priority="0" must sort AFTER a row with "1",
        # not before — the JS upstream treats 0 as a sentinel.
        rows = [
            _make_phase4_row(ASIN="B0ZERO", **{"Commercial Priority": "0"}),
            _make_phase4_row(ASIN="B0ONE", **{"Commercial Priority": "1"}),
        ]
        df = pd.DataFrame(rows)
        final_df, _, _ = compute_phase5(df)
        assert list(final_df["ASIN"]) == ["B0ONE", "B0ZERO"]

    def test_commercial_priority_empty_string_falls_back_to_99(self):
        rows = [
            _make_phase4_row(ASIN="B0EMPTY", **{"Commercial Priority": ""}),
            _make_phase4_row(ASIN="B0FIVE", **{"Commercial Priority": "5"}),
        ]
        df = pd.DataFrame(rows)
        final_df, _, _ = compute_phase5(df)
        # Priority 5 (rank 5) sorts before empty (rank 99).
        assert list(final_df["ASIN"]) == ["B0FIVE", "B0EMPTY"]

    def test_unknown_verdict_lowercase_does_not_match(self):
        # Reviewer M2: JS does case-sensitive verdict lookup. "yes" (lowercase)
        # doesn't match VERDICT_ORDER and falls through to the 99 sentinel.
        rows = [
            _make_phase4_row(
                ASIN="B0LOWER",
                **{"Verdict": "yes", "Commercial Priority": "1"},
            ),
            _make_phase4_row(
                ASIN="B0UPPER",
                **{"Verdict": "YES", "Commercial Priority": "1"},
            ),
        ]
        df = pd.DataFrame(rows)
        final_df, _, _ = compute_phase5(df)
        # "YES" (rank 1) ranks above "yes" (rank 99).
        assert list(final_df["ASIN"]) == ["B0UPPER", "B0LOWER"]


# ---------------------------------------------------------------------------
# NaN safety + missing columns
# ---------------------------------------------------------------------------


class TestNaNSafety:
    def test_nan_numeric_columns_coerce_to_zero(self):
        row = _make_phase4_row()
        row["Monthly Gross Profit"] = float("nan")
        row["Current Price"] = float("nan")
        df = pd.DataFrame([row])
        final_df, _, _ = compute_phase5(df)
        # No crash; outputs are formatted GBP strings.
        out = final_df.iloc[0]
        assert out["Current Price"] == "GBP0.00"
        assert out["Monthly Gross Profit"] == "GBP0"

    def test_pd_na_does_not_crash_or_leak_into_output(self):
        # Reviewer L1: pd.NA is not a float (so the float-NaN check doesn't
        # catch it) and bool(pd.NA) raises. The CLI uses dtype=str so this
        # never appears via that path, but run_step accepts arbitrary frames.
        row = _make_phase4_row()
        row["Monthly Gross Profit"] = pd.NA
        row["Current Price"] = pd.NA
        row["Brand"] = pd.NA  # categorical with pd.NA must not become "<NA>".
        df = pd.DataFrame([row])
        final_df, _, _ = compute_phase5(df)
        out = final_df.iloc[0]
        assert out["Current Price"] == "GBP0.00"
        assert out["Monthly Gross Profit"] == "GBP0"
        assert out["Brand"] == ""  # Critical: NOT the literal "<NA>".

    def test_missing_optional_columns_tolerated(self):
        row = {"ASIN": "B0BARE", "Title": "Bare"}
        df = pd.DataFrame([row])
        with pytest.warns(UserWarning, match="missing input columns"):
            final_df, _, _ = compute_phase5(df)
        # No crash; row falls through with defaults.
        assert len(final_df) == 1
        assert final_df.iloc[0]["ASIN"] == "B0BARE"

    def test_empty_df_returns_empty_with_full_schema(self):
        df = pd.DataFrame(columns=["ASIN"])
        final_df, supplier_df, rejected_df = compute_phase5(df)
        assert list(final_df.columns) == FINAL_HEADERS
        assert list(supplier_df.columns) == SUPPLIER_HEADERS
        assert len(final_df) == 0
        assert len(rejected_df) == 0


# ---------------------------------------------------------------------------
# Step contract
# ---------------------------------------------------------------------------


class TestRunStep:
    def test_run_step_returns_final_df_only(self):
        df = pd.DataFrame([_make_phase4_row()])
        out = run_step(df, {})
        assert list(out.columns) == FINAL_HEADERS

    def test_run_step_does_not_require_niche(self):
        df = pd.DataFrame([_make_phase4_row()])
        # Decision step also doesn't require niche; preserved here.
        out = run_step(df, {"niche": "kids-toys"})
        assert "ASIN" in out.columns


# ---------------------------------------------------------------------------
# Stats / handoff
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_includes_niche_and_counts(self):
        df = pd.DataFrame(
            [_make_phase4_row(ASIN="B001"), _make_phase4_row(ASIN="B002")]
        )
        final_df, _, rejected_df = compute_phase5(df)
        text = build_stats(
            final_df, rejected_df, niche="kids-toys", reject_csv_path="kids_toys_reject.csv"
        )
        assert "Niche: kids-toys" in text
        assert "Phase 5 Build Output" in text
        assert "Products in final CSV: 2" in text
        assert "Columns: 67" in text

    def test_stats_lane_breakdown(self):
        df = pd.DataFrame(
            [
                _make_phase4_row(ASIN="B001", **{"Opportunity Lane": "BALANCED"}),
                _make_phase4_row(ASIN="B002", **{"Opportunity Lane": "PROFIT"}),
            ]
        )
        final_df, _, rejected_df = compute_phase5(df)
        text = build_stats(
            final_df, rejected_df, niche="kids-toys", reject_csv_path="."
        )
        assert "BALANCED" in text
        assert "PROFIT" in text


class TestHandoff:
    def test_handoff_includes_summary(self):
        df = pd.DataFrame([_make_phase4_row()])
        final_df, _, _ = compute_phase5(df)
        text = build_handoff(final_df, niche="kids-toys")
        assert "Phase 5 Handoff" in text
        assert "kids-toys" in text
        assert "BUILD COMPLETE" in text
        assert "Columns: 67" in text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unicode_brand_does_not_crash(self):
        df = pd.DataFrame(
            [
                _make_phase4_row(
                    Brand="Café Ø",
                    Title="日本商品",
                )
            ]
        )
        final_df, _, _ = compute_phase5(df)
        assert len(final_df) == 1

    def test_verdict_order_mapping_includes_known_values(self):
        for verdict in ["YES", "MAYBE", "BRAND APPROACH", "BUY THE DIP", "MAYBE-ROI", "GATED"]:
            assert verdict in VERDICT_ORDER

    def test_unknown_verdict_sorts_last(self):
        rows = [
            _make_phase4_row(
                ASIN="B0KNOWN",
                **{"Verdict": "YES", "Commercial Priority": "1"},
            ),
            _make_phase4_row(
                ASIN="B0WEIRD",
                **{"Verdict": "WEIRDVERDICT", "Commercial Priority": "1"},
            ),
        ]
        df = pd.DataFrame(rows)
        final_df, _, _ = compute_phase5(df)
        # Unknown verdict gets sentinel rank (99); YES (rank 1) comes first.
        assert list(final_df["ASIN"])[0] == "B0KNOWN"


class TestBoughtFiniteness:
    """Regression: bought_int must not crash on NaN/inf."""

    def test_inf_bought_per_month_does_not_crash(self):
        # Upstream arithmetic could in principle propagate inf into the
        # numeric column. Even when input is a string, _parse_money
        # handles it; the explicit guard matters when bought is a real
        # float that happens to be infinite.
        df = pd.DataFrame([_make_phase4_row(**{"Bought per Month": "inf"})])
        final_df, _, _ = compute_phase5(df)
        # Should not raise; result is some non-crashing value.
        assert len(final_df) == 1

    def test_nan_bought_per_month_does_not_crash(self):
        df = pd.DataFrame([_make_phase4_row(**{"Bought per Month": "nan"})])
        final_df, _, _ = compute_phase5(df)
        assert len(final_df) == 1


class TestRunCli:
    """Regression: end-to-end run() writes outputs atomically with utf-8-sig
    encoding so non-ASCII brands round-trip cleanly back through pd.read_csv."""

    def _setup_input(self, base: Path, niche_snake: str) -> Path:
        working = base / "working"
        working.mkdir(parents=True, exist_ok=True)
        input_path = working / f"{niche_snake}_phase4_ip_risk.csv"
        df = pd.DataFrame([_make_phase4_row(Brand="Café Ø")])
        df.to_csv(input_path, index=False, encoding="utf-8-sig")
        return input_path

    def test_run_writes_utf8_sig_round_trip(self, tmp_path: Path):
        from fba_engine.steps.build_output import run as run_cli
        self._setup_input(tmp_path, "kids_toys")
        run_cli("kids-toys", tmp_path)
        out_path = tmp_path / "kids_toys_final_results.csv"
        assert out_path.exists()
        # Read back with utf-8-sig — BOM stripped — Brand column
        # preserves non-ASCII characters cleanly.
        round_trip = pd.read_csv(out_path, dtype=str, encoding="utf-8-sig")
        assert "Café Ø" in round_trip["Brand"].iloc[0]
        # Verify BOM was actually written (atomic write didn't drop it).
        with open(out_path, "rb") as fh:
            head = fh.read(3)
        assert head == b"\xef\xbb\xbf"

    def test_run_does_not_create_working_when_input_missing(self, tmp_path: Path):
        from fba_engine.steps.build_output import run as run_cli
        # No setup — input does not exist.
        with pytest.raises(SystemExit):
            run_cli("kids-toys", tmp_path)
        # working/ should NOT have been created on the missing-input path.
        assert not (tmp_path / "working").exists()

    def test_run_atomic_write_does_not_leave_tmp_files(self, tmp_path: Path):
        from fba_engine.steps.build_output import run as run_cli
        self._setup_input(tmp_path, "kids_toys")
        run_cli("kids-toys", tmp_path)
        # No leftover .tmp siblings.
        leftovers = list(tmp_path.rglob("*.tmp"))
        assert leftovers == []
