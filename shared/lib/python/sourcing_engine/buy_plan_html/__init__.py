"""09_buy_plan_html — buyer-report writer (v2).

See docs/PRD-buyer-report.md.

Submodules:
    payload   — pure JSON payload builder (DataFrame → dict)
    analyst   — deterministic fallback verdict + narrative + public
                entry (`fallback_analyse`). Cowork orchestration
                replaces this with a Claude API call when wired.
    scoring   — 4 dimension sub-score functions
                (Profit / Competition / Stability / Operational)
    trend     — direction arrows (↗ / → / ↘) + 1-line trend story
    renderer  — verdict-led HTML emitter (dict → HTML)
    cli       — `render-from-json` callable for Cowork to re-render
                after upgrading the analyst blocks
    _helpers  — internal `_num` / `_safe_get` shared by all of above
"""
