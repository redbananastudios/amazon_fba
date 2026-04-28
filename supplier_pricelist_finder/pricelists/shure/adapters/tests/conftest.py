"""pytest configuration for per-supplier adapter tests.

Makes two things importable:
  - `adapters.ingest` and `adapters.normalise` (the supplier-specific modules
    next to this conftest's grandparent folder)
  - `sourcing_engine.config`, `sourcing_engine.utils.flags`, etc. (from
    shared/lib/python/, used by normalise.py and ingest.py)
"""
import sys
from pathlib import Path

# 1. Make `adapters.X` importable.
#    parents[0] = tests, [1] = adapters, [2] = <supplier>
_supplier_dir = Path(__file__).resolve().parents[2]
if str(_supplier_dir) not in sys.path:
    sys.path.insert(0, str(_supplier_dir))

# 2. Make `sourcing_engine.X` importable.
#    Walk up looking for shared/lib/python.
_root = _supplier_dir
for _ in range(8):
    candidate = _root / "shared" / "lib" / "python"
    if candidate.is_dir() and (candidate / "sourcing_engine").is_dir():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        break
    _root = _root.parent
