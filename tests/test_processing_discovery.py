"""Tests for the QGIS Processing Algorithm Discovery tool."""

import re


class TestProcessingDiscoveryModule:
    """Tests for the processing_discovery module structure."""

    def test_module_importable(self):
        """The module should import without errors outside QGIS."""
        from aery_plugin.processing_discovery import (
            PROCESSING_DISCOVERY_TOOLS,
            discover_qgis_algorithms,
            _get_parameter_type_name,
            _build_parameter_schema,
            _safe_value,
        )
        assert PROCESSING_DISCOVERY_TOOLS is not None
        assert callable(discover_qgis_algorithms)

    def test_tool_def_structure(self):
        """Each tool in PROCESSING_DISCOVERY_TOOLS must have the correct keys."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        expected_keys = {"name", "description", "parameters", "execute"}
        assert len(PROCESSING_DISCOVERY_TOOLS) >= 1
        for tool in PROCESSING_DISCOVERY_TOOLS:
            assert set(tool.keys()) == expected_keys, f"Tool {tool.get('name')} has wrong keys"

    def test_tool_name_nonempty(self):
        """Tool name must be a non-empty string."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        for tool in PROCESSING_DISCOVERY_TOOLS:
            assert isinstance(tool["name"], str) and len(tool["name"]) > 0

    def test_tool_description_nonempty(self):
        """Tool description must be a non-empty string."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        for tool in PROCESSING_DISCOVERY_TOOLS:
            desc = tool["description"]
            assert isinstance(desc, str) and len(desc) > 100

    def test_tool_execute_is_callable(self):
        """The execute field must be a callable function."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        for tool in PROCESSING_DISCOVERY_TOOLS:
            assert callable(tool["execute"]), f"Tool {tool['name']} execute is not callable"

    def test_tool_execute_takes_iface(self):
        """The execute function should accept an iface parameter (injected by executor)."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        for tool in PROCESSING_DISCOVERY_TOOLS:
            import inspect

            sig = inspect.signature(tool["execute"])
            assert "iface" in sig.parameters, f"Tool {tool['name']} missing iface param"


class TestToolParameterSchema:
    """Tests for the parameter schema (JSON Schema) of the tool."""

    def test_parameters_is_object(self):
        """The parameters schema must have type 'object'."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        for tool in PROCESSING_DISCOVERY_TOOLS:
            params = tool["parameters"]
            assert params["type"] == "object"

    def test_parameters_has_properties(self):
        """The parameters schema must define properties."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        for tool in PROCESSING_DISCOVERY_TOOLS:
            assert "properties" in tool["parameters"]
            props = tool["parameters"]["properties"]
            assert len(props) > 0

    def test_required_params_in_properties(self):
        """Required parameter names must exist in the properties dict."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        for tool in PROCESSING_DISCOVERY_TOOLS:
            params = tool["parameters"]
            if "required" in params:
                props = params["properties"]
                for req in params["required"]:
                    assert req in props, f"Required param '{req}' not in properties"

    def test_each_property_has_type(self):
        """Every parameter property must define a type."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        for tool in PROCESSING_DISCOVERY_TOOLS:
            for name, prop in tool["parameters"]["properties"].items():
                assert "type" in prop, f"Property '{name}' missing type"

    def test_all_properties_have_description(self):
        """Every parameter should have a description to guide the LLM."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        for tool in PROCESSING_DISCOVERY_TOOLS:
            for name, prop in tool["parameters"]["properties"].items():
                desc = prop.get("description", "")
                assert len(desc) > 10, f"Property '{name}' description too short: {desc}"


class TestParameterTypeMapping:
    """Tests for the parameter type name helper (no QGIS required)."""

    def test_safe_value_none(self):
        """None should remain None."""
        from aery_plugin.processing_discovery import _safe_value

        assert _safe_value(None) is None

    def test_safe_value_primitive(self):
        """Primitive values should pass through unchanged."""
        from aery_plugin.processing_discovery import _safe_value

        assert _safe_value(42) == 42
        assert _safe_value(3.14) == 3.14
        assert _safe_value("hello") == "hello"
        assert _safe_value(True) is True
        assert _safe_value(False) is False

    def test_safe_value_list(self):
        """Lists should pass through unchanged."""
        from aery_plugin.processing_discovery import _safe_value

        assert _safe_value([1, 2, 3]) == [1, 2, 3]
        assert _safe_value(["a", "b"]) == ["a", "b"]

    def test_safe_value_unserializable(self):
        """Non-serializable objects should be converted to string."""
        from aery_plugin.processing_discovery import _safe_value

        class Unserializable:
            def __repr__(self):
                return "Unserializable()"

        result = _safe_value(Unserializable())
        assert isinstance(result, str)
        assert "Unserializable" in result


class TestRegistration:
    """Tests for the registration in tools.py."""

    def test_register_processing_discovery_exists(self):
        """The _register_processing_discovery method should exist on ToolRegistry."""
        from aery_plugin.tools import ToolRegistry

        assert hasattr(ToolRegistry, "_register_processing_discovery")
        assert callable(ToolRegistry._register_processing_discovery)

    def test_registration_call_exists(self):
        """_register_core_tools should call _register_processing_discovery."""
        import inspect
        from aery_plugin.tools import ToolRegistry

        source = inspect.getsource(ToolRegistry._register_core_tools)
        assert "_register_processing_discovery" in source

    def test_registration_uses_geospatial_executor(self):
        """The registration should wrap executors via _make_geospatial_executor."""
        import inspect
        from aery_plugin.tools import ToolRegistry

        source = inspect.getsource(ToolRegistry._register_processing_discovery)
        assert "_make_geospatial_executor" in source


class TestDiscoverFunction:
    """Tests for the discover_qgis_algorithms function behavior."""

    def test_returns_dict(self):
        """The function should always return a dict (even if QGIS is not available)."""
        from aery_plugin.processing_discovery import discover_qgis_algorithms

        # When QGIS is not available, it returns an error dict
        result = discover_qgis_algorithms(keyword="buffer")
        assert isinstance(result, dict)
        assert "algorithms" in result

    def test_no_keyword_returns_error(self):
        """Calling without keyword or algorithm_id should return useful error."""
        from aery_plugin.processing_discovery import discover_qgis_algorithms

        result = discover_qgis_algorithms()
        assert "error" in result

    def test_unknown_algorithm_id_returns_error(self):
        """Unknown algorithm_id should return a helpful error."""
        from aery_plugin.processing_discovery import discover_qgis_algorithms

        result = discover_qgis_algorithms(algorithm_id="nonexistent:fake")
        assert "error" in result
        assert "suggestion" in result

    def test_algorithm_id_with_keyword(self):
        """algorithm_id should take priority over keyword."""
        from aery_plugin.processing_discovery import discover_qgis_algorithms

        result = discover_qgis_algorithms(
            keyword="buffer", algorithm_id="nonexistent:fake"
        )
        assert "error" in result
        assert "nonexistent:fake" in result["error"]

    def test_execute_is_same_as_function(self):
        """The TOOLS execute reference should point to discover_qgis_algorithms."""
        from aery_plugin.processing_discovery import (
            PROCESSING_DISCOVERY_TOOLS,
            discover_qgis_algorithms,
        )

        assert PROCESSING_DISCOVERY_TOOLS[0]["execute"] is discover_qgis_algorithms


class TestGetAlgorithmParameters:
    """Tests for the get_algorithm_parameters function."""

    def test_function_exists(self):
        """The function should be importable."""
        from aery_plugin.processing_discovery import get_algorithm_parameters

        assert callable(get_algorithm_parameters)

    def test_returns_dict(self):
        """Should always return a dict (even when QGIS is unavailable)."""
        from aery_plugin.processing_discovery import get_algorithm_parameters

        result = get_algorithm_parameters(algorithm_id="native:buffer")
        assert isinstance(result, dict)

    def test_no_algorithm_id_returns_error(self):
        """Calling without algorithm_id should return an error."""
        from aery_plugin.processing_discovery import get_algorithm_parameters

        result = get_algorithm_parameters()
        assert "error" in result

    def test_unknown_algorithm_returns_error(self):
        """Unknown algorithm_id should return a helpful error."""
        from aery_plugin.processing_discovery import get_algorithm_parameters

        result = get_algorithm_parameters(algorithm_id="nonexistent:fake")
        assert "error" in result
        assert "suggestion" in result

    def test_execute_is_same_as_function(self):
        """The TOOLS execute reference should point to get_algorithm_parameters."""
        from aery_plugin.processing_discovery import (
            PROCESSING_DISCOVERY_TOOLS,
            get_algorithm_parameters,
        )

        # Find the get_algorithm_parameters tool
        for tool in PROCESSING_DISCOVERY_TOOLS:
            if tool["name"] == "get_algorithm_parameters":
                assert tool["execute"] is get_algorithm_parameters
                break
        else:
            assert False, "get_algorithm_parameters not found in PROCESSING_DISCOVERY_TOOLS"

    def test_accepts_iface(self):
        """Should accept iface parameter for geospatial executor injection."""
        from aery_plugin.processing_discovery import get_algorithm_parameters

        import inspect
        sig = inspect.signature(get_algorithm_parameters)
        assert "iface" in sig.parameters

    def test_tool_description_algorithm_id_param(self):
        """The tool description should explain how to find algorithm IDs."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        tool = next(t for t in PROCESSING_DISCOVERY_TOOLS if t["name"] == "get_algorithm_parameters")
        desc = tool["description"]
        assert "discover_qgis_algorithms" in desc or "algorithm_id" in desc


class TestDocstringExamples:
    """Tests that example usage in docstrings/tool-descriptions is valid."""

    def test_description_mentions_run_processing_algorithm(self):
        """The tool description should guide users to use run_processing_algorithm."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        desc = PROCESSING_DISCOVERY_TOOLS[0]["description"]
        assert "run_processing_algorithm" in desc

    def test_description_explains_workflow(self):
        """The description should explain the search-then-execute workflow."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        desc = PROCESSING_DISCOVERY_TOOLS[0]["description"]
        assert "HOW TO USE" in desc or "search" in desc.lower()

    def test_parameter_descriptions_meaningful(self):
        """Parameter descriptions should be meaningful, not just "A parameter"."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS

        for tool in PROCESSING_DISCOVERY_TOOLS:
            for name, prop in tool["parameters"]["properties"].items():
                desc = prop.get("description", "")
                assert "parameter" not in desc[:20].lower() or len(desc) > 40, (
                    f"Property '{name}' description is generic: {desc}"
                )
