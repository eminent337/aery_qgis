"""Tests for explicit handling of truncated/malformed streaming chunks (P3 Step 4).

Previously, json.JSONDecodeError in _do_stream_request was silently ignored
with `pass`, so truncated SSE data frames never surfaced. Now partial JSON
lines are buffered and merged with the next line, malformed lines are logged
and counted, and too many malformed chunks raise a retryable APIError.
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from aery_plugin.llm_client import OpenAIClient, APIError


class _FakeResponse:
    def __init__(self, lines):
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}
        self._lines = lines

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _fake_client(resp):
    """Return a mock httpx.AsyncClient whose stream() context manager yields resp."""
    client = MagicMock()

    @asynccontextmanager
    async def _stream(*args, **kwargs):
        yield resp

    client.stream = _stream
    return client


def test_stream_repairs_truncated_json_across_lines():
    """A JSON object split across two SSE lines is buffered and parsed."""
    lines = [
        'data: {"choices": [{"delta": {"content": "hel',
        'lo"}}]}',
    ]
    client = OpenAIClient(base_url="https://example.com", api_key="test")
    client._client = _fake_client(_FakeResponse(lines))

    async def _collect():
        chunks = []
        async for chunk in client.chat_stream([], "model"):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())
    assert len(chunks) == 1
    assert chunks[0]["choices"][0]["delta"]["content"] == "hello"


def test_stream_logs_malformed_non_partial_chunks():
    """A line that is clearly not partial JSON is dropped and counted, not
    silently ignored forever."""
    lines = [
        'data: not-json-at-all',
        'data: {"choices": [{"delta": {"content": "ok"}}]}',
    ]
    client = OpenAIClient(base_url="https://example.com", api_key="test")
    client._client = _fake_client(_FakeResponse(lines))

    async def _collect():
        chunks = []
        async for chunk in client.chat_stream([], "model"):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())
    assert len(chunks) == 1
    assert chunks[0]["choices"][0]["delta"]["content"] == "ok"


def test_stream_raises_after_too_many_malformed_chunks():
    """More than 10 malformed chunks should raise a retryable APIError."""
    lines = ['data: garbage'] * 12
    client = OpenAIClient(base_url="https://example.com", api_key="test")
    client._client = _fake_client(_FakeResponse(lines))

    async def _collect():
        chunks = []
        async for chunk in client.chat_stream([], "model"):
            chunks.append(chunk)
        return chunks

    with pytest.raises(APIError, match="too many malformed chunks"):
        asyncio.run(_collect())
def test_stream_ignores_sse_comments_and_metadata_without_error():
    """SSE comments (like ': keep-alive' or ': ping') and event metadata
    (like 'event: ping') are valid protocol frames and must be ignored.
    Verify they do NOT count towards the malformed chunk limit (which would
    otherwise raise after 10 pings)."""
    lines = [
        ": keep-alive",
        "event: ping",
        ": keep-alive",
        "event: ping",
        ": keep-alive",
        ": keep-alive",
        ": keep-alive",
        ": keep-alive",
        ": keep-alive",
        ": keep-alive",
        ": keep-alive",
        ": keep-alive",
        ": keep-alive",  # 13 keep-alives/pings total (> 10)
        'data: {"choices": [{"delta": {"content": "ok"}}]}',
    ]
    client = OpenAIClient(base_url="https://example.com", api_key="test")
    client._client = _fake_client(_FakeResponse(lines))
    async def _collect():
        chunks = []
        async for chunk in client.chat_stream([], "model"):
            chunks.append(chunk)
        return chunks
    chunks = asyncio.run(_collect())
    assert len(chunks) == 1
    assert chunks[0]["choices"][0]["delta"]["content"] == "ok"
