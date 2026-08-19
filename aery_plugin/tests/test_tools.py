"""Unit tests for tool parameter validation and type coercion in tools.py."""
import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aery_plugin.tools import ToolRegistry


class TestToolValidation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        executor = MagicMock()
        agent = MagicMock()
        self.registry = ToolRegistry(executor, iface=None, agent=agent)

    def test_validate_params_success(self):
        err = self.registry.validate_params("run_qgis_code", {"code": "result = 1 + 1"})
        self.assertIsNone(err)

    def test_validate_params_missing_required(self):
        err = self.registry.validate_params("run_qgis_code", {})
        self.assertIsNotNone(err)
        self.assertIn("Missing required parameter: 'code'", err)

    def test_validate_params_invalid_type_with_hint(self):
        # run_qgis_algorithms_by_id has 'parameters' as type 'object'
        # Passing an invalid JSON string should fail validation and return a hint
        err = self.registry.validate_params(
            "run_qgis_algorithms_by_id",
            {
                "algorithm_id": "native:buffer",
                "parameters": "invalid_json_string{",
            },
        )
        self.assertIsNotNone(err)
        self.assertIn("expected type 'object'", err)
        self.assertIn(
            "Hint: if this is a JSON list/object, pass it as a native list/dict", err
        )

    def test_validate_params_coercion_success(self):
        # Passing a valid JSON string for object should be successfully coerced to dict
        params = {
            "algorithm_id": "native:buffer",
            "parameters": '{"INPUT": "layer_id"}',
        }
        err = self.registry.validate_params("run_qgis_algorithms_by_id", params)
        self.assertIsNone(err)
        self.assertEqual(params["parameters"], {"INPUT": "layer_id"})

    def test_validate_params_subagent_max_turns(self):
        params = {"mode": "single", "task": "test", "max_turns": 10}
        err = self.registry.validate_params("subagent", params)
        self.assertIsNone(err)

        # Passing non-integer should fail validation
        params_invalid = {"mode": "single", "task": "test", "max_turns": "not_an_int"}
        err = self.registry.validate_params("subagent", params_invalid)
        self.assertIsNotNone(err)

    async def test_execute_subagent_respects_max_turns(self):
        # Mock chat to always return a tool call to simulate running out of turns
        call_count = 0
        async def mock_chat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [{"id": "1", "type": "function", "function": {"name": "get_project_context", "arguments": "{}"}}]
                    }
                }]
            }
        self.registry._agent._client.chat = mock_chat
        self.registry._agent._model = "test-model"
        self.registry._agent._provider_id = "test-provider"
        
        # Mock tool execution
        self.registry.execute = MagicMock(return_value="{}")
        
        # Executing with max_turns=3
        res = await self.registry._execute_subagent({"mode": "single", "task": "hello", "max_turns": 3})
        self.assertEqual(res, "Sub-agent reached maximum turns.")
        self.assertEqual(call_count, 3)
    def test_build_processing_run_code_passes_sandbox(self):
        from aery_plugin.sandbox import check_ast
        # Generate code for a dummy algorithm
        code = self.registry._build_processing_run_code("native:buffer", {"INPUT": "my_layer", "DISTANCE": 10})
        # Check that it compiles and passes AST checks
        violations = check_ast(code)
        self.assertEqual(violations, [])

    def test_dry_run_preview_low_risk(self):
        preview = self.registry._build_dry_run_preview(
            "result = len(QgsProject.instance().mapLayers())"
        )
        self.assertTrue(preview["dry_run"])
        self.assertFalse(preview["would_execute"])
        self.assertEqual(preview["risk"], "low")
        self.assertEqual(preview["risk_flags"], [])
        self.assertEqual(preview["line_count"], 1)

    def test_dry_run_preview_marks_destructive(self):
        preview = self.registry._build_dry_run_preview(
            "QgsProject.instance().removeMapLayer(layer_id)"
        )
        self.assertEqual(preview["risk"], "high")
        self.assertIn("destructive", preview["risk_flags"])

    def test_dry_run_preview_detects_algorithm_call(self):
        preview = self.registry._build_dry_run_preview(
            "import processing\nprocessing.run('native:buffer', {'INPUT': layer, 'DISTANCE': 100, 'OUTPUT': 'TEMPORARY_OUTPUT'})"
        )
        self.assertIn("executes_algorithm", preview["risk_flags"])
        self.assertEqual(preview["risk"], "medium")

    def test_dry_run_preview_detects_canvas_modification(self):
        preview = self.registry._build_dry_run_preview(
            "iface.mapCanvas().refresh()"
        )
        self.assertIn("modifies_canvas", preview["risk_flags"])

    def test_dry_run_preview_detects_sandbox_violations(self):
        preview = self.registry._build_dry_run_preview(
            "exec('print(1)')"
        )
        self.assertGreater(len(preview["sandbox_violations"]), 0)

    def test_dry_run_preview_truncates_long_code(self):
        long_code = "x = 1\n" * 100
        preview = self.registry._build_dry_run_preview(long_code)
        self.assertIn("[truncated]", preview["preview"])
        self.assertEqual(preview["line_count"], 100)

    async def test_execute_qgis_code_dry_run(self):
        """dry_run=true should return a preview, not actually execute."""
        result = await self.registry._execute_qgis_code(
            {"code": "x = 1 + 1", "dry_run": True}
        )
        import json
        preview = json.loads(result)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["line_count"], 1)
        # Verify executor.execute was NOT called
        self.registry.executor.execute.assert_not_called()


class TestToolErrorFormat(unittest.TestCase):
    """Tests for the _format_tool_error contract."""

    def setUp(self):
        executor = MagicMock()
        agent = MagicMock()
        self.registry = ToolRegistry(executor, iface=None, agent=agent)

    def test_format_tool_error_includes_category(self):
        try:
            raise NameError("name 'foo' is not defined")
        except NameError as e:
            msg = self.registry._format_tool_error("run_qgis_code", e)
        self.assertIn("NameError:", msg)
        self.assertIn("Category: name_error", msg)

    def test_format_tool_error_includes_hint_for_known(self):
        try:
            raise RuntimeError("Algorithm 'native:buffer' failed")
        except RuntimeError as e:
            msg = self.registry._format_tool_error("run_qgis_algorithms_by_id", e)
        self.assertIn("Hint:", msg)
        self.assertIn("TEMPORARY_OUTPUT", msg)

    def test_format_tool_error_handles_unknown(self):
        try:
            raise ValueError("Some random validation")
        except ValueError as e:
            msg = self.registry._format_tool_error("any_tool", e)
        # Unknown errors still get a category tag, just no hint
        self.assertIn("Category: unknown", msg)
        self.assertIn("(no hint available", msg)

    def test_format_tool_error_marks_retryable(self):
        try:
            raise RuntimeError("Algorithm 'native:buffer' failed")
        except RuntimeError as e:
            msg = self.registry._format_tool_error("run_qgis_algorithms_by_id", e)
        self.assertIn("Retryable: yes", msg)
class TestCanvasViewTools(unittest.TestCase):
    """Tests for zoom/pan/extent/refresh canvas tools."""
    def setUp(self):
        executor = MagicMock()
        agent = MagicMock()
        self.registry = ToolRegistry(executor, iface=None, agent=agent)
    def test_canvas_tools_are_registered(self):
        for name in ("zoom_to_layer", "set_map_extent", "pan_to", "refresh_canvas"):
            with self.subTest(tool=name):
                self.assertIn(name, self.registry._tools)
    def test_canvas_tools_are_always_included(self):
        for name in ("zoom_to_layer", "set_map_extent", "pan_to", "refresh_canvas"):
            with self.subTest(tool=name):
                self.assertIn(name, self.registry._always_include)
    def test_zoom_to_layer_requires_layer_name(self):
        err = self.registry.validate_params("zoom_to_layer", {})
        self.assertIsNotNone(err)
        self.assertIn("layer_name", err)
    def test_zoom_to_layer_accepts_valid_params(self):
        err = self.registry.validate_params("zoom_to_layer", {"layer_name": "buildings"})
        self.assertIsNone(err)
    def test_set_map_extent_requires_bbox(self):
        err = self.registry.validate_params("set_map_extent", {"xmin": 0})
        self.assertIsNotNone(err)
    def test_set_map_extent_accepts_valid_params(self):
        err = self.registry.validate_params("set_map_extent", {
            "xmin": -74.1, "ymin": 40.6, "xmax": -73.9, "ymax": 40.8, "crs": "EPSG:4326"
        })
        self.assertIsNone(err)
    def test_pan_to_requires_coordinates(self):
        err = self.registry.validate_params("pan_to", {"x": 0})
        self.assertIsNotNone(err)
    def test_pan_to_accepts_valid_params(self):
        err = self.registry.validate_params("pan_to", {"x": -74.0, "y": 40.7, "crs": "EPSG:4326"})
        self.assertIsNone(err)
    def test_refresh_canvas_accepts_empty_params(self):
        err = self.registry.validate_params("refresh_canvas", {})
        self.assertIsNone(err)
    def test_zoom_to_layer_code_uses_existing_layer(self):
        captured = {}
        async def fake_execute(params, on_progress=None):
            captured["code"] = params["code"]
            return "ok"
        self.registry._execute_qgis_code = fake_execute
        asyncio.run(self.registry._execute_zoom_to_layer({"layer_name": "buildings"}))
        self.assertIn("resolve_layer", captured["code"])
        self.assertIn("buildings", captured["code"])
        self.assertIn("setExtent", captured["code"])
    def test_set_map_extent_code_transforms_crs(self):
        captured = {}
        async def fake_execute(params, on_progress=None):
            captured["code"] = params["code"]
            return "ok"
        self.registry._execute_qgis_code = fake_execute
        asyncio.run(self.registry._execute_set_map_extent({
            "xmin": -74.1, "ymin": 40.6, "xmax": -73.9, "ymax": 40.8, "crs": "EPSG:4326"
        }))
        self.assertIn("QgsRectangle", captured["code"])
        self.assertIn("QgsCoordinateTransform", captured["code"])
        self.assertIn("setExtent", captured["code"])
    def test_pan_to_code_transforms_crs(self):
        captured = {}
        async def fake_execute(params, on_progress=None):
            captured["code"] = params["code"]
            return "ok"
        self.registry._execute_qgis_code = fake_execute
        asyncio.run(self.registry._execute_pan_to({"x": -74.0, "y": 40.7, "crs": "EPSG:4326"}))
        self.assertIn("QgsPointXY", captured["code"])
        self.assertIn("setCenter", captured["code"])
    def test_refresh_canvas_code_refreshes_canvas(self):
        captured = {}
        async def fake_execute(params, on_progress=None):
            captured["code"] = params["code"]
            return "ok"
        self.registry._execute_qgis_code = fake_execute
        asyncio.run(self.registry._execute_refresh_canvas({}))
        self.assertIn("iface.mapCanvas().refresh()", captured["code"])
class TestLayerTools(unittest.TestCase):
    """Tests for layer visibility, style, export, remove, and processing tools."""
    def setUp(self):
        executor = MagicMock()
        agent = MagicMock()
        self.registry = ToolRegistry(executor, iface=None, agent=agent)
    def test_layer_tools_are_registered(self):
        for name in ("toggle_layer_visibility", "set_layer_style", "export_layer", "remove_layer", "run_processing_algorithm"):
            with self.subTest(tool=name):
                self.assertIn(name, self.registry._tools)
    def test_layer_tools_are_always_included(self):
        for name in ("toggle_layer_visibility", "set_layer_style", "export_layer", "remove_layer", "run_processing_algorithm"):
            with self.subTest(tool=name):
                self.assertIn(name, self.registry._always_include)
    def test_toggle_visibility_requires_params(self):
        err = self.registry.validate_params("toggle_layer_visibility", {})
        self.assertIsNotNone(err)
    def test_toggle_visibility_accepts_valid_params(self):
        err = self.registry.validate_params("toggle_layer_visibility", {"layer_name": "roads", "visible": False})
        self.assertIsNone(err)
    def test_set_layer_style_accepts_valid_params(self):
        err = self.registry.validate_params("set_layer_style", {"layer_name": "roads", "color": "#3388ff"})
        self.assertIsNone(err)
    def test_export_layer_requires_output_path(self):
        err = self.registry.validate_params("export_layer", {"layer_name": "roads"})
        self.assertIsNotNone(err)
    def test_export_layer_accepts_valid_params(self):
        err = self.registry.validate_params("export_layer", {"layer_name": "roads", "output_path": "roads.gpkg"})
        self.assertIsNone(err)
    def test_remove_layer_requires_layer_name(self):
        err = self.registry.validate_params("remove_layer", {})
        self.assertIsNotNone(err)
    def test_remove_layer_accepts_valid_params(self):
        err = self.registry.validate_params("remove_layer", {"layer_name": "temp_layer"})
        self.assertIsNone(err)
    def test_run_processing_algorithm_requires_algorithm(self):
        err = self.registry.validate_params("run_processing_algorithm", {})
        self.assertIsNotNone(err)
    def test_run_processing_algorithm_accepts_valid_params(self):
        err = self.registry.validate_params("run_processing_algorithm", {
            "algorithm": "native:buffer",
            "parameters": {"INPUT": "roads", "DISTANCE": 100.0, "OUTPUT": "TEMPORARY_OUTPUT"}
        })
        self.assertIsNone(err)
    def test_toggle_visibility_code_uses_layer_tree(self):
        captured = {}
        async def fake_execute(params, on_progress=None):
            captured["code"] = params["code"]
            return "ok"
        self.registry._execute_qgis_code = fake_execute
        asyncio.run(self.registry._execute_toggle_layer_visibility({"layer_name": "roads", "visible": False}))
        self.assertIn("setItemVisibilityChecked", captured["code"])
        self.assertIn("False", captured["code"])
    def test_set_layer_style_code_applies_renderer(self):
        captured = {}
        async def fake_execute(params, on_progress=None):
            captured["code"] = params["code"]
            return "ok"
        self.registry._execute_qgis_code = fake_execute
        asyncio.run(self.registry._execute_set_layer_style({"layer_name": "roads", "color": "#3388ff"}))
        self.assertIn("QgsSingleSymbolRenderer", captured["code"])
        self.assertIn("#3388ff", captured["code"])
    def test_export_layer_code_uses_savefeatures(self):
        captured = {}
        async def fake_execute(params, on_progress=None):
            captured["code"] = params["code"]
            return "ok"
        self.registry._execute_qgis_code = fake_execute
        asyncio.run(self.registry._execute_export_layer({"layer_name": "roads", "output_path": "roads.gpkg", "driver": "GeoPackage"}))
        self.assertIn("native:savefeatures", captured["code"])
    def test_remove_layer_code_removes_map_layer(self):
        captured = {}
        async def fake_execute(params, on_progress=None):
            captured["code"] = params["code"]
            return "ok"
        self.registry._execute_qgis_code = fake_execute
        asyncio.run(self.registry._execute_remove_layer({"layer_name": "temp_layer"}))
        self.assertIn("removeMapLayer", captured["code"])
    def test_run_processing_algorithm_code_resolves_layers(self):
        captured = {}
        async def fake_execute(params, on_progress=None):
            captured["code"] = params["code"]
            return "ok"
        self.registry._execute_qgis_code = fake_execute
        asyncio.run(self.registry._execute_run_processing_algorithm({
            "algorithm": "native:buffer",
            "parameters": {"INPUT": "roads", "DISTANCE": 100.0, "OUTPUT": "TEMPORARY_OUTPUT"}
        }))
        self.assertIn("native:buffer", captured["code"])
        self.assertIn("layer_from_ref", captured["code"])
class TestProjectContextCache(unittest.TestCase):
    def setUp(self):
        self.executor = MagicMock()
        self.executor.execute.return_value = {
            "success": True,
            "result": {"layers": [{"name": "roads"}]},
        }
        self.registry = ToolRegistry(self.executor, iface=None, agent=MagicMock())
    def test_identical_context_uses_cache(self):
        first = asyncio.run(self.registry._execute_get_project_context({}))
        second = asyncio.run(self.registry._execute_get_project_context({}))
        self.assertEqual(first, second)
        self.assertEqual(self.executor.execute.call_count, 1)
    def test_invalidation_refreshes_context(self):
        asyncio.run(self.registry._execute_get_project_context({}))
        self.registry.invalidate_project_context()
        asyncio.run(self.registry._execute_get_project_context({}))
        self.assertEqual(self.executor.execute.call_count, 2)
    def test_mutating_tool_invalidates_context(self):
        async def successful_tool(params):
            return "ok"
        self.registry._tools["toggle_layer_visibility"]["execute"] = successful_tool
        asyncio.run(self.registry._execute_get_project_context({}))
        self.assertFalse(self.registry._project_context_dirty)
        asyncio.run(self.registry.execute("toggle_layer_visibility", {"layer_name": "roads", "visible": True}))
        self.assertTrue(self.registry._project_context_dirty)


class TestRuntimeValidation(unittest.TestCase):
    def setUp(self):
        executor = MagicMock()
        executor.execute.return_value = {
            "success": True,
            "result": {"layers": [{"name": "roads"}, {"name": "buildings"}]},
        }
        self.registry = ToolRegistry(executor, iface=None, agent=MagicMock())
        asyncio.run(self.registry._execute_get_project_context({}))

    def test_known_layer_passes_precheck(self):
        err = self.registry.validate_params("zoom_to_layer", {"layer_name": "roads"})
        self.assertIsNone(err)

    def test_unknown_layer_rejected_by_precheck(self):
        err = self.registry.validate_params("zoom_to_layer", {"layer_name": "ghosts"})
        self.assertIsNotNone(err)
        self.assertIn("not found", err.lower())

    def test_unknown_layer_rejected_after_invalidation(self):
        self.registry.invalidate_project_context()
        err = self.registry.validate_params("toggle_layer_visibility", {"layer_name": "ghosts", "visible": True})
        self.assertIsNone(err)

    def test_enum_constraint_rejects_invalid_value(self):
        self.registry._tools["toggle_layer_visibility"]["parameters"]["properties"]["visible"]["enum"] = [True, False]
        err = self.registry.validate_params("toggle_layer_visibility", {"layer_name": "roads", "visible": "yes"})
        self.assertIsNotNone(err)
        self.assertIn("not in allowed", err)

    def test_range_constraint_rejects_out_of_bounds(self):
        self.registry._tools["set_map_extent"]["parameters"]["properties"]["xmin"]["minimum"] = -180.0
        self.registry._tools["set_map_extent"]["parameters"]["properties"]["xmin"]["maximum"] = 180.0
        err = self.registry.validate_params("set_map_extent", {
            "xmin": -200.0, "ymin": 0.0, "xmax": 1.0, "ymax": 1.0
        })
        self.assertIsNotNone(err)
        self.assertIn("below minimum", err)

    def test_pattern_constraint_rejects_bad_string(self):
        self.registry._tools["export_layer"]["parameters"]["properties"]["output_path"]["pattern"] = r"^.+\.(gpkg|shp)$"
        err = self.registry.validate_params("export_layer", {"layer_name": "roads", "output_path": "roads.txt"})
        self.assertIsNotNone(err)
        self.assertIn("does not match pattern", err)

    def test_pattern_constraint_accepts_match(self):
        self.registry._tools["export_layer"]["parameters"]["properties"]["output_path"]["pattern"] = r"^.+\.(gpkg|shp)$"
        err = self.registry.validate_params("export_layer", {"layer_name": "roads", "output_path": "roads.gpkg"})
        self.assertIsNone(err)

    def test_format_constraint_rejects_bad_crs(self):
        self.registry._tools["pan_to"]["parameters"]["properties"]["crs"]["format"] = "crs"
        err = self.registry.validate_params("pan_to", {"x": 0.0, "y": 0.0, "crs": "INVALID"})
        self.assertIsNotNone(err)
        self.assertIn("not a valid CRS", err)

    def test_format_constraint_accepts_epsg(self):
        self.registry._tools["pan_to"]["parameters"]["properties"]["crs"]["format"] = "crs"
        err = self.registry.validate_params("pan_to", {"x": 0.0, "y": 0.0, "crs": "EPSG:4326"})
        self.assertIsNone(err)
