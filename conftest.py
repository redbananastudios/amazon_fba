"""Pytest path setup mirroring `run.py`.

`run.py` inserts `shared/lib/python` at the front of `sys.path` so the
canonical `sourcing_engine` package (and now `keepa_client`,
`oa_importers`) resolve. We do the same here so `pytest` invoked from
the repo root sees the same import shape.

Without this, tests that touch `from oa_importers import …` or
`from keepa_client import …` from outside `shared/lib/python` fail with
ModuleNotFoundError.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
_LIB = _REPO_ROOT / "shared" / "lib" / "python"
if _LIB.is_dir() and str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


def pytest_addoption(parser):
    """Repo-wide pytest options.

    --snapshot-update is consumed by snapshot-style tests (e.g.
    buy_plan_html test_html_snapshot) — pass to regenerate stored
    snapshots intentionally.
    """
    parser.addoption(
        "--snapshot-update", action="store_true", default=False,
        help="Update stored test snapshots in place",
    )
