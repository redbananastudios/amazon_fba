"""Repo-root launcher for the sourcing engine.

Usage:
    python run.py --supplier abgee
    python run.py --supplier connect-beauty --market-data /path/to/keepa.csv

    # `open` subcommand — one-shot browser launches:
    python run.py open --asin B0XXXXXXX --target keepa
    python run.py open --seller A1B2C3D4E5 --target storefront

    # `--strategy` dispatch — Cowork-friendly entry to YAML strategies:
    python run.py --strategy keepa_finder \\
        --csv ./output/2026-05-02/keepa_amazon_oos.csv \\
        --recipe amazon_oos_wholesale \\
        --output-dir ./output/2026-05-02

This is a thin wrapper that adds shared/lib/python to PYTHONPATH so the
canonical sourcing_engine package is importable, then dispatches:
  - If the first arg is a recognised subcommand (`open`), forward to
    that subcommand's `main()`.
  - If `--strategy` appears anywhere in argv, forward to the strategy
    dispatcher in ``cli.strategy``.
  - Otherwise, forward to `sourcing_engine.main` (default
    supplier_pricelist invocation — preserves backwards compat with
    `python run.py --supplier ...`).

You can also invoke the engine directly with:
    PYTHONPATH=shared/lib/python python -m sourcing_engine.main --supplier abgee
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent
_LIB = _REPO / "shared" / "lib" / "python"
if not _LIB.is_dir():
    print(f"ERROR: shared/lib/python not found at {_LIB}", file=sys.stderr)
    sys.exit(1)
sys.path.insert(0, str(_LIB))


# Map of subcommand name → callable that takes the remaining argv list.
# Add a row to register a new subcommand.
def _dispatch(argv: list[str]) -> int:
    if argv and argv[0] == "open":
        from cli.launch import main as launch_main  # noqa: E402
        return launch_main(argv[1:])

    if "--strategy" in argv:
        # Forward to the named-strategy dispatcher. Resolves the YAML,
        # loads the recipe (if --recipe given), forwards calculate /
        # decide config knobs, runs the chain, prints a summary.
        from cli.strategy import main as strategy_main  # noqa: E402
        return strategy_main(argv)

    # Default: forward to the canonical sourcing_engine entry point.
    # sourcing_engine.main reads sys.argv directly, so we rebuild it from
    # `argv` to match the legacy contract.
    from sourcing_engine.main import main as _main  # noqa: E402
    sys.argv = ["run.py", *argv]
    return _main() or 0


if __name__ == "__main__":
    sys.exit(_dispatch(sys.argv[1:]))
