"""
Adapter loader for the sourcing engine.

A "supplier adapter" is a pair of modules — `ingest.py` and `normalise.py` —
that handle the supplier-specific bits of a pipeline run:
  - `ingest.py`: read raw supplier files (CSV/XLSX/PDF/HTML) into a DataFrame
  - `normalise.py`: map supplier-specific column names to the canonical schema

Each supplier's adapter lives in:
  fba_engine/adapters/<supplier>/

The adapter must expose:
  - `ingest_directory(path: str) -> pd.DataFrame`
  - `ingest_file(path: str) -> pd.DataFrame`
  - `normalise(df: pd.DataFrame) -> pd.DataFrame`

Loading uses absolute file paths via importlib so that hyphenated supplier
names (e.g. "connect-beauty") work without folder renames.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


class AdapterNotFoundError(FileNotFoundError):
    """Raised when an adapter folder doesn't exist or is missing required modules."""


def _load_module_by_path(name: str, path: Path) -> ModuleType:
    """Load a module from an absolute file path, registering it in sys.modules."""
    if not path.exists():
        raise AdapterNotFoundError(f"Adapter module not found: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AdapterNotFoundError(f"Could not create import spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def find_adapter_dir(supplier: str, repo_root: Path | None = None) -> Path:
    """
    Locate the adapter directory for a given supplier name.

    Resolution order:
      1. If repo_root is provided, look there
      2. Walk up from this file looking for `fba_engine/adapters/<supplier>`
    """
    candidates: list[Path] = []
    if repo_root is not None:
        candidates.append(
            Path(repo_root) / "fba_engine" / "adapters" / supplier
        )
    here = Path(__file__).resolve()
    for ancestor in [here, *here.parents]:
        c = ancestor / "fba_engine" / "adapters" / supplier
        if c not in candidates:
            candidates.append(c)

    for c in candidates:
        if c.is_dir():
            return c

    raise AdapterNotFoundError(
        f"No adapter folder found for supplier '{supplier}'. "
        f"Tried: {[str(c) for c in candidates]}"
    )


def load_supplier_adapter(supplier: str, repo_root: Path | None = None) -> tuple[ModuleType, ModuleType]:
    """
    Load the (ingest, normalise) modules for the given supplier.

    Returns a tuple of two module objects. Each adapter is loaded under a
    namespaced module name so multiple suppliers can be loaded in one process
    without colliding (matters for cross-supplier strategies in step 6).

    Args:
        supplier: e.g. "abgee" or "connect-beauty" (folder name as-is)
        repo_root: optional explicit root; defaults to walking up from this file

    Returns:
        (ingest_module, normalise_module)
    """
    adapter_dir = find_adapter_dir(supplier, repo_root)
    safe_name = supplier.replace("-", "_")  # for module name
    ingest_mod = _load_module_by_path(
        f"_supplier_adapters.{safe_name}.ingest",
        adapter_dir / "ingest.py",
    )
    normalise_mod = _load_module_by_path(
        f"_supplier_adapters.{safe_name}.normalise",
        adapter_dir / "normalise.py",
    )
    return ingest_mod, normalise_mod


__all__ = [
    "AdapterNotFoundError",
    "find_adapter_dir",
    "load_supplier_adapter",
]
