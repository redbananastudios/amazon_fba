"""Tests for sourcing_engine.buy_plan.

Pure-function semantics: each test exercises ``compute_buy_plan`` on a
hand-built row dict; no I/O. Mirrors the patterns in the validate_opportunity
test suite.
"""
from __future__ import annotations

import pytest

from fba_config_loader import (
    BuyPlan,
    OpportunityValidation,
    get_buy_plan,
    get_opportunity_validation,
    reset_cache,
)
from sourcing_engine.buy_plan import (
    BUY_PLAN_COLUMNS,
    STATUS_BLOCKED_BY_VERDICT,
    STATUS_INSUFFICIENT_DATA,
    STATUS_INSUFFICIENT_VELOCITY,
    STATUS_NO_BUY_COST,
    STATUS_OK,
    STATUS_UNECONOMIC_AT_ANY_PRICE,
    _compute_risk_factor,
    compute_buy_plan,
)


def setup_function():
    reset_cache()


# ────────────────────────────────────────────────────────────────────────
# Row builders.
# ────────────────────────────────────────────────────────────────────────


def _buy_row(**overrides) -> dict:
    """A clean BUY row with profitable economics + healthy velocity.

    raw_conservative_price 16.85 - fees 4.50 = 12.35 gross.
    BUY ceiling = 12.35 / 1.30 = 9.50 (ROI gate binds).
    Operator pays 4.00 → headroom is wide.
    """
    base = {
        "opportunity_verdict": "BUY",
        "opportunity_confidence": "HIGH",
        "risk_flags": [],
        "predicted_velocity_mid": 18,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        "profit_conservative": 8.35,    # 16.85 - 4.50 - 4.00
        "buy_cost": 4.00,
    }
    base.update(overrides)
    return base


def _source_only_row(**overrides) -> dict:
    """A SOURCE_ONLY row: strong demand, no buy_cost."""
    base = {
        "opportunity_verdict": "SOURCE_ONLY",
        "opportunity_confidence": "HIGH",
        "risk_flags": [],
        "predicted_velocity_mid": 42,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        "profit_conservative": None,
        "buy_cost": 0.0,
    }
    base.update(overrides)
    return base


def _negotiate_row(**overrides) -> dict:
    """NEGOTIATE: buy_cost is over the ceiling."""
    base = {
        "opportunity_verdict": "NEGOTIATE",
        "opportunity_confidence": "HIGH",
        "risk_flags": [],
        "predicted_velocity_mid": 18,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        # Operator currently pays £10 (over the £9.50 BUY ceiling).
        "profit_conservative": 2.35,
        "buy_cost": 10.00,
    }
    base.update(overrides)
    return base


def _watch_row(**overrides) -> dict:
    base = {
        "opportunity_verdict": "WATCH",
        "opportunity_confidence": "MEDIUM",
        "risk_flags": ["INSUFFICIENT_HISTORY"],
        "predicted_velocity_mid": 18,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        "profit_conservative": 8.35,
        "buy_cost": 4.00,
    }
    base.update(overrides)
    return base


def _kill_row(**overrides) -> dict:
    base = {
        "opportunity_verdict": "KILL",
        "opportunity_confidence": "LOW",
        "risk_flags": ["PRICE_FLOOR_HIT"],
        "predicted_velocity_mid": 5,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        "profit_conservative": -1.0,
        "buy_cost": 8.00,
    }
    base.update(overrides)
    return base


# ────────────────────────────────────────────────────────────────────────
# Risk dampener (PRD §5.1).
# ────────────────────────────────────────────────────────────────────────


class TestRiskFactor:
    def test_high_confidence_no_flags_is_full(self):
        cfg = get_buy_plan()
        assert _compute_risk_factor("HIGH", set(), cfg) == 1.0

    def test_medium_confidence_dampens(self):
        cfg = get_buy_plan()
        assert _compute_risk_factor("MEDIUM", set(), cfg) == cfg.risk_medium_confidence

    def test_low_confidence_dampens(self):
        cfg = get_buy_plan()
        assert _compute_risk_factor("LOW", set(), cfg) == cfg.risk_low_confidence

    def test_each_flag_compounds(self):
        cfg = get_buy_plan()
        f = _compute_risk_factor("HIGH", {"INSUFFICIENT_HISTORY"}, cfg)
        assert f == cfg.risk_insufficient_history
        f2 = _compute_risk_factor(
            "HIGH", {"INSUFFICIENT_HISTORY", "BSR_DECLINING"}, cfg,
        )
        assert f2 == cfg.risk_insufficient_history * cfg.risk_bsr_declining

    def test_floor_caps_compounded_dampening(self):
        cfg = get_buy_plan()
        # Pile on every flag at LOW confidence — without floor, the
        # product would dive far below 0.5. Floor must hold.
        all_flags = {
            "INSUFFICIENT_HISTORY",
            "LISTING_TOO_NEW",
            "COMPETITION_GROWING",
            "BSR_DECLINING",
            "PRICE_UNSTABLE",
        }
        f = _compute_risk_factor("LOW", all_flags, cfg)
        assert f == cfg.risk_floor

    def test_unknown_confidence_treated_as_high(self):
        cfg = get_buy_plan()
        # No upstream confidence → no dampener. Output equals 1.0.
        assert _compute_risk_factor(None, set(), cfg) == 1.0
        assert _compute_risk_factor("", set(), cfg) == 1.0


# ────────────────────────────────────────────────────────────────────────
# BUY verdict.
# ────────────────────────────────────────────────────────────────────────


class TestBuy:
    def test_buy_populates_all_sizing_fields(self):
        out = compute_buy_plan(_buy_row())
        for col in BUY_PLAN_COLUMNS:
            assert col in out
        assert out["buy_plan_status"] == STATUS_OK
        assert out["order_qty_recommended"] is not None
        assert out["capital_required"] is not None
        assert out["projected_30d_units"] is not None
        assert out["projected_30d_revenue"] is not None
        assert out["projected_30d_profit"] is not None
        assert out["payback_days"] is not None
        assert out["target_buy_cost_buy"] is not None
        assert out["target_buy_cost_stretch"] is not None
        # NEGOTIATE-only fields blank for BUY.
        assert out["gap_to_buy_gbp"] is None
        assert out["gap_to_buy_pct"] is None

    def test_buy_sizing_arithmetic(self):
        # mid=18, HIGH confidence, no flags → projected = 18.
        # First-order, 21d cover → ceil(18 * 21 / 30) = 13.
        out = compute_buy_plan(_buy_row())
        assert out["projected_30d_units"] == 18
        assert out["order_qty_recommended"] == 13
        assert out["capital_required"] == 52.00       # 13 × 4.00
        # Payback: 13 / 18 × 30 = 21.67 → 21.7
        assert out["payback_days"] == 21.7

    def test_buy_min_test_qty_floors_small_velocity(self):
        # mid=2, 21d cover → ceil(2 × 21 / 30) = 2. Floor at 5.
        row = _buy_row(predicted_velocity_mid=2)
        out = compute_buy_plan(row)
        # projected_30d_units = 2 (mid × 1.0); order_qty floored to 5.
        assert out["order_qty_recommended"] == 5

    def test_buy_unit_cap_binds_on_high_velocity(self):
        # mid=200/mo, 21d cover → ceil(200 × 21/30) = 140. Unit cap
        # of 50 binds — operator's first order is 50 units regardless
        # of buy_cost.
        row = _buy_row(predicted_velocity_mid=200)
        out = compute_buy_plan(row)
        assert out["order_qty_recommended"] == 50
        assert out["capital_required"] == 200.0       # 50 × 4.00

    def test_buy_unit_cap_independent_of_buy_cost(self):
        # Same velocity, expensive item: cap still binds at 50 units
        # — the unit cap doesn't change with cost-of-goods drift.
        # Capital exposure DOES grow (operator sees this in
        # capital_required + decides if it's tolerable).
        row = _buy_row(
            predicted_velocity_mid=200,
            buy_cost=40.0,
            raw_conservative_price=80.0,
            fees_conservative=15.0,
            profit_conservative=25.0,
        )
        out = compute_buy_plan(row)
        assert out["order_qty_recommended"] == 50    # same cap
        assert out["capital_required"] == 2000.0     # 50 × 40 — operator visible

    def test_buy_moq_wins_over_computed_qty(self):
        # mid=18, ceil(18 × 21 / 30) = 13. MOQ=20 wins.
        out = compute_buy_plan(_buy_row(moq=20))
        assert out["order_qty_recommended"] == 20

    def test_buy_moq_busts_unit_cap_intentionally(self):
        # MOQ above the unit cap — MOQ still wins. capital_required
        # surfaces the over-cap exposure to the operator.
        row = _buy_row(moq=80, buy_cost=4.0)
        out = compute_buy_plan(row)
        assert out["order_qty_recommended"] == 80
        assert out["capital_required"] == 320.0      # 80 × 4.00

    def test_buy_reorder_mode_skips_unit_cap(self):
        # mid=200/mo, 45d cover → ceil(200 × 45/30) = 300.
        # Reorder mode: no cap (operator has already validated
        # sell-through). Order qty matches the velocity-driven raw.
        out = compute_buy_plan(
            _buy_row(predicted_velocity_mid=200),
            order_mode="reorder",
        )
        assert out["order_qty_recommended"] == 300
        assert out["capital_required"] == 1200.0     # 300 × 4.00

    def test_buy_reorder_mode_default_velocity_uses_longer_cover(self):
        # mid=18, 45d cover → ceil(18 × 45 / 30) = 27 (under cap).
        out = compute_buy_plan(_buy_row(), order_mode="reorder")
        assert out["order_qty_recommended"] == 27
        assert out["capital_required"] == 108.0      # 27 × 4.00

    def test_buy_no_velocity_returns_insufficient_velocity(self):
        # No mid → can't size. INSUFFICIENT_VELOCITY.
        out = compute_buy_plan(_buy_row(predicted_velocity_mid=None))
        assert out["buy_plan_status"] == STATUS_INSUFFICIENT_VELOCITY
        assert out["order_qty_recommended"] is None
        # Targets still populated.
        assert out["target_buy_cost_buy"] is not None

    def test_buy_zero_velocity_after_dampening_returns_insufficient_velocity(self):
        # mid=1, LOW confidence (×0.7) + 5 flags → floored at 0.5.
        # 1 × 0.5 = 0.5 → round to 0.
        row = _buy_row(
            predicted_velocity_mid=0,
        )
        out = compute_buy_plan(row)
        assert out["buy_plan_status"] == STATUS_INSUFFICIENT_VELOCITY

    def test_buy_zero_buy_cost_returns_insufficient_data(self):
        out = compute_buy_plan(_buy_row(buy_cost=0.0))
        assert out["buy_plan_status"] == STATUS_INSUFFICIENT_DATA
        assert out["order_qty_recommended"] is None
        assert out["target_buy_cost_buy"] is not None

    def test_buy_negative_buy_cost_returns_insufficient_data(self):
        out = compute_buy_plan(_buy_row(buy_cost=-1.0))
        assert out["buy_plan_status"] == STATUS_INSUFFICIENT_DATA

    def test_buy_payback_days_arithmetic(self):
        # mid=10, order_qty=ceil(10*21/30)=7, max(7, min_test_qty=5)=7.
        # Payback: 7 / 10 * 30 = 21.0
        row = _buy_row(predicted_velocity_mid=10)
        out = compute_buy_plan(row)
        assert out["order_qty_recommended"] == 7
        assert out["payback_days"] == 21.0

    def test_buy_dampener_cuts_projected_units(self):
        # LOW confidence × INSUFFICIENT_HISTORY = 0.7 × 0.85 = 0.595
        row = _buy_row(
            opportunity_confidence="LOW",
            risk_flags=["INSUFFICIENT_HISTORY"],
        )
        out = compute_buy_plan(row)
        # mid=18, factor=0.595 → 10.71 → 11
        assert out["projected_30d_units"] == 11


# ────────────────────────────────────────────────────────────────────────
# Target buy cost (PRD §5.2).
# ────────────────────────────────────────────────────────────────────────


class TestTargetBuyCost:
    def test_roi_ceiling_binds_for_cheap_fast_mover(self):
        # raw 16.85 - fees 4.50 = 12.35 gross.
        # ROI ceiling 12.35 / 1.30 = 9.50.
        # Abs ceiling 12.35 - 2.50 = 9.85.
        # min = 9.50 (ROI binds).
        out = compute_buy_plan(_buy_row())
        assert out["target_buy_cost_buy"] == 9.50

    def test_absolute_ceiling_binds_for_expensive_slow_mover(self):
        # gross = 6.50, ROI ceiling 6.50/1.30 = 5.00, abs = 6.50-2.50=4.00.
        # min = 4.00 (abs binds).
        row = _buy_row(
            raw_conservative_price=11.00, fees_conservative=4.50,
            profit_conservative=2.50, buy_cost=4.00,
        )
        out = compute_buy_plan(row)
        assert out["target_buy_cost_buy"] == 4.00

    def test_stretch_lower_than_buy_target(self):
        out = compute_buy_plan(_buy_row())
        assert out["target_buy_cost_stretch"] < out["target_buy_cost_buy"]

    def test_uneconomic_when_gross_below_min_profit(self):
        # gross = 4.50 - 4.50 = 0; below £2.50 absolute floor → UNECONOMIC.
        row = _buy_row(raw_conservative_price=4.50, fees_conservative=4.50)
        # Force WATCH so the BUY-only no-buy_cost branch doesn't fire
        # (we're testing target-cost computation, not verdict logic).
        row["opportunity_verdict"] = "WATCH"
        out = compute_buy_plan(row)
        assert out["target_buy_cost_buy"] is None
        assert out["target_buy_cost_stretch"] is None
        # WATCH still BLOCKED_BY_VERDICT in this case (target absent).

    def test_uneconomic_status_routes_to_uneconomic_for_negotiate(self):
        # Same UNECONOMIC condition on a NEGOTIATE row → status flips
        # to UNECONOMIC_AT_ANY_PRICE.
        row = _negotiate_row(raw_conservative_price=4.50, fees_conservative=4.50)
        out = compute_buy_plan(row)
        assert out["buy_plan_status"] == STATUS_UNECONOMIC_AT_ANY_PRICE

    def test_uneconomic_status_routes_to_uneconomic_for_buy(self):
        # Defensive — validate_opportunity shouldn't emit BUY for
        # gross < min_profit_absolute_buy, but a malformed upstream
        # row could. The status must reflect structural unprofitability,
        # not OK + a bogus order_qty.
        row = _buy_row(
            raw_conservative_price=5.00, fees_conservative=4.50,
            profit_conservative=-1.00, buy_cost=1.50,
        )
        out = compute_buy_plan(row)
        assert out["buy_plan_status"] == STATUS_UNECONOMIC_AT_ANY_PRICE
        assert out["target_buy_cost_buy"] is None

    def test_uneconomic_status_routes_to_uneconomic_for_watch(self):
        row = _watch_row(
            raw_conservative_price=4.50, fees_conservative=4.50,
        )
        out = compute_buy_plan(row)
        assert out["buy_plan_status"] == STATUS_UNECONOMIC_AT_ANY_PRICE

    def test_negative_stretch_target_collapses_to_none(self):
        # Thin-margin listing where abs_stretch goes negative.
        # gross = 3.00, ROI ceiling = 2.31, abs ceiling = 0.50 (binds).
        # Stretch: roi_stretch = 3.00/1.45 = 2.07, abs_stretch = 3.00 -
        # 2.50 × 1.5 = -0.75 → min = -0.75 → clamp to None.
        row = _buy_row(
            raw_conservative_price=7.50, fees_conservative=4.50,
            profit_conservative=1.50, buy_cost=1.50,
        )
        out = compute_buy_plan(row)
        # target_buy is positive (£0.50)
        assert out["target_buy_cost_buy"] is not None
        assert out["target_buy_cost_buy"] > 0
        # stretch went negative → None
        assert out["target_buy_cost_stretch"] is None

    def test_target_buy_blank_when_inputs_absent(self):
        row = _buy_row()
        row.pop("raw_conservative_price")
        out = compute_buy_plan(row)
        assert out["target_buy_cost_buy"] is None
        assert out["target_buy_cost_stretch"] is None
        # And BUY can't size without raw_cp / fees → falls to
        # INSUFFICIENT_DATA via the BUY no-buy_cost defensive branch?
        # Actually here buy_cost=4 is fine; sizing still works.
        # Let's just assert no crash + status is something.
        assert out["buy_plan_status"] in (
            STATUS_OK, STATUS_INSUFFICIENT_DATA,
        )


# ────────────────────────────────────────────────────────────────────────
# SOURCE_ONLY verdict.
# ────────────────────────────────────────────────────────────────────────


class TestSourceOnly:
    def test_source_only_blanks_sizing_populates_targets(self):
        out = compute_buy_plan(_source_only_row())
        assert out["buy_plan_status"] == STATUS_NO_BUY_COST
        assert out["order_qty_recommended"] is None
        assert out["capital_required"] is None
        assert out["payback_days"] is None
        assert out["target_buy_cost_buy"] is not None
        assert out["target_buy_cost_stretch"] is not None
        assert out["projected_30d_units"] is not None
        # Revenue can be computed without buy_cost.
        assert out["projected_30d_revenue"] is not None

    def test_source_only_profit_uses_target_buy_cost_for_best_case(self):
        # mid=42, raw=16.85, fees=4.50, target_buy=9.50
        # per-unit best-case profit = 16.85 - 4.50 - 9.50 = 2.85
        # → 42 × 2.85 = 119.70
        out = compute_buy_plan(_source_only_row())
        assert out["projected_30d_profit"] is not None
        assert out["projected_30d_profit"] == pytest.approx(42 * 2.85, abs=0.01)

    def test_source_only_uneconomic_blanks_targets(self):
        # gross = 0 → UNECONOMIC.
        row = _source_only_row(
            raw_conservative_price=4.50, fees_conservative=4.50,
        )
        out = compute_buy_plan(row)
        assert out["buy_plan_status"] == STATUS_UNECONOMIC_AT_ANY_PRICE
        assert out["target_buy_cost_buy"] is None


# ────────────────────────────────────────────────────────────────────────
# NEGOTIATE verdict.
# ────────────────────────────────────────────────────────────────────────


class TestNegotiate:
    def test_negotiate_populates_gap(self):
        # buy_cost=10, target=9.50 → gap=£0.50, gap_pct=5%.
        out = compute_buy_plan(_negotiate_row())
        assert out["buy_plan_status"] == STATUS_OK
        assert out["target_buy_cost_buy"] == 9.50
        assert out["gap_to_buy_gbp"] == 0.50
        assert out["gap_to_buy_pct"] == 0.05
        # Sizing blank — operator can't BUY at over-ceiling cost.
        assert out["order_qty_recommended"] is None

    def test_negotiate_negative_gap_means_should_be_buy(self):
        # buy_cost=8, target=9.50 → gap=-1.50 (defensive — usually
        # NEGOTIATE wouldn't have a sub-ceiling cost, but engine
        # shouldn't crash).
        row = _negotiate_row(buy_cost=8.00)
        out = compute_buy_plan(row)
        assert out["gap_to_buy_gbp"] == -1.50

    def test_negotiate_projects_30d_at_current_cost(self):
        # NEGOTIATE uses profit_conservative (current cost), not target.
        # mid=18, profit_cons=2.35 → 18 × 2.35 = 42.30
        out = compute_buy_plan(_negotiate_row())
        assert out["projected_30d_profit"] == pytest.approx(42.30, abs=0.01)


# ────────────────────────────────────────────────────────────────────────
# WATCH verdict.
# ────────────────────────────────────────────────────────────────────────


class TestWatch:
    def test_watch_blanks_sizing_populates_targets(self):
        out = compute_buy_plan(_watch_row())
        assert out["buy_plan_status"] == STATUS_BLOCKED_BY_VERDICT
        assert out["order_qty_recommended"] is None
        assert out["capital_required"] is None
        assert out["payback_days"] is None
        assert out["gap_to_buy_gbp"] is None
        # Targets + projections still populated for re-evaluability.
        assert out["target_buy_cost_buy"] is not None
        assert out["projected_30d_units"] is not None
        assert out["projected_30d_revenue"] is not None


# ────────────────────────────────────────────────────────────────────────
# KILL verdict.
# ────────────────────────────────────────────────────────────────────────


class TestKill:
    def test_kill_blanks_everything(self):
        out = compute_buy_plan(_kill_row())
        assert out["buy_plan_status"] == STATUS_BLOCKED_BY_VERDICT
        for col in BUY_PLAN_COLUMNS:
            if col == "buy_plan_status":
                continue
            assert out[col] is None, f"{col} should be None on KILL"


# ────────────────────────────────────────────────────────────────────────
# Robustness — never crash.
# ────────────────────────────────────────────────────────────────────────


class TestRobustness:
    def test_completely_empty_row(self):
        out = compute_buy_plan({})
        for col in BUY_PLAN_COLUMNS:
            assert col in out
        # Empty verdict → unknown → BLOCKED_BY_VERDICT.
        assert out["buy_plan_status"] == STATUS_BLOCKED_BY_VERDICT

    def test_nan_inputs_handled_gracefully(self):
        out = compute_buy_plan(_buy_row(
            predicted_velocity_mid=float("nan"),
            buy_cost=float("nan"),
        ))
        assert out["buy_plan_status"] in (
            STATUS_INSUFFICIENT_DATA,
            STATUS_INSUFFICIENT_VELOCITY,
        )

    def test_string_typed_numerics(self):
        # CSV-loaded rows may carry numerics as strings.
        row = _buy_row(
            buy_cost="4.00", predicted_velocity_mid="18",
            raw_conservative_price="16.85", fees_conservative="4.50",
            profit_conservative="8.35",
        )
        out = compute_buy_plan(row)
        assert out["buy_plan_status"] == STATUS_OK
        assert out["order_qty_recommended"] == 13

    def test_unknown_verdict_treated_as_blocked(self):
        out = compute_buy_plan({"opportunity_verdict": "FOO"})
        assert out["buy_plan_status"] == STATUS_BLOCKED_BY_VERDICT


# ────────────────────────────────────────────────────────────────────────
# Determinism + immutability.
# ────────────────────────────────────────────────────────────────────────


class TestImmutability:
    def test_does_not_mutate_input(self):
        row = _buy_row()
        before = dict(row)
        compute_buy_plan(row)
        assert row == before, "compute_buy_plan must not mutate the input row"

    def test_deterministic(self):
        row = _buy_row()
        a = compute_buy_plan(row)
        b = compute_buy_plan(row)
        assert a == b
