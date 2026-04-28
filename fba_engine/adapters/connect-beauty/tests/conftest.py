"""pytest configuration for per-supplier adapter tests.

Makes two things importable:
  - `ingest` and `normalise` (the supplier-specific modules in this adapter
    folder — i.e. the parent of this tests/ folder)
  - `sourcing_engine.config`, `sourcing_engine.utils.flags`, etc. (from
    shared/lib/python/, used by normalise.py and ingest.py)

Layout assumed:
  fba_engine/adapters/<supplier>/
    ingest.py
    normalise.py
    tests/
      conftest.py     ← this file
      test_ingest.py
      test_normalise.py
"""
import sys
from pathlib import Path

# 1. Make `ingest` and `normalise` importable as top-level modules.
#    parents[0] = tests, [1] = <supplier>
_adapter_dir = Path(__file__).resolve().parents[1]
if str(_adapter_dir) not in sys.path:
    sys.path.insert(0, str(_adapter_dir))

# 2. Make `sourcing_engine.X` importable.
#    Walk up looking for shared/lib/python.
_root = _adapter_dir
for _ in range(8):
    candidate = _root / "shared" / "lib" / "python"
    if candidate.is_dir() and (candidate / "sourcing_engine").is_dir():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        break
    _root = _root.parent
