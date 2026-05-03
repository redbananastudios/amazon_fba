"""YAML strategy runner.

Loads a strategy YAML describing an ordered chain of pipeline steps
(modules under `fba_engine.steps.*`), then pipes a DataFrame through each
step's `run_step(df, config) -> df` contract. Variable substitution
(``{niche}``, ``{base}``, etc.) lets the same strategy file run across
niches without per-niche copies.

The contract every step exposes is:

    def run_step(df: pd.DataFrame, config: dict) -> pd.DataFrame: ...

The runner does **not** know which steps emit side-outputs (XLSX files,
GSheets uploads, supplier-skeleton CSVs). Steps that need to write side-
outputs read the relevant paths from the `config` dict — the runner just
passes through whatever the YAML specified, with `{name}` interpolation
applied to string values.

When ``output.csv`` is set in the YAML, the runner also writes a
``run_summary.json`` sibling file capturing per-step row counts +
durations + the final output paths. This is intended for downstream
observability (operators tailing a strategies-run log, Cowork
orchestration that wants to know if a step is silently dropping rows).

Standalone CLI invocation:

    python -m fba_engine.strategies.runner \\
        --strategy fba_engine/strategies/keepa_niche.yaml \\
        --context niche=kids-toys base=fba_engine/data/niches/kids-toys
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import pandas as pd
import yaml

from fba_engine.steps._helpers import atomic_write


# ────────────────────────────────────────────────────────────────────────
# Errors.
# ────────────────────────────────────────────────────────────────────────


class StrategyConfigError(ValueError):
    """The strategy YAML is malformed, references a missing module, or is
    missing a required configuration key."""


class StrategyExecutionError(RuntimeError):
    """A step's `run_step` raised. The wrapped exception is preserved as
    the cause; the message identifies which step failed."""


# ────────────────────────────────────────────────────────────────────────
# Definitions.
# ────────────────────────────────────────────────────────────────────────


@dataclass
class StepDef:
    """One step in a strategy chain."""

    name: str            # logical name shown in errors (e.g. "ip_risk")
    module: str          # importable module path (e.g. "fba_engine.steps.ip_risk")
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyDef:
    """A loaded strategy definition. Immutable after `load_strategy`."""

    name: str
    description: str
    steps: list[StepDef]
    input_path: str | None = None       # interpolation-friendly
    input_encoding: str = "utf-8-sig"
    input_discover: bool = False        # first step creates the DataFrame
    output_csv: str | None = None       # interpolation-friendly
    output_xlsx: str | None = None      # interpolation-friendly; styled
                                         # workbook via excel_writer (URL
                                         # cells are clickable hyperlinks)
    output_gsheet: dict[str, Any] | None = None  # {title, folder_id?, id_file?}
                                                  # uploads the XLSX to Google
                                                  # Drive as a Sheet via the
                                                  # legacy push_to_gsheets.js
                                                  # script. Requires a working
                                                  # google-service-account.json.


# ────────────────────────────────────────────────────────────────────────
# Variable interpolation.
# ────────────────────────────────────────────────────────────────────────


def interpolate(value: Any, context: dict[str, Any]) -> Any:
    """Substitute ``{name}`` placeholders from `context` into a string value.

    Non-strings pass through unchanged. Missing context keys raise
    ``StrategyConfigError`` with the offending key in the message — silent
    pass-through would leave literal ``{niche}`` in output paths.
    """
    if not isinstance(value, str):
        return value
    try:
        return value.format(**context)
    except KeyError as err:
        missing = err.args[0] if err.args else "?"
        raise StrategyConfigError(
            f"interpolation: missing context key '{missing}' in value {value!r}"
        ) from err


def _interpolate_config(config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Apply `interpolate` to every value in a step config.

    **Note**: substitution is one level deep. Nested mappings or lists pass
    through unchanged. If a future step needs interpolation inside a list
    of paths or a sub-dict, extend this helper to recurse — but assert at
    the YAML load step so a typo doesn't silently leave `{niche}` in a
    nested value.
    """
    return {k: interpolate(v, context) for k, v in config.items()}


# ────────────────────────────────────────────────────────────────────────
# YAML loading.
# ────────────────────────────────────────────────────────────────────────


def load_strategy(yaml_path: Path | str) -> StrategyDef:
    """Read a strategy YAML and return a `StrategyDef`. Validates required keys.

    Raises FileNotFoundError if the file is missing, StrategyConfigError if
    the YAML is structurally invalid.
    """
    yaml_path = Path(yaml_path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"strategy YAML not found: {yaml_path}")

    with yaml_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    if not isinstance(data, dict):
        raise StrategyConfigError(
            f"{yaml_path}: top-level YAML must be a mapping, got {type(data).__name__}"
        )

    if "name" not in data:
        raise StrategyConfigError(f"{yaml_path}: required field 'name' missing")
    if "steps" not in data:
        raise StrategyConfigError(f"{yaml_path}: required field 'steps' missing")

    raw_steps = data["steps"] or []
    if not isinstance(raw_steps, list):
        raise StrategyConfigError(
            f"{yaml_path}: 'steps' must be a list, got {type(raw_steps).__name__}"
        )

    steps: list[StepDef] = []
    for i, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise StrategyConfigError(
                f"{yaml_path}: step #{i} must be a mapping, got {type(raw).__name__}"
            )
        if "name" not in raw:
            raise StrategyConfigError(
                f"{yaml_path}: step #{i} missing required field 'name'"
            )
        if "module" not in raw:
            raise StrategyConfigError(
                f"{yaml_path}: step '{raw['name']}' missing required field 'module'"
            )
        steps.append(
            StepDef(
                name=str(raw["name"]),
                module=str(raw["module"]),
                config=dict(raw.get("config") or {}),
            )
        )

    input_block = data.get("input") or {}
    output_block = data.get("output") or {}

    # `discover` must be a YAML boolean (`true` / `false`), not a
    # quoted string — `bool("false")` is True, which silently flips
    # the wrong contract. Reject anything that isn't a real bool so
    # the YAML author sees the typo at load time.
    discover_raw = input_block.get("discover", False)
    if not isinstance(discover_raw, bool):
        raise StrategyConfigError(
            f"{yaml_path}: input.discover must be a YAML boolean "
            f"(true/false), got {type(discover_raw).__name__}: "
            f"{discover_raw!r}"
        )

    # output.gsheet: structured block (not a path string) — must be a
    # mapping with at least `title`. Optional: `folder_id`, `id_file`.
    # We accept None / missing entirely; reject any other shape so a
    # malformed YAML fails at load, not when the runner tries to
    # subprocess-dispatch with garbage args.
    gsheet_raw = output_block.get("gsheet")
    gsheet_cfg: dict[str, Any] | None = None
    if gsheet_raw is not None:
        if not isinstance(gsheet_raw, dict):
            raise StrategyConfigError(
                f"{yaml_path}: output.gsheet must be a mapping with at "
                f"least `title`, got {type(gsheet_raw).__name__}"
            )
        if "title" not in gsheet_raw:
            raise StrategyConfigError(
                f"{yaml_path}: output.gsheet requires a `title` field"
            )
        gsheet_cfg = {
            "title": str(gsheet_raw["title"]),
            "folder_id": gsheet_raw.get("folder_id"),
            "id_file": gsheet_raw.get("id_file"),
        }

    return StrategyDef(
        name=str(data["name"]),
        description=str(data.get("description", "")),
        input_path=input_block.get("path"),
        input_encoding=str(input_block.get("encoding", "utf-8-sig")),
        input_discover=discover_raw,
        steps=steps,
        output_csv=output_block.get("csv"),
        output_xlsx=output_block.get("xlsx"),
        output_gsheet=gsheet_cfg,
    )


# ────────────────────────────────────────────────────────────────────────
# Execution.
# ────────────────────────────────────────────────────────────────────────


def _load_step_module(step: StepDef):
    """Import a step module and verify it exposes a callable `run_step`."""
    try:
        module = importlib.import_module(step.module)
    except ModuleNotFoundError as err:
        raise StrategyConfigError(
            f"step '{step.name}': cannot import module '{step.module}': {err}"
        ) from err
    run_step = getattr(module, "run_step", None)
    if not callable(run_step):
        raise StrategyConfigError(
            f"step '{step.name}': module '{step.module}' has no callable "
            f"run_step() (got {type(run_step).__name__})"
        )
    return module


def run_strategy(
    strategy: StrategyDef,
    context: dict[str, Any],
    df_in: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Execute the strategy. Returns the DataFrame after the final step.

    If `df_in` is None, the runner reads the strategy's input_path
    (interpolated against `context`). If both are None, raises
    StrategyConfigError.

    Each step's config has its string values interpolated before the step
    runs — so `config: {niche: "{niche}"}` resolves to whatever
    `context["niche"]` is at runtime.

    The final DataFrame is also written to `strategy.output_csv` (also
    interpolated) if that key is set, alongside a ``run_summary.json``
    capturing per-step row counts, durations, and output paths.
    """
    started_at_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    started_at_mono = time.monotonic()

    # Seed universally-applicable defaults so callers don't have to
    # know every step's interpolation knobs. Copy first so we never
    # mutate the caller's context dict.
    context = {**context}
    context.setdefault("order_mode", "first")

    df = _resolve_input_df(strategy, context, df_in)
    initial_rows = len(df)

    step_summaries: list[dict[str, Any]] = []

    for step in strategy.steps:
        module = _load_step_module(step)
        resolved_config = _interpolate_config(step.config, context)
        rows_in = len(df)
        step_start = time.monotonic()
        try:
            df = module.run_step(df, resolved_config)
        except Exception as err:
            duration = round(time.monotonic() - step_start, 4)
            step_summaries.append({
                "name": step.name,
                "module": step.module,
                "rows_in": rows_in,
                "rows_out": None,
                "duration_seconds": duration,
                "error": f"{type(err).__name__}: {err}",
            })
            raise StrategyExecutionError(
                f"step '{step.name}' ({step.module}) failed: "
                f"{type(err).__name__}: {err}"
            ) from err
        step_summaries.append({
            "name": step.name,
            "module": step.module,
            "rows_in": rows_in,
            "rows_out": len(df),
            "duration_seconds": round(time.monotonic() - step_start, 4),
        })

    outputs: dict[str, str] = {}

    if strategy.output_csv:
        out_path = Path(interpolate(strategy.output_csv, context))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic tmp+rename — matches the convention used by build_output's
        # CSV writes so a crash mid-write doesn't leave a partial file for
        # downstream consumers (chained strategies, scheduled re-runs).
        atomic_write(
            out_path,
            lambda p: df.to_csv(p, index=False, encoding="utf-8-sig"),
        )
        outputs["csv"] = str(out_path)

    if strategy.output_xlsx:
        # Styled XLSX via excel_writer — URL-format cells become
        # one-click hyperlinks (Amazon URL, Ungate Links). Auto-excludes
        # REJECT rows so the operator's working set is REVIEW + SHORTLIST
        # only. Lazy-imported because the writer pulls openpyxl which
        # tests / cli paths that only need CSV shouldn't have to load.
        from sourcing_engine.output.excel_writer import write_excel

        xlsx_path = Path(interpolate(strategy.output_xlsx, context))
        xlsx_path.parent.mkdir(parents=True, exist_ok=True)
        # write_excel handles its own try/except; passes through silent
        # on failure with a logged exception (so a bad sheet doesn't
        # blow up the whole run after CSV already wrote).
        write_excel(df, str(xlsx_path))
        if xlsx_path.exists():
            outputs["xlsx"] = str(xlsx_path)
        else:
            logger.warning(
                "runner: xlsx output declared at %s but file not written "
                "(see excel_writer logs for the underlying error)",
                xlsx_path,
            )

    if strategy.output_gsheet and outputs.get("xlsx"):
        # Upload the XLSX to Google Drive as a Sheet via the legacy
        # push_to_gsheets.js script. Requires:
        #   - node on PATH
        #   - googleapis npm package installed (run `npm install` in
        #     fba_engine/_legacy_keepa/)
        #   - service account key at
        #     fba_engine/_legacy_keepa/config/google-service-account.json
        # Silent skip when any prerequisite is missing — matches the
        # preflight contract: an unconfigured environment shouldn't
        # block CSV/XLSX from landing.
        url = _push_xlsx_to_gsheet(
            xlsx_path=outputs["xlsx"],
            gsheet_cfg=strategy.output_gsheet,
            context=context,
        )
        if url:
            outputs["gsheet_url"] = url

    if strategy.output_csv:
        # Write the run_summary.json sibling. Naming: <csv-stem>.summary.json
        # so multiple strategies sharing a parent directory don't clash.
        summary_path = out_path.with_suffix(".summary.json")
        summary = {
            "strategy": strategy.name,
            "context": context,
            "started_at": started_at_iso,
            "completed_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "duration_seconds": round(time.monotonic() - started_at_mono, 4),
            "initial_rows": initial_rows,
            "final_rows": len(df),
            "step_summary": step_summaries,
            "outputs": {**outputs, "summary_json": str(summary_path)},
        }
        atomic_write(
            summary_path,
            lambda p: p.write_text(
                json.dumps(summary, indent=2, default=str),
                encoding="utf-8",
            ),
        )

    return df


def _push_xlsx_to_gsheet(
    xlsx_path: str,
    gsheet_cfg: dict[str, Any],
    context: dict[str, Any],
) -> str | None:
    """Upload an XLSX to Google Drive as a Sheet via push_to_gsheets.js.

    Returns the Sheet URL on success; ``None`` when prerequisites aren't
    met (no node, no googleapis package, no service account key, or
    upload fails). Silent-skip on prerequisite-missing matches the
    preflight contract — an unconfigured env shouldn't block the file
    outputs already on disk.

    Hard-fail (raises) only if the gsheet config itself is malformed
    after interpolation — that's a YAML bug, not an env issue.
    """
    import shutil
    import subprocess

    # Locate the script + service account key + node binary.
    repo_root = Path(__file__).resolve().parents[2]
    script_path = (
        repo_root / "fba_engine" / "_legacy_keepa" / "skills"
        / "skill-5-build-output" / "push_to_gsheets.js"
    )
    key_path = (
        repo_root / "fba_engine" / "_legacy_keepa" / "config"
        / "google-service-account.json"
    )

    if shutil.which("node") is None:
        logger.info("gsheet: skipping (node executable not on PATH)")
        return None
    if not script_path.exists():
        logger.info("gsheet: skipping (push_to_gsheets.js not at %s)", script_path)
        return None
    if not key_path.exists():
        logger.info("gsheet: skipping (service account key not at %s)", key_path)
        return None

    # Interpolate the title + id_file. folder_id is passed through —
    # the script falls back to GOOGLE_DRIVE_FOLDER_ID env var when None.
    title = interpolate(gsheet_cfg["title"], context)
    id_file = gsheet_cfg.get("id_file")
    if id_file:
        id_file = interpolate(id_file, context)
    folder_id = gsheet_cfg.get("folder_id")

    cmd = [
        "node", str(script_path),
        "--xlsx", str(xlsx_path),
        "--title", title,
    ]
    if id_file:
        cmd += ["--id-file", str(id_file)]
    if folder_id:
        cmd += ["--folder", str(folder_id)]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("gsheet: subprocess failed (%s)", e)
        return None

    if result.returncode != 0:
        logger.warning(
            "gsheet: push_to_gsheets.js exited %d. stderr: %s",
            result.returncode, result.stderr.strip()[:500],
        )
        return None

    # The script prints `URL: https://...` on success. Find that line.
    for line in (result.stdout or "").splitlines():
        if line.startswith("URL:"):
            return line[len("URL:"):].strip()
    logger.warning(
        "gsheet: push_to_gsheets.js did not emit a URL line. stdout: %s",
        (result.stdout or "").strip()[:500],
    )
    return None


def _resolve_input_df(
    strategy: StrategyDef,
    context: dict[str, Any],
    df_in: pd.DataFrame | None,
) -> pd.DataFrame:
    """Use df_in if provided; otherwise read from strategy.input_path.

    When the strategy's ``input.discover`` flag is set, returns an
    empty DataFrame — the first step is expected to be a discovery
    step that creates the rows from supplier files / API calls and
    ignores the input df. The legacy "must have an input" guard rail
    still applies for non-discover strategies so a typo in
    ``input.path`` doesn't silently run on empty data.
    """
    if df_in is not None:
        return df_in
    if strategy.input_discover:
        return pd.DataFrame()
    if strategy.input_path is None:
        raise StrategyConfigError(
            "no input: strategy has no 'input.path' and no df_in was provided"
        )
    path = Path(interpolate(strategy.input_path, context))
    if not path.exists():
        raise StrategyConfigError(f"input CSV not found: {path}")
    return pd.read_csv(
        path, dtype=str, keep_default_na=False, encoding=strategy.input_encoding
    )


# ────────────────────────────────────────────────────────────────────────
# CLI.
# ────────────────────────────────────────────────────────────────────────


def _parse_context_pairs(pairs: list[str]) -> dict[str, str]:
    """Parse `key=value key=value` CLI args into a context dict."""
    context: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"--context entry must be key=value, got: {p!r}")
        key, _, value = p.partition("=")
        if not key:
            raise SystemExit(f"--context entry has empty key: {p!r}")
        context[key] = value
    return context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a YAML strategy: an ordered chain of pipeline steps "
            "exposing `run_step(df, config) -> df`."
        )
    )
    parser.add_argument(
        "--strategy", required=True, type=Path,
        help="Path to the strategy YAML file.",
    )
    parser.add_argument(
        "--context", nargs="*", default=[],
        metavar="KEY=VALUE",
        help="Context variables for {placeholder} interpolation in YAML strings.",
    )
    args = parser.parse_args(argv)

    context = _parse_context_pairs(args.context)
    # Seed universally-applicable defaults so direct runner invocations
    # don't have to know every step's interpolation knobs. Any key the
    # operator passes via --context overrides these. Mirrors the
    # default-seeding in cli/strategy.py:_build_context.
    context.setdefault("order_mode", "first")

    # Wrap the load + run in clean stderr error reporting so operators see
    # a one-line message instead of a Python traceback for the common
    # config / missing-file failures.
    try:
        strategy = load_strategy(args.strategy)
    except (FileNotFoundError, StrategyConfigError) as err:
        print(f"Error loading strategy: {err}", file=sys.stderr)
        return 1
    print(f"Loaded strategy: {strategy.name} ({len(strategy.steps)} steps)")

    try:
        df = run_strategy(strategy, context=context)
    except StrategyConfigError as err:
        print(f"Error in strategy config: {err}", file=sys.stderr)
        return 1
    except StrategyExecutionError as err:
        print(f"Strategy step failed: {err}", file=sys.stderr)
        return 2

    print(f"Strategy complete: {len(df)} rows, {len(df.columns)} columns")
    if strategy.output_csv:
        print(f"Output CSV: {interpolate(strategy.output_csv, context)}")
    if strategy.output_xlsx:
        print(f"Output XLSX: {interpolate(strategy.output_xlsx, context)}")
    if strategy.output_gsheet:
        # The runner already wrote it (or silently skipped) inside
        # run_strategy; the URL is in run_summary.json — we don't
        # re-fetch here to avoid double work. Operators read either the
        # CLI summary or the JSON.
        print(
            f"Output GSheet: declared with title "
            f"\"{interpolate(strategy.output_gsheet['title'], context)}\""
            " — see run_summary.json for the resulting URL"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
