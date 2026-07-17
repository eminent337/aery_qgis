"""Tests for the process_workflow tool."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestProcessWorkflowRegistration:
    """Verify the tool is correctly registered in _register_processing_discovery."""

    def test_process_workflow_tool_registered(self):
        """The process_workflow tool should be registered by _register_processing_discovery."""
        from aery_plugin.tools import ToolRegistry

        # Verify the class has the execute method
        assert hasattr(ToolRegistry, "_execute_process_workflow")
        assert callable(ToolRegistry._execute_process_workflow)

    def test_process_workflow_is_coroutine(self):
        """The execute method should be an async coroutine function."""
        from aery_plugin.tools import ToolRegistry
        import inspect

        assert inspect.iscoroutinefunction(
            ToolRegistry._execute_process_workflow
        ), "_execute_process_workflow must be async def"

    def test_register_method_exists(self):
        """The _register_processing_discovery method should exist and be callable."""
        from aery_plugin.tools import ToolRegistry

        assert hasattr(ToolRegistry, "_register_processing_discovery")
        assert callable(ToolRegistry._register_processing_discovery)


class TestProcessWorkflowValidation:
    """Tests for parameter validation in _execute_process_workflow."""

    def test_empty_task_returns_error(self):
        """Empty or missing task should return an error string immediately."""
        from aery_plugin.tools import ToolRegistry

        async def run():
            registry = ToolRegistry.__new__(ToolRegistry)
            registry._agent = None
            result = await registry._execute_process_workflow({"task": ""})
            assert "required" in result.lower()
            assert "task" in result.lower()

            result2 = await registry._execute_process_workflow({})
            assert "required" in result2.lower()
            return True

        assert asyncio.run(run())

    def test_no_agent_returns_error(self):
        """When _agent is None, should propagate error from _execute_subagent."""
        from aery_plugin.tools import ToolRegistry

        async def run():
            registry = ToolRegistry.__new__(ToolRegistry)
            registry._agent = None
            result = await registry._execute_process_workflow({
                "task": "buffer a layer"
            })
            # Should reach _execute_subagent which checks for _agent
            assert "active LLM client" in result or "Error" in result
            return True

        assert asyncio.run(run())


class TestProcessWorkflowDelegation:
    """Tests that _execute_process_workflow delegates correctly to _execute_subagent."""

    def test_delegates_with_correct_params(self):
        """Should call _execute_subagent with task, system prompt, and mode=single."""
        from aery_plugin.tools import ToolRegistry

        async def run():
            registry = ToolRegistry.__new__(ToolRegistry)

            # Mock _execute_subagent to capture params
            mock = AsyncMock(return_value="mock result")
            registry._execute_subagent = mock
            registry._agent = MagicMock()
            registry._agent._client = MagicMock()

            result = await registry._execute_process_workflow({
                "task": "buffer roads by 50m and clip against zones"
            })

            # Verify delegation
            mock.assert_awaited_once()
            call_args = mock.call_args[0][0]  # first positional arg (the params dict)

            assert call_args["task"] == "buffer roads by 50m and clip against zones"
            assert call_args["mode"] == "single"
            assert call_args["system"] is not None
            assert "Processing Expert" in call_args["system"]
            assert "discover_qgis_algorithms" in call_args["system"]
            assert "run_qgis_algorithms_by_id" in call_args["system"]
            assert "chain_processing_algorithms" in call_args["system"]
            assert "WORKFLOW" in call_args["system"]

            assert result == "mock result"
            return True

        assert asyncio.run(run())

    def test_system_prompt_contains_all_tools(self):
        """The system prompt should list all the key processing tools."""
        from aery_plugin.tools import ToolRegistry

        async def run():
            registry = ToolRegistry.__new__(ToolRegistry)
            registry._execute_subagent = AsyncMock(return_value="")
            registry._agent = MagicMock()
            registry._agent._client = MagicMock()

            await registry._execute_process_workflow({
                "task": "test task"
            })

            call_args = registry._execute_subagent.call_args[0][0]
            prompt = call_args["system"]

            # All these tools should be mentioned in the prompt
            expected_tools = [
                "discover_qgis_algorithms",
                "get_algorithm_parameters",
                "resolve_algorithm_param",
                "validate_algorithm_run",
                "run_qgis_algorithms_by_id",
                "chain_processing_algorithms",
                "summarize_processing_result",
                "run_qgis_code",
                "capture_canvas",
            ]
            for tool in expected_tools:
                assert tool in prompt, f"Tool '{tool}' missing from system prompt"

            # The workflow steps should be mentioned
            workflow_steps = [
                "Understand", "Discover", "Plan", "Resolve", "Execute", "Verify", "Report"
            ]
            for step in workflow_steps:
                assert step in prompt, f"Workflow step '{step}' missing from system prompt"

            return True

        assert asyncio.run(run())

    def test_preserves_exact_task_string(self):
        """The task string should be passed verbatim to _execute_subagent."""
        from aery_plugin.tools import ToolRegistry

        async def run():
            registry = ToolRegistry.__new__(ToolRegistry)
            registry._execute_subagent = AsyncMock(return_value="")
            registry._agent = MagicMock()
            registry._agent._client = MagicMock()

            complex_task = "Buffer layer_A by 100 meters, then clip with boundary_Z, finally dissolve by field 'category'"
            await registry._execute_process_workflow({
                "task": complex_task
            })

            call_args = registry._execute_subagent.call_args[0][0]
            assert call_args["task"] == complex_task
            return True

        assert asyncio.run(run())


class TestProcessWorkflowErrorHandling:
    """Tests that the delegation properly handles sub-agent errors."""

    def test_subagent_exception_propagates(self):
        """If _execute_subagent raises, the method should propagate it."""
        from aery_plugin.tools import ToolRegistry

        async def run():
            registry = ToolRegistry.__new__(ToolRegistry)

            # Mock to raise an exception
            async def mock_subagent(params):
                raise RuntimeError("sub-agent failed")
            registry._execute_subagent = mock_subagent
            registry._agent = MagicMock()
            registry._agent._client = MagicMock()

            with pytest.raises(RuntimeError, match="sub-agent failed"):
                await registry._execute_process_workflow({
                    "task": "buffer a layer"
                })

            return True

        assert asyncio.run(run())

    def test_subagent_error_string_passed_through(self):
        """Error strings from _execute_subagent should be returned as-is."""
        from aery_plugin.tools import ToolRegistry

        async def run():
            registry = ToolRegistry.__new__(ToolRegistry)

            mock = AsyncMock(return_value="Error: subagent requires an active LLM client")
            registry._execute_subagent = mock
            registry._agent = MagicMock()
            registry._agent._client = MagicMock()

            result = await registry._execute_process_workflow({
                "task": "buffer a layer"
            })

            assert "active LLM client" in result
            return True

        assert asyncio.run(run())

    def test_none_task_returns_error(self):
        """If task is None (not just empty), should return error."""
        from aery_plugin.tools import ToolRegistry

        async def run():
            registry = ToolRegistry.__new__(ToolRegistry)
            registry._agent = None
            result = await registry._execute_process_workflow({"task": None})
            assert "required" in result.lower()
            return True

        assert asyncio.run(run())
