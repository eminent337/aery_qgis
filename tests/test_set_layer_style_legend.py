"""Tests for set_layer_style legend_title and legend_expression parameters."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY_TS = ROOT / "runner" / "entry.ts"


def test_set_layer_style_registers_tool():
    """set_layer_style must appear in runner/entry.ts tools."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    assert 'name: "set_layer_style"' in src, "set_layer_style not found in entry.ts"


def test_set_layer_style_has_legend_title_param():
    """set_layer_style exposes legend_title parameter."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    idx = src.index('name: "set_layer_style"')
    block = src[idx:idx + 6000]
    assert "legend_title" in block, "legend_title must be a parameter of set_layer_style"


def test_set_layer_style_has_legend_expression_param():
    """set_layer_style exposes legend_expression parameter."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    idx = src.index('name: "set_layer_style"')
    block = src[idx:idx + 6000]
    assert "legend_expression" in block, "legend_expression must be a parameter of set_layer_style"


def test_set_layer_style_mentions_QgsSingleBandPseudoColorRenderer():
    """set_layer_style uses the QGIS pseudocolor/categorized/graduated API."""
    src = ENTRY_TS.read_text(encoding="utf-8")
    idx = src.index('name: "set_layer_style"')
    block = src[idx:idx + 8000]
    assert "QgsSingleBandPseudoColorRenderer" in block, (
        "set_layer_style should use the QGIS pseudocolor renderer for singleband colormaps"
    )
