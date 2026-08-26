"""Unit tests for multimodal image attachment and format_message_pair vision blocks."""

from aery_plugin.llm_client import OpenAIClient


def test_format_message_pair_with_vision_data():
    client = OpenAIClient(base_url="https://api.openai.com/v1", api_key="dummy")
    tool_call = {"id": "call_123", "function": {"name": "capture_canvas", "arguments": "{}"}}
    tool_result = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    
    pair = client.format_message_pair(tool_call, tool_result)
    assert len(pair) == 2
    assert pair[0]["role"] == "assistant"
    assert pair[1]["role"] == "tool"
    assert isinstance(pair[1]["content"], list)
    assert pair[1]["content"][1]["type"] == "image_url"
    assert pair[1]["content"][1]["image_url"]["url"] == tool_result
