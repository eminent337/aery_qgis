"""Tests for generic OAuth token refresh - Kilo provider."""

import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from aery_plugin.core.ai import auth as oauth_auth
from aery_plugin import llm_client
from aery_plugin.vault import get_vault, reset_vault


def _make_expired_entry():
    return {
        "access": "old-access-token",
        "expires": int(time.time() * 1000) - 10000,  # 10 seconds ago
        "refresh": "refresh-xyz",
        "tokenType": "Bearer",
    }


def _mock_urlopen(response_data):
    """Return a MagicMock suitable as a urllib.request.urlopen context manager."""
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = json.dumps(response_data).encode()
    mock_resp.status = 200
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_resp
    mock_cm.__exit__.return_value = None
    return mock_cm


@pytest.fixture(autouse=True)
def _isolate_vault_for_test():
    """Isolate vault directory to temp folder during test run."""
    import tempfile, shutil
    from pathlib import Path
    temp_dir = Path(tempfile.mkdtemp())
    orig_env = os.environ.get("AERY_VAULT_DIR")
    os.environ["AERY_VAULT_DIR"] = str(temp_dir)
    reset_vault()
    yield
    reset_vault()
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    if orig_env:
        os.environ["AERY_VAULT_DIR"] = orig_env
    else:
        os.environ.pop("AERY_VAULT_DIR", None)

def test_refresh_oauth_token_returns_new_access():
    """Generic refresh works for Kilo OAuth provider."""
    vault = get_vault("auth")
    vault.set_oauth_tokens(
        "kilo",
        "old-access-token",
        "refresh-xyz",
        int(time.time() * 1000) - 10000  # expired
    )

    refresh_response = {
        "access_token": "new-access-token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }

    with patch("urllib.request.urlopen", return_value=_mock_urlopen(refresh_response)) as mock_open:
        result = oauth_auth.refresh_oauth_token("kilo")

    assert result["access_token"] == "new-access-token"
    assert result["refresh_token"] == "refresh-xyz"
    assert result["expires_at"] > int(time.time() * 1000)

    # Verify the request was made with correct refresh token
    call_args = mock_open.call_args
    req = call_args[0][0]
    body = req.data.decode()
    assert "refresh_token=refresh-xyz" in body
    assert "grant_type=refresh_token" in body


def test_resolve_api_key_refreshes_expired_kilo_oauth():
    """llm_client._resolve_api_key now refreshes an expired Kilo token
    instead of raising a 401."""
    vault = get_vault("auth")
    vault.set_oauth_tokens(
        "kilo",
        "old-access-token",
        "refresh-xyz",
        int(time.time() * 1000) - 10000  # expired
    )

    refresh_response = {
        "access_token": "refreshed-kilo-token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }

    with patch("urllib.request.urlopen", return_value=_mock_urlopen(refresh_response)):
        key = llm_client._resolve_api_key("kilo", {})

    assert key == "refreshed-kilo-token"


def test_resolve_api_key_returns_valid_kilo_oauth_without_refresh():
    """If the Kilo token is still valid, no refresh is attempted."""
    vault = get_vault("auth")
    vault.set_oauth_tokens(
        "kilo",
        "valid-access-token",
        "refresh-xyz",
        int(time.time() * 1000) + 3600000  # 1 hour from now
    )

    with patch("urllib.request.urlopen", return_value=_mock_urlopen({})) as mock_open:
        key = llm_client._resolve_api_key("kilo", {})

    assert key == "valid-access-token"
    mock_open.assert_not_called()


def test_refresh_oauth_token_requires_refresh_token():
    """Generic refresh fails if no refresh token is stored."""
    vault = get_vault("auth")
    vault.set_oauth_tokens(
        "kilo",
        "old-access-token",
        "",  # no refresh token
        int(time.time() * 1000) - 10000
    )

    with pytest.raises(RuntimeError, match="No refresh token"):
        oauth_auth.refresh_oauth_token("kilo")


def test_refresh_oauth_token_missing_credentials():
    """Generic refresh fails if no credentials exist for provider."""
    with pytest.raises(RuntimeError):
        oauth_auth.refresh_oauth_token("kilo")