"""Tests for the inspect_image tool — a port of the main Aery agent's
inspect-image.ts pattern: a SEPARATE one-shot vision completion that returns a
plain-text description, plus the _trim_messages image-payload protections.
"""

import os
import asyncio
import tempfile
from unittest.mock import MagicMock

import pytest
from PIL import Image

from aery_plugin.agent import (
    Agent,
    _is_image_data_uri,
    _content_has_image_blocks,
    _image_paths_from_message,
)
from aery_plugin.tools import ToolRegistry, _extract_chat_text
from aery_plugin.prompts import build_system_prompt


def _make_png(tmp_path=None, color="red", size=(100, 100)) -> str:
    """Write a small PNG and return its path."""
    path = tempfile.mktemp(suffix=".png", dir=tmp_path)
    img = Image.new("RGB", size, color=color)
    img.save(path, format="PNG")
    return path


def test_inspect_image_tool_registered():
    reg = ToolRegistry(executor=MagicMock())
    tool = reg._tools.get("inspect_image")
    assert tool is not None
    assert tool["execute"] == reg._execute_inspect_image
    assert "path" in tool["parameters"]["properties"]
    assert "question" in tool["parameters"]["properties"]


def test_extract_chat_text_openai_shape():
    resp = {"choices": [{"message": {"content": "A red circle on white."}}]}
    assert _extract_chat_text(resp) == "A red circle on white."


def test_extract_chat_text_anthropic_shape():
    resp = {"content": [{"type": "text", "text": "Two buildings."}]}
    assert _extract_chat_text(resp) == "Two buildings."


def test_extract_chat_text_gemini_shape():
    resp = {"candidates": [{"content": {"parts": [{"text": "A coastline."}]}}]}
    assert _extract_chat_text(resp) == "A coastline."


def test_extract_chat_text_empty_and_garbage():
    assert _extract_chat_text({}) == ""
    assert _extract_chat_text(None) == ""
    assert _extract_chat_text("not a dict") == ""


def test_execute_inspect_image_returns_text_from_vision_call(tmp_path):
    """The one-shot call must embed an image_url block and return the model's
    TEXT reply (never base64) as the tool result."""
    png = _make_png(tmp_path)
    client = MagicMock()
    async def _fake_chat(**kw):
        return {"choices": [{"message": {"content": "It is a solid red square."}}]}
    client.chat = MagicMock(side_effect=_fake_chat)
    agent = MagicMock()
    agent._client = client
    agent._model = "stepfun/step-3.7-flash"
    reg = ToolRegistry(executor=MagicMock(), agent=agent)

    result = asyncio.run(reg._execute_inspect_image({"path": png}))

    assert result == "It is a solid red square."
    # The one-shot payload must be a fresh user message with image_url content
    call_kwargs = client.chat.call_args.kwargs
    messages = call_kwargs["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    blocks = messages[0]["content"]
    types = [b.get("type") for b in blocks if isinstance(b, dict)]
    assert "image_url" in types
    image_block = next(b for b in blocks if b.get("type") == "image_url")
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,")
    # Default question embedded as the text block
    text_block = next(b for b in blocks if b.get("type") == "text")
    assert "Describe this image in detail" in text_block["text"]


def test_execute_inspect_image_custom_question(tmp_path):
    png = _make_png(tmp_path)
    client = MagicMock()
    async def _fake_chat(**kw):
        return {"choices": [{"message": {"content": "There are 3 trees."}}]}
    client.chat = MagicMock(side_effect=_fake_chat)
    agent = MagicMock()
    agent._client = client
    agent._model = "m"
    reg = ToolRegistry(executor=MagicMock(), agent=agent)

    result = asyncio.run(reg._execute_inspect_image({"path": png, "question": "How many trees?"}))
    assert result == "There are 3 trees."
    text_block = next(
        b for b in client.chat.call_args.kwargs["messages"][0]["content"]
        if isinstance(b, dict) and b.get("type") == "text"
    )
    assert text_block["text"] == "How many trees?"


def test_execute_inspect_image_requires_path():
    reg = ToolRegistry(executor=MagicMock())
    with pytest.raises(RuntimeError, match="requires a 'path'"):
        asyncio.run(reg._execute_inspect_image({}))


def test_execute_inspect_image_falls_back_without_client(tmp_path):
    """No live agent client -> return the data URI (legacy behaviour)."""
    png = _make_png(tmp_path)
    reg = ToolRegistry(executor=MagicMock(), agent=None)
    result = asyncio.run(reg._execute_inspect_image({"path": png}))
    assert result.startswith("data:image/png;base64,")


def test_execute_inspect_image_reports_vision_error(tmp_path):
    png = _make_png(tmp_path)
    client = MagicMock()
    client.chat = MagicMock(side_effect=RuntimeError("400 bad request"))
    agent = MagicMock()
    agent._client = client
    agent._model = "m"
    reg = ToolRegistry(executor=MagicMock(), agent=agent)

    result = asyncio.run(reg._execute_inspect_image({"path": png}))
    assert result.startswith("[inspect_image] vision request failed:")


# ── _trim_messages image-payload protections ───────────────────────────────

def test_is_image_data_uri_helpers():
    assert _is_image_data_uri("data:image/png;base64,AAAA")
    assert _is_image_data_uri("  data:image/jpeg;base64,BBBB")
    assert not _is_image_data_uri("data:application/json;base64,{}")
    assert not _is_image_data_uri("plain text")
    assert not _is_image_data_uri(None)
    assert _content_has_image_blocks([{"type": "text", "text": "x"},
                                      {"type": "image_url", "image_url": {"url": "u"}}])
    assert _content_has_image_blocks([{"type": "image", "data": "abc"}])
    assert _content_has_image_blocks("data:image/png;base64,AAAA")
    assert not _content_has_image_blocks([{"type": "text", "text": "x"}])
    assert not _content_has_image_blocks("plain text")


def test_trim_messages_never_truncates_image_data_uri_string():
    agent = Agent(None)
    huge_uri = "data:image/png;base64," + "A" * 50000  # > 8000-char cap
    agent._messages = [
        {"role": "tool", "content": huge_uri, "tool_call_id": "c1"},
    ]
    agent._trim_messages()
    assert agent._messages[0]["content"] == huge_uri


def test_trim_messages_drops_image_tool_message_whole():
    """Compaction must DROP image-bearing tool messages, never mangle the
    image_url list into a 120-char '[Compacted]' summary."""
    agent = Agent(None)
    agent._max_context_messages = 2
    huge_uri = "data:image/png;base64," + "A" * 400_000  # ~100k tokens -> force compaction
    agent._messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "tool",
         "content": [{"type": "text", "text": "read_image result"},
                     {"type": "image_url", "image_url": {"url": huge_uri}}],
         "tool_call_id": "c1", "name": "read_image"},
    ]
    agent._trim_messages()
    for m in agent._messages:
        assert not (isinstance(m.get("content"), str) and "[Compacted]" in m["content"])
        assert not (isinstance(m.get("content"), list) and
                    any(b.get("type") == "image_url" for b in m["content"]))


def test_trim_messages_keeps_image_data_uri_string_whole_under_pressure():
    """A raw data-URI string tool result is dropped whole too, not sliced."""
    agent = Agent(None)
    agent._max_context_messages = 2
    huge_uri = "data:image/png;base64," + "A" * 400_000
    agent._messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "tool", "content": huge_uri, "tool_call_id": "c1", "name": "read_image"},
    ]
    agent._trim_messages()
    for m in agent._messages:
        if isinstance(m.get("content"), str):
            assert "[Compacted]" not in m["content"]


def test_image_paths_from_message_extracts_paths():
    content = [
        {"type": "text", "text": "view this\n\n[Attached files:\n- /a/b.png\n- /c/d.jpg\n]"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
        {"type": "text", "text": "[Session attached files: Inspect the attached image file: /a/b.png; Inspect the attached image file: /c/d.jpg] If you cannot see the image pixels, call inspect_image(path=...)."},
    ]
    paths = _image_paths_from_message(content)
    assert "/a/b.png" in paths
    assert "/c/d.jpg" in paths
    assert _image_paths_from_message("plain text") == []
    assert _image_paths_from_message([{"type": "image_url", "image_url": {"url": "u"}}]) == []


def test_trim_messages_keeps_image_path_note_when_user_image_message_compacted():
    """Compaction dropping the multimodal user message must leave a compact
    TEXT note naming the attached file(s) so the model can re-view them via
    inspect_image(path=...). Silently losing the image AND its path is what
    made follow-up "view the image" prompts flail (e.g. drawing polygons)."""
    agent = Agent(None)
    agent._max_context_messages = 10  # token pressure (not count) forces compaction
    huge_uri = "data:image/png;base64," + "A" * 400_000  # ~100k tokens
    agent._messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "view this image\n\n[Attached files:\n- /tmp/photo.png\n]"},
            {"type": "image_url", "image_url": {"url": huge_uri}},
            {"type": "text", "text": "[Session attached files: Inspect the attached image file: /tmp/photo.png] If you cannot see the image pixels in your context, call inspect_image(path=...) to view it with a vision model."},
        ]},
        {"role": "assistant", "content": "I see it."},
        {"role": "tool", "content": "some result", "tool_call_id": "c1", "name": "get_project_context"},
    ]
    agent._trim_messages()
    # The image pixels are gone (dropped under pressure)...
    assert not any(
        isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "image_url" for b in m["content"])
        for m in agent._messages
    )
    # ...but a compact text note keeps the path + inspect_image hint, and no
    # mangled/truncated base64 survives.
    joined = " ".join(str(m.get("content")) for m in agent._messages)
    assert "/tmp/photo.png" in joined
    assert "inspect_image" in joined
    assert "[Compacted]" in joined
    assert "data:image/png" not in joined


# ── start_with_images explicit action + instruction ────────────────────────

def test_start_with_images_adds_action_and_inspect_instruction():
    agent = Agent(None)
    agent.start = MagicMock()
    agent.start_with_images("What is in this photo?",
                            [("/tmp/a.png", "data:image/png;base64,AAAA")])
    msg = agent._messages[-1]
    assert msg["role"] == "user"
    blocks = msg["content"]
    types = [b.get("type") for b in blocks if isinstance(b, dict)]
    assert "image_url" in types
    text = " ".join(b.get("text", "") for b in blocks
                    if isinstance(b, dict) and b.get("type") == "text")
    assert "Inspect the attached image file: /tmp/a.png" in text
    assert "inspect_image(path=...)" in text
    agent.start.assert_called_once()


# ── system prompt instructs inspect_image ──────────────────────────────────

def test_system_prompt_instructs_inspect_image():
    prompt = build_system_prompt("look at the attached image")
    assert "inspect_image" in prompt
    assert "NEVER" in prompt and "base64" in prompt
