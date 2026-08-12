"""Assert a built wheel actually contains the application.

The wheel is assembled by `force-include` mappings in pyproject.toml rather than
by importable-package discovery, which has one bad failure mode: if a mapping
breaks, the wheel still builds, still installs, and still exposes the console
script — it just serves an empty app at runtime. Nothing before this point
notices, so the checks live here and run on every build, dry runs included.

Usage: python scripts/check_wheel.py dist/amicoscript-*.whl
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

PKG = "amicoscript"

# Files whose absence means a broken mapping rather than a missing feature.
REQUIRED = [
    f"{PKG}/__init__.py",
    f"{PKG}/cli.py",
    # run.py, renamed. cli.py resolves the launcher by this name.
    f"{PKG}/_run.py",
    # The backend is imported flat off sys.path, exactly as in a source checkout.
    f"{PKG}/backend/main.py",
    f"{PKG}/backend/config.py",
    f"{PKG}/backend/runtime_pack.py",
    # run_windowed() reads this for the window title.
    f"{PKG}/backend/VERSION",
    f"{PKG}/frontend/index.html",
    # The frontend links these for download; meeting_watcher_host.start() runs
    # whichever matches the host.
    f"{PKG}/scripts/meeting_watcher/setup.bat",
    f"{PKG}/scripts/meeting_watcher/setup.command",
    f"{PKG}/scripts/meeting_watcher/setup.sh",
    # watcher.py imports its platform backend from here — without the package it
    # starts and dies on the first import.
    f"{PKG}/scripts/meeting_watcher/watcher_platform/__init__.py",
    f"{PKG}/scripts/meeting_watcher/watcher_platform/windows.py",
    f"{PKG}/scripts/meeting_watcher/watcher_platform/macos.py",
    f"{PKG}/scripts/meeting_watcher/watcher_platform/linux.py",
]


def check(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        index = archive.read(f"{PKG}/frontend/index.html").decode("utf-8")

    problems = [f"missing {name}" for name in REQUIRED if name not in names]

    # The vendored assets are what let the UI render with no network at all, and
    # they are the largest force-included tree — the most likely to be dropped.
    # tests/test_frontend_assets.py guards the checkout; this guards the wheel.
    for ref in sorted(set(re.findall(r"vendor/[A-Za-z0-9._/-]+", index))):
        ref = ref.rstrip(".")
        if f"{PKG}/frontend/{ref}" not in names:
            problems.append(f"index.html references {ref}, which is not in the wheel")

    # A wheel built from a dirty checkout can otherwise ship stale bytecode.
    if any("__pycache__" in n or n.endswith(".pyc") for n in names):
        problems.append("wheel contains __pycache__/.pyc entries")

    # The backend is a package tree, not a handful of modules; a mapping that
    # silently flattened would still satisfy the checks above.
    routes = sum(1 for n in names if n.startswith(f"{PKG}/backend/api/routes/"))
    if routes < 5:
        problems.append(f"only {routes} backend API route modules in the wheel")

    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2

    wheel = Path(argv[1])
    if not wheel.is_file():
        print(f"No such wheel: {wheel}", file=sys.stderr)
        return 2

    problems = check(wheel)
    if problems:
        print(f"{wheel.name} is incomplete:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"{wheel.name}: contents OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
