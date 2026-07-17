"""Tests for create_client model-family routing on google-antigravity.

Regression guard for the bug where non-Gemini models (Claude, GPT-OSS)
on the google-antigravity provider were routed to GeminiClient pointed
at the native Gemini API, producing a guaranteed 404 on every request.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

import aery_plugin
from aery_plugin import llm_client
from aery_plugin.llm_client import (
    create_client,
    AnthropicClient,
    OpenAIClient,
    GeminiClient,
    APIError,
)


def _dummy_auth():
    """Auth entry with a direct API key so _resolve_api_key returns it
    without touching OAuth refresh paths."""
    return {"type": "api_key", "key": "test-key-123"}


def test_claude_model_routes_to_anthropic_client():
    """claude-sonnet-4-6 on google-antigravity must use AnthropicClient,
    NOT GeminiClient, and must use the Antigravity gateway base_url."""
    client, model = create_client("google-antigravity", _dummy_auth(), "claude-sonnet-4-6")
    assert isinstance(client, AnthropicClient), f"expected AnthropicClient, got {type(client).__name__}"
    assert "cloudcode" in client.base_url, f"expected Antigravity gateway base_url, got {client.base_url}"
    assert model == "claude-sonnet-4-6"


def test_gemini_model_routes_to_gemini_client():
    """gemini-3-flash on google-antigravity still routes to GeminiClient.
    Patch get_auth_entry to return a valid Gemini API key so the gemini
    path constructs a GeminiClient without raising."""
    with patch("aery_plugin.oauth_helper.get_auth_entry", return_value={"key": "AIza-test-gemini-key"}):
        client, model = create_client("google-antigravity", _dummy_auth(), "gemini-3-flash")
    assert isinstance(client, GeminiClient), f"expected GeminiClient, got {type(client).__name__}"
    assert model == "gemini-3-flash"


def test_gpt_oss_model_routes_to_openai_client():
    """gpt-oss-120b-medium on google-antigravity must use OpenAIClient."""
    client, model = create_client("google-antigravity", _dummy_auth(), "gpt-oss-120b-medium")
    assert isinstance(client, OpenAIClient), f"expected OpenAIClient, got {type(client).__name__}"
    assert "cloudcode" in client.base_url, f"expected Antigravity gateway base_url, got {client.base_url}"


def test_unknown_family_raises_clear_error():
    """An unrecognised model family must raise a clear APIError, not a
    silent broken GeminiClient."""
    try:
        create_client("google-antigravity", _dummy_auth(), "weird-model-xyz")
        assert False, "expected APIError for unknown model family"
    except APIError as e:
        assert e.status_code == 400
        assert e.status_code == 400
        assert "no registry entry" in str(e)