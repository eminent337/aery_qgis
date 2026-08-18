"""Tests for the GeoLibre-style typed tool surface (aery_plugin/tools_new.py).

These tests run without a live QGIS instance, so they exercise:
- Tool schema shape (OpenAI-format definitions)
- TypedToolBridge routing (read-only inline vs. mutating via executor)
- The `__tool__:` marshal format
- Destructive-tool permission behavior
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

from aery_plugin.tools_new import TypedToolBridge, create_tools, tool_schemas


class TestToolSchemas(unittest.TestCase):
    def test_create_tools_has_expected_names(self):
        names = {t.name for t in create_tools()}
        for expected in (
            "list_layers",
            "get_layer_schema",
            "set_layer_visibility",
            "capture_canvas",
            "zoom_to_layer",
            "zoom_to_place",
            "run_processing_algorithm",
            "load_basemap",
            "remove_layer",
            "apply_symbology",
        ):
            self.assertIn(expected, names)

    def test_unique_names(self):
        names = [t.name for t in create_tools()]
        self.assertEqual(len(names), len(set(names)), "Tool names must be unique")

    def test_openai_schema_shape(self):
        schemas = tool_schemas()
        self.assertGreater(len(schemas), 0)
        for s in schemas:
            self.assertEqual(s["type"], "function")
            fn = s["function"]
            self.assertIn("name", fn)
            self.assertIn("description", fn)
            self.assertEqual(fn["parameters"]["type"], "object")

    def test_required_params(self):
        tools = {t.name: t for t in create_tools()}
        # remove_layer requires 'layer'
        self.assertEqual(tools["remove_layer"].required, ["layer"])
        # run_processing_algorithm requires 'algorithm'
        self.assertEqual(tools["run_processing_algorithm"].required, ["algorithm"])

    def test_every_tool_has_handler(self):
        for t in create_tools():
            self.assertTrue(callable(t.handler), f"{t.name} missing handler")
            self.assertTrue(t.description, f"{t.name} missing description")


class TestTypedToolBridge(unittest.TestCase):
    def setUp(self):
        self.bridge = TypedToolBridge()

    def test_has_tool(self):
        self.assertTrue(self.bridge.has_tool("list_layers"))
        self.assertFalse(self.bridge.has_tool("does_not_exist"))

    def test_check_permission_allow_read_only(self):
        perm = self.bridge.check_permission("list_layers", {})
        self.assertEqual(perm["behavior"], "allow")
        perm = self.bridge.check_permission("get_layer_schema", {"layer": "x"})
        self.assertEqual(perm["behavior"], "allow")

    def test_check_permission_ask_destructive(self):
        perm = self.bridge.check_permission("remove_layer", {"layer": "x"})
        self.assertEqual(perm["behavior"], "ask")

    def test_bypass_permission_mode(self):
        self.bridge.set_permission_mode("bypassPermissions")
        perm = self.bridge.check_permission("remove_layer", {"layer": "x"})
        self.assertEqual(perm["behavior"], "allow")

    def test_dontask_permission_mode(self):
        self.bridge.set_permission_mode("dontAsk")
        perm = self.bridge.check_permission("list_layers", {})
        self.assertEqual(perm["behavior"], "deny")

    def test_execute_unknown_tool_raises(self):
        with self.assertRaises(ValueError):
            asyncio.run(self.bridge.execute("nope", {}))

    @patch("aery_plugin.tools_new._h_list_layers")
    def test_execute_read_only_runs_inline(self, mock_handler):
        mock_handler.return_value = {"layers": []}
        # Re-create the bridge inside the patch context so the handler
        # reference is bound to the mock (create_tools runs in __init__).
        bridge = TypedToolBridge()
        result = asyncio.run(bridge.execute("list_layers", {}))
        mock_handler.assert_called_once()
        self.assertIn("layers", json.loads(result))

    def test_marshal_format(self):
        from aery_plugin.tools_new import _marshal_tool_call
        code = _marshal_tool_call("remove_layer", {"layer": "roads"})
        self.assertTrue(code.startswith("__tool__:remove_layer:"))
        payload = code.split(":", 2)[2]
        self.assertEqual(json.loads(payload), {"layer": "roads"})


class TestMarshalRoundTrip(unittest.TestCase):
    """Verify the __tool__:<name>:<json> wire format parses back correctly."""

    def test_roundtrip(self):
        from aery_plugin.tools_new import _marshal_tool_call
        code = _marshal_tool_call("load_basemap", {"basemap": "osm"})
        rest = code[len("__tool__:"):]
        name, params_json = rest.split(":", 1)
        self.assertEqual(name, "load_basemap")
        self.assertEqual(json.loads(params_json), {"basemap": "osm"})


if __name__ == "__main__":
    unittest.main()