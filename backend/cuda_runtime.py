"""Make the CUDA libraries inside a frozen bundle findable at runtime.

faster-whisper transcribes with CTranslate2, not with torch, and CTranslate2
loads cuBLAS and cuDNN through the dynamic linker — by soname, at the moment
the first model is created. In a normal pip install those live in the
`nvidia-*` site-packages, which the linker finds because torch has already
loaded them from the same place.

In a PyInstaller bundle they land under ``_internal/nvidia/<lib>/`` instead,
which is on no search path at all. The result is a "GPU" build where torch (so
diarization) uses the GPU while Whisper silently falls back to the CPU — the
kind of failure that looks like a slow machine rather than a broken build.

So the libraries are loaded here, by absolute path, before anything imports
CTranslate2. On Linux a library already loaded into the process satisfies a
later ``dlopen`` of the same soname; on Windows the directory is added to the
DLL search path, which is the equivalent.

Nothing here is required for a CPU build, and every failure is soft: the worst
outcome is the CPU fallback that would have happened anyway.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# cuDNN must come after cuBLAS: it links against it, and loading it first makes
# the linker resolve cuBLAS from wherever it can, which may be nowhere.
_LIBRARY_ORDER = ("cublas", "cudnn")


def _bundle_root() -> Path | None:
    """The directory PyInstaller extracted into, or None outside a bundle."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def _library_dirs(root: Path) -> list[Path]:
    """Directories under the bundle that hold nvidia shared libraries."""
    nvidia = root / "nvidia"
    if not nvidia.is_dir():
        return []

    found: list[Path] = []
    for package in sorted(nvidia.iterdir()):
        if not package.is_dir():
            continue
        # Linux keeps them in lib/, Windows in bin/; some wheels put them at
        # the package root.
        for candidate in (package / "lib", package / "bin", package):
            if candidate.is_dir() and any(
                child.suffix in {".so", ".dll"} or ".so." in child.name
                for child in candidate.iterdir()
                if child.is_file()
            ):
                found.append(candidate)
    return found


def _sort_key(path: Path) -> tuple[int, str]:
    """Order directories so a library's dependencies load before it does."""
    name = path.parent.name.lower() if path.name in {"lib", "bin"} else path.name.lower()
    for index, marker in enumerate(_LIBRARY_ORDER):
        if marker in name:
            return (index, name)
    return (len(_LIBRARY_ORDER), name)


def preload(verbose: bool = False) -> list[str]:
    """Load the bundled CUDA libraries. Returns what was loaded, for logging."""
    root = _bundle_root()
    if root is None:
        return []

    directories = sorted(_library_dirs(root), key=_sort_key)
    if not directories:
        return []

    loaded: list[str] = []
    for directory in directories:
        if hasattr(os, "add_dll_directory"):  # Windows
            try:
                os.add_dll_directory(str(directory))
                loaded.append(str(directory))
            except OSError:
                continue
            continue

        # Linux: loading by absolute path satisfies a later dlopen by soname.
        import ctypes

        for library in sorted(directory.iterdir()):
            if not library.is_file() or ".so" not in library.name:
                continue
            try:
                ctypes.CDLL(str(library), mode=getattr(ctypes, "RTLD_GLOBAL", 0))
                loaded.append(library.name)
            except OSError:
                # A stub, or a library for a driver this machine does not have.
                # CTranslate2 will fall back to the CPU, which still works.
                continue

    if verbose and loaded:
        print(f"Preloaded {len(loaded)} bundled CUDA libraries")
    return loaded
