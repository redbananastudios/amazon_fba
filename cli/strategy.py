"""`--strategy` subcommand — dispatch a named strategy via the runner.

Resolves a strategy YAML by name from ``fba_engine/strategies/``,
optionally loads a recipe JSON from
``fba_engine/_legacy_keepa/skills/keepa-product-finder/recipes/`` to
fill calculate/decide config knobs, builds the runner context, and
invokes ``fba_engine.strategies.runner.run_strategy``.

Usage:

    # The Cowork-friendly form (what orchestration/runs/keepa_finder.yaml
    # dispatches):
    python run.py --strategy keepa_finder \
        --csv ./output/2026-05-02/keepa_amazon_oos.csv \
        --recipe amazon_oos_wholesale \
        --output-dir ./output/2026-05-02

    # Manual entry — same args, run from the terminal during development.

The recipe JSON contributes:
  - ``calculate_config`` — forwarded to the calculate step's config
    (e.g. ``{"compute_stability_score": true}`` for amazon_oos /
    stable_price recipes)
  - ``decide_overrides`` — forwarded to the decide step's config under
    the ``overrides`` key (e.g. ``{"min_sales_shortlist": 5}`` for
    no_rank_hidden_gem)

If no recipe matches the ``--recipe`` arg or the recipe JSON is
malformed, the dispatcher logs a warning and continues with engine
defaults — so a recipe-name typo doesn't kill the run, but the
operator sees the issue. Strict-mode (fail on missing recipe) can be
added later if Cowork wants it.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Path resolution.
# ────────────────────────────────────────────────────────────────────────

# Repo root — strategies live under <repo>/fba_engine/strategies/, recipes
# under <repo>/fba_engine/_legacy_keepa/skills/keepa-product-finder/recipes/.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_STRATEGIES_DIR = _REPO_ROOT / "fba_engine" / "strategies"
_RECIPES_DIR = (
    _REPO_ROOT / "fba_engine" / "_legacy_keepa" / "skills"
    / "keepa-product-finder" / "recipes"
)

# Strategy / recipe identifiers MUST match this pattern. Restricting the
# character class to letters / digits / underscore / hyphen (with no dots
# or slashes) blocks path traversal: a value like "../../etc/passwd" is
# rejected before it reaches Path joining. Belt-and-braces below also
# verifies the resolved path stays inside the parent dir.
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_name(name: str, kind: str) -> None:
    """Reject any name that could escape its parent directory.

    Raises ``SystemExit`` (loud, not silent) so a typo or a malicious
    invocation surfaces immediately rather than reading an unexpected file.
    """
    if not _NAME_RE.fullmatch(name):
        raise SystemExit(
            f"Invalid {kind} name {name!r}. Names must match {_NAME_RE.pattern} "
            f"(letters, digits, underscore, hyphen) — no path separators."
        )


def _resolve_strategy_yaml(name: str) -> Path:
    """Resolve a strategy name to its YAML path. Raises if missing."""
    _validate_name(name, "strategy")
    p = _STRATEGIES_DIR / f"{name}.yaml"
    # Defence-in-depth: even with the regex above, confirm the resolved
    # path stays inside the strategies directory. Catches symlinks
    # planted by an attacker who controls part of the filesystem.
    resolved = p.resolve()
    strategies_resolved = _STRATEGIES_DIR.resolve()
    if not str(resolved).startswith(str(strategies_resolved)):
        raise SystemExit(
            f"Strategy path {resolved} resolves outside {strategies_resolved}"
        )
    if not p.exists():
        available = sorted(
            f.stem for f in _STRATEGIES_DIR.glob("*.yaml")
            if f.is_file() and not f.stem.startswith("_")
        )
        raise SystemExit(
            f"Strategy {name!r} not found at {p}. "
            f"Available: {', '.join(available) if available else '(none)'}"
        )
    return p


def _resolve_recipe_json(name: str | None) -> dict[str, Any]:
    """Load a recipe JSON if --recipe is supplied; return {} otherwise."""
    if not name:
        return {}
    _validate_name(name, "recipe")
    p = _RECIPES_DIR / f"{name}.json"
    resolved = p.resolve()
    recipes_resolved = _RECIPES_DIR.resolve()
    if not str(resolved).startswith(str(recipes_resolved)):
        raise SystemExit(
            f"Recipe path {resolved} resolves outside {recipes_resolved}"
        )
    if not p.exists():
        logger.warning(
            "recipe %r not found at %s — running with engine defaults", name, p,
        )
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning(
            "recipe %r at %s is malformed (%s) — running with engine defaults",
            name, p, e,
        )
        return {}


# ────────────────────────────────────────────────────────────────────────
# Recipe → runner-context translation.
# ────────────────────────────────────────────────────────────────────────


def _apply_recipe_to_strategy(strat, recipe_data: dict[str, Any]) -> None:
    """Mutate the loaded StrategyDef so calculate/decide steps pick up
    recipe-specific config knobs.

    Mutating the loaded config (rather than going through the YAML
    interpolation layer) keeps the YAML free of recipe-specific syntax —
    each strategy YAML stays generic, recipes drive the differences.

    Logs a WARNING (loud but non-fatal) if the recipe declares a knob
    but no step in the strategy can receive it — surfaces a strategy /
    recipe mismatch instead of silently dropping config.
    """
    calc_cfg = recipe_data.get("calculate_config") or {}
    decide_overrides = recipe_data.get("decide_overrides") or {}

    if not calc_cfg and not decide_overrides:
        return

    calc_applied = False
    decide_applied = False
    for step in strat.steps:
        if step.name == "calculate" and calc_cfg:
            step.config.update(calc_cfg)
            calc_applied = True
        elif step.name == "decide" and decide_overrides:
            # decide.run_step reads config["overrides"] — wrap the dict
            # one level deep so the contract matches.
            step.config["overrides"] = dict(decide_overrides)
            decide_applied = True

    if calc_cfg and not calc_applied:
        logger.warning(
            "recipe %r declares calculate_config %r but strategy %r has no "
            "'calculate' step — config dropped",
            recipe_data.get("name", "?"), calc_cfg, strat.name,
        )
    if decide_overrides and not decide_applied:
        logger.warning(
            "recipe %r declares decide_overrides %r but strategy %r has no "
            "'decide' step — config dropped",
            recipe_data.get("name", "?"), decide_overrides, strat.name,
        )


# ────────────────────────────────────────────────────────────────────────
# CLI.
# ────────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run.py --strategy",
        description=(
            "Dispatch a named strategy through the engine runner. "
            "Pairs with the upstream Keepa Product Finder skill — see "
            "orchestration/runs/keepa_finder.yaml for the Cowork pattern."
        ),
    )
    parser.add_argument(
        "--strategy", required=True,
        help="Strategy YAML name (sans .yaml). e.g. keepa_finder",
    )
    parser.add_argument(
        "--csv", default=None,
        help=(
            "Path to the discovery CSV (Keepa Product Finder export, OA "
            "feed, etc.). Required for strategies that ingest a CSV; "
            "ignored otherwise."
        ),
    )
    parser.add_argument(
        "--recipe", default=None,
        help=(
            "Recipe id — selects a JSON in "
            "fba_engine/_legacy_keepa/skills/keepa-product-finder/recipes/. "
            "Forwards calculate_config + decide_overrides to the engine. "
            "Also tags discovery_strategy on every output row."
        ),
    )
    parser.add_argument(
        "--seller-id", default=None, dest="seller_id",
        help=(
            "Amazon merchant ID — required by storefront-walking "
            "strategies (e.g. seller_storefront_csv). Tags every "
            "output row's seller_id column and flows into output "
            "filenames + Sheet titles via {seller_id} interpolation."
        ),
    )
    parser.add_argument(
        "--output-dir", default=None, dest="output_dir",
        help=(
            "Directory for run artifacts (canonical CSV, supplier_leads.md, "
            "metadata sidecar). Default: ./output/{timestamp}/"
        ),
    )
    parser.add_argument(
        "--timestamp", default=None,
        help=(
            "Run identifier — used in output filenames and as part of "
            "the default output dir. Default: UTC ISO timestamp."
        ),
    )
    parser.add_argument(
        "--context", action="append", default=[],
        metavar="KEY=VALUE",
        help=(
            "Additional context keys forwarded to the runner. Repeatable. "
            "Useful for strategy YAMLs that interpolate non-standard vars."
        ),
    )
    return parser.parse_args(argv)


def _build_context(args: argparse.Namespace) -> dict[str, str]:
    """Compose the runner context from argparse + reasonable defaults.

    Default timestamp uses UTC ISO compact format ``YYYYmmdd_HHMMSS`` to
    match existing output filename conventions (oa_decisions_*,
    keepa_finder_*) and to sort lexicographically.
    """
    timestamp = args.timestamp or datetime.now(timezone.utc).strftime(
        "%Y%m%d_%H%M%S"
    )
    output_dir = args.output_dir or f"./output/{timestamp}"

    ctx: dict[str, str] = {
        "timestamp": timestamp,
        "output_dir": output_dir,
    }
    if args.csv:
        ctx["csv_path"] = args.csv
    if args.recipe:
        ctx["recipe"] = args.recipe
    if args.seller_id:
        ctx["seller_id"] = args.seller_id

    # Extra context pairs from --context k=v (each occurrence overrides
    # auto-resolved defaults — last wins).
    for raw in args.context:
        if "=" not in raw:
            raise SystemExit(f"--context expects KEY=VALUE, got {raw!r}")
        k, v = raw.split("=", 1)
        ctx[k.strip()] = v

    return ctx


def main(argv: list[str] | None = None) -> int:
    """Entry point invoked by run.py when ``--strategy`` is in argv."""
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    yaml_path = _resolve_strategy_yaml(args.strategy)
    recipe_data = _resolve_recipe_json(args.recipe)
    context = _build_context(args)

    # Lazy-imported so cli.strategy is cheap to import (the module is
    # loaded for every `python run.py` invocation via the dispatcher;
    # only --strategy runs need the full runner stack).
    from fba_engine.strategies.runner import load_strategy, run_strategy

    strat = load_strategy(yaml_path)
    _apply_recipe_to_strategy(strat, recipe_data)

    # Make sure the output dir exists — the runner's atomic_write
    # handles the file-level "create parent dir" but not the run dir
    # for the first artifact. Idempotent.
    Path(context["output_dir"]).mkdir(parents=True, exist_ok=True)

    df = run_strategy(strat, context=context, df_in=None)

    # Run summary — operator-friendly counts.
    print(f"\nstrategy={args.strategy} recipe={args.recipe or '-'}")
    print(f"rows: {len(df)}")
    if "decision" in df.columns:
        for verdict in ("SHORTLIST", "REVIEW", "REJECT"):
            count = (df["decision"] == verdict).sum()
            print(f"  {verdict:10s}: {count}")
    print(f"output_dir: {context['output_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
