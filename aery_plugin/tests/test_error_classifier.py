"""Unit tests for aery_plugin.error_classifier."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aery_plugin.error_classifier import (
    classify,
    format_for_agent,
    format_structured,
    classify_from_string,
    wrap_tool_error,
)


class TestClassify(unittest.TestCase):
    def test_name_error(self):
        try:
            raise NameError("name 'river_lyr' is not defined")
        except NameError as e:
            env = classify(e)
        self.assertEqual(env["category"], "name_error")
        self.assertIn("river_lyr", env["hint"])
        self.assertFalse(env["retryable"])
        self.assertIn("NameError: name 'river_lyr'", env["message"])

    def test_type_error_keyword(self):
        try:
            raise TypeError("set_layer_style_simple() got an unexpected keyword argument 'color'")
        except TypeError as e:
            env = classify(e)
        self.assertEqual(env["category"], "type_error")
        self.assertIn("set_layer_style_simple", env["hint"])
        self.assertIn("'color'", env["hint"])

    def test_type_error_qgsfield(self):
        try:
            raise TypeError(
                "QgsField(): arguments did not match any overloaded call:\n"
                "  overload 1: argument 2 has unexpected type 'int'"
            )
        except TypeError as e:
            env = classify(e)
        self.assertEqual(env["category"], "type_error_qgsfield")
        self.assertIn("QMetaType.Type", env["hint"])

    def test_attribute_error(self):
        try:
            raise AttributeError(
                "'str' object has no attribute 'featureCount'"
            )
        except AttributeError as e:
            env = classify(e)
        self.assertEqual(env["category"], "attribute_error")
        self.assertIn("str", env["hint"])
        self.assertIn("resolve_layer", env["hint"])

    def test_key_error(self):
        try:
            raise KeyError("name")
        except KeyError as e:
            env = classify(e)
        self.assertEqual(env["category"], "key_error")
        self.assertIn("'name'", env["hint"])

    def test_index_error(self):
        try:
            raise IndexError("list index out of range")
        except IndexError as e:
            env = classify(e)
        self.assertEqual(env["category"], "index_error")
        self.assertIn("next(", env["hint"])

    def test_not_subscriptable(self):
        try:
            raise TypeError(
                "'QgsFeatureIterator' object is not subscriptable"
            )
        except TypeError as e:
            env = classify(e)
        self.assertEqual(env["category"], "not_subscriptable")
        self.assertIn("QgsFeatureIterator", env["hint"])
        self.assertIn("next(layer.getFeatures()", env["hint"])

    def test_import_error(self):
        try:
            raise ImportError("cannot import name 'QgsField'")
        except ImportError as e:
            env = classify(e)
        self.assertEqual(env["category"], "import_error")
        self.assertIn("QgsField", env["hint"])

    def test_sandbox_violation(self):
        try:
            raise RuntimeError("Sandbox violation: calling forbidden function 'type'")
        except RuntimeError as e:
            env = classify(e)
        self.assertEqual(env["category"], "sandbox_violation")
        self.assertIn("TOOL RULES", env["hint"])

    def test_algorithm_failed(self):
        try:
            raise RuntimeError("Algorithm 'native:buffer' failed")
        except RuntimeError as e:
            env = classify(e)
        self.assertEqual(env["category"], "algorithm_failed")
        self.assertIn("native:buffer", env["hint"])
        self.assertTrue(env["retryable"])

    def test_unknown(self):
        try:
            raise ValueError("Some random validation failure")
        except ValueError as e:
            env = classify(e)
        self.assertEqual(env["category"], "unknown")
        self.assertIsNone(env["hint"])
        self.assertFalse(env["retryable"])


class TestFormatters(unittest.TestCase):
    def test_format_for_agent_includes_hint(self):
        try:
            raise NameError("name 'foo' is not defined")
        except NameError as e:
            text = format_for_agent(e)
        self.assertIn("NameError:", text)
        self.assertIn("Hint:", text)
        self.assertIn("Category: name_error", text)
        self.assertIn("Retryable: no", text)

    def test_format_for_agent_unknown_has_placeholder(self):
        try:
            raise RuntimeError("Some random failure")
        except RuntimeError as e:
            text = format_for_agent(e)
        self.assertIn("(no hint available", text)
        self.assertIn("Category: unknown", text)

    def test_format_structured_is_valid_json(self):
        import json
        try:
            raise KeyError("missing_key")
        except KeyError as e:
            raw = format_structured(e)
        data = json.loads(raw)
        self.assertEqual(data["category"], "key_error")
        self.assertIn("missing_key", data["message"])
        self.assertFalse(data["retryable"])
        # Traceback is excluded from structured JSON
        self.assertNotIn("traceback", data)


class TestWrapToolError(unittest.TestCase):
    def test_wrap_adds_category_and_hint(self):
        out = wrap_tool_error(
            "run_qgis_code",
            "NameError: name 'river_lyr' is not defined",
        )
        self.assertIn("NameError:", out)
        self.assertIn("Hint:", out)
        self.assertIn("Category: name_error", out)
        self.assertIn("Tool: run_qgis_code", out)

    def test_wrap_marks_retryable(self):
        out = wrap_tool_error(
            "run_qgis_algorithms_by_id",
            "RuntimeError: Algorithm 'native:buffer' failed",
        )
        self.assertIn("Category: algorithm_failed", out)
        self.assertIn("transient", out.lower())

    def test_wrap_passthrough_unknown(self):
        msg = "Some unclassifiable failure message"
        out = wrap_tool_error("any_tool", msg)
        self.assertEqual(out, msg)

    def test_wrap_handles_empty(self):
        out = wrap_tool_error("any_tool", "")
        self.assertEqual(out, "")


class TestClassifyFromString(unittest.TestCase):
    def test_matches_runtimeerror_algorithm(self):
        env = classify_from_string("RuntimeError: Algorithm 'native:buffer' failed")
        self.assertEqual(env["category"], "algorithm_failed")
        self.assertTrue(env["retryable"])

    def test_matches_sandbox_violation(self):
        env = classify_from_string(
            'Sandbox violation: calling forbidden function "type"'
        )
        self.assertEqual(env["category"], "sandbox_violation")

    def test_unknown_for_garbage(self):
        env = classify_from_string("xyzzy nothing recognizable")
        self.assertEqual(env["category"], "unknown")
        self.assertIsNone(env["hint"])
