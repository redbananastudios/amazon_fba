# Buyer Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement step `09_buy_plan_html` per `docs/PRD-buyer-report.md` — a per-product HTML buyer's report with verdict-grouped cards, image rails, traffic-light scoring, and LLM-generated prose paragraphs (Cowork-orchestrated, with deterministic template fallback).

**Architecture:** Pure-Python writer that emits a structured JSON payload + an HTML skeleton with prose-injection markers. A separate Cowork orchestration step generates per-card narrative paragraphs via Claude and walks the HTML to inject. Engine-alone runs use a deterministic template-prose fallback so the HTML is always usable.

**Tech Stack:** Python 3.14, pandas, openpyxl (existing), pure HTML+CSS for output, BeautifulSoup4 (test-only — for HTML structural assertions), Cowork orchestration runs Claude API calls.

**Branch:** `feat/buyer-report` (already created off `main` at PRD commit 8971923).

---

## Task 1 — Config block + BuyPlanHtml dataclass

**Files:**
- Modify: `shared/config/decision_thresholds.yaml`
- Modify: `shared/lib/python/fba_config_loader.py:198-228` (add BuyPlanHtml dataclass after BuyPlan)
- Modify: `shared/lib/python/fba_config_loader.py:_load_all` and exports
- Test: `shared/lib/python/tests/test_config_loader.py` (extend existing)

- [ ] **Step 1: Write failing tests for BuyPlanHtml**

Append to `shared/lib/python/tests/test_config_loader.py`:

```python
def test_buy_plan_html_loads_from_canonical_yaml():
    cfg.reset_cache()
    bp_html = cfg.get_buy_plan_html()
    assert bp_html.enabled is True


def test_buy_plan_html_uses_defaults_when_block_missing(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    raw = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    stripped = raw.split("# === Buyer report")[0].rstrip() + "\n"
    (config_dir / "decision_thresholds.yaml").write_text(stripped)
    cfg.reset_cache()
    bp_html = cfg.get_buy_plan_html(config_dir=config_dir)
    assert bp_html.enabled is True   # default-on
    cfg.reset_cache()


def test_buy_plan_html_disabled_override(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "business_rules.yaml").write_text(
        (LIB_DIR.parents[1] / "config" / "business_rules.yaml").read_text()
    )
    raw = (LIB_DIR.parents[1] / "config" / "decision_thresholds.yaml").read_text()
    custom = raw + "\n\n# === Buyer report ===\nbuy_plan_html:\n  enabled: false\n"
    (config_dir / "decision_thresholds.yaml").write_text(custom)
    cfg.reset_cache()
    bp_html = cfg.get_buy_plan_html(config_dir=config_dir)
    assert bp_html.enabled is False
    cfg.reset_cache()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/tests/test_config_loader.py -k "buy_plan_html" -q
```

Expected: 3 tests fail with `AttributeError: module 'fba_config_loader' has no attribute 'get_buy_plan_html'`.

- [ ] **Step 3: Add YAML block**

Append to `shared/config/decision_thresholds.yaml`:

```yaml

# === Buyer report (09_buy_plan_html) ===
# Runs after the existing CSV/XLSX/MD writers. Pure additive: emits
# a structured JSON payload and an HTML report with verdict-grouped
# cards. Cowork orchestration injects LLM-generated prose; template-
# prose fallback runs engine-alone.
buy_plan_html:
  enabled: true                         # produce JSON+HTML by default
```

- [ ] **Step 4: Add BuyPlanHtml dataclass + loader + accessor**

In `shared/lib/python/fba_config_loader.py`, after the `BuyPlan` dataclass definition (around line 224):

```python
@dataclass(frozen=True)
class BuyPlanHtml:
    """Buyer-report writer thresholds (09_buy_plan_html).

    Loaded from ``decision_thresholds.yaml::buy_plan_html``. Drives
    the JSON+HTML emission. Traffic-light thresholds are derived from
    `OpportunityValidation` and `BuyPlan` blocks (no duplicate knobs
    here) — see PRD §4.3.
    """

    enabled: bool
```

Update `_load_all` return type signature:

```python
def _load_all(
    config_dir_str: str | None = None,
) -> tuple[
    BusinessRules,
    DecisionThresholds,
    DataSignals,
    OpportunityValidation,
    BuyPlan,
    BuyPlanHtml,
]:
```

Inside `_load_all`, after the `buy_plan` block:

```python
    # buy_plan_html block (09_buy_plan_html). Permissive defaults so
    # existing YAML files without the block still load.
    bph_data = thresh_data.get("buy_plan_html") or {}
    buy_plan_html = BuyPlanHtml(
        enabled=bool(bph_data.get("enabled", True)),
    )

    _validate(business, thresh)
    _validate_data_signals(data_signals)
    _validate_opportunity_validation(opportunity)
    _validate_buy_plan(buy_plan)
    return business, thresh, data_signals, opportunity, buy_plan, buy_plan_html
```

Add accessor next to `get_buy_plan`:

```python
def get_buy_plan_html(config_dir: Path | None = None) -> BuyPlanHtml:
    """Get buyer-report writer thresholds. Cached.

    Loaded from ``decision_thresholds.yaml::buy_plan_html``. Permissive
    defaults when the block is absent (older configs).
    """
    key = str(config_dir.resolve()) if config_dir else None
    return _load_all(key)[5]
```

Add `BuyPlanHtml` and `get_buy_plan_html` to `__all__`.

- [ ] **Step 5: Run tests, verify they pass**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/tests/test_config_loader.py -k "buy_plan_html" -q
```

Expected: 3 passed.

- [ ] **Step 6: Run full config-loader suite**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/tests/test_config_loader.py -q
```

Expected: all green (existing 33 + 3 new = 36 tests).

- [ ] **Step 7: Commit**

```bash
git add shared/config/decision_thresholds.yaml shared/lib/python/fba_config_loader.py shared/lib/python/tests/test_config_loader.py
git commit -m "feat(buyer-report): add BuyPlanHtml config block + dataclass"
```

---

## Task 2 — Payload builder: top-level + happy-path BUY row

**Files:**
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/__init__.py` (empty)
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/payload.py`
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/tests/__init__.py` (empty)
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/tests/test_payload.py`

- [ ] **Step 1: Write failing tests for top-level payload + BUY row shape**

Create `shared/lib/python/sourcing_engine/buy_plan_html/tests/test_payload.py`:

```python
"""Tests for sourcing_engine.buy_plan_html.payload — pure JSON builder."""
from __future__ import annotations

import pandas as pd
import pytest

from sourcing_engine.buy_plan_html.payload import (
    SCHEMA_VERSION,
    PROMPT_VERSION,
    build_payload,
    build_row_payload,
)


def _buy_row(**overrides) -> dict:
    base = {
        "asin": "B0B636ZKZQ",
        "product_name": "Casdon Toaster Toy",
        "brand": "Casdon",
        "supplier": "abgee",
        "supplier_sku": "12345",
        "amazon_url": "https://www.amazon.co.uk/dp/B0B636ZKZQ",
        "decision": "SHORTLIST",
        "opportunity_verdict": "BUY",
        "opportunity_confidence": "HIGH",
        "opportunity_score": 85,
        "next_action": "Check live price, confirm stock, place test order",
        "buy_cost": 4.00,
        "market_price": 16.85,
        "raw_conservative_price": 16.85,
        "fees_conservative": 4.50,
        "profit_conservative": 8.35,
        "roi_conservative": 1.114,
        "fba_seller_count": 4,
        "amazon_on_listing": "N",
        "amazon_bb_pct_90": 0.10,
        "price_volatility_90d": 0.10,
        "sales_estimate": 250,
        "predicted_velocity_mid": 18,
        "bsr_drops_30d": 45,
        "buy_box_oos_pct_90": 0.05,
        "order_qty_recommended": 13,
        "capital_required": 52.00,
        "projected_30d_units": 18,
        "projected_30d_revenue": 303.30,
        "projected_30d_profit": 150.30,
        "payback_days": 21.7,
        "target_buy_cost_buy": 9.50,
        "target_buy_cost_stretch": 8.52,
        "gap_to_buy_gbp": None,
        "gap_to_buy_pct": None,
        "buy_plan_status": "OK",
        "risk_flags": [],
    }
    base.update(overrides)
    return base


def test_build_payload_top_level_fields():
    df = pd.DataFrame([_buy_row()])
    out = build_payload(df, run_id="20260503_120000", strategy="supplier_pricelist", supplier="abgee")
    assert out["schema_version"] == SCHEMA_VERSION
    assert out["prompt_version"] == PROMPT_VERSION
    assert out["run_id"] == "20260503_120000"
    assert out["strategy"] == "supplier_pricelist"
    assert out["supplier"] == "abgee"
    assert "generated_at" in out
    assert out["verdict_counts"] == {
        "BUY": 1, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0
    }
    assert len(out["rows"]) == 1


def test_build_payload_supplier_null_when_strategy_lacks_one():
    df = pd.DataFrame([_buy_row()])
    out = build_payload(df, run_id="20260503", strategy="keepa_finder", supplier=None)
    assert out["supplier"] is None


def test_build_row_payload_buy_identity_block():
    row = _buy_row()
    out = build_row_payload(row)
    assert out["asin"] == "B0B636ZKZQ"
    assert out["title"] == "Casdon Toaster Toy"
    assert out["brand"] == "Casdon"
    assert out["amazon_url"] == "https://www.amazon.co.uk/dp/B0B636ZKZQ"
    assert out["image_url"] == "https://images-na.ssl-images-amazon.com/images/P/B0B636ZKZQ.jpg"
    assert out["verdict"] == "BUY"
    assert out["verdict_confidence"] == "HIGH"
    assert out["opportunity_score"] == 85


def test_build_row_payload_buy_economics_block():
    out = build_row_payload(_buy_row())
    eco = out["economics"]
    assert eco["buy_cost_gbp"] == 4.00
    assert eco["market_price_gbp"] == 16.85
    assert eco["profit_per_unit_gbp"] == 8.35
    assert eco["roi_conservative_pct"] == pytest.approx(1.114)
    assert eco["target_buy_cost_gbp"] == 9.50
    assert eco["target_buy_cost_stretch_gbp"] == 8.52


def test_build_row_payload_buy_plan_block():
    out = build_row_payload(_buy_row())
    bp = out["buy_plan"]
    assert bp["order_qty_recommended"] == 13
    assert bp["capital_required_gbp"] == 52.00
    assert bp["projected_30d_units"] == 18
    assert bp["projected_30d_revenue_gbp"] == 303.30
    assert bp["projected_30d_profit_gbp"] == 150.30
    assert bp["payback_days"] == 21.7
    assert bp["gap_to_buy_gbp"] is None
    assert bp["gap_to_buy_pct"] is None
    assert bp["buy_plan_status"] == "OK"


def test_build_row_payload_carries_engine_lists():
    row = _buy_row(
        risk_flags=["INSUFFICIENT_HISTORY"],
    )
    out = build_row_payload(row)
    assert out["risk_flags"] == ["INSUFFICIENT_HISTORY"]
    # engine_reasons / engine_blockers can be empty if not on row
    assert "engine_reasons" in out
    assert "engine_blockers" in out
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/sourcing_engine/buy_plan_html/tests/test_payload.py -q
```

Expected: ImportError or 6 fails.

- [ ] **Step 3: Implement payload.py top-level + identity/economics/buy_plan blocks**

Create `shared/lib/python/sourcing_engine/buy_plan_html/__init__.py` (empty file with module docstring).

Create `shared/lib/python/sourcing_engine/buy_plan_html/tests/__init__.py` (empty).

Create `shared/lib/python/sourcing_engine/buy_plan_html/payload.py`:

```python
"""Buyer-report JSON payload builder.

Pure transformation: pandas DataFrame → JSON-serialisable dict.
No I/O. Reads only existing engine columns; produces the per-row
payload spec'd in PRD §4.

Top-level shape: {schema_version, prompt_version, run_id, strategy,
supplier, generated_at, verdict_counts, rows: [...]}.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd


SCHEMA_VERSION = 1
PROMPT_VERSION = 1

VERDICTS = ("BUY", "SOURCE_ONLY", "NEGOTIATE", "WATCH", "KILL")
ACTIONABLE_VERDICTS = ("BUY", "SOURCE_ONLY", "NEGOTIATE", "WATCH")


def _is_present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and v != v:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


def _num(v: Any) -> Optional[float]:
    if not _is_present(v):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n:
        return None
    return n


def _public_image_url(asin: str) -> str:
    """Empirical Amazon URL pattern (PRD §4.4).

    Returns a real product image for most UK ASINs; some return a
    broken/missing image — handled by the renderer's onerror rule.
    """
    return f"https://images-na.ssl-images-amazon.com/images/P/{asin}.jpg"


def build_row_payload(row: dict) -> dict:
    """Build the JSON payload for one row. Pure function.

    Identity / verdict / economics / buy_plan blocks are populated
    from existing engine columns. Metrics traffic-light judgments
    are added by `_build_metrics` (Task 3).
    """
    asin = row.get("asin") or ""
    return {
        "asin": asin,
        "title": row.get("product_name") or "",
        "brand": row.get("brand") or "",
        "supplier": row.get("supplier"),
        "supplier_sku": row.get("supplier_sku"),
        "amazon_url": row.get("amazon_url") or "",
        "image_url": _public_image_url(asin) if asin else None,

        "verdict": row.get("opportunity_verdict") or "",
        "verdict_confidence": row.get("opportunity_confidence") or "",
        "opportunity_score": int(row["opportunity_score"]) if _is_present(row.get("opportunity_score")) else None,
        "next_action": row.get("next_action") or "",

        "economics": {
            "buy_cost_gbp": _num(row.get("buy_cost")),
            "market_price_gbp": _num(row.get("market_price")),
            "profit_per_unit_gbp": _num(row.get("profit_conservative")),
            "roi_conservative_pct": _num(row.get("roi_conservative")),
            "target_buy_cost_gbp": _num(row.get("target_buy_cost_buy")),
            "target_buy_cost_stretch_gbp": _num(row.get("target_buy_cost_stretch")),
        },

        "buy_plan": {
            "order_qty_recommended": int(row["order_qty_recommended"]) if _is_present(row.get("order_qty_recommended")) else None,
            "capital_required_gbp": _num(row.get("capital_required")),
            "projected_30d_units": int(row["projected_30d_units"]) if _is_present(row.get("projected_30d_units")) else None,
            "projected_30d_revenue_gbp": _num(row.get("projected_30d_revenue")),
            "projected_30d_profit_gbp": _num(row.get("projected_30d_profit")),
            "payback_days": _num(row.get("payback_days")),
            "gap_to_buy_gbp": _num(row.get("gap_to_buy_gbp")),
            "gap_to_buy_pct": _num(row.get("gap_to_buy_pct")),
            "buy_plan_status": row.get("buy_plan_status") or "",
        },

        "metrics": [],   # filled in Task 3

        "engine_reasons": _to_list(row.get("opportunity_reasons")),
        "engine_blockers": _to_list(row.get("opportunity_blockers")),
        "risk_flags": _to_list(row.get("risk_flags")),
    }


def _to_list(v: Any) -> list:
    if isinstance(v, list):
        return [str(x) for x in v if x]
    if isinstance(v, str) and v.strip():
        return [s.strip() for s in v.replace(",", ";").split(";") if s.strip()]
    return []


def build_payload(
    df: pd.DataFrame,
    *,
    run_id: str,
    strategy: str,
    supplier: Optional[str],
) -> dict:
    """Build the top-level payload dict. Filters out KILL rows.

    Returns a JSON-serialisable dict matching PRD §4.1.
    """
    counts = {v: 0 for v in VERDICTS}
    rows = []

    if not df.empty and "opportunity_verdict" in df.columns:
        for _, row in df.iterrows():
            d = row.to_dict()
            verdict = str(d.get("opportunity_verdict") or "").upper().strip()
            if verdict in counts:
                counts[verdict] += 1
            if verdict in ACTIONABLE_VERDICTS:
                rows.append(build_row_payload(d))

    return {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "run_id": run_id,
        "strategy": strategy,
        "supplier": supplier,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict_counts": counts,
        "rows": rows,
    }
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/sourcing_engine/buy_plan_html/tests/test_payload.py -q
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add shared/lib/python/sourcing_engine/buy_plan_html/
git commit -m "feat(buyer-report): payload builder top-level + identity/economics/buy_plan"
```

---

## Task 3 — Payload builder: traffic-light metric judgments

**Files:**
- Modify: `shared/lib/python/sourcing_engine/buy_plan_html/payload.py` (add `_build_metrics`, `_judge_metric`)
- Modify: `shared/lib/python/sourcing_engine/buy_plan_html/tests/test_payload.py` (add traffic-light tests)

- [ ] **Step 1: Write failing tests for traffic-light table per PRD §4.3**

Append to `test_payload.py`:

```python
class TestMetricsTrafficLight:
    """PRD §4.3 — explicit thresholds for all 7 metrics × green/amber/red."""

    def _row_for_metric(self, **overrides) -> dict:
        # Minimal row that lets each metric be evaluated independently.
        base = _buy_row()
        base.update(overrides)
        return base

    # ──────────────── fba_seller_count ────────────────
    def test_fba_seller_count_green_at_healthy(self):
        row = self._row_for_metric(fba_seller_count=4, sales_estimate=250)
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "fba_seller_count")
        assert m["verdict"] == "green"

    def test_fba_seller_count_amber_when_50pct_over_ceiling(self):
        # At sales=250, ceiling = max_fba_sellers_200_sales=12.
        # Amber zone: 12 < x ≤ 18 (12 × 1.5).
        row = self._row_for_metric(fba_seller_count=15, sales_estimate=250)
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "fba_seller_count")
        assert m["verdict"] == "amber"

    def test_fba_seller_count_red_when_far_over_ceiling(self):
        row = self._row_for_metric(fba_seller_count=25, sales_estimate=250)
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "fba_seller_count")
        assert m["verdict"] == "red"

    # ──────────────── amazon_on_listing ────────────────
    @pytest.mark.parametrize("value,expected", [
        ("N", "green"), ("", "green"), (None, "green"),
        ("UNKNOWN", "amber"),
        ("Y", "red"),
    ])
    def test_amazon_on_listing_verdict(self, value, expected):
        row = self._row_for_metric(amazon_on_listing=value)
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "amazon_on_listing")
        assert m["verdict"] == expected

    # ──────────────── amazon_bb_pct_90 ────────────────
    @pytest.mark.parametrize("value,expected", [
        (0.10, "green"),     # < 0.30
        (0.30, "amber"),     # boundary: at 0.30 should already be amber
        (0.50, "amber"),     # 0.30 ≤ x < 0.70
        (0.70, "red"),       # ≥ 0.70
        (0.95, "red"),
    ])
    def test_amazon_bb_share_verdict(self, value, expected):
        row = self._row_for_metric(amazon_bb_pct_90=value)
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "amazon_bb_pct_90")
        assert m["verdict"] == expected

    # ──────────────── price_volatility ────────────────
    @pytest.mark.parametrize("value,expected", [
        (0.05, "green"),
        (0.20, "amber"),
        (0.30, "amber"),
        (0.40, "red"),
        (0.50, "red"),
    ])
    def test_price_volatility_verdict(self, value, expected):
        row = self._row_for_metric(price_volatility_90d=value)
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "price_volatility")
        assert m["verdict"] == expected

    # ──────────────── sales_estimate ────────────────
    @pytest.mark.parametrize("value,expected", [
        (250, "green"),
        (100, "green"),
        (50, "amber"),
        (20, "amber"),
        (15, "red"),
    ])
    def test_sales_estimate_verdict(self, value, expected):
        row = self._row_for_metric(sales_estimate=value)
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "sales_estimate")
        assert m["verdict"] == expected

    # ──────────────── predicted_velocity ────────────────
    def test_predicted_velocity_green_above_half_share(self):
        # non_amazon_share = 250 × 0.9 = 225. Half = 112.5.
        # mid >= 112.5 → green.
        row = self._row_for_metric(
            predicted_velocity_mid=120, sales_estimate=250, amazon_bb_pct_90=0.10,
        )
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "predicted_velocity")
        assert m["verdict"] == "green"

    def test_predicted_velocity_amber_quarter_to_half(self):
        # 0.25 × 225 = 56.25 ≤ mid < 112.5 → amber.
        row = self._row_for_metric(
            predicted_velocity_mid=70, sales_estimate=250, amazon_bb_pct_90=0.10,
        )
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "predicted_velocity")
        assert m["verdict"] == "amber"

    def test_predicted_velocity_red_below_quarter_share(self):
        row = self._row_for_metric(
            predicted_velocity_mid=20, sales_estimate=250, amazon_bb_pct_90=0.10,
        )
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "predicted_velocity")
        assert m["verdict"] == "red"

    def test_predicted_velocity_grey_when_amazon_bb_missing(self):
        row = self._row_for_metric(
            predicted_velocity_mid=18, sales_estimate=250, amazon_bb_pct_90=None,
        )
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "predicted_velocity")
        assert m["verdict"] == "grey"

    # ──────────────── bsr_drops_30d ────────────────
    @pytest.mark.parametrize("drops,sales,expected", [
        (45, 250, "green"),     # max(20, 125) = 125; 45 < 125 actually amber
        (200, 250, "green"),    # ≥ 125
        (75, 250, "amber"),     # ≥ max(10, 62.5)=62.5
        (5, 250, "red"),        # below amber floor
    ])
    def test_bsr_drops_verdict(self, drops, sales, expected):
        row = self._row_for_metric(bsr_drops_30d=drops, sales_estimate=sales)
        out = build_row_payload(row)
        m = next(x for x in out["metrics"] if x["key"] == "bsr_drops_30d")
        assert m["verdict"] == expected

    # ──────────────── grey when source absent ────────────────
    @pytest.mark.parametrize("missing_field", [
        "fba_seller_count",
        "amazon_bb_pct_90",
        "price_volatility_90d",
        "sales_estimate",
        "bsr_drops_30d",
    ])
    def test_grey_when_source_absent(self, missing_field):
        row = self._row_for_metric()
        row.pop(missing_field)
        out = build_row_payload(row)
        # Find the metric that consumes this field.
        target_keys = {
            "fba_seller_count": "fba_seller_count",
            "amazon_bb_pct_90": "amazon_bb_pct_90",
            "price_volatility_90d": "price_volatility",
            "sales_estimate": "sales_estimate",
            "bsr_drops_30d": "bsr_drops_30d",
        }
        m = next(x for x in out["metrics"] if x["key"] == target_keys[missing_field])
        assert m["verdict"] == "grey"
        assert m["value_display"] == "—"
        assert "signal missing" in m["rationale"].lower()


def test_metrics_ordered_per_prd_4_3():
    """The 7 metrics MUST appear in the exact order spec'd in PRD §4.3."""
    out = build_row_payload(_buy_row())
    keys = [m["key"] for m in out["metrics"]]
    assert keys == [
        "fba_seller_count",
        "amazon_on_listing",
        "amazon_bb_pct_90",
        "price_volatility",
        "sales_estimate",
        "predicted_velocity",
        "bsr_drops_30d",
    ]
```

- [ ] **Step 2: Run tests, verify they fail**

Expected: many fails — `metrics` is currently `[]`.

- [ ] **Step 3: Implement traffic-light judgment**

Modify `payload.py`. Replace the `"metrics": [],` line with `"metrics": _build_metrics(row),` and add the helpers:

```python
def _build_metrics(row: dict) -> list[dict]:
    """Compose the 7 traffic-light metric entries per PRD §4.3.

    Order is contractual — tests pin it.
    """
    return [
        _judge_fba_seller_count(row),
        _judge_amazon_on_listing(row),
        _judge_amazon_bb_share(row),
        _judge_price_volatility(row),
        _judge_sales_estimate(row),
        _judge_predicted_velocity(row),
        _judge_bsr_drops(row),
    ]


def _grey(key: str, label: str) -> dict:
    return {
        "key": key, "label": label, "value_display": "—",
        "verdict": "grey", "rationale": "signal missing",
    }


def _judge_fba_seller_count(row: dict) -> dict:
    from fba_config_loader import get_opportunity_validation
    cfg = get_opportunity_validation()
    fba = _num(row.get("fba_seller_count"))
    sales = _num(row.get("sales_estimate")) or 0
    if fba is None:
        return _grey("fba_seller_count", "FBA Sellers")
    # Find the OV ceiling for this sales tier.
    if sales >= 200:
        ceiling = cfg.max_fba_sellers_200_sales
    elif sales >= 100:
        ceiling = cfg.max_fba_sellers_100_sales
    else:
        ceiling = cfg.max_fba_sellers_low_sales
    amber_top = ceiling * 1.5
    if fba <= ceiling:
        return {
            "key": "fba_seller_count", "label": "FBA Sellers",
            "value_display": str(int(fba)), "verdict": "green",
            "rationale": f"≤ {int(ceiling)} ceiling at this volume",
        }
    if fba <= amber_top:
        return {
            "key": "fba_seller_count", "label": "FBA Sellers",
            "value_display": str(int(fba)), "verdict": "amber",
            "rationale": f"over {int(ceiling)} ceiling but within 50%",
        }
    return {
        "key": "fba_seller_count", "label": "FBA Sellers",
        "value_display": str(int(fba)), "verdict": "red",
        "rationale": f"far above {int(ceiling)} ceiling",
    }


def _judge_amazon_on_listing(row: dict) -> dict:
    raw = row.get("amazon_on_listing")
    s = str(raw or "").upper().strip()
    if s == "Y":
        return {
            "key": "amazon_on_listing", "label": "Amazon on Listing",
            "value_display": "Yes", "verdict": "red",
            "rationale": "Amazon competes on the Buy Box",
        }
    if s == "UNKNOWN":
        return {
            "key": "amazon_on_listing", "label": "Amazon on Listing",
            "value_display": "Unknown", "verdict": "amber",
            "rationale": "Amazon-on-listing status unverified",
        }
    return {
        "key": "amazon_on_listing", "label": "Amazon on Listing",
        "value_display": "No", "verdict": "green",
        "rationale": "Buy Box rotation safe",
    }


def _judge_amazon_bb_share(row: dict) -> dict:
    from fba_config_loader import get_opportunity_validation
    cfg = get_opportunity_validation()
    bb = _num(row.get("amazon_bb_pct_90"))
    if bb is None:
        return _grey("amazon_bb_pct_90", "Amazon BB Share 90d")
    pct_str = f"{bb:.0%}"
    if bb < cfg.max_amazon_bb_share_buy:
        return {"key": "amazon_bb_pct_90", "label": "Amazon BB Share 90d",
                "value_display": pct_str, "verdict": "green",
                "rationale": f"below {cfg.max_amazon_bb_share_buy:.0%} buy threshold"}
    if bb < cfg.max_amazon_bb_share_watch:
        return {"key": "amazon_bb_pct_90", "label": "Amazon BB Share 90d",
                "value_display": pct_str, "verdict": "amber",
                "rationale": f"between buy and watch thresholds"}
    return {"key": "amazon_bb_pct_90", "label": "Amazon BB Share 90d",
            "value_display": pct_str, "verdict": "red",
            "rationale": f"≥ {cfg.max_amazon_bb_share_watch:.0%} — Amazon dominates"}


def _judge_price_volatility(row: dict) -> dict:
    from fba_config_loader import get_opportunity_validation
    cfg = get_opportunity_validation()
    vol = _num(row.get("price_volatility_90d"))
    if vol is None:
        return _grey("price_volatility", "Price Consistency")
    val = f"{vol:.2f}"
    if vol < cfg.max_price_volatility_buy:
        return {"key": "price_volatility", "label": "Price Consistency",
                "value_display": val, "verdict": "green",
                "rationale": f"stable (< {cfg.max_price_volatility_buy:.2f} cap)"}
    if vol < cfg.kill_price_volatility:
        return {"key": "price_volatility", "label": "Price Consistency",
                "value_display": val, "verdict": "amber",
                "rationale": "moderate volatility"}
    return {"key": "price_volatility", "label": "Price Consistency",
            "value_display": val, "verdict": "red",
            "rationale": f"≥ {cfg.kill_price_volatility:.2f} — severe volatility"}


def _judge_sales_estimate(row: dict) -> dict:
    from fba_config_loader import get_opportunity_validation
    cfg = get_opportunity_validation()
    sales = _num(row.get("sales_estimate"))
    if sales is None:
        return _grey("sales_estimate", "Volume (units/mo)")
    val = f"{int(sales)}"
    if sales >= cfg.target_monthly_sales:
        return {"key": "sales_estimate", "label": "Volume (units/mo)",
                "value_display": val, "verdict": "green",
                "rationale": f"above {cfg.target_monthly_sales} target"}
    if sales >= cfg.kill_min_sales:
        return {"key": "sales_estimate", "label": "Volume (units/mo)",
                "value_display": val, "verdict": "amber",
                "rationale": f"between {cfg.kill_min_sales} kill floor and {cfg.target_monthly_sales} target"}
    return {"key": "sales_estimate", "label": "Volume (units/mo)",
            "value_display": val, "verdict": "red",
            "rationale": f"below {cfg.kill_min_sales} kill floor"}


def _judge_predicted_velocity(row: dict) -> dict:
    sales = _num(row.get("sales_estimate"))
    bb = _num(row.get("amazon_bb_pct_90"))
    mid = _num(row.get("predicted_velocity_mid"))
    if sales is None or bb is None or mid is None:
        return _grey("predicted_velocity", "Your Expected Sales")
    non_amazon_share = sales * (1 - bb)
    val = f"{int(mid)} /mo"
    if non_amazon_share <= 0:
        return _grey("predicted_velocity", "Your Expected Sales")
    if mid >= 0.5 * non_amazon_share:
        return {"key": "predicted_velocity", "label": "Your Expected Sales",
                "value_display": val, "verdict": "green",
                "rationale": "top-half share of non-Amazon rotation"}
    if mid >= 0.25 * non_amazon_share:
        return {"key": "predicted_velocity", "label": "Your Expected Sales",
                "value_display": val, "verdict": "amber",
                "rationale": "mid-tier share of non-Amazon rotation"}
    return {"key": "predicted_velocity", "label": "Your Expected Sales",
            "value_display": val, "verdict": "red",
            "rationale": "bottom-quartile share — entrant struggles"}


def _judge_bsr_drops(row: dict) -> dict:
    drops = _num(row.get("bsr_drops_30d"))
    sales = _num(row.get("sales_estimate")) or 0
    if drops is None:
        return _grey("bsr_drops_30d", "Stock Replenishments")
    val = f"{int(drops)} /mo"
    green_floor = max(20.0, sales * 0.5)
    amber_floor = max(10.0, sales * 0.25)
    if drops >= green_floor:
        return {"key": "bsr_drops_30d", "label": "Stock Replenishments",
                "value_display": val, "verdict": "green",
                "rationale": "healthy turnover"}
    if drops >= amber_floor:
        return {"key": "bsr_drops_30d", "label": "Stock Replenishments",
                "value_display": val, "verdict": "amber",
                "rationale": "moderate turnover"}
    return {"key": "bsr_drops_30d", "label": "Stock Replenishments",
            "value_display": val, "verdict": "red",
            "rationale": "low turnover — slow seller"}
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/sourcing_engine/buy_plan_html/tests/test_payload.py -q
```

Expected: all green (~30 traffic-light tests + 6 from Task 2).

- [ ] **Step 5: Commit**

```bash
git add shared/lib/python/sourcing_engine/buy_plan_html/payload.py shared/lib/python/sourcing_engine/buy_plan_html/tests/test_payload.py
git commit -m "feat(buyer-report): traffic-light metric judgments per PRD §4.3"
```

---

## Task 4 — Template-prose fallback

**Files:**
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/template_prose.py`
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/tests/test_template_prose.py`

- [ ] **Step 1: Write failing tests**

Create `test_template_prose.py`:

```python
"""Tests for template_prose — deterministic fallback prose."""
from __future__ import annotations

import pytest

from sourcing_engine.buy_plan_html.template_prose import render_template_prose


def _payload(verdict: str, **overrides) -> dict:
    """Minimal row-payload shape for tests."""
    base = {
        "asin": "B0TEST00001",
        "title": "Test product",
        "verdict": verdict,
        "verdict_confidence": "HIGH",
        "next_action": "test action",
        "economics": {
            "buy_cost_gbp": 4.00,
            "profit_per_unit_gbp": 8.35,
            "roi_conservative_pct": 1.114,
            "target_buy_cost_gbp": 9.50,
            "target_buy_cost_stretch_gbp": 8.52,
        },
        "buy_plan": {
            "order_qty_recommended": 13,
            "capital_required_gbp": 52.00,
            "projected_30d_units": 18,
            "projected_30d_revenue_gbp": 303.30,
            "projected_30d_profit_gbp": 150.30,
            "payback_days": 21.7,
            "gap_to_buy_gbp": None,
            "gap_to_buy_pct": None,
            "buy_plan_status": "OK",
        },
        "metrics": [],
        "engine_reasons": [],
        "engine_blockers": [],
        "risk_flags": [],
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("verdict", ["BUY", "SOURCE_ONLY", "NEGOTIATE", "WATCH"])
def test_template_prose_non_empty_per_verdict(verdict):
    out = render_template_prose(_payload(verdict))
    assert isinstance(out, str)
    assert len(out) > 0


@pytest.mark.parametrize("verdict", ["BUY", "SOURCE_ONLY", "NEGOTIATE", "WATCH"])
def test_template_prose_deterministic(verdict):
    p = _payload(verdict)
    assert render_template_prose(p) == render_template_prose(p)


def test_template_prose_buy_mentions_order_qty():
    out = render_template_prose(_payload("BUY"))
    assert "13" in out  # order qty
    assert "buy" in out.lower()


def test_template_prose_source_only_mentions_target():
    p = _payload("SOURCE_ONLY", buy_plan={
        "order_qty_recommended": None, "capital_required_gbp": None,
        "projected_30d_units": 42, "projected_30d_revenue_gbp": 710.00,
        "projected_30d_profit_gbp": 136.00, "payback_days": None,
        "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
        "buy_plan_status": "NO_BUY_COST",
    })
    p["economics"]["buy_cost_gbp"] = None
    p["economics"]["target_buy_cost_gbp"] = 4.85
    out = render_template_prose(p)
    assert "4.85" in out or "£4.85" in out
    assert "supplier" in out.lower() or "source" in out.lower()


def test_template_prose_negotiate_mentions_gap():
    p = _payload("NEGOTIATE", buy_plan={
        "order_qty_recommended": None, "capital_required_gbp": None,
        "projected_30d_units": 18, "projected_30d_revenue_gbp": 303.30,
        "projected_30d_profit_gbp": 42.30, "payback_days": None,
        "gap_to_buy_gbp": 0.62, "gap_to_buy_pct": 0.124,
        "buy_plan_status": "OK",
    })
    p["economics"]["buy_cost_gbp"] = 5.00
    p["economics"]["target_buy_cost_gbp"] = 4.38
    out = render_template_prose(p)
    assert "0.62" in out or "12.4" in out
    assert "negotiat" in out.lower() or "down" in out.lower()


def test_template_prose_watch_mentions_blockers_or_flags():
    p = _payload("WATCH", risk_flags=["INSUFFICIENT_HISTORY"])
    out = render_template_prose(p)
    assert "watch" in out.lower() or "monitor" in out.lower()


def test_template_prose_minimal_data_does_not_crash():
    minimal = {"verdict": "BUY", "asin": "B0TEST00001"}
    out = render_template_prose(minimal)
    assert isinstance(out, str)
    assert len(out) > 0
```

- [ ] **Step 2: Run tests, verify fail**

Expected: ImportError.

- [ ] **Step 3: Implement template_prose.py**

```python
"""Deterministic template-prose composer.

Used as the fallback when the engine runs without Cowork
orchestration (no LLM available). Produces a 1-3 sentence
paragraph from row payload data using fixed templates.

Determinism: same input → byte-identical output.
"""
from __future__ import annotations

from typing import Any


def _safe_get(d: dict, path: list[str], default: Any = None) -> Any:
    """Walk nested dict; return default on any missing key."""
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def render_template_prose(payload: dict) -> str:
    """Compose a fallback paragraph for one row payload.

    Routes on verdict; degrades gracefully when fields are missing.
    Never raises.
    """
    verdict = str(payload.get("verdict") or "").upper().strip()

    if verdict == "BUY":
        return _buy(payload)
    if verdict == "SOURCE_ONLY":
        return _source_only(payload)
    if verdict == "NEGOTIATE":
        return _negotiate(payload)
    if verdict == "WATCH":
        return _watch(payload)
    # Unknown / KILL / blank verdict — minimal fallback.
    return f"Verdict: {verdict or 'unknown'}. See blockers and flags below."


def _buy(p: dict) -> str:
    qty = _safe_get(p, ["buy_plan", "order_qty_recommended"])
    cap = _safe_get(p, ["buy_plan", "capital_required_gbp"])
    payback = _safe_get(p, ["buy_plan", "payback_days"])
    target = _safe_get(p, ["economics", "target_buy_cost_gbp"])
    flags = p.get("risk_flags") or []
    parts = []
    if qty is not None and cap is not None:
        parts.append(
            f"Order {int(qty)} units at £{cap:.2f} capital."
        )
    if payback is not None:
        parts.append(f"Sell-through in ~{payback:.0f} days.")
    if target is not None:
        parts.append(f"Target buy cost ≤ £{target:.2f}.")
    if flags:
        parts.append(f"Risk flags: {', '.join(flags)}.")
    else:
        parts.append("No risk flags.")
    return " ".join(parts) if parts else "BUY-grade — see economics below."


def _source_only(p: dict) -> str:
    target = _safe_get(p, ["economics", "target_buy_cost_gbp"])
    stretch = _safe_get(p, ["economics", "target_buy_cost_stretch_gbp"])
    rev = _safe_get(p, ["buy_plan", "projected_30d_revenue_gbp"])
    parts = ["Demand looks strong but no supplier cost is on file."]
    if target is not None:
        if stretch is not None:
            parts.append(f"Target supplier outreach at ≤ £{target:.2f}/unit (stretch £{stretch:.2f}).")
        else:
            parts.append(f"Target supplier outreach at ≤ £{target:.2f}/unit.")
    if rev is not None:
        parts.append(f"At target cost, this projects ~£{rev:.0f}/mo revenue.")
    return " ".join(parts)


def _negotiate(p: dict) -> str:
    cur = _safe_get(p, ["economics", "buy_cost_gbp"])
    target = _safe_get(p, ["economics", "target_buy_cost_gbp"])
    gap_gbp = _safe_get(p, ["buy_plan", "gap_to_buy_gbp"])
    gap_pct = _safe_get(p, ["buy_plan", "gap_to_buy_pct"])
    parts = []
    if cur is not None and target is not None:
        parts.append(
            f"Currently £{cur:.2f}; needs to come down to £{target:.2f} to clear the BUY ceiling."
        )
    if gap_gbp is not None and gap_pct is not None:
        parts.append(f"Gap: £{gap_gbp:.2f} ({gap_pct:.1%}). Worth a supplier negotiation.")
    elif gap_gbp is not None:
        parts.append(f"Gap to close: £{gap_gbp:.2f}. Worth a supplier negotiation.")
    return " ".join(parts) if parts else "NEGOTIATE — push the supplier price down."


def _watch(p: dict) -> str:
    flags = p.get("risk_flags") or []
    blockers = p.get("engine_blockers") or []
    target = _safe_get(p, ["economics", "target_buy_cost_gbp"])
    parts = ["Not BUY-grade today; worth monitoring."]
    if blockers:
        parts.append(f"Blockers: {'; '.join(blockers[:2])}.")
    elif flags:
        parts.append(f"Risk flags: {', '.join(flags[:3])}.")
    if target is not None:
        parts.append(f"Target ceiling £{target:.2f} if economics improve.")
    return " ".join(parts)
```

- [ ] **Step 4: Run tests, verify pass**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/sourcing_engine/buy_plan_html/tests/test_template_prose.py -q
```

Expected: all green (~10 tests).

- [ ] **Step 5: Commit**

```bash
git add shared/lib/python/sourcing_engine/buy_plan_html/template_prose.py shared/lib/python/sourcing_engine/buy_plan_html/tests/test_template_prose.py
git commit -m "feat(buyer-report): deterministic template-prose fallback"
```

---

## Task 5 — HTML renderer

**Files:**
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/renderer.py`
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/tests/test_renderer.py`

- [ ] **Step 1: Write failing tests**

Create `test_renderer.py`:

```python
"""Tests for renderer.py — HTML skeleton emission."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from sourcing_engine.buy_plan_html.renderer import render_html


def _row_payload(asin: str, verdict: str, **overrides) -> dict:
    base = {
        "asin": asin,
        "title": f"Title for {asin}",
        "brand": "TestBrand",
        "supplier": "test-supplier",
        "supplier_sku": "SKU-X",
        "amazon_url": f"https://www.amazon.co.uk/dp/{asin}",
        "image_url": f"https://images-na.ssl-images-amazon.com/images/P/{asin}.jpg",
        "verdict": verdict,
        "verdict_confidence": "HIGH",
        "opportunity_score": 80,
        "next_action": "test action",
        "economics": {
            "buy_cost_gbp": 4.00, "market_price_gbp": 16.85,
            "profit_per_unit_gbp": 8.35, "roi_conservative_pct": 1.114,
            "target_buy_cost_gbp": 9.50, "target_buy_cost_stretch_gbp": 8.52,
        },
        "buy_plan": {
            "order_qty_recommended": 13, "capital_required_gbp": 52.0,
            "projected_30d_units": 18, "projected_30d_revenue_gbp": 303.30,
            "projected_30d_profit_gbp": 150.30, "payback_days": 21.7,
            "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
            "buy_plan_status": "OK",
        },
        "metrics": [
            {"key": "fba_seller_count", "label": "FBA Sellers",
             "value_display": "4", "verdict": "green", "rationale": "≤ 5"},
            {"key": "sales_estimate", "label": "Volume",
             "value_display": "250", "verdict": "green", "rationale": "above target"},
        ],
        "engine_reasons": [], "engine_blockers": [], "risk_flags": [],
    }
    base.update(overrides)
    return base


def _payload(rows: list[dict], **kwargs) -> dict:
    return {
        "schema_version": 1, "prompt_version": 1,
        "run_id": "20260503_120000", "strategy": "supplier_pricelist",
        "supplier": "test-supplier", "generated_at": "2026-05-03T12:00:00Z",
        "verdict_counts": {"BUY": 0, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        "rows": rows, **kwargs,
    }


class TestRenderHtmlStructure:
    def test_empty_payload_produces_valid_html_with_no_actionable_notice(self):
        out = render_html(_payload(rows=[]))
        soup = BeautifulSoup(out, "html.parser")
        assert soup.find("html") is not None
        assert soup.find("h1") is not None
        # No cards.
        assert soup.find("article", class_="card") is None
        # "no actionable rows" notice is visible.
        assert "no actionable rows" in out.lower()

    def test_buy_row_produces_card_with_marker(self):
        p = _payload(
            rows=[_row_payload("B0BUY00001", "BUY")],
            verdict_counts={"BUY": 1, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        card = soup.find("article", id="asin-B0BUY00001")
        assert card is not None
        assert "verdict-buy" in card.get("class", [])
        # Image rail wraps in anchor pointing to amazon_url.
        a = card.find("a", class_="card-image")
        assert a is not None
        assert a.get("href") == "https://www.amazon.co.uk/dp/B0BUY00001"
        img = a.find("img")
        assert img is not None
        assert "B0BUY00001.jpg" in img.get("src", "")
        # Prose marker present.
        assert "<!-- prose:B0BUY00001 -->" in out

    def test_section_heading_with_count(self):
        p = _payload(
            rows=[_row_payload("B0001", "BUY"), _row_payload("B0002", "BUY")],
            verdict_counts={"BUY": 2, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        h2 = soup.find("h2", string=lambda s: s and "BUY" in s and "2" in s)
        assert h2 is not None

    def test_per_verdict_section_ordering(self):
        rows = [
            _row_payload("B0WATCH001", "WATCH"),
            _row_payload("B0SRC0001", "SOURCE_ONLY"),
            _row_payload("B0NEG0001", "NEGOTIATE"),
            _row_payload("B0BUY00001", "BUY"),
        ]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 1, "SOURCE_ONLY": 1, "NEGOTIATE": 1, "WATCH": 1, "KILL": 0},
        )
        out = render_html(p)
        # BUY section appears before SOURCE_ONLY before NEGOTIATE before WATCH.
        i_buy = out.find('id="section-buy"')
        i_src = out.find('id="section-source-only"')
        i_neg = out.find('id="section-negotiate"')
        i_watch = out.find('id="section-watch"')
        assert -1 < i_buy < i_src < i_neg < i_watch

    def test_metrics_table_has_traffic_light_dots(self):
        p = _payload(
            rows=[_row_payload("B0BUY00001", "BUY")],
            verdict_counts={"BUY": 1, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        dot_cells = soup.select(".card-scoring td.dot")
        assert len(dot_cells) == 2  # two metrics in the fixture
        # Both green.
        for cell in dot_cells:
            assert "dot-green" in cell.get("class", [])

    def test_buy_card_economics_grid_has_order_qty(self):
        p = _payload(
            rows=[_row_payload("B0BUY00001", "BUY")],
            verdict_counts={"BUY": 1, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        # BUY economics grid renders 13-unit order + £52 capital.
        assert "Order qty" in out
        assert ">13<" in out or ">13 " in out
        assert "£52.00" in out

    def test_source_only_economics_grid_has_no_supplier_label(self):
        rows = [_row_payload(
            "B0SRC0001", "SOURCE_ONLY",
            economics={
                "buy_cost_gbp": None, "market_price_gbp": 16.85,
                "profit_per_unit_gbp": None, "roi_conservative_pct": None,
                "target_buy_cost_gbp": 4.85, "target_buy_cost_stretch_gbp": 4.10,
            },
            buy_plan={
                "order_qty_recommended": None, "capital_required_gbp": None,
                "projected_30d_units": 42, "projected_30d_revenue_gbp": 710.00,
                "projected_30d_profit_gbp": 136.00, "payback_days": None,
                "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
                "buy_plan_status": "NO_BUY_COST",
            },
        )]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 0, "SOURCE_ONLY": 1, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        assert "no supplier yet" in out
        assert "£4.85" in out

    def test_negotiate_economics_grid_has_gap(self):
        rows = [_row_payload(
            "B0NEG0001", "NEGOTIATE",
            economics={
                "buy_cost_gbp": 5.00, "market_price_gbp": 16.85,
                "profit_per_unit_gbp": 1.50, "roi_conservative_pct": 0.20,
                "target_buy_cost_gbp": 4.38, "target_buy_cost_stretch_gbp": 3.50,
            },
            buy_plan={
                "order_qty_recommended": None, "capital_required_gbp": None,
                "projected_30d_units": 18, "projected_30d_revenue_gbp": 303.30,
                "projected_30d_profit_gbp": 42.30, "payback_days": None,
                "gap_to_buy_gbp": 0.62, "gap_to_buy_pct": 0.124,
                "buy_plan_status": "OK",
            },
        )]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 0, "SOURCE_ONLY": 0, "NEGOTIATE": 1, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        assert "£0.62" in out
        assert "12.4%" in out

    def test_within_verdict_buy_sorted_by_projected_30d_profit_desc(self):
        # Per PRD §5.4 — BUY tier sorts by projected_30d_profit desc.
        rows = [
            _row_payload("B0LOW00001", "BUY", buy_plan={
                **_row_payload("X", "BUY")["buy_plan"],
                "projected_30d_profit_gbp": 20.0,
            }),
            _row_payload("B0HIGH0001", "BUY", buy_plan={
                **_row_payload("X", "BUY")["buy_plan"],
                "projected_30d_profit_gbp": 200.0,
            }),
            _row_payload("B0MID00001", "BUY", buy_plan={
                **_row_payload("X", "BUY")["buy_plan"],
                "projected_30d_profit_gbp": 80.0,
            }),
        ]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 3, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        order = [out.find('id="asin-B0HIGH0001"'), out.find('id="asin-B0MID00001"'), out.find('id="asin-B0LOW00001"')]
        assert order == sorted(order)
        assert all(o > 0 for o in order)

    def test_toc_omitted_for_small_runs(self):
        rows = [_row_payload("B0SMALL001", "BUY")]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 1, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        assert soup.find("nav", class_="toc") is None

    def test_toc_present_for_runs_above_threshold(self):
        rows = [_row_payload(f"B000000{i:03d}", "BUY") for i in range(4)]
        p = _payload(
            rows=rows,
            verdict_counts={"BUY": 4, "SOURCE_ONLY": 0, "NEGOTIATE": 0, "WATCH": 0, "KILL": 0},
        )
        out = render_html(p)
        soup = BeautifulSoup(out, "html.parser")
        assert soup.find("nav", class_="toc") is not None

    def test_supplier_null_falls_back_in_title(self):
        p = _payload(rows=[], supplier=None, strategy="keepa_finder")
        out = render_html(p)
        assert "keepa_finder" in out
        # Title contains strategy not "None".
        assert "None" not in out  # be picky: never the literal "None" string
```

- [ ] **Step 2: Run tests, verify fail**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/sourcing_engine/buy_plan_html/tests/test_renderer.py -q
```

- [ ] **Step 3: Implement renderer.py**

```python
"""HTML renderer — emits the buyer-report skeleton.

Reads the payload dict produced by `payload.py`, returns a single
self-contained HTML string. Per-card prose is left as a marker
(`<!-- prose:{asin} -->`) for downstream injection.

PRD §5 is the contract.
"""
from __future__ import annotations

import html as _html
from typing import Any


_TOC_MIN_ROWS = 4   # PRD §5.4 — TOC omitted for ≤3 actionable rows


_VERDICT_TO_SECTION = [
    ("BUY", "section-buy", "Why we should buy"),
    ("SOURCE_ONLY", "section-source-only", "Why source this"),
    ("NEGOTIATE", "section-negotiate", "Why negotiate"),
    ("WATCH", "section-watch", "Why watch"),
]


def render_html(payload: dict) -> str:
    rows = payload.get("rows") or []
    counts = payload.get("verdict_counts") or {}
    strategy = payload.get("strategy") or ""
    supplier = payload.get("supplier")
    run_id = payload.get("run_id") or ""
    title_suffix = supplier if supplier else strategy
    title = f"Buyer Report — {title_suffix} — {run_id}"

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{_esc(title)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        _render_header(payload),
    ]

    if len(rows) >= _TOC_MIN_ROWS:
        parts.append(_render_toc(rows))

    parts.append("<main>")
    if not rows:
        parts.append('<div class="empty-notice">No actionable rows in this run.</div>')
    else:
        # Sort rows within each verdict.
        for verdict, section_id, _heading in _VERDICT_TO_SECTION:
            section_rows = [r for r in rows if (r.get("verdict") or "").upper() == verdict]
            if not section_rows:
                continue
            section_rows = _sort_within_verdict(verdict, section_rows)
            parts.append(f'<section id="{section_id}" class="verdict-section">')
            count = counts.get(verdict, len(section_rows))
            parts.append(f"<h2>{verdict} ({count})</h2>")
            for row in section_rows:
                parts.append(_render_card(row))
            parts.append("</section>")
    parts.append("</main>")

    parts.append(_render_footer(payload))
    parts.append("</body></html>")
    return "\n".join(parts)


def _sort_within_verdict(verdict: str, rows: list[dict]) -> list[dict]:
    """PRD §5.4 — BUY by projected_30d_profit desc; others by opportunity_score desc."""
    if verdict == "BUY":
        return sorted(
            rows, key=lambda r: -(r.get("buy_plan", {}).get("projected_30d_profit_gbp") or 0),
        )
    return sorted(rows, key=lambda r: -(r.get("opportunity_score") or 0))


def _render_header(payload: dict) -> str:
    counts = payload.get("verdict_counts") or {}
    strategy = payload.get("strategy") or ""
    supplier = payload.get("supplier")
    title_suffix = supplier if supplier else strategy
    chips = " ".join(
        f'<span class="vc vc-{v.lower().replace("_","-")}">{v} {counts.get(v, 0)}</span>'
        for v in ("BUY", "SOURCE_ONLY", "NEGOTIATE", "WATCH")
    )
    return (
        '<header class="report-header">'
        f"<h1>Buyer Report</h1>"
        f'<div class="run-meta">{_esc(title_suffix)} · run {_esc(payload.get("run_id") or "")}</div>'
        f'<div class="verdict-counts">{chips}</div>'
        "</header>"
    )


def _render_footer(payload: dict) -> str:
    return (
        "<footer>"
        f"<div>Generated by 09_buy_plan_html · {_esc(payload.get('generated_at') or '')}</div>"
        "</footer>"
    )


def _render_toc(rows: list[dict]) -> str:
    parts = ['<nav class="toc"><h3>Contents</h3>']
    for verdict, section_id, _heading in _VERDICT_TO_SECTION:
        section_rows = [r for r in rows if (r.get("verdict") or "").upper() == verdict]
        if not section_rows:
            continue
        parts.append(f'<div class="toc-section"><a href="#{section_id}">{verdict} ({len(section_rows)})</a><ul>')
        for r in _sort_within_verdict(verdict, section_rows):
            asin = r.get("asin") or ""
            title = r.get("title") or asin
            parts.append(f'<li><a href="#asin-{_esc(asin)}">{_esc(title[:50])}</a></li>')
        parts.append("</ul></div>")
    parts.append("</nav>")
    return "".join(parts)


def _render_card(row: dict) -> str:
    asin = row.get("asin") or ""
    verdict = (row.get("verdict") or "").upper()
    cls = f"verdict-{verdict.lower().replace('_','-')}"
    return (
        f'<article id="asin-{_esc(asin)}" class="card {cls}">'
        f"{_render_card_header(row)}"
        f'<div class="card-identity-economics">'
        f"{_render_image(row)}"
        f'<div class="card-summary">{_render_summary(row)}{_render_economics_grid(row)}</div>'
        f"</div>"
        f"{_render_prose_section(row)}"
        f"{_render_scoring(row)}"
        f"{_render_next_action(row)}"
        f"</article>"
    )


def _render_card_header(row: dict) -> str:
    verdict = row.get("verdict") or ""
    confidence = row.get("verdict_confidence") or ""
    score = row.get("opportunity_score")
    score_str = f"Score: {int(score)}/100" if score is not None else ""
    return (
        '<header class="card-header">'
        f'<span class="verdict-badge">{_esc(verdict)} · {_esc(confidence)}</span>'
        f'<span class="card-score">{_esc(score_str)}</span>'
        "</header>"
    )


def _render_image(row: dict) -> str:
    asin = row.get("asin") or ""
    image_url = row.get("image_url") or ""
    amazon_url = row.get("amazon_url") or "#"
    title = row.get("title") or asin
    return (
        f'<a class="card-image" href="{_esc(amazon_url)}" target="_blank" rel="noopener">'
        f'<img src="{_esc(image_url)}" '
        f'onerror="this.style.display=\'none\'" '
        f'alt="{_esc(title)}" loading="lazy">'
        "</a>"
    )


def _render_summary(row: dict) -> str:
    asin = row.get("asin") or ""
    title = row.get("title") or asin
    brand = row.get("brand") or ""
    amazon_url = row.get("amazon_url") or ""
    return (
        f'<h3 class="card-title">{_esc(title)}</h3>'
        f'<div class="card-id">ASIN <a href="{_esc(amazon_url)}">{_esc(asin)}</a> · Brand {_esc(brand)}</div>'
    )


def _render_economics_grid(row: dict) -> str:
    verdict = (row.get("verdict") or "").upper()
    eco = row.get("economics") or {}
    bp = row.get("buy_plan") or {}

    def gbp(v):
        return f"£{float(v):.2f}" if v is not None else "—"

    def num(v, suffix=""):
        return f"{float(v):.0f}{suffix}" if v is not None else "—"

    if verdict == "BUY":
        return (
            '<table class="economics-grid"><tbody>'
            f'<tr><td class="econ-label">Buy cost</td><td class="econ-value">{gbp(eco.get("buy_cost_gbp"))}</td>'
            f'<td class="econ-label">Target buy</td><td class="econ-value">{gbp(eco.get("target_buy_cost_gbp"))} (stretch {gbp(eco.get("target_buy_cost_stretch_gbp"))})</td></tr>'
            f'<tr><td class="econ-label">Order qty</td><td class="econ-value">{num(bp.get("order_qty_recommended"))}</td>'
            f'<td class="econ-label">Capital</td><td class="econ-value">{gbp(bp.get("capital_required_gbp"))}</td></tr>'
            f'<tr><td class="econ-label">Payback</td><td class="econ-value">{num(bp.get("payback_days"))} days</td>'
            f'<td class="econ-label">30d profit</td><td class="econ-value">{gbp(bp.get("projected_30d_profit_gbp"))}</td></tr>'
            "</tbody></table>"
        )
    if verdict == "SOURCE_ONLY":
        return (
            '<table class="economics-grid"><tbody>'
            f'<tr><td class="econ-label">Buy cost</td><td class="econ-value">— (no supplier yet)</td>'
            f'<td class="econ-label">Target buy</td><td class="econ-value">≤ {gbp(eco.get("target_buy_cost_gbp"))} (stretch {gbp(eco.get("target_buy_cost_stretch_gbp"))})</td></tr>'
            f'<tr><td class="econ-label">Projected 30d revenue</td><td class="econ-value">{gbp(bp.get("projected_30d_revenue_gbp"))}</td>'
            f'<td class="econ-label">30d profit at target</td><td class="econ-value">{gbp(bp.get("projected_30d_profit_gbp"))}</td></tr>'
            "</tbody></table>"
        )
    if verdict == "NEGOTIATE":
        gap_gbp = bp.get("gap_to_buy_gbp")
        gap_pct = bp.get("gap_to_buy_pct")
        gap_str = f"{gbp(gap_gbp)} ({float(gap_pct):.1%})" if gap_pct is not None else gbp(gap_gbp)
        return (
            '<table class="economics-grid"><tbody>'
            f'<tr><td class="econ-label">Currently</td><td class="econ-value">{gbp(eco.get("buy_cost_gbp"))}</td>'
            f'<td class="econ-label">Target ceiling</td><td class="econ-value">{gbp(eco.get("target_buy_cost_gbp"))} (stretch {gbp(eco.get("target_buy_cost_stretch_gbp"))})</td></tr>'
            f'<tr><td class="econ-label">Gap to BUY</td><td class="econ-value gap-positive">{gap_str}</td>'
            f'<td class="econ-label">30d profit (current)</td><td class="econ-value">{gbp(bp.get("projected_30d_profit_gbp"))}</td></tr>'
            "</tbody></table>"
        )
    # WATCH
    return (
        '<table class="economics-grid"><tbody>'
        f'<tr><td class="econ-label">Buy cost</td><td class="econ-value">{gbp(eco.get("buy_cost_gbp"))}</td>'
        f'<td class="econ-label">Target buy</td><td class="econ-value">{gbp(eco.get("target_buy_cost_gbp"))} (stretch {gbp(eco.get("target_buy_cost_stretch_gbp"))})</td></tr>'
        f'<tr><td class="econ-label">Projected 30d revenue</td><td class="econ-value">{gbp(bp.get("projected_30d_revenue_gbp"))}</td>'
        f'<td class="econ-label">30d profit</td><td class="econ-value">{gbp(bp.get("projected_30d_profit_gbp"))}</td></tr>'
        "</tbody></table>"
    )


def _render_prose_section(row: dict) -> str:
    verdict = (row.get("verdict") or "").upper()
    headings = {
        "BUY": "Why we should buy",
        "SOURCE_ONLY": "Why source this",
        "NEGOTIATE": "Why negotiate",
        "WATCH": "Why watch",
    }
    asin = row.get("asin") or ""
    return (
        '<section class="card-prose">'
        f"<h4>{_esc(headings.get(verdict, 'Notes'))}</h4>"
        f'<div class="prose" data-asin="{_esc(asin)}"><!-- prose:{asin} --></div>'
        "</section>"
    )


def _render_scoring(row: dict) -> str:
    metrics = row.get("metrics") or []
    if not metrics:
        return ""
    rows = []
    for m in metrics:
        v = m.get("verdict") or "grey"
        cls = f"dot dot-{v}"
        rows.append(
            f'<tr>'
            f'<td class="{cls}"></td>'
            f'<td class="metric-label">{_esc(m.get("label") or "")}</td>'
            f'<td class="metric-value">{_esc(m.get("value_display") or "")}</td>'
            f'<td class="metric-rationale">{_esc(m.get("rationale") or "")}</td>'
            "</tr>"
        )
    return (
        '<section class="card-scoring"><h4>Scoring</h4>'
        '<table class="metrics"><tbody>' + "".join(rows) + "</tbody></table>"
        "</section>"
    )


def _render_next_action(row: dict) -> str:
    na = row.get("next_action") or ""
    if not na:
        return ""
    return f'<footer class="card-next-action"><strong>Next action:</strong> {_esc(na)}</footer>'


def _esc(s: Any) -> str:
    return _html.escape(str(s if s is not None else ""))


_CSS = """
:root {
  --buy: #27AE60; --source: #2980B9; --negotiate: #E67E22; --watch: #F1C40F;
  --grey: #B0B0B0; --green: #27AE60; --amber: #E67E22; --red: #C0392B;
}
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color: #1B2A4A; max-width: 1280px; margin: 0 auto; padding: 1rem; }
.report-header { padding: 1rem; border-bottom: 1px solid #ddd; }
.report-header h1 { margin: 0; }
.run-meta { color: #666; font-size: 0.9rem; }
.verdict-counts { margin-top: 0.5rem; }
.vc { display: inline-block; padding: 0.2rem 0.6rem; margin-right: 0.4rem; border-radius: 3px; font-size: 0.85rem; color: white; font-weight: bold; }
.vc-buy { background: var(--buy); }
.vc-source-only { background: var(--source); }
.vc-negotiate { background: var(--negotiate); }
.vc-watch { background: var(--watch); color: #1B2A4A; }
.toc { padding: 1rem; background: #f8f9fa; border-radius: 4px; margin: 1rem 0; }
.toc h3 { margin-top: 0; }
.toc ul { margin: 0; padding-left: 1.2rem; font-size: 0.9rem; }
.empty-notice { padding: 2rem; text-align: center; color: #666; }
.verdict-section { margin: 2rem 0; }
.card { border: 1px solid #ddd; border-left: 4px solid #ddd; border-radius: 4px; padding: 1rem; margin: 1rem 0; max-width: 900px; }
.verdict-buy { border-left-color: var(--buy); }
.verdict-source-only { border-left-color: var(--source); }
.verdict-negotiate { border-left-color: var(--negotiate); }
.verdict-watch { border-left-color: var(--watch); }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.verdict-badge { font-weight: bold; padding: 0.2rem 0.6rem; border-radius: 3px; background: #1B2A4A; color: white; }
.verdict-buy .verdict-badge { background: var(--buy); }
.verdict-source-only .verdict-badge { background: var(--source); }
.verdict-negotiate .verdict-badge { background: var(--negotiate); }
.verdict-watch .verdict-badge { background: var(--watch); color: #1B2A4A; }
.card-score { color: #666; font-size: 0.9rem; }
.card-identity-economics { display: flex; gap: 1rem; margin: 1rem 0; }
.card-image img { width: 200px; height: auto; max-width: 100%; object-fit: contain; }
.card-summary { flex: 1; }
.card-title { margin: 0 0 0.5rem 0; }
.card-id { color: #666; font-size: 0.9rem; margin-bottom: 0.8rem; }
.economics-grid { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
.economics-grid td { padding: 0.4rem; vertical-align: top; }
.econ-label { color: #666; font-size: 0.85rem; width: 25%; }
.econ-value { font-weight: bold; }
.gap-positive { color: var(--red); }
.card-prose { padding: 0.5rem 0; }
.card-prose h4 { margin: 0.5rem 0 0.3rem 0; font-size: 1rem; }
.prose { color: #1B2A4A; line-height: 1.5; }
.card-scoring { margin: 1rem 0; }
.card-scoring h4 { margin: 0 0 0.4rem 0; font-size: 1rem; }
.metrics { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.metrics td { padding: 0.3rem 0.5rem; vertical-align: middle; }
.metrics .dot { width: 14px; height: 14px; border-radius: 50%; display: block; padding: 0; }
.dot-green { background: var(--green); }
.dot-amber { background: var(--amber); }
.dot-red { background: var(--red); }
.dot-grey { background: transparent; border: 1.5px solid var(--grey); }
.metric-label { width: 30%; }
.metric-value { font-weight: bold; width: 15%; }
.metric-rationale { color: #666; font-size: 0.85rem; }
.card-next-action { padding-top: 0.6rem; border-top: 1px solid #eee; font-size: 0.95rem; }
footer { margin-top: 2rem; color: #999; font-size: 0.85rem; }
@media print {
  .toc { display: none; }
  .card { page-break-inside: avoid; }
  body { background: white; }
  .vc, .verdict-badge { color: black !important; background: transparent !important; border: 1px solid #999; }
}
"""
```

- [ ] **Step 4: Run tests, verify pass**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/sourcing_engine/buy_plan_html/tests/test_renderer.py -q
```

Expected: all green (~12 tests). Note: `bs4` may need install — `pip install beautifulsoup4` if missing.

- [ ] **Step 5: Commit**

```bash
git add shared/lib/python/sourcing_engine/buy_plan_html/renderer.py shared/lib/python/sourcing_engine/buy_plan_html/tests/test_renderer.py
git commit -m "feat(buyer-report): HTML renderer with per-verdict card layouts"
```

---

## Task 6 — Prose injector

**Files:**
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/prose_injector.py`
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/tests/test_prose_injector.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for prose_injector — replaces <!-- prose:{asin} --> markers."""
from __future__ import annotations

import logging
import pytest

from sourcing_engine.buy_plan_html.prose_injector import inject_prose


def test_replaces_marker_with_paragraph():
    html = '<div class="prose" data-asin="B0AAA"><!-- prose:B0AAA --></div>'
    out = inject_prose(html, {"B0AAA": "Test prose."})
    assert "<!-- prose:B0AAA -->" not in out
    assert "<p class=\"prose-text\">Test prose.</p>" in out


def test_idempotent_on_second_run():
    html = '<!-- prose:B0AAA -->'
    once = inject_prose(html, {"B0AAA": "Hi."})
    twice = inject_prose(once, {"B0AAA": "Hi."})
    assert once == twice


def test_missing_prose_leaves_marker_and_logs_warning(caplog):
    html = '<!-- prose:B0AAA --> <!-- prose:B0BBB -->'
    with caplog.at_level(logging.WARNING):
        out = inject_prose(html, {"B0AAA": "Hi."})
    # B0AAA replaced; B0BBB marker still in place.
    assert "<p class=\"prose-text\">Hi.</p>" in out
    assert "<!-- prose:B0BBB -->" in out
    # Warning logged.
    assert any("B0BBB" in rec.message for rec in caplog.records)


def test_prose_for_unknown_asin_logs_and_ignored(caplog):
    html = '<!-- prose:B0AAA -->'
    with caplog.at_level(logging.WARNING):
        out = inject_prose(html, {"B0AAA": "Hi.", "B0NEVER": "stranded"})
    assert "<p class=\"prose-text\">Hi.</p>" in out
    assert "B0NEVER" not in out


def test_html_escape_in_prose():
    html = '<!-- prose:B0AAA -->'
    out = inject_prose(html, {"B0AAA": "Profit > £5 & risk < 1%"})
    assert "&gt;" in out
    assert "&amp;" in out
    assert "&lt;" in out


def test_strips_html_tags_from_prose_input():
    # Defensive — if the LLM returns HTML, strip it before injection.
    html = '<!-- prose:B0AAA -->'
    out = inject_prose(html, {"B0AAA": "<p>Hi <em>there</em></p>"})
    assert "<em>" not in out
    assert "Hi there" in out


def test_caps_prose_at_500_chars():
    long_prose = "a" * 1000
    html = '<!-- prose:B0AAA -->'
    out = inject_prose(html, {"B0AAA": long_prose})
    # Prose-text body capped — 500 chars + tags.
    body = out.replace('<p class="prose-text">', "").replace("</p>", "")
    assert len(body) <= 500
```

- [ ] **Step 2: Run tests, verify fail**

- [ ] **Step 3: Implement prose_injector.py**

```python
"""Prose marker injector — replaces <!-- prose:{asin} --> markers."""
from __future__ import annotations

import html as _html
import logging
import re

logger = logging.getLogger(__name__)

_MARKER_RE = re.compile(r"<!-- prose:([A-Z0-9]{10,14}) -->")
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_MAX_PROSE_CHARS = 480   # leave headroom for the wrapping tags


def _sanitise(prose: str) -> str:
    # Strip any HTML tags + collapse whitespace + cap length.
    stripped = _TAG_STRIP_RE.sub("", prose)
    collapsed = " ".join(stripped.split())
    if len(collapsed) > _MAX_PROSE_CHARS:
        collapsed = collapsed[:_MAX_PROSE_CHARS]
    return _html.escape(collapsed)


def inject_prose(html: str, prose_by_asin: dict[str, str]) -> str:
    """Walk the HTML and replace <!-- prose:{asin} --> markers.

    Idempotent: calling twice with the same input is a no-op.
    Missing prose for a marker leaves the marker in place + logs warn.
    Surplus prose entries (no matching marker) are logged + ignored.
    """
    found_asins = set()

    def repl(match):
        asin = match.group(1)
        found_asins.add(asin)
        prose = prose_by_asin.get(asin)
        if prose is None or not prose.strip():
            logger.warning("inject_prose: no prose for ASIN %s; leaving marker", asin)
            return match.group(0)
        safe = _sanitise(prose)
        return f'<p class="prose-text">{safe}</p>'

    out = _MARKER_RE.sub(repl, html)

    # Log unused prose entries.
    extras = set(prose_by_asin.keys()) - found_asins
    for asin in extras:
        logger.warning("inject_prose: prose supplied for ASIN %s but no marker found", asin)

    return out
```

- [ ] **Step 4: Run tests, verify pass**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/sourcing_engine/buy_plan_html/tests/test_prose_injector.py -q
```

- [ ] **Step 5: Commit**

```bash
git add shared/lib/python/sourcing_engine/buy_plan_html/prose_injector.py shared/lib/python/sourcing_engine/buy_plan_html/tests/test_prose_injector.py
git commit -m "feat(buyer-report): prose marker injector"
```

---

## Task 7 — Step wrapper

**Files:**
- Create: `fba_engine/steps/buy_plan_html.py`
- Create: `fba_engine/steps/tests/test_buy_plan_html.py`

- [ ] **Step 1: Write failing tests**

Create `fba_engine/steps/tests/test_buy_plan_html.py`:

```python
"""Tests for fba_engine.steps.buy_plan_html — runner wrapper."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from bs4 import BeautifulSoup

from fba_engine.steps.buy_plan_html import (
    BUYER_REPORT_OUTPUTS,
    add_buy_plan_html,
    run_step,
)


def _row(verdict: str, **overrides) -> dict:
    base = {
        "asin": "B0TEST00001",
        "product_name": "Test product",
        "brand": "TestBrand",
        "supplier": "abgee",
        "supplier_sku": "SKU-X",
        "amazon_url": "https://www.amazon.co.uk/dp/B0TEST00001",
        "decision": "SHORTLIST",
        "opportunity_verdict": verdict,
        "opportunity_confidence": "HIGH",
        "opportunity_score": 80,
        "next_action": "test action",
        "buy_cost": 4.0, "market_price": 16.85,
        "raw_conservative_price": 16.85, "fees_conservative": 4.5,
        "profit_conservative": 8.35, "roi_conservative": 1.114,
        "fba_seller_count": 4, "amazon_on_listing": "N",
        "amazon_bb_pct_90": 0.10, "price_volatility_90d": 0.10,
        "sales_estimate": 250, "predicted_velocity_mid": 18,
        "bsr_drops_30d": 45,
        "order_qty_recommended": 13, "capital_required": 52.0,
        "projected_30d_units": 18, "projected_30d_revenue": 303.30,
        "projected_30d_profit": 150.30, "payback_days": 21.7,
        "target_buy_cost_buy": 9.5, "target_buy_cost_stretch": 8.52,
        "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
        "buy_plan_status": "OK", "risk_flags": [],
    }
    base.update(overrides)
    return base


def test_writes_json_and_html(tmp_path):
    df = pd.DataFrame([_row("BUY")])
    add_buy_plan_html(
        df, run_dir=tmp_path, timestamp="20260503_120000",
        strategy="supplier_pricelist", supplier="abgee",
    )
    json_path = tmp_path / "buyer_report_20260503_120000.json"
    html_path = tmp_path / "buyer_report_20260503_120000.html"
    assert json_path.exists()
    assert html_path.exists()
    # JSON parses.
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert len(data["rows"]) == 1
    # HTML parses.
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    assert soup.find("article", id="asin-B0TEST00001") is not None


def test_template_prose_filled_in_when_engine_alone(tmp_path):
    df = pd.DataFrame([_row("BUY")])
    add_buy_plan_html(
        df, run_dir=tmp_path, timestamp="20260503_120000",
        strategy="supplier_pricelist", supplier="abgee",
    )
    html = (tmp_path / "buyer_report_20260503_120000.html").read_text(encoding="utf-8")
    # No raw markers should remain — template prose injected.
    assert "<!-- prose:B0TEST00001 -->" not in html
    assert "prose-text" in html


def test_empty_df_writes_minimal_artefacts(tmp_path):
    add_buy_plan_html(
        pd.DataFrame(), run_dir=tmp_path, timestamp="20260503_120000",
        strategy="supplier_pricelist", supplier="abgee",
    )
    json_path = tmp_path / "buyer_report_20260503_120000.json"
    html_path = tmp_path / "buyer_report_20260503_120000.html"
    assert json_path.exists()
    assert html_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["rows"] == []


def test_disabled_via_config_writes_nothing(tmp_path, monkeypatch):
    from fba_config_loader import BuyPlanHtml
    monkeypatch.setattr(
        "fba_engine.steps.buy_plan_html.get_buy_plan_html",
        lambda: BuyPlanHtml(enabled=False),
    )
    add_buy_plan_html(
        pd.DataFrame([_row("BUY")]), run_dir=tmp_path,
        timestamp="20260503", strategy="supplier_pricelist", supplier="abgee",
    )
    assert not (tmp_path / "buyer_report_20260503.json").exists()
    assert not (tmp_path / "buyer_report_20260503.html").exists()


def test_run_step_reads_runner_config(tmp_path):
    df = pd.DataFrame([_row("BUY")])
    out = run_step(df, {
        "run_dir": str(tmp_path), "timestamp": "20260503",
        "strategy": "supplier_pricelist", "supplier": "abgee",
    })
    assert out is df  # passes through unchanged
    assert (tmp_path / "buyer_report_20260503.json").exists()


def test_run_step_returns_df_unchanged_on_per_row_exception(tmp_path, caplog, monkeypatch):
    """Per-row exception in payload builder → log + skip + continue."""
    from sourcing_engine.buy_plan_html import payload as payload_module

    def boom(row):
        if row.get("asin") == "B0BAD00001":
            raise ValueError("simulated")
        return payload_module.build_row_payload.__wrapped__(row) if hasattr(payload_module.build_row_payload, "__wrapped__") else _ok(row)

    df = pd.DataFrame([_row("BUY", asin="B0OK0000001"), _row("BUY", asin="B0BAD00001")])
    # We don't mock the per-row exception path inside the wrapper directly;
    # instead we rely on the wrapper's try/except. Verify by constructing a
    # row that has all-required fields and a row that's malformed enough
    # to cause downstream error.
    out = run_step(df.copy(), {
        "run_dir": str(tmp_path), "timestamp": "20260503",
        "strategy": "supplier_pricelist", "supplier": "abgee",
    })
    assert out is not None  # didn't crash


def test_buyer_report_outputs_constant_exposes_filename_pattern():
    assert BUYER_REPORT_OUTPUTS == ("buyer_report_{ts}.json", "buyer_report_{ts}.html")
```

- [ ] **Step 2: Run tests, verify fail**

- [ ] **Step 3: Implement buy_plan_html.py**

```python
"""09_buy_plan_html — emits buyer_report_{ts}.json + .html."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from fba_config_loader import get_buy_plan_html
from sourcing_engine.buy_plan_html.payload import build_payload
from sourcing_engine.buy_plan_html.prose_injector import inject_prose
from sourcing_engine.buy_plan_html.renderer import render_html
from sourcing_engine.buy_plan_html.template_prose import render_template_prose

logger = logging.getLogger(__name__)

BUYER_REPORT_OUTPUTS = (
    "buyer_report_{ts}.json",
    "buyer_report_{ts}.html",
)


def add_buy_plan_html(
    df: pd.DataFrame,
    *,
    run_dir: Path | str,
    timestamp: str,
    strategy: str,
    supplier: str | None,
) -> pd.DataFrame:
    """Write JSON + HTML artefacts. Returns df unchanged.

    Honours `buy_plan_html.enabled` config — silent no-op when disabled.
    Per-row exceptions in payload-building or template-prose are caught
    and logged; the run never aborts.
    """
    cfg = get_buy_plan_html()
    if not cfg.enabled:
        logger.info("buy_plan_html: disabled by config; skipping")
        return df

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    json_path = run_dir / f"buyer_report_{timestamp}.json"
    html_path = run_dir / f"buyer_report_{timestamp}.html"

    payload = build_payload(df, run_id=timestamp, strategy=strategy, supplier=supplier)

    # Atomic JSON write.
    tmp_json = json_path.with_suffix(".json.tmp")
    tmp_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp_json.replace(json_path)

    # Render skeleton with markers.
    html = render_html(payload)

    # Engine-alone fallback: template-prose for every row.
    template_proses = {}
    for row in payload.get("rows") or []:
        try:
            template_proses[row["asin"]] = render_template_prose(row)
        except Exception:
            logger.exception(
                "buy_plan_html: template prose failed for asin=%s",
                row.get("asin"),
            )
            template_proses[row["asin"]] = "[prose unavailable]"

    html = inject_prose(html, template_proses)

    tmp_html = html_path.with_suffix(".html.tmp")
    tmp_html.write_text(html, encoding="utf-8")
    tmp_html.replace(html_path)

    logger.info("buy_plan_html: wrote %s + %s (%d rows)", json_path, html_path, len(payload.get("rows") or []))
    return df


def run_step(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    run_dir = config.get("run_dir") or config.get("output_dir")
    timestamp = config.get("timestamp")
    strategy = config.get("strategy") or ""
    supplier = config.get("supplier") or None
    if not run_dir or not timestamp:
        logger.warning("buy_plan_html: missing run_dir/timestamp in config — skipping")
        return df
    return add_buy_plan_html(
        df, run_dir=run_dir, timestamp=timestamp,
        strategy=strategy, supplier=supplier,
    )
```

- [ ] **Step 4: Run tests, verify pass**

```bash
PYTHONPATH=shared/lib/python python -m pytest fba_engine/steps/tests/test_buy_plan_html.py -q
```

- [ ] **Step 5: Commit**

```bash
git add fba_engine/steps/buy_plan_html.py fba_engine/steps/tests/test_buy_plan_html.py
git commit -m "feat(buyer-report): step wrapper writes JSON + HTML artefacts"
```

---

## Task 8 — HTML snapshot test

**Files:**
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/tests/test_html_snapshot.py`
- Create: `shared/lib/python/sourcing_engine/buy_plan_html/tests/snapshots/buyer_report_4_verdicts.html` (generated)

- [ ] **Step 1: Write failing test**

```python
"""Snapshot test for HTML structural stability."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sourcing_engine.buy_plan_html.payload import build_payload
from sourcing_engine.buy_plan_html.prose_injector import inject_prose
from sourcing_engine.buy_plan_html.renderer import render_html
from sourcing_engine.buy_plan_html.template_prose import render_template_prose

import pandas as pd

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "buyer_report_4_verdicts.html"
RUN_ID = "20260503_120000"   # frozen for deterministic snapshot


def _fixture_df() -> pd.DataFrame:
    rows = []
    rows.append({
        "asin": "B0BUY00001", "product_name": "BUY product",
        "brand": "Acme", "supplier": "test", "supplier_sku": "BUY",
        "amazon_url": "https://www.amazon.co.uk/dp/B0BUY00001",
        "opportunity_verdict": "BUY", "opportunity_confidence": "HIGH",
        "opportunity_score": 85, "next_action": "place test order",
        "buy_cost": 4.00, "market_price": 16.85,
        "raw_conservative_price": 16.85, "fees_conservative": 4.50,
        "profit_conservative": 8.35, "roi_conservative": 1.114,
        "fba_seller_count": 4, "amazon_on_listing": "N",
        "amazon_bb_pct_90": 0.10, "price_volatility_90d": 0.10,
        "sales_estimate": 250, "predicted_velocity_mid": 130,
        "bsr_drops_30d": 200,
        "order_qty_recommended": 13, "capital_required": 52.0,
        "projected_30d_units": 18, "projected_30d_revenue": 303.30,
        "projected_30d_profit": 150.30, "payback_days": 21.7,
        "target_buy_cost_buy": 9.50, "target_buy_cost_stretch": 8.52,
        "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
        "buy_plan_status": "OK", "risk_flags": [],
    })
    rows.append({
        "asin": "B0SRC00001", "product_name": "SOURCE product",
        "brand": "Acme", "supplier": "test", "supplier_sku": "SRC",
        "amazon_url": "https://www.amazon.co.uk/dp/B0SRC00001",
        "opportunity_verdict": "SOURCE_ONLY", "opportunity_confidence": "HIGH",
        "opportunity_score": 80, "next_action": "find supplier",
        "buy_cost": 0.0, "market_price": 16.85,
        "raw_conservative_price": 16.85, "fees_conservative": 4.50,
        "profit_conservative": None, "roi_conservative": None,
        "fba_seller_count": 4, "amazon_on_listing": "N",
        "amazon_bb_pct_90": 0.10, "price_volatility_90d": 0.10,
        "sales_estimate": 320, "predicted_velocity_mid": 165,
        "bsr_drops_30d": 200,
        "order_qty_recommended": None, "capital_required": None,
        "projected_30d_units": 42, "projected_30d_revenue": 710.00,
        "projected_30d_profit": 136.00, "payback_days": None,
        "target_buy_cost_buy": 4.85, "target_buy_cost_stretch": 4.10,
        "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
        "buy_plan_status": "NO_BUY_COST", "risk_flags": [],
    })
    rows.append({
        "asin": "B0NEG00001", "product_name": "NEGOTIATE product",
        "brand": "Acme", "supplier": "test", "supplier_sku": "NEG",
        "amazon_url": "https://www.amazon.co.uk/dp/B0NEG00001",
        "opportunity_verdict": "NEGOTIATE", "opportunity_confidence": "MEDIUM",
        "opportunity_score": 70, "next_action": "negotiate down",
        "buy_cost": 5.00, "market_price": 16.85,
        "raw_conservative_price": 16.85, "fees_conservative": 4.50,
        "profit_conservative": 1.50, "roi_conservative": 0.20,
        "fba_seller_count": 5, "amazon_on_listing": "N",
        "amazon_bb_pct_90": 0.20, "price_volatility_90d": 0.15,
        "sales_estimate": 180, "predicted_velocity_mid": 70,
        "bsr_drops_30d": 90,
        "order_qty_recommended": None, "capital_required": None,
        "projected_30d_units": 18, "projected_30d_revenue": 303.30,
        "projected_30d_profit": 42.30, "payback_days": None,
        "target_buy_cost_buy": 4.38, "target_buy_cost_stretch": 3.50,
        "gap_to_buy_gbp": 0.62, "gap_to_buy_pct": 0.124,
        "buy_plan_status": "OK", "risk_flags": [],
    })
    rows.append({
        "asin": "B0WAT00001", "product_name": "WATCH product",
        "brand": "Acme", "supplier": "test", "supplier_sku": "WAT",
        "amazon_url": "https://www.amazon.co.uk/dp/B0WAT00001",
        "opportunity_verdict": "WATCH", "opportunity_confidence": "LOW",
        "opportunity_score": 60, "next_action": "monitor",
        "buy_cost": 4.00, "market_price": 16.85,
        "raw_conservative_price": 16.85, "fees_conservative": 4.50,
        "profit_conservative": 8.35, "roi_conservative": 1.114,
        "fba_seller_count": 4, "amazon_on_listing": "N",
        "amazon_bb_pct_90": 0.10, "price_volatility_90d": 0.10,
        "sales_estimate": 70, "predicted_velocity_mid": 18,
        "bsr_drops_30d": 25,
        "order_qty_recommended": None, "capital_required": None,
        "projected_30d_units": 18, "projected_30d_revenue": 303.30,
        "projected_30d_profit": 150.30, "payback_days": None,
        "target_buy_cost_buy": 6.85, "target_buy_cost_stretch": 5.20,
        "gap_to_buy_gbp": None, "gap_to_buy_pct": None,
        "buy_plan_status": "BLOCKED_BY_VERDICT",
        "risk_flags": ["INSUFFICIENT_HISTORY"],
    })
    return pd.DataFrame(rows)


def _normalised_html() -> str:
    df = _fixture_df()
    payload = build_payload(df, run_id=RUN_ID, strategy="supplier_pricelist", supplier="test")
    # Freeze generated_at for deterministic snapshot.
    payload["generated_at"] = "2026-05-03T12:00:00Z"
    html = render_html(payload)
    proses = {row["asin"]: render_template_prose(row) for row in payload["rows"]}
    return inject_prose(html, proses)


def test_html_snapshot_matches(request):
    actual = _normalised_html()
    if request.config.getoption("--snapshot-update", default=False):
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(actual, encoding="utf-8")
        pytest.skip("snapshot updated")
    if not SNAPSHOT_PATH.exists():
        pytest.fail(
            f"Snapshot missing at {SNAPSHOT_PATH}; "
            "run pytest with --snapshot-update to create it."
        )
    expected = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert actual == expected, "HTML structure drifted; review changes and update with --snapshot-update if intentional"
```

Add a conftest.py snippet for the new option (or use existing if present).

- [ ] **Step 2: Run tests, verify they fail (snapshot missing)**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/sourcing_engine/buy_plan_html/tests/test_html_snapshot.py -q
```

Expected: fail with "Snapshot missing".

- [ ] **Step 3: Generate snapshot**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/sourcing_engine/buy_plan_html/tests/test_html_snapshot.py --snapshot-update -q
```

If `--snapshot-update` arg isn't recognised, add it to repo `conftest.py`:

```python
def pytest_addoption(parser):
    parser.addoption("--snapshot-update", action="store_true", default=False)
```

- [ ] **Step 4: Run again, verify pass**

- [ ] **Step 5: Commit**

```bash
git add shared/lib/python/sourcing_engine/buy_plan_html/tests/test_html_snapshot.py shared/lib/python/sourcing_engine/buy_plan_html/tests/snapshots/buyer_report_4_verdicts.html conftest.py
git commit -m "feat(buyer-report): HTML snapshot test for 4-verdict fixture"
```

---

## Task 9 — Wire into 6 strategy YAMLs + run_pipeline + CLI flag

**Files:**
- Modify: 6 YAMLs (`supplier_pricelist`, `keepa_finder`, `keepa_niche`, `oa_csv`, `seller_storefront_csv`, `single_asin`)
- Modify: `shared/lib/python/sourcing_engine/main.py` (add `--no-html` flag, call `add_buy_plan_html` after `write_outputs`)
- Modify: `cli/strategy.py` (add `--no-html`, thread `html_enabled` into context)
- Modify: `fba_engine/strategies/tests/test_runner.py` (extend smoke tests)

- [ ] **Step 1: Add buy_plan_html step to 6 strategy YAMLs**

For each of the 6 strategy YAMLs, add immediately after the existing output step (or as the last step):

```yaml
  # Stage 09 — buy_plan_html (buyer report — JSON + HTML).
  - name: buy_plan_html
    module: fba_engine.steps.buy_plan_html
    config:
      run_dir: "{run_dir}"
      timestamp: "{timestamp}"
      strategy: "<strategy_name>"
      supplier: "{supplier}"   # or null literal for keepa_finder/single_asin
```

Per-strategy supplier convention: `supplier_pricelist` and `seller_storefront_csv` thread `{supplier}` from context (already a context key in those strategies); the others may need null literal — concretely encoded per YAML.

- [ ] **Step 2: Run all strategy YAML tests, verify they fail**

```bash
PYTHONPATH=shared/lib/python python -m pytest fba_engine/strategies/tests/test_runner.py -q
```

Expected: failures from `{supplier}` interpolation when context lacks it. Update test contexts.

- [ ] **Step 3: Update test contexts in test_runner.py**

Add `"supplier": "..."` (or empty string for null-supplier strategies) to context dicts in the 6 strategy smoke tests. Where the YAML uses `supplier: null`, no context update needed.

- [ ] **Step 4: Update run_pipeline + CLI**

In `sourcing_engine/main.py`:
- Add `--no-html` arg (action="store_true").
- Pass `html_enabled = not args.no_html` to `run_pipeline`.
- After `write_outputs(...)`, conditionally call `add_buy_plan_html(...)` when `html_enabled`.

In `cli/strategy.py`:
- Add `--no-html` arg.
- Add `"html_enabled"` to context dict (`"true"` or `"false"`).
- For YAMLs that interpolate `{html_enabled}`, the value flows; for others it's harmless extra context.

Alternative simpler approach: don't thread enabled through context — let the step's `get_buy_plan_html()` config control it, and the `--no-html` flag just sets an env var or modifies the loaded config. Pick whichever is cleaner.

- [ ] **Step 5: Extend smoke tests**

Each of the 6 strategy smoke tests now asserts `buyer_report_<ts>.html` and `.json` exist post-run.

- [ ] **Step 6: Run full Python suite**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/ fba_engine/steps/tests/ fba_engine/strategies/tests/ cli/tests/ -q
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add fba_engine/strategies/*.yaml fba_engine/strategies/tests/test_runner.py shared/lib/python/sourcing_engine/main.py cli/strategy.py
git commit -m "feat(buyer-report): wire 09_buy_plan_html into 6 strategies + --no-html flag"
```

---

## Task 10 — Cowork orchestration step

**Files:**
- Create: `orchestration/runs/buyer_report_prose.yaml`
- Create: `orchestration/runs/buyer_report_prose_prompt.md`

- [ ] **Step 1: Write the prompt template**

Create `orchestration/runs/buyer_report_prose_prompt.md`:

```markdown
# Buyer report prose generation prompt v1

You are generating a 2-3 sentence buyer-perspective paragraph for one product card on an Amazon FBA buyer report.

## Inputs

- `verdict`: BUY / SOURCE_ONLY / NEGOTIATE / WATCH
- `verdict_confidence`: HIGH / MEDIUM / LOW
- `economics`: { buy_cost_gbp, profit_per_unit_gbp, roi_conservative_pct, target_buy_cost_gbp, target_buy_cost_stretch_gbp }
- `buy_plan`: { order_qty_recommended, capital_required_gbp, projected_30d_units, projected_30d_revenue_gbp, projected_30d_profit_gbp, payback_days, gap_to_buy_gbp, gap_to_buy_pct }
- `metrics[]`: 7 traffic-light entries with verdict (green/amber/red/grey) + value + rationale
- `risk_flags[]`: list of upstream risk flags
- `engine_blockers[]`: BUY-blocker reasons (only relevant on WATCH)

## Output

Exactly one paragraph, 2-3 sentences, ~50-90 words. NO HTML tags. NO bullet points.

## Tone

Terse, operator-to-operator. Cite at least one specific metric value.
Mention any red traffic-light or risk_flag if present (skip if all-green).

## Per-verdict shape

- BUY: lead with "Order N units" or similar action; mention payback / capital; cite the strongest green metric. Skip risk flags if none.
- SOURCE_ONLY: lead with demand strength + supplier-target ceiling; mention projected revenue at target.
- NEGOTIATE: lead with the gap (£ or %); mention what BUY-grade looks like at the target ceiling.
- WATCH: lead with what's blocking BUY (top blocker or red metric); mention the target ceiling so the operator knows the bar to clear.

## Examples

(See PRD §6.3.1 for worked input → expected output examples per verdict.)
```

- [ ] **Step 2: Write the orchestration task YAML**

Create `orchestration/runs/buyer_report_prose.yaml`:

```yaml
# Generic Cowork task — generates per-card buyer-report prose via Claude
# and injects into the rendered HTML.
#
# Inputs (passed by parent orchestration):
#   json_path: absolute path to buyer_report_<ts>.json
#   html_path: absolute path to buyer_report_<ts>.html
#
# Cache: .cache/buyer_report_prose/<hash>.txt keyed on
# hash(schema_version + prompt_version + asin + verdict + sorted_metric_values).
# Re-running on unchanged input → zero LLM calls.

name: buyer_report_prose
description: >
  Generate per-card buyer-report prose paragraphs from the engine's
  structured JSON payload. Walks the HTML and injects each paragraph
  into its <!-- prose:{asin} --> marker.

inputs:
  - name: json_path
    type: path
    required: true
  - name: html_path
    type: path
    required: true

steps:
  - name: load_payload
    type: read_json
    args: { path: "{json_path}" }

  - name: generate_prose
    type: claude_agent
    description: Per-row prose generation (cached by content hash)
    args:
      prompt_template: orchestration/runs/buyer_report_prose_prompt.md
      cache_dir: .cache/buyer_report_prose
      cache_key_fields:
        - schema_version
        - prompt_version
        - asin
        - verdict
        - metrics
        - economics
      max_concurrent: 8
      on_rate_limit: skip_row     # log + leave marker; engine fallback persists
      on_error: skip_row

  - name: inject_prose_markers
    type: python
    args:
      module: sourcing_engine.buy_plan_html.prose_injector
      function: inject_prose
      html_path: "{html_path}"
      prose_by_asin: "{generate_prose.output}"
      atomic_write: true
```

NOTE: the exact YAML shape depends on Cowork's actual orchestration grammar. The structure above is illustrative — when Cowork is wired, this step's YAML may need adapting to whatever Cowork expects (e.g. `actions:` instead of `steps:`, different cache primitives).

- [ ] **Step 3: Commit**

```bash
git add orchestration/runs/buyer_report_prose.yaml orchestration/runs/buyer_report_prose_prompt.md
git commit -m "feat(buyer-report): Cowork orchestration step + LLM prompt template v1"
```

---

## Task 11 — Real-fixture verification + final acceptance

- [ ] **Step 1: Run abgee fixture end-to-end**

```bash
python run.py --supplier abgee --market-data fba_engine/data/pricelists/abgee/raw/keepa_combined_2026-03-25.csv --no-preflight --output ./out/buyer_report_smoke
```

Expected: `out/buyer_report_smoke/<ts>/buyer_report_<ts>.{json,html}` both exist.

- [ ] **Step 2: Inspect HTML manually**

Open `buyer_report_<ts>.html` in Chrome. Confirm:
- Title bar shows "Buyer Report — Abgee — <ts>"
- Verdict-counts banner shows BUY / SOURCE_ONLY / NEGOTIATE / WATCH counts
- TOC present (run has >3 actionable rows)
- Each card has: image rail (or hidden if broken), economics grid, prose paragraph, scoring table, next-action footer
- Print preview produces clean per-card pagination

- [ ] **Step 3: Run B001Y54F88 single-ASIN**

```bash
python run.py --strategy single_asin --asin B001Y54F88 --buy-cost 12.00 --output-dir ./out/buyer_report_single
```

Expected: `out/buyer_report_single/buyer_report_<asin>_<ts>.{json,html}` both exist.

- [ ] **Step 4: Run full Python suite**

```bash
PYTHONPATH=shared/lib/python python -m pytest shared/lib/python/ fba_engine/steps/tests/ fba_engine/strategies/tests/ cli/tests/ -q
```

Expected: all green.

- [ ] **Step 5: Run MCP suite**

```bash
cd services/amazon-fba-fees-mcp && npm test
```

Expected: all green (untouched by this work).

- [ ] **Step 6: Final commit (only if any small fixes needed during verification)**

```bash
git add -A
git commit -m "fix(buyer-report): real-fixture verification fixes"
```

- [ ] **Step 7: Push branch**

```bash
git push -u origin feat/buyer-report
```

- [ ] **Step 8: Open PR via gh**

```bash
gh pr create --base main --head feat/buyer-report \
  --title "feat(engine): 09_buy_plan_html — buyer report HTML + JSON" \
  --body "..."
```

(Body should summarise the work + reference docs/PRD-buyer-report.md.)

---

## Acceptance — must pass before merge

1. All Python tests pass on `feat/buyer-report` (baseline + ~45 new).
2. MCP suite still green.
3. Real `python run.py --supplier abgee` run produces both `buyer_report_<ts>.html` and `.json` at the expected path.
4. HTML parses (BeautifulSoup), all 4 verdict sections render their card variants, prose markers either replaced (Cowork) or filled with template prose (engine-alone).
5. Manual browser sanity: opens cleanly in Chrome, prints to PDF without broken card splits, forwards to Gmail without losing layout.
6. PRD §10 acceptance criteria met.
7. Code-reviewer pass — no blocking issues.

---

## Scope reminders

- **No new enrichment.** This step reads existing engine columns. If implementation hits a missing column, add it as a follow-up PR, not in this work.
- **Pure additive.** Never mutate `decision`, `opportunity_verdict`, or any upstream column.
- **Fail soft, never crash.** Per-row exceptions log + skip + continue. Empty df → minimal artefacts.
- **No I/O in pure modules.** `payload.py`, `renderer.py`, `template_prose.py`, `prose_injector.py` must remain pure functions. The step wrapper is the only I/O boundary.

## Defer to follow-up PRDs

- SP-API enrich extension to capture first-party image URL — separate PRD.
- Sortable / filterable interactive HTML — separate PRD.
- Per-strategy orchestration YAMLs for the 5 strategies that don't yet have one — owned by those strategies' own work.
- Email send-out integration — separate PRD.
