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
        "--asin", default=None,
        help=(
            "Amazon ASIN — required by single-ASIN strategies (e.g. "
            "single_asin). Tags every output row and flows into "
            "output filenames via {asin} interpolation."
        ),
    )
    parser.add_argument(
        "--buy-cost", default=None, dest="buy_cost",
        help=(
            "Operator's buy cost in GBP for the wholesale/retail flow. "
            "Used by single_asin and any strategy that interpolates "
            "{buy_cost}. When omitted (or zero), the engine emits "
            "max_buy_price as the supplier-negotiation ceiling instead "
            "of literal ROI."
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
    if args.asin:
        ctx["asin"] = args.asin
        # `buy_cost` always seeded when an ASIN is supplied so strategy
        # YAML `{buy_cost}` interpolation resolves even when --buy-cost
        # is omitted. Empty string collapses to 0.0 (wholesale flow) at
        # the receiving step.
        ctx["buy_cost"] = args.buy_cost if args.buy_cost is not None else ""
    elif args.buy_cost is not None:
        # Threaded as a string — strategy YAML interpolation only
        # substitutes string values, and the receiving step parses the
        # float itself.
        ctx["buy_cost"] = args.buy_cost

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
    # Windows consoles default to cp1252 which can't encode the unicode
    # characters our verdict / score / reason strings carry (→, ►, £).
    # Reconfigure stdout/stderr to UTF-8 with replacement fallback so a
    # rogue character can never crash a verdict print mid-flow. Python
    # 3.7+ guarantees `reconfigure` on TextIOWrapper; guard anyway in
    # case stdout was redirected to something exotic.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    args = _parse_args(argv if argv is not None else sys.argv[1:])

    yaml_path = _resolve_strategy_yaml(args.strategy)
    recipe_data = _resolve_recipe_json(args.recipe)

    # Strategy-specific required-arg gates — fail loud at the CLI layer
    # rather than letting the runner's interpolation surface a less
    # helpful KeyError downstream.
    if args.strategy == "single_asin" and not args.asin:
        raise SystemExit(
            "single_asin strategy requires --asin. Example: "
            "python run.py --strategy single_asin --asin B0EXAMPLE1"
        )

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

    # Single-ASIN strategies want a human-readable verdict block in
    # stdout, not just a verdict count. Anything else (storefronts,
    # finder runs, supplier pricelists) gets the bulk-summary form.
    if args.strategy == "single_asin" and len(df) == 1:
        _print_single_asin_verdict(df.iloc[0], context)
        return 0

    # Run summary — operator-friendly counts.
    print(f"\nstrategy={args.strategy} recipe={args.recipe or '-'}")
    print(f"rows: {len(df)}")
    if "decision" in df.columns:
        for verdict in ("SHORTLIST", "REVIEW", "REJECT"):
            count = (df["decision"] == verdict).sum()
            print(f"  {verdict:10s}: {count}")
    print(f"output_dir: {context['output_dir']}")
    return 0


def _print_single_asin_verdict(row: Any, context: dict[str, Any]) -> None:
    """Pretty-print the single-ASIN verdict to stdout.

    Operator running ``run.py --strategy single_asin --asin B0XXX...`` gets
    the answer in the terminal, not in a CSV they have to open. The CSV
    is still written (audit trail) but the primary deliverable is the
    block of text printed here.

    Layout:
      - Decision line (SHORTLIST / REVIEW / REJECT) with reason
      - Market snapshot (Buy Box current vs avg90, sales, sellers)
      - Gating + restriction links
      - Economics (profit/ROI or max_buy_price for wholesale flow)
      - Risk flags
    """
    import math

    def _is_missing(v) -> bool:
        # Treat None and NaN as missing — Pandas DataFrames produce one
        # or the other depending on dtype after a CSV round-trip.
        if v is None:
            return True
        try:
            return math.isnan(float(v))
        except (TypeError, ValueError):
            return False

    def _fmt(v, prefix="GBP ", default="-"):
        if _is_missing(v):
            return default
        try:
            return f"{prefix}{float(v):.2f}"
        except (TypeError, ValueError):
            return str(v) if v else default

    def _fmt_int(v, default="-"):
        if _is_missing(v):
            return default
        try:
            return f"{int(v)}"
        except (TypeError, ValueError):
            return str(v) if v else default

    decision = row.get("decision") or "-"
    reason = row.get("decision_reason") or "-"
    asin = row.get("asin") or "-"

    # Final operator-facing verdict (07_validate_opportunity, PR #58).
    # When present, this is the "is this worth acting on NOW?" answer
    # — leads the printout. Falls back to the decide-step verdict for
    # legacy callers / strategies that haven't wired the new step.
    opp_verdict = row.get("opportunity_verdict")
    opp_score = row.get("opportunity_score")
    opp_conf = row.get("opportunity_confidence")
    next_action = row.get("next_action")

    bb_current = row.get("buy_box_price")
    bb_avg90 = row.get("buy_box_avg90")
    delta_pct = None
    if not _is_missing(bb_current) and not _is_missing(bb_avg90) and bb_avg90 > 0:
        delta_pct = (bb_current - bb_avg90) / bb_avg90 * 100

    print()
    print("=" * 72)
    if opp_verdict and not _is_missing(opp_verdict):
        score_part = (
            f"   score {int(opp_score)}/100"
            if opp_score is not None and not _is_missing(opp_score) else ""
        )
        conf_part = f"   ({opp_conf} confidence)" if opp_conf else ""
        print(f"VERDICT: {opp_verdict}{conf_part}{score_part}")
        print(f"ASIN:    {asin}")
        print("=" * 72)
        if next_action:
            print(f"  >> {next_action}")
        print(f"  Decision (engine):     {decision} - {reason}")
    else:
        print(f"VERDICT: {decision}   ASIN: {asin}")
        print("=" * 72)
        print(f"  {reason}")
    print()

    print("Market:")
    print(f"  Buy Box (current):     {_fmt(bb_current)}")
    print(f"  Buy Box (90d avg):     {_fmt(bb_avg90)}"
          f"   delta: {f'{delta_pct:+.1f}%' if delta_pct is not None else '-'}")
    print(f"  3rd-party FBA:         {_fmt(row.get('new_fba_price'))}")
    # Amazon price — shown always so the operator sees what's actually
    # happening on the listing even when Buy Box / FBA stats are sparse.
    # When Amazon is the only price the engine had to work with, the
    # AMAZON_ONLY_PRICE flag on the row tells the operator the
    # economics ride on this number rather than the more representative
    # Buy Box / lowest-FBA reading.
    print(f"  Amazon (current):      {_fmt(row.get('amazon_price'))}")
    print(f"  FBA seller count:      {_fmt_int(row.get('fba_seller_count'))}")
    print(f"  Sales/month (Keepa):   {_fmt_int(row.get('sales_estimate'))}")
    print(f"  Amazon status:         {row.get('amazon_status') or '-'}")

    # When Keepa stats arrays return -1 sentinels for all current price
    # series, market_snapshot() yields all-None and the engine REJECTs
    # with "No valid market price". Operator should know this is a Keepa
    # data-availability issue, not a malformed ASIN.
    if (_is_missing(bb_current) and _is_missing(row.get("new_fba_price"))
            and _is_missing(row.get("amazon_price"))):
        print()
        print("  NOTE: Keepa's stats arrays show no current FBA / Amazon /")
        print("  Buy Box offer for this ASIN. Common reasons: listing is")
        print("  currently OOS across all sellers, or Keepa has not yet")
        print("  profiled it (low historical traffic). Try again later or")
        print("  use the seller_storefront_csv path which reads richer")
        print("  history columns from the Keepa Browser CSV export.")
    print()

    print("Gating (SP-API):")
    print(f"  Status:                {row.get('restriction_status') or '-'}")
    print(f"  Gated:                 {row.get('gated') or '-'}")
    if row.get("restriction_links"):
        # First link only - most gated rows have one ungate URL.
        first_link = str(row["restriction_links"]).split(";", 1)[0].strip()
        print(f"  Ungate link:           {first_link}")
    print()

    print("Economics:")
    buy_cost = row.get("buy_cost") or 0.0
    if float(buy_cost) > 0:
        print(f"  Buy cost (operator):   {_fmt(buy_cost)}")
        print(f"  Profit (current):      {_fmt(row.get('profit_current'))}")
        print(f"  Profit (conservative): {_fmt(row.get('profit_conservative'))}")
        roi_c = row.get("roi_current")
        roi_cs = row.get("roi_conservative")
        if roi_c is not None:
            print(f"  ROI (current):         {float(roi_c) * 100:.1f}%")
        if roi_cs is not None:
            print(f"  ROI (conservative):    {float(roi_cs) * 100:.1f}%")
    else:
        print(f"  Buy cost:              not provided (wholesale flow)")
        print(f"  Max buy price:         {_fmt(row.get('max_buy_price'))}"
              f"   <- supplier-negotiation ceiling")
    print()

    risk = row.get("risk_flags")
    # risk_flags can come back as a list, a stringified list, or empty
    # string depending on whether the row went through DataFrame round-
    # trip or stayed in-memory. Normalise to display string either way.
    if isinstance(risk, list) and risk:
        print(f"Risk flags:  {', '.join(risk)}")
    elif isinstance(risk, str) and risk and risk != "[]":
        print(f"Risk flags:  {risk}")

    # Opportunity-validator notes — surface the blockers (why isn't
    # this a BUY?) and a short reasons line. Same list-or-string
    # normalisation as risk_flags.
    blockers = row.get("opportunity_blockers")
    if isinstance(blockers, list) and blockers:
        print(f"Blockers:    {'; '.join(blockers)}")
    elif isinstance(blockers, str) and blockers and blockers not in ("[]", ""):
        print(f"Blockers:    {blockers}")
    reasons = row.get("opportunity_reasons")
    if isinstance(reasons, list) and reasons:
        print(f"Why:         {'; '.join(reasons)}")
    elif isinstance(reasons, str) and reasons and reasons not in ("[]", ""):
        print(f"Why:         {reasons}")
    print()

    # Action-oriented next step — operator-facing summary of what to
    # actually do with this verdict. Engine produces a verdict from the
    # data; the operator's loop is verdict → supplier outreach → real
    # buy_cost → re-run with --buy-cost for the ROI verdict.
    max_buy = row.get("max_buy_price")
    print("Next step:")
    if decision == "SHORTLIST":
        print(f"  PURSUE - passes all gates at conservative price.")
        if not _is_missing(max_buy):
            print(f"  Negotiate with supplier; ceiling is {_fmt(max_buy)} for 30% ROI.")
    elif decision == "REVIEW":
        if float(buy_cost) == 0 and not _is_missing(max_buy):
            print(f"  WORTH A SUPPLIER ASK - data signal is positive but needs cost.")
            print(f"  Go research a supplier; ceiling is {_fmt(max_buy)} for 30% ROI.")
            print(f"  Re-run with --buy-cost <X> when supplier responds for ROI verdict.")
        else:
            print(f"  REVIEW - needs operator judgment; check the reason above.")
    elif decision == "REJECT":
        if "No valid market price" in (reason or ""):
            print(f"  WAIT - Keepa lacks current price data for this ASIN.")
            print(f"  Try again later, or run via seller_storefront_csv path")
            print(f"  if this ASIN appears in a competitor's exported storefront.")
        else:
            print(f"  SKIP - engine rejected based on the data above.")
    print()
    print(f"Audit CSV: {context['output_dir']}")


if __name__ == "__main__":
    sys.exit(main())
