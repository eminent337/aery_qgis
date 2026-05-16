"""Tests for multi_map_layout tool."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY_TS = ROOT / "runner" / "entry.ts"


def test_multi_map_layout_registers_tool():
    """multi_map_layout must appear in runner/entry.ts."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    assert 'name: "multi_map_layout"' in src, (
        "multi_map_layout tool registration not found in runner/entry.ts"
    )


def test_multi_map_layout_requires_name_and_output():
    """multi_map_layout requires layout_name and output_path."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    idx = src.index('name: "multi_map_layout"')
    block = src[idx:idx + 2000]
    assert 'layout_name' in block, "Missing required layout_name parameter"
    assert 'output_path' in block, "Missing required output_path parameter"
    assert 'panels' in block, "Missing panels parameter"
    assert 'grid' in block, "Missing grid parameter"


def test_multi_map_layout_mentions_pdf_export():
    """multi_map_layout should mention PDF export."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    assert '"pdf"' in src.lower() or "PdfExportSettings" in src or "exportToPdf" in src


def test_multi_map_layout_mentions_QgsLayoutItemMap():
    """The tool should use QgsLayoutItemMap for each panel."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    idx = src.index('name: "multi_map_layout"')
    block = src[idx:idx + 6000]
    assert "QgsLayoutItemMap" in block, "multi_map_layout should create QgsLayoutItemMap per panel"
    assert "QgsLayoutExporter" in block, "multi_map_layout should use QgsLayoutExporter"
