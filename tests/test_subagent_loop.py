"""Mock-based integration tests for the sub-agent loop.

These tests simulate the LLM response cycle without needing a real provider,
QGIS, or any external service. They mock only the LLM client.chat() call
and test the actual _execute_subagent and _execute_process_workflow logic.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock


def _make_chat_response(content: str, tool_calls: list = None):
    """Build a mock LLM response dict."""
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "choices": [{"message": msg}]
    }


def _make_tool_call(name: str, args: dict, call_id: str = "call_1"):
    """Build a mock tool call dict."""
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": json.dumps(args),
        },
    }


def _make_registry(
    chat_responses: list = None,
    execute_results: dict = None,
):
    """Build a ToolRegistry with mocked agent, client, and optional responses.

    Args:
        chat_responses: List of dicts to return from chat() in sequence.
                        If None, returns empty content (no tool calls).
        execute_results: Dict mapping tool name -> result string.
                         If None, all tools return "mock result".
    """
    from aery_plugin.tools import ToolRegistry

    registry = ToolRegistry.__new__(ToolRegistry)
    registry._agent = MagicMock()
    registry._agent._model = "test-model"
    registry._agent._provider_id = "test-provider"

    # Mock the LLM client
    mock_client = MagicMock()

    if chat_responses:
        # Return responses in sequence
        mock_client.chat = AsyncMock(side_effect=chat_responses)
    else:
        # Default: respond with no tool calls on first try
        mock_client.chat = AsyncMock(return_value=_make_chat_response(
            "Task complete. Here are the results."
        ))

    registry._agent._client = mock_client

    # Mock execute to return controlled results
    async def _mock_execute(name: str, args: dict):
        if execute_results and name in execute_results:
            return execute_results[name]
        return "mock result for " + name

    registry.execute = _mock_execute

    # Helper to capture the sub-agent tool list
    original_list_tools = registry.list_tools if hasattr(registry, 'list_tools') else lambda: []
    registry.list_tools = MagicMock(return_value=[
        {"function": {"name": "discover_qgis_algorithms"}},
        {"function": {"name": "run_qgis_algorithms_by_id"}},
        {"function": {"name": "chain_processing_algorithms"}},
        {"function": {"name": "summarize_processing_result"}},
        {"function": {"name": "get_project_context"}},
        {"function": {"name": "subagent"}},  # should be filtered out
    ])

    return registry


class TestSubAgentSingleMode:
    """Test the sub-agent loop in 'single' mode."""

    def test_single_mode_returns_content_directly(self):
        """When LLM responds with content and no tool calls, return it directly."""
        registry = _make_registry(chat_responses=[
            _make_chat_response("Buffer complete. Created layer: buffered_roads."),
        ])

        async def run():
            result = await registry._execute_subagent({
                "task": "Buffer roads by 50m",
                "mode": "single",
            })
            assert "Buffer complete" in result
            assert "buffered_roads" in result
            return True

        assert asyncio.run(run())

    def test_single_mode_executes_tool_call(self):
        """Should execute tool calls and feed results back to LLM."""
        registry = _make_registry(chat_responses=[
            # Turn 1: LLM wants to discover algorithms
            _make_chat_response(
                "Let me find the right algorithm.",
                tool_calls=[_make_tool_call(
                    "discover_qgis_algorithms",
                    {"query": "buffer"},
                    call_id="call_1",
                )],
            ),
            # Turn 2: LLM responds with results (no tool calls -> done)
            _make_chat_response(
                "Found native:buffer. Running it now. Layer buffered_roads created.",
            ),
        ], execute_results={
            "discover_qgis_algorithms": '{"algorithms": [{"id": "native:buffer", "name": "Buffer"}]}',
        })

        async def run():
            result = await registry._execute_subagent({
                "task": "Buffer roads by 50m",
                "mode": "single",
            })
            assert "native:buffer" in result
            assert "buffered_roads" in result
            return True

        assert asyncio.run(run())

    def test_single_mode_handles_multi_turn_workflow(self):
        """Should handle multiple tool calls across turns (discover -> run)."""
        registry = _make_registry(chat_responses=[
            # Turn 1: discover
            _make_chat_response(
                "Let me find the algorithms.",
                tool_calls=[_make_tool_call(
                    "discover_qgis_algorithms", {"query": "buffer"}, "call_1"
                )],
            ),
            # Turn 2: discover result fed back, now run
            _make_chat_response(
                "Found it. Now running buffer.",
                tool_calls=[_make_tool_call(
                    "run_qgis_algorithms_by_id",
                    {"algorithm_id": "native:buffer", "parameters": {"INPUT": "roads", "DISTANCE": 50}},
                    "call_2",
                )],
            ),
            # Turn 3: done
            _make_chat_response(
                "Buffer complete. Output layer: buffered_roads.",
            ),
        ], execute_results={
            "discover_qgis_algorithms": '{"algorithms": [{"id": "native:buffer"}]}',
            "run_qgis_algorithms_by_id": '{"output": {"OUTPUT": "buffered_roads"}}',
        })

        async def run():
            result = await registry._execute_subagent({
                "task": "Buffer roads by 50m",
                "mode": "single",
            })
            assert "Buffer complete" in result
            assert "buffered_roads" in result
            return True

        assert asyncio.run(run())

    def test_single_mode_max_turns_reached(self):
        """Should return max-turns message when LLM keeps making tool calls."""
        # LLM keeps calling tools every turn (6 responses = 5 turns + 1 extra)
        registry = _make_registry(chat_responses=[
            _make_chat_response(
                "Thinking...",
                tool_calls=[_make_tool_call("discover_qgis_algorithms", {"query": "x"}, f"call_{i}")],
            )
            for i in range(6)
        ], execute_results={
            "discover_qgis_algorithms": '{"algorithms": []}',
        })

        async def run():
            result = await registry._execute_subagent({
                "task": "Do something",
                "mode": "single",
            })
            assert "maximum turns" in result.lower()
            return True

        assert asyncio.run(run())

    def test_single_mode_tool_error_continues_loop(self):
        """If a tool call raises, error should be fed back to LLM, not crash."""
        async def _mock_execute_failing(name: str, args: dict):
            if name == "discover_qgis_algorithms":
                raise RuntimeError("QGIS not available")
            return "ok"

        registry = _make_registry(chat_responses=[
            _make_chat_response(
                "Let me find it.",
                tool_calls=[_make_tool_call("discover_qgis_algorithms", {"query": "x"}, "call_1")],
            ),
            _make_chat_response(
                "Error occurred. Let me try a different approach.",
            ),
        ])
        registry.execute = _mock_execute_failing

        async def run():
            result = await registry._execute_subagent({
                "task": "Do something",
                "mode": "single",
            })
            assert "different approach" in result
            return True

        assert asyncio.run(run())

    def test_single_mode_subagent_tool_filtered(self):
        """The subagent tool should not be passed to the sub-agent (no recursion)."""
        registry = _make_registry()

        async def run():
            await registry._execute_subagent({
                "task": "test",
                "mode": "single",
            })
            # Verify subagent tool was filtered from the tools sent to LLM
            call_args = registry._agent._client.chat.call_args
            if call_args:
                tools = call_args[1].get("tools", [])
                names = [t["function"]["name"] for t in tools]
                assert "subagent" not in names
                assert "discover_qgis_algorithms" in names
            return True

        assert asyncio.run(run())


class TestSubAgentChainMode:
    """Test the sub-agent loop in 'chain' mode."""

    def test_chain_mode_executes_sequentially(self):
        """Chain mode should execute tasks sequentially with {previous} replacement."""
        registry = _make_registry(chat_responses=[
            _make_chat_response("Step 1 complete: roads buffered."),
            _make_chat_response("Step 2 complete: clipped to boundary."),
        ])

        async def run():
            result = await registry._execute_subagent({
                "mode": "chain",
                "chain": [
                    {"task": "Buffer roads by 50m"},
                    {"task": "Clip {previous} with boundary"},
                ],
            })
            assert "Step 1" in result
            assert "Step 2" in result
            return True

        assert asyncio.run(run())

    def test_chain_mode_replaces_previous(self):
        """{previous} should be replaced with the output of the prior step."""
        # Step 1 returns "roads buffered", step 2 returns "clipped"
        registry = _make_registry(chat_responses=[
            _make_chat_response("roads buffered"),
            _make_chat_response("clipped to boundary"),
        ])

        async def run():
            await registry._execute_subagent({
                "mode": "chain",
                "chain": [
                    {"task": "Buffer roads"},
                    {"task": "Clip {previous} with boundary"},
                ],
            })
            calls = registry._agent._client.chat.call_args_list
            # Should have 2 separate chat calls (one per chain step)
            assert len(calls) == 2
            # Second call should have {previous} replaced with step 1 output
            messages = calls[1][1]["messages"]
            user_msg = [m for m in messages if m["role"] == "user"]
            assert len(user_msg) == 1
            # The step 1 result should appear in the user message
            assert "roads buffered" in user_msg[0]["content"]
            return True

        assert asyncio.run(run())


class TestSubAgentParallelMode:
    """Test the sub-agent loop in 'parallel' mode."""

    def test_parallel_mode_runs_multiple_tasks(self):
        """Parallel mode should run tasks concurrently."""
        registry = _make_registry(chat_responses=[
            _make_chat_response("Task A: buffered."),
            _make_chat_response("Task B: clipped."),
            _make_chat_response("Task C: dissolved."),
        ])

        async def run():
            result = await registry._execute_subagent({
                "mode": "parallel",
                "tasks": [
                    {"task": "Buffer layer A"},
                    {"task": "Clip layer B"},
                    {"task": "Dissolve layer C"},
                ],
            })
            assert "Task A" in result
            assert "Task B" in result
            assert "Task C" in result
            return True

        assert asyncio.run(run())


class TestProcessWorkflowSubAgentIntegration:
    """Test that process_workflow correctly drives the sub-agent with a processing focus."""

    def test_process_workflow_uses_processing_prompt(self):
        """process_workflow should pass its specialized prompt to the sub-agent."""
        registry = _make_registry(chat_responses=[
            _make_chat_response("Buffered roads and clipped to zones."),
        ])

        async def run():
            await registry._execute_process_workflow({
                "task": "Buffer all major roads by 50m and clip against city zones"
            })

            # Verify the system prompt passed to chat contains processing tools
            call_args = registry._agent._client.chat.call_args
            messages = call_args[1]["messages"]
            system_msg = [m for m in messages if m["role"] == "system"]
            assert len(system_msg) == 1
            prompt = system_msg[0]["content"]

            assert "Processing Expert" in prompt
            assert "discover_qgis_algorithms" in prompt
            assert "run_qgis_algorithms_by_id" in prompt
            assert "chain_processing_algorithms" in prompt
            assert "WORKFLOW" in prompt
            return True

        assert asyncio.run(run())

    def test_process_workflow_full_multi_step_workflow(self):
        """Full simulation: process_workflow drives discover -> run -> summarize."""
        registry = _make_registry(chat_responses=[
            # Turn 1: discover
            _make_chat_response(
                "Let me find buffer and clip algorithms.",
                tool_calls=[_make_tool_call(
                    "discover_qgis_algorithms", {"query": "buffer"}, "call_1"
                )],
            ),
            # Turn 2: discover result fed back, now get params
            _make_chat_response(
                "Found native:buffer. Let me check its parameters.",
                tool_calls=[_make_tool_call(
                    "get_algorithm_parameters",
                    {"algorithm_id": "native:buffer"},
                    "call_2",
                )],
            ),
            # Turn 3: now run
            _make_chat_response(
                "Parameters look good. Running buffer.",
                tool_calls=[_make_tool_call(
                    "run_qgis_algorithms_by_id",
                    {"algorithm_id": "native:buffer", "parameters": {"INPUT": "roads", "DISTANCE": 50}},
                    "call_3",
                )],
            ),
            # Turn 4: summarize
            _make_chat_response(
                "Buffer done. Let me summarize.",
                tool_calls=[_make_tool_call(
                    "summarize_processing_result",
                    {"result_json": '{"output": {"OUTPUT": "buffered_roads"}}'},
                    "call_4",
                )],
            ),
            # Turn 5: done
            _make_chat_response(
                "Complete. Created buffered_roads with 150 features.",
            ),
        ], execute_results={
            "discover_qgis_algorithms": '{"algorithms": [{"id": "native:buffer", "name": "Buffer"}]}',
            "get_algorithm_parameters": '{"parameters": {"INPUT": {"type": "source"}, "DISTANCE": {"type": "distance"}}}',
            "run_qgis_algorithms_by_id": '{"output": {"OUTPUT": "buffered_roads"}, "feature_count": 150}',
            "summarize_processing_result": "Created layer 'buffered_roads' with 150 features from buffering 'roads'.",
        })

        async def run():
            result = await registry._execute_process_workflow({
                "task": "Buffer roads by 50m"
            })
            assert "buffered_roads" in result or "Complete" in result or "150" in result
            return True

        assert asyncio.run(run())

    def test_process_workflow_no_llm_client(self):
        """Should propagate error when no LLM client is available."""
        from aery_plugin.tools import ToolRegistry

        async def run():
            registry = ToolRegistry.__new__(ToolRegistry)
            registry._agent = None
            result = await registry._execute_process_workflow({
                "task": "Buffer roads"
            })
            assert "active LLM client" in result or "Error" in result
            return True

        assert asyncio.run(run())
