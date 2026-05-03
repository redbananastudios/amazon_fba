"""09_buy_plan_html — buyer-report writer (v2).

See docs/PRD-buyer-report.md.

Submodules:
    payload   — pure JSON payload builder (DataFrame → dict)
    analyst   — deterministic fallback that produces the buyer's
                analyst block (verdict + score + 4-dim breakdown +
                trend story + narrative + action). Cowork
                orchestration replaces this with a Claude API call
                when wired.
    renderer  — verdict-led HTML emitter (dict → HTML)
    cli       — `render-from-json` callable for Cowork to re-render
                after upgrading the analyst blocks
"""
