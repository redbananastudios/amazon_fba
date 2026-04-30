"""Supplier leads step (Skill 99 v1).

Bridges the gap between "we should sell this ASIN" and "here's where to
source it" by generating Google search URLs per shortlisted ASIN.

Logic per `docs/PRD-sourcing-strategies.md` §8:

- For every row in the input DataFrame, render each configured search
  template against the row's `brand` / `product_name` / etc., URL-encode,
  prepend the search engine URL, and store as a new column.
- Templates with `skip_if_brand_missing: true` produce empty strings on
  rows where `brand` is empty (for OA / generic-product rows).
- A side-output markdown file is written if the run_step config supplies
  `output_md_path` — one section per row with all the URLs for an
  operator to click through.

This step does not transform the existing columns; it only appends new
columns. Side-output is opt-in via `config["output_md_path"]`.

Standalone CLI invocation:

    python -m fba_engine.steps.supplier_leads \\
        --csv-in fba_engine/data/strategies/.../shortlist.csv \\
        --md-out fba_engine/data/strategies/.../supplier_leads.md \\
        --niche kids-toys
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import yaml

from fba_engine.steps._helpers import atomic_write, coerce_str

# ────────────────────────────────────────────────────────────────────────
# Constants.
# ────────────────────────────────────────────────────────────────────────

# Path to the canonical YAML config — read by `run_step` when caller
# doesn't supply an override.
DEFAULT_SUPPLIER_LEADS_CONFIG_PATH: Path = (
    Path(__file__).resolve().parents[2]
    / "shared"
    / "config"
    / "supplier_leads.yaml"
)

# Output column names — pin these so downstream consumers (XLSX writer,
# CSV schema additions per PRD §11) can rely on the names.
SUPPLIER_SEARCH_COLUMNS: tuple[str, ...] = (
    "supplier_search_brand_distributor",
    "supplier_search_product_wholesale",
    "supplier_search_brand_trade",
)

# Row-field aliases for case-insensitive lookup. Phase-3 / Phase-5 frames
# use Title Case ("Brand", "Product Name", "Category", "ASIN"); the
# canonical engine uses snake_case ("brand", "product_name"). Tolerate both.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "brand": ("Brand", "brand"),
    "product_name": ("Product Name", "product_name", "Title", "title"),
    "asin": ("ASIN", "asin"),
    "category": ("Category", "category"),
}


# ────────────────────────────────────────────────────────────────────────
# Config types.
# ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SearchTemplate:
    id: str
    label: str
    template: str
    skip_if_brand_missing: bool = False

    @property
    def output_column(self) -> str:
        return f"supplier_search_{self.id}"


@dataclass(frozen=True)
class SupplierLeadsConfig:
    search_templates: list[SearchTemplate]
    search_engine_url: str


def load_supplier_leads_config(path: Path | str) -> SupplierLeadsConfig:
    """Read the YAML config and return a typed `SupplierLeadsConfig`."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    raw_templates = data.get("search_templates") or []
    templates = [
        SearchTemplate(
            id=str(t["id"]),
            label=str(t.get("label", t["id"])),
            template=str(t["template"]),
            skip_if_brand_missing=bool(t.get("skip_if_brand_missing", False)),
        )
        for t in raw_templates
    ]
    return SupplierLeadsConfig(
        search_templates=templates,
        search_engine_url=str(
            data.get("search_engine_url", "https://www.google.com/search?q=")
        ),
    )


# ────────────────────────────────────────────────────────────────────────
# Pure helpers.
# ────────────────────────────────────────────────────────────────────────


def _row_field(row: dict, name: str) -> str:
    """Look up a logical field on a row, tolerating multiple column-name spellings."""
    for alias in _FIELD_ALIASES.get(name, (name,)):
        if alias in row:
            value = coerce_str(row[alias])
            if value:
                return value
    return ""


def _render_search_url(
    template: SearchTemplate, row: dict, search_engine_url: str
) -> str:
    """Substitute the template against the row, URL-encode, prepend the engine URL.

    Returns "" when `skip_if_brand_missing=True` and the row's brand is empty.
    """
    if template.skip_if_brand_missing and not _row_field(row, "brand"):
        return ""

    substitutions = {
        "brand": _row_field(row, "brand"),
        "product_name": _row_field(row, "product_name"),
        "category": _row_field(row, "category"),
        "asin": _row_field(row, "asin"),
    }
    try:
        rendered = template.template.format(**substitutions)
    except KeyError as err:
        # Template references a placeholder we don't supply — skip rather
        # than crash, but document.
        return ""

    # If the rendered query is empty (e.g. product_name was missing), skip.
    if not rendered.strip():
        return ""

    return search_engine_url + quote_plus(rendered)


# ────────────────────────────────────────────────────────────────────────
# DataFrame entry point.
# ────────────────────────────────────────────────────────────────────────


def compute_supplier_leads(
    df: pd.DataFrame, config: SupplierLeadsConfig
) -> pd.DataFrame:
    """Append `supplier_search_*` columns to a copy of `df`.

    Pure: doesn't mutate the input. Empty input returns an empty frame
    with the new columns added.
    """
    out = df.copy()
    if df.empty:
        for tpl in config.search_templates:
            out[tpl.output_column] = pd.Series(dtype=object)
        return out

    rows = out.to_dict(orient="records")
    for tpl in config.search_templates:
        out[tpl.output_column] = [
            _render_search_url(tpl, row, config.search_engine_url) for row in rows
        ]
    return out


# ────────────────────────────────────────────────────────────────────────
# Markdown side-output.
# ────────────────────────────────────────────────────────────────────────


def build_supplier_leads_md(df: pd.DataFrame, niche: str | None) -> str:
    """Render the supplier leads as a single-page operator-friendly markdown.

    Each row gets a section with: ASIN heading, brand+category line,
    a bullet list of the populated search URLs (empties skipped), and
    Keepa + Amazon listing links.
    """
    niche_label = niche or "all"
    lines: list[str] = [
        f"# Supplier leads — {niche_label} — {date.today().isoformat()}",
        "",
    ]
    if df.empty:
        lines.append("_No shortlisted ASINs in this run._")
        return "\n".join(lines) + "\n"

    for _, row in df.iterrows():
        asin = coerce_str(row.get("ASIN") or row.get("asin"))
        brand = coerce_str(row.get("Brand") or row.get("brand"))
        product_name = coerce_str(
            row.get("Product Name") or row.get("product_name") or row.get("Title")
        )
        category = coerce_str(row.get("Category") or row.get("category"))

        title_line = f"## {asin}"
        if product_name:
            title_line += f" — {product_name}"
        lines.append(title_line)

        meta_parts = []
        if brand:
            meta_parts.append(f"Brand: {brand}")
        if category:
            meta_parts.append(f"Category: {category}")
        if meta_parts:
            lines.append(" | ".join(meta_parts))
        lines.append("")

        # Search URL bullets, skipping blanks.
        url_specs = (
            ("Brand distributor UK", "supplier_search_brand_distributor"),
            ("Product wholesale", "supplier_search_product_wholesale"),
            ("Brand trade account", "supplier_search_brand_trade"),
        )
        for label, col in url_specs:
            url = coerce_str(row.get(col, ""))
            if url:
                lines.append(f"- [{label}]({url})")

        # Always-present Keepa + Amazon links so the operator has the
        # source-of-truth references one click away.
        if asin:
            lines.append(f"- [Open Keepa chart](https://keepa.com/#!product/2-{asin})")
            lines.append(f"- [Open Amazon listing](https://www.amazon.co.uk/dp/{asin})")
        lines.append("")

    return "\n".join(lines) + "\n"


# ────────────────────────────────────────────────────────────────────────
# Step contract.
# ────────────────────────────────────────────────────────────────────────


def run_step(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Append supplier search columns; optionally write the markdown side-output.

    Config keys:
      - `config_path`: optional path to a non-canonical YAML config.
      - `output_md_path`: optional path; if set, write the markdown leads file.
      - `niche`: optional niche slug for the markdown header.
    """
    config_path = config.get("config_path") or DEFAULT_SUPPLIER_LEADS_CONFIG_PATH
    leads_config = load_supplier_leads_config(config_path)

    enriched = compute_supplier_leads(df, leads_config)

    output_md_path = config.get("output_md_path")
    if output_md_path:
        out_path = Path(output_md_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md = build_supplier_leads_md(enriched, niche=config.get("niche"))
        atomic_write(out_path, lambda p: p.write_text(md, encoding="utf-8"))

    return enriched


# ────────────────────────────────────────────────────────────────────────
# CLI.
# ────────────────────────────────────────────────────────────────────────


def run(csv_in: Path, md_out: Path | None, niche: str | None, csv_out: Path | None) -> None:
    df = pd.read_csv(
        csv_in, dtype=str, keep_default_na=False, encoding="utf-8-sig"
    )
    leads_config = load_supplier_leads_config(DEFAULT_SUPPLIER_LEADS_CONFIG_PATH)
    enriched = compute_supplier_leads(df, leads_config)

    if csv_out:
        csv_out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            csv_out,
            lambda p: enriched.to_csv(p, index=False, encoding="utf-8-sig"),
        )
        print(f"Saved: {csv_out}")

    if md_out:
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md = build_supplier_leads_md(enriched, niche=niche)
        atomic_write(md_out, lambda p: p.write_text(md, encoding="utf-8"))
        print(f"Saved: {md_out}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Supplier-leads step (Skill 99 v1) — appends Google search URLs "
            "per shortlisted ASIN and optionally writes a markdown side-file."
        )
    )
    parser.add_argument("--csv-in", required=True, type=Path)
    parser.add_argument("--csv-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--niche", default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    run(
        csv_in=args.csv_in,
        md_out=args.md_out,
        niche=args.niche,
        csv_out=args.csv_out,
    )
