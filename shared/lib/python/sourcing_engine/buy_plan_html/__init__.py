"""09_buy_plan_html — buyer-report writer.

See docs/PRD-buyer-report.md.

Submodules:
    payload         — pure JSON payload builder (DataFrame → dict)
    renderer        — HTML skeleton emitter (dict → HTML with markers)
    template_prose  — deterministic per-verdict prose fallback
    prose_injector  — replaces <!-- prose:{asin} --> markers in HTML
"""
