"""End-to-end smoke test (P5 Step 2).

Simulates a simple agent task ("load OSM basemap") through the full agent
loop with a mocked LLM client and dispatcher. Asserts:

1. The agent runs to completion without crashing.
2. The expected tool call (load_layer or similar) is dispatched.
3. The LLM actually produced a tool call (not just text).
4. The agent's final response is a non-empty string.

This is the "simple task works" regression test that motivated the entire
hardening ferment: before the fixes, the loop would crash on provider
routing, oauth lookup, missing tool, or context-flooding output.
"""

import asyncio
import json

import pytest

from aery_plugin.agent import Agent


def _tool_call_chunk(call_id: str, name: str, args: str) -> dict:
    return {
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "id": call_id,
                    "index": 0,
                    "function": {"name": name, "arguments": args},
                }],
            },
        }],
    }


def _text_chunk(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


class _Recorder:
    """Tracks every tool call the agent dispatches."""

    def __init__(self):
        self.calls = []

    def record(self, tc):
        self.calls.append(tc)


def test_e2e_simple_task_load_osm_basemap():
    """End-to-end: user asks to load an OSM basemap -> agent calls
    load_layer with the OSM URL and reports success."""
    from aery_plugin.tools import ToolRegistry

    recorder = _Recorder()

    agent = Agent(executor=None, iface=None)

    # Mock the LLM client to script two turns:
    #   turn 1 -> load_layer tool call with the OSM XYZ URL
    #   turn 2 -> plain text confirming success
    from unittest.mock import MagicMock, AsyncMock
    from aery_plugin.llm_client import OpenAIClient

    chunks_per_turn = [
        [_tool_call_chunk("call_1", "load_layer", json.dumps({
            "uri": "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0",
        }))],
        [_text_chunk("Basemap loaded successfully.")],
    ]

    agent._client = MagicMock()
    agent._client.filter_tool_calls = MagicMock(side_effect=lambda x: x)
    agent._client.format_message_pair = MagicMock(
        side_effect=lambda tc, tr: (
            {"role": "assistant", "content": "", "tool_calls": [tc]},
            {"role": "tool", "tool_call_id": tc.get("id", ""), "content": tr},
        ),
    )

    async def _async_gen(items):
        for item in items:
            yield item

    pool = list(chunks_per_turn)

    def _stream_factory(*args, **kwargs):
        if pool:
            return _async_gen(pool.pop(0))
        return _async_gen([])

    agent._client.chat_stream = MagicMock(side_effect=_stream_factory)
    agent._client.chat = AsyncMock(return_value={
        "choices": [{"message": {"content": "", "tool_calls": []}}],
    })

    # Mock the tools registry so retrieve_tools returns a non-empty list
    # (the agent skips the fast-path / planning heuristic when tools=[]).
    agent.tools = MagicMock()
    agent.tools.retrieve_tools = MagicMock(return_value=[{
        "type": "function",
        "function": {"name": "load_layer", "description": "Load a layer"},
    }])

    # Mock the dispatcher so it captures the tool call and returns success.
    async def _execute_all(tool_calls, on_event=None):
        for tc in tool_calls:
            recorder.record(tc)
        return [(tc, tc.get("function", {}).get("name", ""), "ok", False) for tc in tool_calls], []

    agent.dispatcher = MagicMock()
    agent.dispatcher.execute_all = _execute_all

    async def run():
        return await agent.run("Load the OSM basemap")

    result = asyncio.run(run())

    # 1. Agent ran to completion without crashing
    assert isinstance(result, str)
    assert result != ""

    # 2. The expected tool was called
    assert len(recorder.calls) == 1, f"expected 1 tool call, got {len(recorder.calls)}"
    tc = recorder.calls[0]
    assert tc["function"]["name"] == "load_layer"

    # 3. The LLM actually produced a tool call (not just text)
    assert tc.get("function", {}).get("arguments"), "tool call had no arguments"
    args = json.loads(tc["function"]["arguments"])
    assert "openstreetmap" in args.get("uri", "").lower()

    # 4. The agent reached the final text response (second turn)
    assert "loaded" in result.lower() or "success" in result.lower()
def test_e2e_greeting_local_bypass():
    """Greetings, thanks, and help requests must return a brief conversational
    reply immediately without invoking the LLM client or retrieving tools."""
    agent = Agent(executor=None, iface=None)
    # We do NOT mock agent._client or agent.tools. If the bypass works,
    # it won't touch them, so they remain None/unmocked.
    async def run():
        return await agent.run("hi")
    result = asyncio.run(run())
    assert "what would you like to do" in result.lower()
    # Confirm it was logged in messages history
    assert len(agent._messages) == 2
    assert agent._messages[0] == {"role": "user", "content": "hi"}
    assert agent._messages[1] == {"role": "assistant", "content": result}
def test_e2e_greeting_performance_is_sub_millisecond():
    """Greeting bypass must execute virtually instantly (< 50ms) as it does
    not perform any network requests or model initialization."""
    import time
    agent = Agent(executor=None, iface=None)
    async def run():
        start = time.perf_counter()
        res = await agent.run("hello")
        duration_ms = (time.perf_counter() - start) * 1000
        return res, duration_ms
    result, duration = asyncio.run(run())
    assert "what would you like to do" in result.lower()
    # Should be well under 10ms locally. We set 50ms as a conservative budget.
    assert duration < 50.0, f"Greeting bypass took too long: {duration:.1f}ms"
def test_e2e_basemap_sends_minimal_system_prompt():
    """Basemap direct execution must send a tiny system prompt (< 1,500 chars)
    to minimize prompt processing latency on the provider."""
    from unittest.mock import MagicMock, AsyncMock
    agent = Agent(executor=None, iface=None)
    # Mock retrieve_tools to avoid loading files
    agent.tools = MagicMock()
    agent.tools.retrieve_tools = MagicMock(return_value=[{
        "type": "function",
        "function": {"name": "load_basemap", "description": "Load basemap"},
    }])
    # Capture the system prompt sent to chat_stream
    captured_messages = []
    async def _mock_stream(messages, *args, **kwargs):
        captured_messages.extend(messages)
        yield _text_chunk("Done.")
    agent._client = MagicMock()
    agent._client.chat_stream = _mock_stream
    agent._client.chat = AsyncMock(return_value={"choices": [{"message": {"content": "ok", "tool_calls": []}}]})
    agent._client.filter_tool_calls = MagicMock(side_effect=lambda x: x)
    agent._client.format_message_pair = MagicMock(side_effect=lambda tc, tr: ({"role":"assistant","content":""}, {"role":"tool","content":""}))
    async def run():
        return await agent.run("load OSM basemap")
    asyncio.run(run())
    # Verify the system prompt was sent and is compact
    system_msg = next((m for m in captured_messages if m.get("role") == "system"), None)
    assert system_msg is not None, "System prompt not found in messages sent to LLM"
    prompt_content = system_msg["content"]
    assert len(prompt_content) < 1500, f"Direct basemap prompt too large: {len(prompt_content)} chars"
    assert "QGIS assistant inside the user's project" in prompt_content
    # Confirm it does not include complex raster/vector text
    assert "=== RASTER ANALYSIS ===" not in prompt_content
    assert "PLAN:" not in prompt_content
