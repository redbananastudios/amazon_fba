"""Tests for fba_engine.steps.ip_risk.

Logic ported 1:1 from `fba_engine/_legacy_keepa/skills/skill-4-ip-risk/
phase4_ip_risk.js` so these tests double as a regression contract for the
JS→Python port.
"""
from __future__ import annotations

import pandas as pd
import pytest

import math
import warnings

from fba_engine.steps.ip_risk import (
    brand_type,
    build_handoff,
    build_stats,
    category_risk_level,
    clamp,
    compute_ip_risk,
    levenshtein,
    normalize_name,
    run_step,
    similarity,
)


# ────────────────────────────────────────────────────────────────────────
# normalize_name
# ────────────────────────────────────────────────────────────────────────


class TestNormalizeName:
    def test_strips_legal_suffixes(self):
        assert normalize_name("Acme Limited") == "acme"
        assert normalize_name("Foo Ltd") == "foo"
        assert normalize_name("Bar Inc") == "bar"

    def test_drops_parenthesised_qualifiers(self):
        assert normalize_name("Acme (UK)") == "acme"
        assert normalize_name("Brand (Trading Name)") == "brand"

    def test_picks_first_segment_before_slash(self):
        assert normalize_name("Acme/SubBrand") == "acme"

    def test_lowercases_and_collapses_whitespace(self):
        assert normalize_name("  ACME  CORP  ") == "acme corp"

    def test_strips_non_alphanumerics(self):
        assert normalize_name("Acme & Co!") == "acme co"
        assert normalize_name("Brand-Name_2024") == "brand name 2024"

    def test_handles_empty_and_none(self):
        assert normalize_name("") == ""
        assert normalize_name(None) == ""


# ────────────────────────────────────────────────────────────────────────
# levenshtein + similarity
# ────────────────────────────────────────────────────────────────────────


class TestLevenshtein:
    def test_identical(self):
        assert levenshtein("abc", "abc") == 0

    def test_empty(self):
        assert levenshtein("", "abc") == 3
        assert levenshtein("abc", "") == 3
        assert levenshtein("", "") == 0

    def test_single_substitution(self):
        assert levenshtein("kitten", "sitten") == 1

    def test_classic_kitten_sitting(self):
        assert levenshtein("kitten", "sitting") == 3


class TestSimilarity:
    def test_identical_normalised_pair_is_one(self):
        assert similarity("Acme", "ACME") == pytest.approx(1.0)

    def test_disjoint_strings_low(self):
        assert similarity("apple", "zebra") < 0.4

    def test_normalisation_kicks_in(self):
        # "Acme Limited" → "acme"; "Acme Ltd" → "acme"; identical after norm.
        assert similarity("Acme Limited", "Acme Ltd") == pytest.approx(1.0)

    def test_empty_returns_zero(self):
        assert similarity("", "anything") == 0.0
        assert similarity(None, None) == 0.0


# ────────────────────────────────────────────────────────────────────────
# category_risk_level
# ────────────────────────────────────────────────────────────────────────


class TestCategoryRiskLevel:
    @pytest.mark.parametrize(
        "niche,expected",
        [
            ("educational-toys", "HIGH"),
            ("kids-toys", "HIGH"),
            ("afro-hair", "MEDIUM"),
            ("pet-care", "MEDIUM"),
            ("sports-goods", "MEDIUM"),
            ("stationery", "LOW"),
        ],
    )
    def test_known_niches(self, niche, expected):
        assert category_risk_level(niche) == expected

    def test_unknown_niche_defaults_medium(self):
        assert category_risk_level("not-a-real-niche") == "MEDIUM"
        assert category_risk_level("") == "MEDIUM"


# ────────────────────────────────────────────────────────────────────────
# brand_type
# ────────────────────────────────────────────────────────────────────────


class TestBrandType:
    @pytest.mark.parametrize("known", ["LEGO", "Pokemon", "Disney", "PlayDoh"])
    def test_known_brands_are_established(self, known):
        assert brand_type(known, review_count=0, rating=0) == "ESTABLISHED"

    def test_high_review_count_and_rating_promotes_to_established(self):
        # > 500 reviews AND > 3.5 rating
        assert brand_type("UnknownBrand", review_count=600, rating=4.0) == "ESTABLISHED"

    def test_high_reviews_but_low_rating_is_not_established(self):
        # Boundary: rating must be > 3.5 (strict)
        assert brand_type("UnknownBrand", review_count=600, rating=3.5) != "ESTABLISHED"

    def test_all_caps_short_is_synthetic(self):
        assert brand_type("XYZ", review_count=0, rating=0) == "SYNTHETIC"
        assert brand_type("AB", review_count=0, rating=0) == "SYNTHETIC"

    def test_contains_two_digits_is_synthetic(self):
        assert brand_type("Brand24", review_count=0, rating=0) == "SYNTHETIC"

    def test_short_brand_three_chars_or_less_is_synthetic(self):
        assert brand_type("Foo", review_count=0, rating=0) == "SYNTHETIC"

    def test_normal_brand_is_generic(self):
        assert brand_type("MidTierToys", review_count=0, rating=0) == "GENERIC"


# ────────────────────────────────────────────────────────────────────────
# clamp
# ────────────────────────────────────────────────────────────────────────


class TestClamp:
    def test_within_range_returns_value(self):
        assert clamp(5, 0, 10) == 5

    def test_below_min_returns_min(self):
        assert clamp(-3, 0, 10) == 0

    def test_above_max_returns_max(self):
        assert clamp(99, 0, 10) == 10


# ────────────────────────────────────────────────────────────────────────
# compute_ip_risk — end-to-end DataFrame transforms.
# ────────────────────────────────────────────────────────────────────────


def _make_row(**overrides) -> dict:
    """Build a minimal Phase-3 shortlist row with sensible defaults."""
    base = {
        "ASIN": "B000TEST",
        "Brand": "Acme",
        "BB Seller": "Some Other Seller",
        "FBA Seller Count": "5",
        "FBA Seller 90d Avg": "5.0",
        "Review Count": "100",
        "Star Rating": "4.0",
        "Has A+ Content": "N",
        "Brand 1P": "N",
        "Monthly Gross Profit": "0",
    }
    base.update(overrides)
    return base


class TestComputeIPRisk:
    def test_empty_df_returns_empty_with_added_columns(self):
        df = pd.DataFrame(columns=["ASIN", "Brand"])
        out = compute_ip_risk(df, niche="kids-toys")
        for header in [
            "Brand Seller Match",
            "Fortress Listing",
            "IP Risk Score",
            "IP Risk Band",
        ]:
            assert header in out.columns
        assert len(out) == 0

    def test_input_df_is_not_mutated(self):
        df = pd.DataFrame([_make_row()])
        before = df.copy()
        _ = compute_ip_risk(df, niche="kids-toys")
        pd.testing.assert_frame_equal(df, before)

    def test_brand_seller_match_yes_when_brand_in_seller_name(self):
        df = pd.DataFrame([_make_row(Brand="Acme", **{"BB Seller": "Acme Direct UK"})])
        out = compute_ip_risk(df, niche="stationery")
        assert out.iloc[0]["Brand Seller Match"] == "YES"

    def test_brand_seller_match_partial_for_close_but_disjoint_pair(self):
        # "Acme" vs "Acne" — neither contains the other after normalisation,
        # but levenshtein distance is 1 over max len 4 → similarity 0.75
        # which clears the >0.7 PARTIAL threshold.
        df = pd.DataFrame([_make_row(Brand="Acme", **{"BB Seller": "Acne"})])
        out = compute_ip_risk(df, niche="stationery")
        assert out.iloc[0]["Brand Seller Match"] == "PARTIAL"

    def test_brand_seller_match_yes_when_normalised_seller_contains_brand(self):
        # "Acme Toys" vs "Acme Toy" — after normalisation "acme toy" is
        # contained in "acme toys", so the inclusion check fires before
        # the fuzzy fallback.
        df = pd.DataFrame([_make_row(Brand="Acme Toys", **{"BB Seller": "Acme Toy"})])
        out = compute_ip_risk(df, niche="stationery")
        assert out.iloc[0]["Brand Seller Match"] == "YES"

    def test_brand_seller_match_no_when_disjoint(self):
        df = pd.DataFrame([_make_row(Brand="Acme", **{"BB Seller": "Zebra Ltd"})])
        out = compute_ip_risk(df, niche="stationery")
        assert out.iloc[0]["Brand Seller Match"] == "NO"

    def test_fortress_listing_yes_when_low_seller_counts(self):
        df = pd.DataFrame(
            [
                _make_row(
                    **{"FBA Seller Count": "1", "FBA Seller 90d Avg": "1.5"}
                )
            ]
        )
        out = compute_ip_risk(df, niche="stationery")
        assert out.iloc[0]["Fortress Listing"] == "YES"

    def test_fortress_listing_no_when_more_sellers(self):
        df = pd.DataFrame(
            [_make_row(**{"FBA Seller Count": "3", "FBA Seller 90d Avg": "2.5"})]
        )
        out = compute_ip_risk(df, niche="stationery")
        assert out.iloc[0]["Fortress Listing"] == "NO"

    def test_aplus_present_yes_for_y_or_yes(self):
        for token in ["Y", "y", "YES", "yes"]:
            df = pd.DataFrame([_make_row(**{"Has A+ Content": token})])
            out = compute_ip_risk(df, niche="stationery")
            assert out.iloc[0]["A+ Content Present"] == "YES", token

    def test_aplus_present_no_for_anything_else(self):
        for token in ["N", "no", "", "maybe"]:
            df = pd.DataFrame([_make_row(**{"Has A+ Content": token})])
            out = compute_ip_risk(df, niche="stationery")
            assert out.iloc[0]["A+ Content Present"] == "NO", token

    def test_brand_store_likely_requires_seller_match_and_aplus(self):
        df = pd.DataFrame(
            [
                _make_row(
                    Brand="Acme",
                    **{
                        "BB Seller": "Acme Direct",
                        "Has A+ Content": "Y",
                    },
                )
            ]
        )
        out = compute_ip_risk(df, niche="stationery")
        assert out.iloc[0]["Brand Store Present"] == "LIKELY"

    def test_brand_store_unlikely_when_either_signal_missing(self):
        # YES seller match but no A+
        df = pd.DataFrame(
            [_make_row(Brand="Acme", **{"BB Seller": "Acme Direct"})]
        )
        out = compute_ip_risk(df, niche="stationery")
        assert out.iloc[0]["Brand Store Present"] == "UNLIKELY"

    def test_score_caps_at_ten(self):
        # Maximum possible signal stack.
        df = pd.DataFrame(
            [
                _make_row(
                    Brand="LEGO",
                    **{
                        "BB Seller": "LEGO UK",
                        "FBA Seller Count": "1",
                        "FBA Seller 90d Avg": "1.0",
                        "Has A+ Content": "Y",
                    },
                )
            ]
        )
        out = compute_ip_risk(df, niche="kids-toys")
        # +3 (YES) +3 (fortress) +1 (established) +1 (A+) +1 (brand store)
        # +1 (HIGH category) = 10. Already at cap.
        assert out.iloc[0]["IP Risk Score"] == 10
        assert out.iloc[0]["IP Risk Band"] == "High"

    def test_score_band_thresholds(self):
        # band == High when score >= 7
        # band == Medium when 4 <= score < 7
        # band == Low when score < 4
        # Construct rows that land in each band.

        # Low: zero contributing factors, niche stationery (LOW).
        low_row = _make_row(
            Brand="Generic",
            **{"BB Seller": "Disjoint", "FBA Seller Count": "5", "FBA Seller 90d Avg": "5"},
        )
        # Medium: PARTIAL match (1) + fortress (3) + medium category (0.5)
        # = 4.5 → round → 5 → Medium.
        # Skip PARTIAL contrivance: use YES match (3) + medium category (0.5)
        # → 3.5 → round → 4 → Medium.
        med_row = _make_row(
            Brand="Acme",
            **{
                "BB Seller": "Acme",
                "FBA Seller Count": "5",
                "FBA Seller 90d Avg": "5",
            },
        )
        # High: YES match (3) + fortress (3) + medium category (0.5)
        # → 6.5 → round → 7 → High.
        high_row = _make_row(
            Brand="Acme",
            **{
                "BB Seller": "Acme",
                "FBA Seller Count": "1",
                "FBA Seller 90d Avg": "1.0",
            },
        )
        df = pd.DataFrame([low_row, med_row, high_row])
        out = compute_ip_risk(df, niche="afro-hair")  # MEDIUM category
        assert out.iloc[0]["IP Risk Band"] == "Low"
        assert out.iloc[1]["IP Risk Band"] == "Medium"
        assert out.iloc[2]["IP Risk Band"] == "High"

    def test_ip_reason_lists_contributing_factors(self):
        df = pd.DataFrame(
            [
                _make_row(
                    Brand="Acme",
                    **{
                        "BB Seller": "Acme",
                        "FBA Seller Count": "1",
                        "FBA Seller 90d Avg": "1.0",
                        "Has A+ Content": "Y",
                    },
                )
            ]
        )
        out = compute_ip_risk(df, niche="kids-toys")
        reason = out.iloc[0]["IP Reason"]
        assert "Brand=Seller match (YES)" in reason
        assert "Fortress listing" in reason
        assert "A+ content" in reason
        assert "Likely brand store" in reason
        assert "Category HIGH risk" in reason

    def test_handles_missing_columns_gracefully(self):
        # A bare-bones DataFrame with only ASIN should still run without crashing.
        # Empty FBA seller fields coerce to 0, which trips the fortress check
        # (0 <= 1 AND 0 <= 1.5 → YES → +3); kids-toys is HIGH category (+1).
        # Final: 4 → Medium band. The missing-columns warning is expected.
        df = pd.DataFrame([{"ASIN": "B0BARE"}])
        with pytest.warns(UserWarning, match="missing input columns"):
            out = compute_ip_risk(df, niche="kids-toys")
        assert len(out) == 1
        assert out.iloc[0]["IP Risk Score"] == 4
        assert out.iloc[0]["IP Risk Band"] == "Medium"

    def test_coerces_currency_strings_in_numeric_fields(self):
        # Legacy CSVs sometimes carry "GBP 5.0" — the coercer must strip "GBP".
        df = pd.DataFrame(
            [_make_row(**{"FBA Seller Count": "1", "FBA Seller 90d Avg": "GBP 1.0"})]
        )
        out = compute_ip_risk(df, niche="stationery")
        assert out.iloc[0]["Fortress Listing"] == "YES"


# ────────────────────────────────────────────────────────────────────────
# Stats / handoff text — light coverage; format isn't load-bearing for
# downstream phases, but regressions in the band counts would be.
# ────────────────────────────────────────────────────────────────────────


class TestStats:
    def test_stats_includes_niche_and_band_counts(self):
        df = pd.DataFrame(
            [
                _make_row(ASIN="B001", Brand="A"),
                _make_row(ASIN="B002", Brand="B"),
            ]
        )
        out = compute_ip_risk(df, niche="kids-toys")
        text = build_stats(out, niche="kids-toys")
        assert "Niche: kids-toys" in text
        assert "IP Risk Band distribution" in text
        assert "High:" in text
        assert "Medium:" in text
        assert "Low:" in text

    def test_handoff_includes_summary_counts(self):
        df = pd.DataFrame([_make_row()])
        out = compute_ip_risk(df, niche="stationery")
        text = build_handoff(out, niche="stationery", output_filename="stationery_phase4_ip_risk.csv")
        assert "Phase 4 Handoff" in text
        assert "Status: COMPLETE" in text
        assert "Run Phase 5" in text


# ────────────────────────────────────────────────────────────────────────
# Reviewer-requested coverage — boundary cases, NaN safety, run_step shape.
# ────────────────────────────────────────────────────────────────────────


class TestScoreBoundaries:
    """The half-rounding boundary is the trickiest porting bug-class
    (JS Math.round vs Python round). Pin it explicitly per band edge."""

    def test_score_3_5_rounds_up_to_4_medium(self):
        # YES match (3) + MEDIUM category (0.5) = 3.5 → round-up → 4 → Medium.
        df = pd.DataFrame(
            [
                _make_row(
                    Brand="Acme",
                    **{
                        "BB Seller": "Acme",
                        "FBA Seller Count": "5",
                        "FBA Seller 90d Avg": "5",
                    },
                )
            ]
        )
        out = compute_ip_risk(df, niche="afro-hair")  # MEDIUM
        assert out.iloc[0]["IP Risk Score"] == 4
        assert out.iloc[0]["IP Risk Band"] == "Medium"

    def test_score_6_5_rounds_up_to_7_high(self):
        # YES match (3) + fortress (3) + MEDIUM category (0.5) = 6.5
        # → round-up → 7 → High.
        df = pd.DataFrame(
            [
                _make_row(
                    Brand="Acme",
                    **{
                        "BB Seller": "Acme",
                        "FBA Seller Count": "1",
                        "FBA Seller 90d Avg": "1.0",
                    },
                )
            ]
        )
        out = compute_ip_risk(df, niche="pet-care")  # MEDIUM
        assert out.iloc[0]["IP Risk Score"] == 7
        assert out.iloc[0]["IP Risk Band"] == "High"

    def test_score_zero_when_no_signals(self):
        # No match, no fortress, generic brand, no A+, LOW category.
        df = pd.DataFrame(
            [
                _make_row(
                    Brand="Generic",
                    **{
                        "BB Seller": "Disjoint Co",
                        "FBA Seller Count": "10",
                        "FBA Seller 90d Avg": "10",
                    },
                )
            ]
        )
        out = compute_ip_risk(df, niche="stationery")  # LOW
        assert out.iloc[0]["IP Risk Score"] == 0
        assert out.iloc[0]["IP Risk Band"] == "Low"

    def test_score_saturates_at_ten_when_signals_overshoot(self):
        # Maximum signal stack is 3+3+1+1+1+1 = 10 exactly. To verify
        # saturation we'd need >10 raw, which the current weights don't
        # produce. Test at-cap behaviour instead — score is exactly 10
        # AND band is High (i.e. clamp didn't accidentally drop it).
        df = pd.DataFrame(
            [
                _make_row(
                    Brand="LEGO",
                    **{
                        "BB Seller": "LEGO UK",
                        "FBA Seller Count": "1",
                        "FBA Seller 90d Avg": "1.0",
                        "Has A+ Content": "Y",
                    },
                )
            ]
        )
        out = compute_ip_risk(df, niche="kids-toys")  # HIGH
        assert out.iloc[0]["IP Risk Score"] == 10
        assert out.iloc[0]["IP Risk Band"] == "High"


class TestPandasNaNSafety:
    """Pandas NaN is a truthy float, so `raw or ""` doesn't catch it.
    The CLI path uses `dtype=str, keep_default_na=False` which avoids
    this — but step 5's runner will pass arbitrary DataFrames where
    NaNs can appear. Pin the safety net here."""

    def test_nan_brand_does_not_become_string_nan(self):
        # NaN brand should be treated as empty, not as the literal "nan"
        # (which is 3 chars and would mis-classify as SYNTHETIC).
        df = pd.DataFrame(
            [
                {
                    "ASIN": "B0NAN",
                    "Brand": float("nan"),
                    "BB Seller": float("nan"),
                    "FBA Seller Count": 5,
                    "FBA Seller 90d Avg": 5.0,
                    "Review Count": 100,
                    "Star Rating": 4.0,
                    "Has A+ Content": "N",
                }
            ]
        )
        out = compute_ip_risk(df, niche="stationery")
        # Empty brand → compact = "" → len <= 3 → SYNTHETIC. Acceptable
        # (current behaviour for empty brand). Critical assertion: did
        # NOT mistake NaN for a 3-char "nan" string and use it for any
        # other downstream signal.
        assert out.iloc[0]["Brand Seller Match"] == "NO"

    def test_nan_numeric_columns_coerce_to_zero(self):
        df = pd.DataFrame(
            [
                {
                    "ASIN": "B0NUMNAN",
                    "Brand": "Acme",
                    "BB Seller": "Disjoint",
                    "FBA Seller Count": float("nan"),
                    "FBA Seller 90d Avg": float("nan"),
                }
            ]
        )
        out = compute_ip_risk(df, niche="stationery")
        # NaN coerced to 0 → fortress YES (0 <= 1 AND 0 <= 1.5).
        assert out.iloc[0]["Fortress Listing"] == "YES"

    def test_warns_when_required_columns_missing(self):
        df = pd.DataFrame([{"ASIN": "B0WARN", "Brand": "Acme"}])
        with pytest.warns(UserWarning, match="missing input columns"):
            _ = compute_ip_risk(df, niche="stationery")

    def test_no_warning_when_all_required_columns_present(self):
        df = pd.DataFrame([_make_row()])
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning becomes a test failure
            _ = compute_ip_risk(df, niche="stationery")


class TestRunStep:
    """Step 5's runner will invoke every step as run_step(df, config).
    Pin the contract."""

    def test_run_step_returns_enriched_dataframe(self):
        df = pd.DataFrame([_make_row()])
        out = run_step(df, {"niche": "stationery"})
        assert "IP Risk Score" in out.columns
        assert len(out) == 1

    def test_run_step_raises_when_niche_missing_from_config(self):
        df = pd.DataFrame([_make_row()])
        with pytest.raises(ValueError, match="niche"):
            run_step(df, {})

    def test_run_step_raises_when_niche_is_empty_string(self):
        df = pd.DataFrame([_make_row()])
        with pytest.raises(ValueError, match="niche"):
            run_step(df, {"niche": ""})


class TestEdgeCaseInputs:
    """Reviewer-flagged: weird brand strings shouldn't crash the scorer."""

    def test_unicode_brand_does_not_crash(self):
        df = pd.DataFrame(
            [
                _make_row(Brand="Ø Café", **{"BB Seller": "Different Co"}),
                _make_row(Brand="日本ブランド", **{"BB Seller": "Different Co"}),
            ]
        )
        out = compute_ip_risk(df, niche="stationery")
        assert len(out) == 2
        # Just verify scoring ran — we don't make claims about Unicode
        # brand classification semantics.
        assert "IP Risk Score" in out.columns

    def test_two_digit_only_brand_is_synthetic(self):
        # "24" → compact="24" → matches \d{2,} → SYNTHETIC.
        assert brand_type("24", review_count=0, rating=0) == "SYNTHETIC"

    def test_pure_punctuation_brand_falls_through_to_short(self):
        # "!!!" → compact="" → len <= 3 → SYNTHETIC.
        assert brand_type("!!!", review_count=0, rating=0) == "SYNTHETIC"
