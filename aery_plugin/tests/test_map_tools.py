"""Tests for interactive region map tool."""

from qgis.gui import QgsMapCanvas
from aery_plugin.map_tools import RegionSelectMapTool


def test_region_map_tool_init():
    canvas = QgsMapCanvas()
    tool = RegionSelectMapTool(canvas)
    assert tool.canvas is canvas
    assert tool.is_drawing is False


def test_region_map_tool_clear():
    canvas = QgsMapCanvas()
    tool = RegionSelectMapTool(canvas)
    tool.is_drawing = True
    tool.clear()
    assert tool.is_drawing is False
    assert tool.start_point is None
