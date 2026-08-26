"""Import-smoke tests that verify every plugin module loads under PyQt6.

Adapted from GeoAI (opengeos/geoai) test_pyqt6_imports.py.

This catches short-form Qt enum regressions (for example ``Qt.AlignCenter``
instead of ``Qt.AlignmentFlag.AlignCenter``) which raise ``AttributeError`` in
PyQt6 during class-body evaluation.

The plugin package is auto-discovered by locating ``metadata.txt``.
"""

import importlib
import pathlib
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _find_plugin_root() -> pathlib.Path:
    """Return the plugin package directory."""
    if (REPO_ROOT / "metadata.txt").exists():
        return REPO_ROOT
    candidates = [
        p.parent for p in REPO_ROOT.glob("*/metadata.txt") if p.parent.name != "tests"
    ]
    if not candidates:
        raise RuntimeError(
            f"Could not locate a QGIS plugin package (no metadata.txt found under {REPO_ROOT})."
        )
    return candidates[0]


PLUGIN_ROOT = _find_plugin_root()


def _module_names():
    """Yield dotted module names for every .py file under the plugin package."""
    for path in sorted(PLUGIN_ROOT.rglob("*.py")):
        # Skip tests directory and virtual environments
        if "tests" in path.parts or ".venv" in path.parts:
            continue
        rel = path.relative_to(PLUGIN_ROOT.parent).with_suffix("")
        parts = rel.parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        yield ".".join(parts)


@pytest.mark.parametrize("module_name", list(_module_names()))
def test_module_imports_under_pyqt6(module_name):
    """Each plugin module must import cleanly when qgis.PyQt maps to PyQt6."""
    importlib.import_module(module_name)
