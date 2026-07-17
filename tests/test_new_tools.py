"""Tests for the new processing tools: resolve_algorithm_param, summarize_processing_result, chain_processing_algorithms."""

import json


class TestResolveAlgorithmParam:
    """Tests for resolve_algorithm_param function (no QGIS required)."""

    def test_function_exists(self):
        """The function should be importable."""
        from aery_plugin.processing_discovery import resolve_algorithm_param
        assert callable(resolve_algorithm_param)

    def test_returns_dict(self):
        """Should always return a dict (even when QGIS is unavailable)."""
        from aery_plugin.processing_discovery import resolve_algorithm_param
        result = resolve_algorithm_param(algorithm_id="native:buffer", param_name="INPUT")
        assert isinstance(result, dict)

    def test_no_algorithm_id_returns_error(self):
        """Missing algorithm_id should return an error."""
        from aery_plugin.processing_discovery import resolve_algorithm_param
        result = resolve_algorithm_param()
        assert "error" in result

    def test_no_param_name_returns_error(self):
        """Missing param_name should return an error."""
        from aery_plugin.processing_discovery import resolve_algorithm_param
        result = resolve_algorithm_param(algorithm_id="native:buffer")
        assert "error" in result

    def test_unknown_algorithm_returns_error(self):
        """Unknown algorithm should return a helpful error."""
        from aery_plugin.processing_discovery import resolve_algorithm_param
        result = resolve_algorithm_param(algorithm_id="nonexistent:fake", param_name="INPUT")
        assert "error" in result
        assert "suggestion" in result

    def test_unknown_param_returns_error(self):
        """Unknown parameter should return a helpful error."""
        from aery_plugin.processing_discovery import resolve_algorithm_param
        result = resolve_algorithm_param(algorithm_id="native:buffer", param_name="DOES_NOT_EXIST")
        assert "error" in result

    def test_accepts_iface(self):
        """Should accept iface parameter for geospatial executor injection."""
        from aery_plugin.processing_discovery import resolve_algorithm_param
        import inspect
        sig = inspect.signature(resolve_algorithm_param)
        assert "iface" in sig.parameters

    def test_registered_in_tools(self):
        """Should be registered in PROCESSING_DISCOVERY_TOOLS."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS
        names = [t["name"] for t in PROCESSING_DISCOVERY_TOOLS]
        assert "resolve_algorithm_param" in names

    def test_tool_execute_is_callable(self):
        """The tool's execute should point to the function."""
        from aery_plugin.processing_discovery import (
            PROCESSING_DISCOVERY_TOOLS, resolve_algorithm_param
        )
        for tool in PROCESSING_DISCOVERY_TOOLS:
            if tool["name"] == "resolve_algorithm_param":
                assert tool["execute"] is resolve_algorithm_param
                break
        else:
            assert False, "resolve_algorithm_param not found in tools"


class TestSummarizeProcessingResult:
    """Tests for summarize_processing_result function."""

    def test_function_exists(self):
        """The function should be importable."""
        from aery_plugin.processing_discovery import summarize_processing_result
        assert callable(summarize_processing_result)

    def test_returns_dict(self):
        """Should always return a dict."""
        from aery_plugin.processing_discovery import summarize_processing_result
        result = summarize_processing_result(result_json='{"success": true}')
        assert isinstance(result, dict)

    def test_empty_input_returns_error(self):
        """Empty string should return an error."""
        from aery_plugin.processing_discovery import summarize_processing_result
        result = summarize_processing_result(result_json="")
        assert "error" in result

    def test_invalid_json_returns_error(self):
        """Invalid JSON should return an error."""
        from aery_plugin.processing_discovery import summarize_processing_result
        result = summarize_processing_result(result_json="not json")
        assert "error" in result

    def test_accepts_dict_directly(self):
        """Should accept a dict directly instead of a JSON string."""
        from aery_plugin.processing_discovery import summarize_processing_result
        result = summarize_processing_result(result_json={"success": True})
        assert isinstance(result, dict)

    def test_has_narrative(self):
        """Result should have a narrative field."""
        from aery_plugin.processing_discovery import summarize_processing_result
        result = summarize_processing_result(result_json='{"success": true, "OUTPUT": {"kind": "layer", "name": "buffer_out", "feature_count": 150}}')
        assert "narrative" in result
        assert "outputs" in result

    def test_detects_outputs(self):
        """Should detect and describe layer outputs."""
        from aery_plugin.processing_discovery import summarize_processing_result
        result = summarize_processing_result(
            result_json='{"OUTPUT": {"kind": "layer", "name": "buffer_out", "feature_count": 150}}'
        )
        assert len(result["outputs"]) > 0
        assert "buffer_out" in result["narrative"]

    def test_accepts_iface(self):
        """Should accept iface parameter."""
        from aery_plugin.processing_discovery import summarize_processing_result
        import inspect
        sig = inspect.signature(summarize_processing_result)
        assert "iface" in sig.parameters

    def test_registered_in_tools(self):
        """Should be registered in PROCESSING_DISCOVERY_TOOLS."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS
        names = [t["name"] for t in PROCESSING_DISCOVERY_TOOLS]
        assert "summarize_processing_result" in names

    def test_layer_detected_in_top_level(self):
        """Should detect layer outputs in top-level keys."""
        from aery_plugin.processing_discovery import summarize_processing_result
        data = {"OUTPUT": {"name": "buffer_out", "feature_count": 100}}
        result = summarize_processing_result(result_json=data)
        assert len(result["outputs"]) > 0

    def test_numeric_result(self):
        """Should handle numeric result values."""
        from aery_plugin.processing_discovery import summarize_processing_result
        data = {"COUNT": 42, "AREA": 1234.5}
        result = summarize_processing_result(result_json=data)
        assert len(result["outputs"]) > 0

    def test_wrong_type_returns_error(self):
        """Passing a wrong type should return an error."""
        from aery_plugin.processing_discovery import summarize_processing_result
        result = summarize_processing_result(result_json=123)
        assert "error" in result


class TestChainProcessingAlgorithms:
    """Tests for chain_processing_algorithms function."""

    def test_function_exists(self):
        """The function should be importable."""
        from aery_plugin.processing_discovery import chain_processing_algorithms
        assert callable(chain_processing_algorithms)

    def test_returns_dict(self):
        """Should always return a dict (even when QGIS is unavailable)."""
        from aery_plugin.processing_discovery import chain_processing_algorithms
        result = chain_processing_algorithms(steps=[
            {"algorithm_id": "native:buffer", "parameters": {"INPUT": "roads", "DISTANCE": 100}}
        ])
        assert isinstance(result, dict)

    def test_no_steps_returns_error(self):
        """Calling without steps should return an error."""
        from aery_plugin.processing_discovery import chain_processing_algorithms
        result = chain_processing_algorithms()
        assert "error" in result

    def test_empty_steps_returns_error(self):
        """Empty steps list should return an error."""
        from aery_plugin.processing_discovery import chain_processing_algorithms
        result = chain_processing_algorithms(steps=[])
        assert "error" in result

    def test_non_list_steps_returns_error(self):
        """Non-list steps should return an error."""
        from aery_plugin.processing_discovery import chain_processing_algorithms
        result = chain_processing_algorithms(steps="not a list")
        assert "error" in result

    def test_too_many_steps_returns_error(self):
        """More than 20 steps should be rejected."""
        from aery_plugin.processing_discovery import chain_processing_algorithms
        steps = [{"algorithm_id": "x", "parameters": {}} for _ in range(21)]
        result = chain_processing_algorithms(steps=steps)
        assert "error" in result

    def test_step_missing_algorithm_id(self):
        """Step without algorithm_id should produce an error."""
        from aery_plugin.processing_discovery import chain_processing_algorithms
        result = chain_processing_algorithms(steps=[{"parameters": {}}])
        assert "errors" in result

    def test_accepts_iface(self):
        """Should accept iface parameter."""
        from aery_plugin.processing_discovery import chain_processing_algorithms
        import inspect
        sig = inspect.signature(chain_processing_algorithms)
        assert "iface" in sig.parameters

    def test_registered_in_tools(self):
        """Should be registered in PROCESSING_DISCOVERY_TOOLS."""
        from aery_plugin.processing_discovery import PROCESSING_DISCOVERY_TOOLS
        names = [t["name"] for t in PROCESSING_DISCOVERY_TOOLS]
        assert "chain_processing_algorithms" in names

    def test_has_step_count(self):
        """Response should include step_count."""
        from aery_plugin.processing_discovery import chain_processing_algorithms
        result = chain_processing_algorithms(steps=[
            {"algorithm_id": "native:buffer", "parameters": {"INPUT": "roads"}}
        ])
        if "error" not in result:
            assert "step_count" in result or "steps_completed" in result

    def test_continue_on_error_flag(self):
        """continue_on_error flag should be accepted."""
        from aery_plugin.processing_discovery import chain_processing_algorithms
        result = chain_processing_algorithms(
            steps=[{"algorithm_id": "native:buffer", "parameters": {"INPUT": "roads"}}],
            continue_on_error=True
        )
        assert isinstance(result, dict)

import pytest
class TestGetLayerSchema:
    def test_get_layer_schema_execution(self):
        import asyncio
        from aery_plugin.tools import ToolRegistry
        from unittest.mock import MagicMock
        executor = MagicMock()
        executor.execute = MagicMock(return_value={"success": True, "result": {"id": "test_id", "fields": []}})
        reg = ToolRegistry(executor=executor)
        res = asyncio.run(reg._execute_get_layer_schema({"layer_name": "roads"}))
        parsed = json.loads(res)
        assert parsed["id"] == "test_id"
        executor.execute.assert_called_once_with("__get_layer_schema__:roads", 30)

class TestOutOfProcessGeoprocessing:
    def test_out_of_process_execution(self):
        import asyncio
        from aery_plugin.tools import ToolRegistry
        from unittest.mock import MagicMock, patch
        
        executor = MagicMock()
        executor.execute = MagicMock(return_value={"success": True, "result": {"is_memory": False, "resolved": {"INPUT": "/data/input.shp"}}})
        
        reg = ToolRegistry(executor=executor)
        
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = MagicMock(return_value=("output_log", "error_log"))
        
        with patch("shutil.which", return_value="/bin/qgis_process"), \
             patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
             
             res = asyncio.run(reg._execute_run_processing_algorithm({
                 "algorithm": "native:buffer",
                 "parameters": {"INPUT": "roads"},
                 "out_of_process": True
             }))
             
             assert "output_log" in res
             mock_popen.assert_called_once()
             cmd = mock_popen.call_args[0][0]
             assert cmd[0] == "/bin/qgis_process"
             assert cmd[1] == "run"
             assert cmd[2] == "native:buffer"
             assert "--INPUT=/data/input.shp" in cmd
