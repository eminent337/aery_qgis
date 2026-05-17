"""Tests for the ToolRegistry."""


def test_tool_registry_has_core_tools():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)
    names = [t["function"]["name"] for t in registry.list_tools()]
    assert "run_qgis_code" in names
    assert "get_project_context" in names
    assert "capture_canvas" in names
