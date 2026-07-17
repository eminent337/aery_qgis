import pytest
from aery_plugin.llm_client import AnthropicClient, OpenAIClient

def test_transform_anthropic_chunk_tool_use():
    client = AnthropicClient(api_key="test")
    chunk = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {
            "type": "tool_use",
            "id": "tool_123",
            "name": "run_qgis_code"
        }
    }
    transformed = client._transform_anthropic_chunk(chunk, "", [])
    assert transformed["choices"][0]["delta"]["tool_calls"][0]["id"] == "tool_123"
    assert transformed["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "run_qgis_code"

def test_transform_anthropic_chunk_text():
    client = AnthropicClient(api_key="test")
    chunk = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {
            "type": "text_delta",
            "text": "Hello world"
        }
    }
    transformed = client._transform_anthropic_chunk(chunk, "", [])
    assert transformed["choices"][0]["delta"]["content"] == "Hello world"
