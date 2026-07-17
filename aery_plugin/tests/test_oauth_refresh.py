"""Tests for generic OAuth token refresh (P3 Step 2).

Before this fix, only Google providers got automatic token refresh in
llm_client._resolve_api_key(); non-Google OAuth providers (kilo,
github-copilot, openai-codex) raised a 401 as soon as their access token
expired. refresh_oauth_token() now handles any provider in OAUTH_CONFIGS.
"""

import json
import os
import sys
import time
from io import BytesIO
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from aery_plugin.core.ai import auth as oauth_auth
from aery_plugin import llm_client


def _make_expired_entry():
    return {
        "type": "oauth",
        "access": "old-access-token",
        "refresh": "refresh-xyz",
        "expires": int(time.time() * 1000) - 60_000,  # expired 1 minute ago
        "tokenType": "Bearer",
    }


def _mock_urlopen(response_data):
    """Return a MagicMock suitable as a urllib.request.urlopen context manager."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_resp)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


def test_refresh_oauth_token_returns_new_access():
    """Generic refresh works for a non-Google OAuth provider."""
    stored = {"kilo": _make_expired_entry()}
    saved = {}

    def _load():
        return dict(stored)

    def _save(data):
        saved.update(data)

    refresh_response = {
        "access_token": "new-access-token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }

    with patch.object(oauth_auth, "_load_auth", side_effect=_load), \
         patch.object(oauth_auth, "_save_auth", side_effect=_save), \
         patch("urllib.request.urlopen", return_value=_mock_urlopen(refresh_response)) as mock_open:
        result = oauth_auth.refresh_oauth_token("kilo")

    assert result["access"] == "new-access-token"
    assert result["refresh"] == "refresh-xyz"
    assert result["tokenType"] == "Bearer"
    assert result["expires"] > int(time.time() * 1000)
    assert saved["kilo"]["access"] == "new-access-token"

    # Verify the refresh request body
    req = mock_open.call_args[0][0]
    body = req.data.decode()
    assert "grant_type=refresh_token" in body
    assert "refresh_token=refresh-xyz" in body


def test_refresh_google_token_alias_uses_generic_refresh():
    """refresh_google_token is now a backward-compatible alias."""
    stored = {
        "google-antigravity": {
            **_make_expired_entry(),
            "projectId": "my-project-123",
        }
    }

    def _load():
        return dict(stored)

    refresh_response = {
        "access_token": "new-google-token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }

    with patch.object(oauth_auth, "_load_auth", side_effect=_load), \
         patch.object(oauth_auth, "_save_auth"), \
         patch("urllib.request.urlopen", return_value=_mock_urlopen(refresh_response)):
        result = oauth_auth.refresh_google_token("google-antigravity")

    wrapped = json.loads(result["access"])
    assert wrapped["token"] == "new-google-token"
    assert wrapped["projectId"] == "my-project-123"


def test_resolve_api_key_refreshes_expired_non_google_oauth():
    """llm_client._resolve_api_key now refreshes an expired non-Google token
    instead of raising a 401."""
    auth_entry = _make_expired_entry()

    refresh_response = {
        "access_token": "refreshed-kilo-token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }

    with patch.object(oauth_auth, "_load_auth", return_value={"kilo": auth_entry}), \
         patch.object(oauth_auth, "_save_auth"), \
         patch("urllib.request.urlopen", return_value=_mock_urlopen(refresh_response)):
        key = llm_client._resolve_api_key("kilo", auth_entry)

    assert key == "refreshed-kilo-token"


def test_resolve_api_key_returns_valid_non_google_oauth_without_refresh():
    """If the non-Google token is still valid, no refresh is attempted."""
    auth_entry = {
        "type": "oauth",
        "access": "valid-token",
        "refresh": "refresh-xyz",
        "expires": int(time.time() * 1000) + 600_000,  # 10 minutes from now
    }

    with patch("urllib.request.urlopen") as mock_open:
        key = llm_client._resolve_api_key("kilo", auth_entry)

    assert key == "valid-token"
    mock_open.assert_not_called()


def test_refresh_oauth_token_requires_refresh_token():
    entry = _make_expired_entry()
    entry["refresh"] = ""

    with patch.object(oauth_auth, "_load_auth", return_value={"kilo": entry}):
        with pytest.raises(RuntimeError, match="No refresh token"):
            oauth_auth.refresh_oauth_token("kilo")
