"""CLI for the buyer-report renderer.

Used by Cowork orchestration to re-render an HTML report after the
analyst step has populated each row's `analyst` block in the JSON.

Invocation:

    python -m sourcing_engine.buy_plan_html.cli render-from-json \
        path/to/buyer_report_<ts>.json \
        path/to/buyer_report_<ts>.html

The HTML is overwritten atomically (tmp + rename). Idempotent —
running again with unchanged JSON produces byte-identical HTML.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sourcing_engine.buy_plan_html.renderer import render_html


def _render_from_json(json_path: Path, html_path: Path) -> int:
    """Read JSON payload, render HTML, atomic-write to html_path."""
    if not json_path.exists():
        print(f"ERROR: JSON not found: {json_path}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: malformed JSON: {e}", file=sys.stderr)
        return 2
    html = render_html(payload)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = html_path.with_suffix(".html.tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(html_path)
    rows = payload.get("rows") or []
    # ASCII-only output so Windows cp1252 consoles don't trip on →.
    print(f"Rendered {len(rows)} rows -> {html_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sourcing_engine.buy_plan_html.cli",
        description="Buyer-report renderer CLI (Cowork-callable).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    rj = sub.add_parser(
        "render-from-json",
        help="Render an HTML buyer-report from a populated JSON payload.",
    )
    rj.add_argument("json_path", type=Path, help="Path to buyer_report_<ts>.json")
    rj.add_argument("html_path", type=Path, help="Path to write buyer_report_<ts>.html")
    args = parser.parse_args(argv)
    if args.cmd == "render-from-json":
        return _render_from_json(args.json_path, args.html_path)
    return 1


if __name__ == "__main__":
    sys.exit(main())
