"""Every deferred import inside the TUI must still resolve.

The TUI defers a lot of imports into function bodies, and has to: the screens
import the palette and the palette pushes the screens, so one of the two
directions can only happen at call time. The cost is that a deferred import is
not checked until the key is pressed. `tui/screens/transcript.py` spent a
release importing `_open_analysis_type_picker` after that function had been
renamed — the analysis key raised ImportError, and nothing else noticed.

A linter cannot see this: the name is only wrong relative to another module.
So resolve them here, by reading the source rather than by pressing keys.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

TUI_DIR = Path(__file__).resolve().parents[1] / "tui"


def _deferred_imports() -> list[tuple[str, str, str, int]]:
    """(module, imported_from, name, lineno) for each import inside a function."""
    found: list[tuple[str, str, str, int]] = []
    for path in sorted(TUI_DIR.rglob("*.py")):
        rel = path.relative_to(TUI_DIR.parent).with_suffix("")
        module = ".".join(rel.parts)
        tree = ast.parse(path.read_text(), filename=str(path))

        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.ImportFrom) or not node.level:
                    continue
                # A relative import resolves against this module's package;
                # each extra dot strips one more parent.
                package = ".".join(rel.parts[: len(rel.parts) - node.level])
                target = f"{package}.{node.module}" if node.module else package
                for alias in node.names:
                    found.append((module, target, alias.name, node.lineno))
    return found


DEFERRED = _deferred_imports()


def test_the_tui_actually_defers_imports():
    """Guards the guard: an empty list would make every case below vacuous."""
    assert len(DEFERRED) > 10


@pytest.mark.parametrize(
    ("module", "target", "name", "lineno"),
    DEFERRED,
    ids=[f"{m}:{ln}:{n}" for m, _t, n, ln in DEFERRED],
)
def test_a_deferred_import_resolves(module, target, name, lineno):
    imported = importlib.import_module(target)

    assert hasattr(imported, name), (
        f"{module}:{lineno} imports {name!r} from {target}, which has no such name"
    )
