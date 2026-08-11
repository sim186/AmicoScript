"""The `amicoscript` console script.

`run.py` is the launcher for every other distribution channel too (`python
run.py`, the PyInstaller bundle), so this module does not reimplement it — it
locates the file and calls its `main()`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _launcher_path() -> Path:
    """Find run.py, whether we are installed or in a checkout."""
    # Installed wheel: hatchling put run.py here as _run.py, next to backend/.
    installed = _HERE / "_run.py"
    if installed.is_file():
        return installed
    # Source checkout (including `pip install -e .`): the shim package sits at
    # <repo>/amicoscript/, so run.py is one level up.
    source = _HERE.parent / "run.py"
    if source.is_file():
        return source
    raise ModuleNotFoundError(
        "Could not locate the AmicoScript launcher (run.py). The installation "
        "looks incomplete — try reinstalling the amicoscript package."
    )


def _load_launcher():
    path = _launcher_path()
    # Loaded by path rather than imported by name so that `__file__` stays the
    # real location: run.py derives BASE_DIR from it, and everything the app
    # serves — backend modules, the frontend, meeting_watcher — hangs off that.
    spec = importlib.util.spec_from_file_location("amicoscript._run", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load the AmicoScript launcher from {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module so a re-entrant import gets the same module
    # rather than running run.py's import-time side effects a second time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    return _load_launcher().main()


if __name__ == "__main__":
    sys.exit(main())
