"""Repo-root launcher for the sourcing engine.

Usage:
    python run.py --supplier abgee
    python run.py --supplier connect-beauty --market-data /path/to/keepa.csv

This is a thin wrapper that adds shared/lib/python to PYTHONPATH so the
canonical sourcing_engine package is importable, then forwards everything
else to sourcing_engine.main.

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

# Forward to the canonical entry point.
from sourcing_engine.main import main as _main  # noqa: E402

if __name__ == "__main__":
    _main()
