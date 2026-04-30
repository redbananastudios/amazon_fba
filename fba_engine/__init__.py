"""Engine root package.

Importing anything from `fba_engine.*` puts `shared/lib/python` on
`sys.path` so child modules can `from keepa_client import ...` /
`from oa_importers import ...` without a separate path-bootstrap step.

This mirrors what `run.py` does at process start; the runtime contract
is the same regardless of how the engine is invoked (CLI launcher,
strategy runner, individual step `python -m`, or pytest).
"""
from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "shared" / "lib" / "python"
if _LIB.is_dir() and str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
