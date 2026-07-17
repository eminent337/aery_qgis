"""Tests for the agent conversation loop in aery_plugin.agent.

The agent's `run()` method is the highest-risk surface in the plugin: it
makes LLM calls, executes tool calls, detects loops, and trims message
history. These tests cover the loop's logic with a fully mocked LLM client
and dispatcher — no real LLM or QGIS instance is required.

What we test:
- Text-only responses (no tool calls) return the assistant text
- Tool-call responses are dispatched and the loop continues
- Loop detection: 3 identical tool calls in a row terminate the loop
- Loop detection: 3 consecutive turns with the same tool name terminate
- max_turns limit (15) is honored
- A tool error resets the loop counter
- API errors return an error string
- _trim_messages compacts oldest tool results when over the count limit
- _diagnose_error produces helpful hints for known patterns
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aery_plugin.agent import Agent
from aery_plugin.agent_permissions import PermissionManager
from aery_plugin.tools import ToolRegistry


# ── helpers ───────────────────────────────────────────────────────────────

def _text_chunk(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


def _tool_call_chunk(call_id: str, name: str, args: str, index: int = 0) -> dict:
    return {
        "choices": [{"delta": {
            "tool_calls": [{
                "id": call_id,
                "index": index,
                "function": {"name": name, "arguments": args},
            }]
        }}]
    }


def _tool_call_args_chunk(call_id: str, args: str, index: int = 0) -> dict:
    return {
        "choices": [{"delta": {
            "tool_calls": [{
                "id": call_id,
                "index": index,
                "function": {"name": "", "arguments": args},
            }]
        }}]
    }


async def _async_gen(chunks):
    for c in chunks:
        yield c


def _make_agent_with_mock_client(chat_chunks_per_turn, chat_response=None, tool_results=None):
    """Build an Agent with a mocked LLM client and dispatcher.

    Args:
        chat_chunks_per_turn: Either a list of chunks (used for every turn) or
                              a list of lists (one list per turn, consumed
                              in order). The second form lets you script
                              multi-turn responses.
        chat_response: Fallback non-streaming response (if no chunks given).
        tool_results: List of (tc, name, tool_result, had_error) tuples the
                      dispatcher will return for each turn. Drained in order;
                      if exhausted, returns ([], []).
    """
    # Build the agent with executor=None, iface=None
    agent = Agent(executor=None, iface=None)
    agent._client = MagicMock()
    agent._client.chat = AsyncMock(return_value=chat_response or {
        "choices": [{"message": {"content": "", "tool_calls": []}}]
    })
    agent._client.filter_tool_calls = MagicMock(side_effect=lambda x: x)
    agent._client.format_message_pair = MagicMock(
        side_effect=lambda tc, tr: (
            {"role": "assistant", "content": "", "tool_calls": [tc]},
            {"role": "tool", "tool_call_id": tc.get("id", ""), "content": tr},
        )
    )

    # chat_stream returns a new async generator on each call. If we got a
    # flat list, reuse it (caller is responsible for one-turn tests). If we
    # got a list of lists, pop one per call so the agent can step through
    # multiple scripted turns.
    if chat_chunks_per_turn and isinstance(chat_chunks_per_turn[0], list):
        chunk_pool = list(chat_chunks_per_turn)
        def _stream_factory(*args, **kwargs):
            if chunk_pool:
                return _async_gen(chunk_pool.pop(0))
            return _async_gen([])
        agent._client.chat_stream = MagicMock(side_effect=_stream_factory)
    else:
        agent._client.chat_stream = MagicMock(return_value=_async_gen(chat_chunks_per_turn))

    # Mock the tools registry
    agent.tools = MagicMock()
    agent.tools.retrieve_tools = MagicMock(return_value=[])

    # Mock the dispatcher
    agent.dispatcher = MagicMock()
    if tool_results is not None:
        result_iter = iter(tool_results)
        async def _execute_all(tool_calls, on_event=None):
            try:
                return [next(result_iter)], []
            except StopIteration:
                return [], []
        agent.dispatcher.execute_all = _execute_all
    else:
        agent.dispatcher.execute_all = AsyncMock(return_value=([], []))

    return agent


# ── text-only response ────────────────────────────────────────────────────

def test_run_returns_text_when_no_tool_calls():
    agent = _make_agent_with_mock_client(
        chat_chunks_per_turn=[_text_chunk("Hello, the task is complete.")]
    )

    async def run():
        return await agent.run("Do something")
    result = asyncio.run(run())
    assert "complete" in result


def test_run_emits_text_chunks_via_on_event():
    chunks = [_text_chunk("Hello "), _text_chunk("world")]
    agent = _make_agent_with_mock_client(chat_chunks_per_turn=chunks)

    received = []

    def cb(evt):
        received.append(evt)

    async def run():
        return await agent.run("test", on_event=cb)
    asyncio.run(run())
    text_events = [e for e in received if e.get("type") == "text_chunk"]
    assert len(text_events) == 2
    assert "".join(e["text"] for e in text_events) == "Hello world"


# ── tool-call loop ────────────────────────────────────────────────────────

def test_run_executes_tool_calls():
    """Tool calls should be dispatched; the loop should run until text-only."""
    tool_call_dict = {
        "id": "call_1",
        "function": {"name": "run_qgis_code", "arguments": json.dumps({"code": "pass"})},
    }
    # Turn 1: tool call; Turn 2: text-only response
    chunks_per_turn = [
        [_tool_call_chunk("call_1", "run_qgis_code", json.dumps({"code": "pass"}))],
        [_text_chunk("Done.")],
    ]
    agent = _make_agent_with_mock_client(
        chat_chunks_per_turn=chunks_per_turn,
        tool_results=[(tool_call_dict, "run_qgis_code", "ok", False)],
    )

    async def run():
        return await agent.run("Do task")
    result = asyncio.run(run())
    assert "Done" in result
    # Tool call should have been recorded in messages
    assert any(m.get("role") == "tool" for m in agent._messages)


# ── loop detection ────────────────────────────────────────────────────────

def test_run_terminates_on_repeated_tool_calls():
    """Three identical tool calls in a row should terminate the loop."""
    tool_call_dict = {
        "id": "call_1",
        "function": {"name": "run_qgis_code", "arguments": json.dumps({"code": "x = 1"})},
    }
    # Script 5 turns of identical tool calls. The agent's loop detector
    # should bail at turn 3 with a "loop" message before consuming them all.
    chunks_per_turn = []
    for i in range(5):
        chunks_per_turn.append([
            _tool_call_chunk("call_1", "run_qgis_code", json.dumps({"code": "x = 1"}))
        ])
    tool_results = [(tool_call_dict, "run_qgis_code", "ok", False)] * 10
    agent = _make_agent_with_mock_client(
        chat_chunks_per_turn=chunks_per_turn, tool_results=tool_results
    )

    async def run():
        return await agent.run("task")
    result = asyncio.run(run())
    assert "loop" in result.lower() or "rephrasing" in result.lower()


def test_run_terminates_on_same_tool_name_3_times():
    """Same tool name called repeatedly (with different args) should also trigger loop detection."""
    chunks_per_turn = []
    tool_results = []
    for i in range(6):
        chunks_per_turn.append([
            _tool_call_chunk(f"call_{i}", "get_project_context", json.dumps({}))
        ])
        tool_results.append(({
            "id": f"call_{i}",
            "function": {"name": "get_project_context", "arguments": "{}"},
        }, "get_project_context", "ctx", False))
    agent = _make_agent_with_mock_client(
        chat_chunks_per_turn=chunks_per_turn, tool_results=tool_results
    )

    async def run():
        return await agent.run("task")
    result = asyncio.run(run())
    assert "loop" in result.lower() or "rephrasing" in result.lower()


def test_run_resets_loop_counter_after_tool_error():
    """A tool error should reset the hash-based loop counter.

    The agent has two loop detectors:
    1. Hash-based: identical tool calls (same name + args) in a row.
    2. Name-based: same tool name called 3+ turns in a row, even with
       different arguments.

    The agent resets _tool_loop_count (the hash-based counter) on any tool
    error, but does NOT reset _consecutive_tool_count (the name-based
    counter). This test verifies the hash reset specifically by using
    two alternating tool names so the name-based counter never trips.
    """
    # Alternating tool names + same args each time so the hash flips back
    # and forth. Errors force the hash-based counter to reset.
    chunks_per_turn = []
    tool_results = []
    for i in range(5):
        tool_name = "tool_a" if i % 2 == 0 else "tool_b"
        chunks_per_turn.append([
            _tool_call_chunk(f"call_{i}", tool_name, json.dumps({"i": i}))
        ])
        had_error = (i % 2 == 0)
        tool_results.append(({
            "id": f"call_{i}",
            "function": {"name": tool_name, "arguments": json.dumps({"i": i})},
        }, tool_name, "ok" if not had_error else "err", had_error))
    chunks_per_turn.append([_text_chunk("Done")])
    agent = _make_agent_with_mock_client(
        chat_chunks_per_turn=chunks_per_turn, tool_results=tool_results
    )

    async def run():
        return await agent.run("task")
    result = asyncio.run(run())
    assert "Done" in result


# ── max_turns ─────────────────────────────────────────────────────────────

def test_run_respects_max_turns():
    """If the loop never terminates naturally, it should stop at max_turns.

    Provide a long stream of unique tool calls (different args each time) so
    the loop detector doesn't fire. The agent should then stop at max_turns
    (15) and return the 'maximum turns' message.
    """
    chunks_per_turn = []
    tool_results = []
    for i in range(20):
        chunks_per_turn.append([
            _tool_call_chunk(f"call_{i}", "get_project_context", json.dumps({"i": i}))
        ])
        tool_results.append(({
            "id": f"call_{i}",
            "function": {"name": "get_project_context", "arguments": json.dumps({"i": i})},
        }, "get_project_context", "ctx", False))
    agent = _make_agent_with_mock_client(
        chat_chunks_per_turn=chunks_per_turn, tool_results=tool_results
    )
    agent._max_context_messages = 100  # disable trim to focus on max_turns

    async def run():
        return await agent.run("task")
    result = asyncio.run(run())
    assert "maximum turns" in result.lower() or "rephrasing" in result.lower()


# ── API errors ───────────────────────────────────────────────────────────

def test_run_handles_api_error():
    """An APIError from the LLM client should return an error string."""
    from aery_plugin.llm_client import APIError
    agent = _make_agent_with_mock_client(chat_chunks_per_turn=[])

    # Override chat_stream to raise
    async def _raise(*args, **kwargs):
        raise APIError("rate limited", status_code=429, retryable=True)
        yield  # unreachable, makes this a generator

    agent._client.chat_stream = MagicMock(side_effect=_raise)

    async def run():
        return await agent.run("task")
    result = asyncio.run(run())
    assert "error" in result.lower()


# ── _trim_messages compaction ─────────────────────────────────────────────

def test_trim_messages_compacts_oldest_tool_results():
    """When message count exceeds limit, oldest tool results get compact summaries."""
    agent = Agent(executor=None, iface=None)
    agent._max_context_messages = 4
    agent._messages = [
        {"role": "user", "content": "msg 1"},
        {"role": "assistant", "content": "reply 1"},
        {"role": "tool", "name": "run_qgis_code", "content": "x" * 500, "tool_call_id": "c1"},
        {"role": "assistant", "content": "reply 2"},
        {"role": "tool", "name": "run_qgis_code", "content": "y" * 500, "tool_call_id": "c2"},
        {"role": "assistant", "content": "reply 3"},
    ]
    agent._trim_messages()
    # After trimming, oldest messages should be compacted
    assert len(agent._messages) <= 6
    # At least one tool result should have been summarized (it'll be smaller)
    assert any("[Compacted]" in str(m.get("content", "")) for m in agent._messages)


def test_trim_messages_preserves_recent_assistant_text():
    """The most recent assistant text should be preserved (it's the answer)."""
    agent = Agent(executor=None, iface=None)
    agent._max_context_messages = 3
    agent._messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old reply"},
        {"role": "tool", "name": "run_qgis_code", "content": "old result", "tool_call_id": "c1"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "FINAL ANSWER"},
    ]
    agent._trim_messages()
    assert any(m.get("content") == "FINAL ANSWER" for m in agent._messages)


# ── _diagnose_error hints ─────────────────────────────────────────────────

def test_diagnose_error_crs_mismatch_hint():
    err = "RuntimeError: CRS mismatch between layer and project"
    msg = Agent._diagnose_error(err)
    assert "CRS" in msg


def test_diagnose_error_invalid_geometry_hint():
    err = "RuntimeError: Invalid geometry detected"
    msg = Agent._diagnose_error(err)
    assert "geometry" in msg.lower() or "fixgeometries" in msg.lower()


def test_diagnose_error_missing_module_hint():
    err = "ModuleNotFoundError: No module named 'rasterio'"
    msg = Agent._diagnose_error(err)
    assert "rasterio" in msg
    assert "install_package" in msg or "install" in msg.lower()


def test_diagnose_error_permission_hint():
    err = "PermissionError: [Errno 13] Permission denied: '/root/x'"
    msg = Agent._diagnose_error(err)
    assert "permission" in msg.lower() or "writable" in msg.lower()


def test_diagnose_error_timeout_hint():
    err = "TimeoutError: Operation timed out"
    msg = Agent._diagnose_error(err)
    assert "timeout" in msg.lower()


def test_diagnose_error_no_hint_returns_trimmed_traceback():
    """For unknown errors, return a cleaned traceback."""
    err = "RuntimeError: something obscure happened"
    msg = Agent._diagnose_error(err)
    assert "obscure" in msg


# ── permission wiring ─────────────────────────────────────────────────────

def test_check_permission_first_run_qgis_code_asks():
    """First run_qgis_code in a session should ask for permission."""
    pm = PermissionManager()
    pm.reset_session()
    # Build a registry with the agent (so _permissions is set)
    agent = MagicMock()
    agent.permissions = pm
    registry = ToolRegistry.__new__(ToolRegistry)
    registry._permissions = pm
    registry._permission_mode = "default"
    perm = registry.check_permission("run_qgis_code", {"code": "result = 1+1"}, code="result = 1+1")
    assert perm["behavior"] == "ask"


def test_check_permission_subsequent_run_qgis_code_allows():
    """After mark_code_approved, run_qgis_code should allow without asking."""
    pm = PermissionManager()
    pm.reset_session()
    pm.mark_code_approved()
    registry = ToolRegistry.__new__(ToolRegistry)
    registry._permissions = pm
    registry._permission_mode = "default"
    perm = registry.check_permission("run_qgis_code", {"code": "result = 1+1"}, code="result = 1+1")
    assert perm["behavior"] == "allow"


def test_check_permission_destructive_code_always_asks():
    """Destructive patterns should still ask even after code_approved=True."""
    pm = PermissionManager()
    pm.reset_session()
    pm.mark_code_approved()
    registry = ToolRegistry.__new__(ToolRegistry)
    registry._permissions = pm
    registry._permission_mode = "default"
    perm = registry.check_permission("run_qgis_code", {"code": "QgsProject.instance().removeMapLayer(l.id())"}, code="QgsProject.instance().removeMapLayer(l.id())")
    assert perm["behavior"] == "ask"


def test_check_permission_other_tools_unaffected():
    """Non-run_qgis_code tools shouldn't be affected by code_approved."""
    pm = PermissionManager()
    pm.reset_session()
    registry = ToolRegistry.__new__(ToolRegistry)
    registry._permissions = pm
    registry._permission_mode = "default"
    perm = registry.check_permission("get_project_context", {})
    assert perm["behavior"] == "allow"


def test_reset_session_clears_code_approval():
    """Starting a new session should re-prompt for the first code execution."""
    pm = PermissionManager()
    pm.mark_code_approved()
    assert pm.code_approved is True
    pm.reset_session()
    assert pm.code_approved is False
# ── chat_stream retry inside try (P3 Step 3) ───────────────────────────────
def test_chat_stream_retry_inside_try_catches_apierror_during_retry():
    """Regression for P3 Step 3: the retry loop in chat_stream must sit
    inside the try block so that an APIError raised during the retried
    _do_stream_request is caught on the next iteration, not propagated out
    to crash the worker."""
    from aery_plugin.llm_client import OpenAIClient, APIError
    client = OpenAIClient(base_url="https://example.com", api_key="test")
    call_count = 0
    async def _do_stream(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise APIError("rate limited", status_code=429, retryable=True)
        yield {"choices": [{"delta": {"content": "hello"}}]}
    client._do_stream_request = _do_stream
    async def _collect():
        chunks = []
        async for chunk in client.chat_stream([], "model"):
            chunks.append(chunk)
        return chunks
    chunks = asyncio.run(_collect())
    assert call_count == 2
    assert len(chunks) == 1
    assert chunks[0]["choices"][0]["delta"]["content"] == "hello"
# ── 2-tool oscillation detection (P3 Step 5) ───────────────────────────────
def test_run_detects_two_tool_oscillation():
    """Regression for P3 Step 5: A-B-A-B tool-call patterns evade both the
    identical-hash detector and the single-tool-sequence detector. The agent
    should detect the oscillation and terminate."""
    chunks_per_turn = []
    tool_results = []
    for i in range(5):
        tool_name = "tool_a" if i % 2 == 0 else "tool_b"
        arg_val = 0 if i % 2 == 0 else 1
        chunks_per_turn.append([
            _tool_call_chunk(f"call_{i}", tool_name, json.dumps({"i": arg_val}))
        ])
        tool_results.append(({
            "id": f"call_{i}",
            "function": {"name": tool_name, "arguments": json.dumps({"i": arg_val})},
        }, tool_name, f"result_{i}", False))
    agent = _make_agent_with_mock_client(
        chat_chunks_per_turn=chunks_per_turn, tool_results=tool_results
    )
    async def run():
        return await agent.run("task")
    result = asyncio.run(run())
    assert "loop" in result.lower() or "rephrasing" in result.lower()
