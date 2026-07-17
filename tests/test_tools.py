"""Tests for the ToolRegistry."""

import json
import os
import tempfile


def test_tool_registry_has_core_tools():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)
    names = [t["function"]["name"] for t in registry.list_tools()]
    assert "run_qgis_code" in names
    assert "get_project_context" in names
    assert "capture_canvas" in names


def test_self_extension_tools_registered():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)
    names = [t["function"]["name"] for t in registry.list_tools()]
    assert "register_tool" in names
    assert "unregister_tool" in names
    assert "list_custom_tools" in names


def test_register_tool_adds_to_registry():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)
    names_before = [t["function"]["name"] for t in registry.list_tools()]
    assert "calculate_test" not in names_before

    import asyncio
    result = asyncio.run(registry._execute_register_tool({
        "name": "calculate_test",
        "description": "A test tool",
        "parameters_schema": json.dumps({
            "type": "object",
            "properties": {"input": {"type": "string"}},
            "required": ["input"],
        }),
        "code": "result = input.upper()",
    }))
    assert "registered successfully" in result

    names_after = [t["function"]["name"] for t in registry.list_tools()]
    assert "calculate_test" in names_after


def test_unregister_tool_removes_from_registry():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)

    import asyncio
    asyncio.run(registry._execute_register_tool({
        "name": "temp_tool",
        "description": "Temporary tool",
        "parameters_schema": json.dumps({"type": "object", "properties": {}}),
        "code": "result = 'done'",
    }))
    assert "temp_tool" in [t["function"]["name"] for t in registry.list_tools()]

    result = asyncio.run(registry._execute_unregister_tool({"name": "temp_tool"}))
    assert "removed successfully" in result
    assert "temp_tool" not in [t["function"]["name"] for t in registry.list_tools()]


def test_cannot_override_core_tool():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)

    import asyncio
    result = asyncio.run(registry._execute_register_tool({
        "name": "run_qgis_code",
        "description": "Override attempt",
        "parameters_schema": json.dumps({"type": "object", "properties": {}}),
        "code": "result = 'hacked'",
    }))
    assert "Cannot override core tool" in result


def test_unregister_nonexistent_tool():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)

    import asyncio
    result = asyncio.run(registry._execute_unregister_tool({"name": "does_not_exist"}))
    assert "not found" in result


def test_cannot_unregister_core_tool():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)

    import asyncio
    result = asyncio.run(registry._execute_unregister_tool({"name": "run_qgis_code"}))
    assert "Cannot remove core tool" in result


def test_list_custom_tools_empty():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)

    import asyncio
    result = asyncio.run(registry._execute_list_custom_tools({}))
    assert "No custom tools registered" in result


def test_list_custom_tools_shows_registered():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)

    import asyncio
    asyncio.run(registry._execute_register_tool({
        "name": "test_tool_alpha",
        "description": "Alpha tool",
        "parameters_schema": json.dumps({"type": "object", "properties": {}}),
        "code": "result = 'alpha'",
    }))
    asyncio.run(registry._execute_register_tool({
        "name": "test_tool_beta",
        "description": "Beta tool",
        "parameters_schema": json.dumps({"type": "object", "properties": {}}),
        "code": "result = 'beta'",
    }))

    result = asyncio.run(registry._execute_list_custom_tools({}))
    data = json.loads(result)
    assert data["count"] == 2
    names = [t["name"] for t in data["custom_tools"]]
    assert "test_tool_alpha" in names
    assert "test_tool_beta" in names


def test_invalid_schema_rejected():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)

    import asyncio
    result = asyncio.run(registry._execute_register_tool({
        "name": "bad_tool",
        "description": "Bad schema",
        "parameters_schema": "not valid json",
        "code": "result = 'fail'",
    }))
    assert "Invalid parameters_schema JSON" in result


def test_invalid_tool_name_rejected():
    from aery_plugin.tools import ToolRegistry
    registry = ToolRegistry(executor=None)

    import asyncio
    result = asyncio.run(registry._execute_register_tool({
        "name": "bad-tool-name!",
        "description": "Bad name",
        "parameters_schema": json.dumps({"type": "object", "properties": {}}),
        "code": "result = 'fail'",
    }))
    assert "must be alphanumeric" in result


def test_custom_tools_persist_to_file():
    from aery_plugin.tools import ToolRegistry
    with tempfile.TemporaryDirectory() as tmpdir:
        project_file = os.path.join(tmpdir, "test.qgs")
        open(project_file, "w").close()

        aery_dir = os.path.join(tmpdir, ".aery")
        os.makedirs(aery_dir, exist_ok=True)

        class FakeProject:
            def fileName(self):
                return project_file

        import unittest.mock as mock
        with mock.patch("qgis.core.QgsProject") as mock_proj:
            mock_proj.instance.return_value = FakeProject()

            registry = ToolRegistry(executor=None)
            import asyncio
            asyncio.run(registry._execute_register_tool({
                "name": "persist_test",
                "description": "Should persist",
                "parameters_schema": json.dumps({"type": "object", "properties": {}}),
                "code": "result = 'persisted'",
            }))

            tools_path = os.path.join(aery_dir, "custom_tools.json")
            assert os.path.exists(tools_path)
            with open(tools_path) as f:
                saved = json.load(f)
            assert len(saved) == 1
            assert saved[0]["name"] == "persist_test"


def test_custom_tools_load_from_file():
    from aery_plugin.tools import ToolRegistry
    with tempfile.TemporaryDirectory() as tmpdir:
        project_file = os.path.join(tmpdir, "test.qgs")
        open(project_file, "w").close()

        aery_dir = os.path.join(tmpdir, ".aery")
        os.makedirs(aery_dir, exist_ok=True)

        tools_path = os.path.join(aery_dir, "custom_tools.json")
        with open(tools_path, "w") as f:
            json.dump([{
                "name": "loaded_tool",
                "description": "Loaded from file",
                "parameters": {"type": "object", "properties": {}},
                "code": "result = 'loaded'",
            }], f)

        class FakeProject:
            def fileName(self):
                return project_file

        import unittest.mock as mock
        with mock.patch("qgis.core.QgsProject") as mock_proj:
            mock_proj.instance.return_value = FakeProject()

            registry = ToolRegistry(executor=None)
            names = [t["function"]["name"] for t in registry.list_tools()]
            assert "loaded_tool" in names



def test_normalize_replaces_qgsmapcanvas_instance():
    from aery_plugin.tools import ToolRegistry
    code = "canvas = QgsMapCanvas.instance()\ncanvas.refresh()"
    out = ToolRegistry._normalize_qgis4_code(code)
    assert "iface.mapCanvas()" in out
    assert "QgsMapCanvas.instance()" not in out

def test_normalize_replaces_qgsproject_triggerrepaint():
    from aery_plugin.tools import ToolRegistry
    code = "QgsProject.instance().triggerRepaint()"
    out = ToolRegistry._normalize_qgis4_code(code)
    assert "iface.mapCanvas().refresh()" in out
    assert "triggerRepaint()" not in out



def test_resolve_basemap_known_names():
    from aery_plugin.tools import resolve_basemap, BASEMAP_REGISTRY
    assert resolve_basemap("osm")["label"] == "OpenStreetMap"
    assert resolve_basemap("esri imagery")["label"] == "Esri World Imagery"
    assert resolve_basemap("carto-dark")["label"] == "CARTO Dark Matter"
    assert resolve_basemap("bogus-name") is None

def test_resolve_basemap_raw_url():
    from aery_plugin.tools import resolve_basemap
    entry = resolve_basemap("https://x.example.com/{z}/{x}/{y}.png")
    assert entry["url"] == "https://x.example.com/{z}/{x}/{y}.png"

def test_load_basemap_registered_and_always_included():
    from aery_plugin.tools import ToolRegistry
    reg = ToolRegistry(executor=None)
    names = [t["function"]["name"] for t in reg.list_tools()]
    assert "load_basemap" in names
    assert "load_basemap" in ToolRegistry._always_include
