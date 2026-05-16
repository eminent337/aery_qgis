"""Tests for save_map_theme and load_map_theme tools."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY_TS = ROOT / "runner" / "entry.ts"


def test_save_map_theme_registers_tool():
    """save_map_theme must appear in runner/entry.ts tool registrations."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    assert 'name: "save_map_theme"' in src, (
        "save_map_theme tool registration not found in runner/entry.ts"
    )


def test_load_map_theme_registers_tool():
    """load_map_theme must appear in runner/entry.ts tool registrations."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    assert 'name: "load_map_theme"' in src, (
        "load_map_theme tool registration not found in runner/entry.ts"
    )


def test_save_map_theme_accepts_name():
    """save_map_theme requires theme_name and accepts optional description."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    idx = src.index('name: "save_map_theme"')
    block = src[idx:idx + 2000]
    assert 'theme_name' in block, "Missing required theme_name parameter"
    assert 'description:' in block, "Missing optional description parameter"


def test_load_map_theme_accepts_name_and_refresh():
    """load_map_theme requires theme_name and accepts refresh flag."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    idx = src.index('name: "load_map_theme"')
    block = src[idx:idx + 2000]
    assert 'theme_name' in block
    assert 'refresh:' in block


def test_map_theme_tools_mention_mapThemeCollection():
    """Theme tools should reference QgsProject.instance().mapThemeCollection()."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    assert "mapThemeCollection" in src, "Tools should reference mapThemeCollection"
    # The exec code strings embed mapThemeCollection() Python calls — validate the file
    # contains the correct API name (not a phantom, non-existent method)
    assert "addMapTheme" in src or "addTheme" in src or "createMapTheme" in src, (
        "save/load tools should use a QgsMapThemeCollection method"
    )
