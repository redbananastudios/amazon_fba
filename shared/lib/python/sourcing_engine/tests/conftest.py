"""pytest configuration for the canonical sourcing_engine tests.

Ensures the engine itself is importable when tests are run from
shared/lib/python/sourcing_engine/tests/ or any other CWD.
"""
import sys
from pathlib import Path

# shared/lib/python is the parent of the sourcing_engine package directory.
_LIB = Path(__file__).resolve().parents[2]
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
