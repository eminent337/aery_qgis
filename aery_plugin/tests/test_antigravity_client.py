"""Tests for the Antigravity (Cloud Code Assist) client wire format.

Verifies the request body mirrors the main Aery agent
(packages/ai/src/providers/google-gemini-cli.ts): POST
{v1internal:streamGenerateContent?alt=sse} with a {project, model,
requestType:"agent", userAgent:"antigravity", requestId, request:{...}} body,
and that streaming emits OpenAI-style chunks agent.py can consume.
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import aery_plugin._http as httpx
from aery_plugin.llm_client import AntigravityClient, _split_data_uri


def _client():
    return AntigravityClient(api_key="tok-123", project_id="proj-42")


def _messages():
    return [
        {"role": "system", "content": "You are a QGIS assistant."},
        {"role": "user", "content": "what is this layer?"},
    ]


def test_split_data_uri():
    assert _split_data_uri("data:image/png;base64,AAAA") == ("image/png", "AAAA")
    assert _split_data_uri("data:image/jpeg;base64,XYZ") == ("image/jpeg", "XYZ")
    assert _split_data_uri("http://example.com/a.png") == ("", "")
    assert _split_data_uri("") == ("", "")


def test_build_payload_shape():
    c = _client()
    payload = c._build_payload(
        _messages(),
        "claude-sonnet-4-6",
        max_tokens=64000,
        temperature=0.2,
        tools=[{
            "type": "function",
            "function": {
                "name": "inspect_image",
                "description": "view an image",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }],
    )
    # Outer envelope mirrors the main agent's buildRequest for Antigravity.
    assert payload["project"] == "proj-42"
    assert payload["model"] == "claude-sonnet-4-6"
    assert payload["requestType"] == "agent"
    assert payload["userAgent"] == "antigravity"
    assert payload["requestId"].startswith("agent-")

    request = payload["request"]
    assert request["sessionId"].startswith("-")  # signed-decimal session id
    assert request["systemInstruction"]["parts"] == [{"text": "You are a QGIS assistant."}]
    assert request["generationConfig"]["maxOutputTokens"] == 64000
    assert request["generationConfig"]["temperature"] == 0.2

    decl = request["tools"][0]["functionDeclarations"][0]
    assert decl["name"] == "inspect_image"
    # Claude models use the legacy `parameters` key (mirrors convertTools).
    assert "parameters" in decl
    assert request["toolConfig"]["functionCallingConfig"]["mode"] == "VALIDATED"


def test_build_payload_non_claude_uses_parameters_json_schema():
    c = _client()
    payload = c._build_payload(
        _messages(), "gemini-3-flash", max_tokens=8192,
        tools=[{"type": "function", "function": {"name": "x", "description": "d", "parameters": {"type": "object"}}}],
    )
    decl = payload["request"]["tools"][0]["functionDeclarations"][0]
    assert "parametersJsonSchema" in decl
    assert payload["request"]["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"


def test_convert_messages_user_image_and_tool_flow():
    c = _client()
    contents, tool_map = c._convert_messages([
        {"role": "user", "content": [
            {"type": "text", "text": "see this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_image", "arguments": '{"path": "/tmp/a.png"}'}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ])
    assert tool_map == {"call_1": "read_image"}
    assert contents[0]["role"] == "user"
    assert contents[0]["parts"][1]["inlineData"] == {"mimeType": "image/png", "data": "AAAA"}
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["functionCall"]["name"] == "read_image"
    assert contents[1]["parts"][0]["functionCall"]["args"] == {"path": "/tmp/a.png"}
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "read_image"
    assert contents[2]["parts"][0]["functionResponse"]["response"]["output"] == "ok"


# ── Streaming (mocked transport) ──────────────────────────────────────────────

def _sse_lines():
    return [
        'data: ' + json.dumps({"response": {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}}),
        'data: ' + json.dumps({"response": {"candidates": [{"content": {"parts": [{"text": " world"}]}}]}}),
        'data: ' + json.dumps({"response": {"candidates": [{
            "content": {"parts": [{"functionCall": {"id": "fc1", "name": "read_image", "args": {"path": "/tmp/x.png"}}}]},
            "finishReason": "STOP",
        }]}}),
    ]


class _FakeResp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def aread(self):
        return b""

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, **kwargs):
        return _FakeResp(self._lines)

    async def aclose(self):
        pass


def _run(coro):
    return asyncio.run(coro)


def test_chat_stream_emits_openai_chunks():
    c = _client()
    chunks = []

    async def main():
        with patch.object(httpx, "AsyncClient", lambda **kw: _FakeClient(_sse_lines())):
            async for chunk in c.chat_stream(_messages(), "claude-sonnet-4-6", max_tokens=1000):
                chunks.append(chunk)

    _run(main())
    deltas = [ch["choices"][0]["delta"] for ch in chunks]
    assert deltas[0]["content"] == "Hello"
    assert deltas[1]["content"] == " world"
    tc = deltas[2]["tool_calls"][0]
    assert tc["id"] == "fc1"
    assert tc["function"]["name"] == "read_image"
    assert json.loads(tc["function"]["arguments"]) == {"path": "/tmp/x.png"}
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_chat_nonstream_aggregates():
    c = _client()

    async def main():
        with patch.object(httpx, "AsyncClient", lambda **kw: _FakeClient(_sse_lines())):
            return await c.chat(_messages(), "claude-sonnet-4-6")

    resp = _run(main())
    message = resp["choices"][0]["message"]
    assert message["content"] == "Hello world"
    assert message["tool_calls"][0]["function"]["name"] == "read_image"
    assert resp["choices"][0]["finish_reason"] == "tool_calls"


def test_http_error_raises_api_error():
    c = _client()

    class _ErrResp(_FakeResp):
        status_code = 429

        async def aread(self):
            return b'{"error": {"message": "rate limited"}}'

    class _ErrClient(_FakeClient):
        def stream(self, method, url, **kwargs):
            return _ErrResp(self._lines)

    async def main():
        with patch.object(httpx, "AsyncClient", lambda **kw: _ErrClient(_sse_lines())):
            async for _ in c.chat_stream(_messages(), "claude-sonnet-4-6"):
                pass

    from aery_plugin.llm_client import APIError
    try:
        _run(main())
        assert False, "expected APIError"
    except APIError as e:
        assert e.status_code == 429
        assert e.retryable is True
