"""Tests for create_client model-family routing on google-antigravity.

Regression guard for the Antigravity provider: every model family (Claude,
Gemini, GPT-OSS) on the google-antigravity provider must route to
AntigravityClient (the Cloud Code Assist wire format), not to a mismatched
Anthropic/Gemini/OpenAI client pointed at the wrong API.
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from aery_plugin.llm_client import (
    create_client,
    AntigravityClient,
    APIError,
)


def _dummy_auth():
    """Auth entry with a direct API key so _resolve_api_key returns it
    without touching OAuth refresh paths."""
    return {"type": "api_key", "key": "test-key-123"}


def _antigravity_auth():
    """Stored Antigravity credentials (JSON-wrapped access with projectId)."""
    return {
        "access_token": json.dumps({"token": "tok-123", "projectId": "proj-42"}),
        "refresh_token": "rt",
        "expires_at": int(time.time() * 1000) + 3600_000,
    }


def _create_antigravity(model: str):
    with patch("aery_plugin.oauth_helper.get_auth_entry", return_value=_antigravity_auth()):
        return create_client("google-antigravity", _dummy_auth(), model)


def test_claude_model_routes_to_antigravity_client():
    """claude-sonnet-4-6 on google-antigravity must use AntigravityClient with
    the Cloud Code Assist sandbox base_url and the stored projectId."""
    client, model = _create_antigravity("claude-sonnet-4-6")
    assert isinstance(client, AntigravityClient), f"expected AntigravityClient, got {type(client).__name__}"
    assert client.project_id == "proj-42"
    assert "daily-cloudcode-pa.sandbox.googleapis.com" in client.base_url
    assert model == "claude-sonnet-4-6"


def test_gemini_model_routes_to_antigravity_client():
    """gemini-3-flash on google-antigravity must use AntigravityClient (Cloud
    Code Assist), not a native Gemini client."""
    client, model = _create_antigravity("gemini-3-flash")
    assert isinstance(client, AntigravityClient), f"expected AntigravityClient, got {type(client).__name__}"
    assert client.project_id == "proj-42"
    assert model == "gemini-3-flash"


def test_gpt_oss_model_routes_to_antigravity_client():
    """gpt-oss-120b-medium on google-antigravity must use AntigravityClient."""
    client, model = _create_antigravity("gpt-oss-120b-medium")
    assert isinstance(client, AntigravityClient), f"expected AntigravityClient, got {type(client).__name__}"
    assert client.project_id == "proj-42"
    assert model == "gpt-oss-120b-medium"


def test_unauthenticated_antigravity_raises_clear_error():
    """Without stored Antigravity credentials, routing raises a 401 APIError."""
    try:
        create_client("google-antigravity", _dummy_auth(), "claude-sonnet-4-6")
        assert False, "expected APIError for unauthenticated antigravity"
    except APIError as e:
        assert e.status_code == 401
        assert "not authenticated" in str(e).lower()
