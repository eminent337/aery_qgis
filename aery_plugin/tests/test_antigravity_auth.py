"""Tests for the Antigravity provider auth layer.

Covers the OAuth config, the model list, the Cloud Code Assist project
discovery (loadCodeAssist -> onboardUser), and project-id preservation across
token refresh — the pieces that make google-antigravity a first-class provider.
"""

import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from aery_plugin.core.ai import auth


# ── Fake vault ────────────────────────────────────────────────────────────────

class _FakeVault:
    def __init__(self):
        self.data = {}

    def set(self, key, value):
        self.data[key] = value
        return True

    def get(self, key, default=None):
        return self.data.get(key, default)

    def delete(self, key):
        self.data.pop(key, None)
        return True

    def list_keys(self):
        return []

    def set_oauth_tokens(self, provider_id, access_token, refresh_token, expires_at):
        self.data[f"oauth:{provider_id}"] = json.dumps({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        })
        return True

    def get_oauth_tokens(self, provider_id):
        data = self.data.get(f"oauth:{provider_id}")
        return json.loads(data) if data else None

    def delete_oauth_tokens(self, provider_id):
        self.data.pop(f"oauth:{provider_id}", None)
        return True

    def set_profile_secret(self, *args):
        return True

    def get_profile_secret(self, *args):
        return None

    def delete_profile_secret(self, *args):
        return True


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self._payload).encode()


def _fake_urlopen(payloads):
    """Return a urlopen stand-in that answers from a queue of payloads."""
    calls = []

    def _urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(url)
        idx = min(len(calls) - 1, len(payloads) - 1)
        return _FakeResp(payloads[idx])

    return _urlopen, calls


# ── Config ────────────────────────────────────────────────────────────────────

def test_oauth_configs_has_antigravity():
    cfg = auth.OAUTH_CONFIGS["google-antigravity"]
    assert cfg["name"] == "Google Antigravity"
    assert cfg["auth_url"] == "https://accounts.google.com/o/oauth2/v2/auth"
    assert cfg["token_url"] == "https://oauth2.googleapis.com/token"
    assert cfg["redirect_port"] == 51121
    assert cfg["redirect_path"] == "/oauth-callback"
    assert cfg["discover_project"] is True
    assert "https://www.googleapis.com/auth/cloud-platform" in cfg["scopes"]
    # Credentials are env-driven, never embedded in the repo.
    assert cfg["client_id"] == os.environ.get("ENV_GOOGLE_OAUTH_CLIENT_ID", "")


def test_oauth_models_antigravity():
    models = auth._oauth_models("google-antigravity")
    ids = [m[0] for m in models]
    assert "claude-sonnet-4-6" in ids
    assert "gemini-3-flash" in ids
    assert "gpt-oss-120b-medium" in ids
    assert len(models) == 15


def test_get_all_providers_includes_antigravity():
    vault = _FakeVault()
    with patch("aery_plugin.core.ai.auth.get_vault", return_value=vault):
        providers = auth.get_all_providers()
    ids = [p["id"] for p in providers]
    assert "google-antigravity" in ids


# ── Project discovery ─────────────────────────────────────────────────────────

def test_discover_existing_project():
    urlopen, calls = _fake_urlopen([
        {"cloudaicompanionProject": "proj-99"},
    ])
    with patch("aery_plugin.core.ai.auth.urllib.request.urlopen", urlopen):
        pid = auth._discover_antigravity_project("tok", "https://cloudcode-pa.googleapis.com")
    assert pid == "proj-99"
    assert any("loadCodeAssist" in u for u in calls)
    assert not any("onboardUser" in u for u in calls)


def test_discover_provisions_new_project():
    urlopen, calls = _fake_urlopen([
        {},  # loadCodeAssist: no project
        {"done": True, "response": {"cloudaicompanionProject": {"id": "proj-77"}}},  # onboardUser
    ])
    with patch("aery_plugin.core.ai.auth.urllib.request.urlopen", urlopen):
        pid = auth._discover_antigravity_project("tok", "https://cloudcode-pa.googleapis.com")
    assert pid == "proj-77"
    assert any("onboardUser" in u for u in calls)


# ── Login / refresh ───────────────────────────────────────────────────────────

def test_pkce_login_requires_client_id():
    cfg = dict(auth.OAUTH_CONFIGS["google-antigravity"])
    cfg["client_id"] = ""  # env not set in CI
    try:
        auth._pkce_login("google-antigravity", cfg)
        assert False, "expected RuntimeError for missing client id"
    except RuntimeError as e:
        assert "client id" in str(e)


def test_refresh_preserves_project_id():
    vault = _FakeVault()
    vault.set_oauth_tokens(
        "google-antigravity",
        json.dumps({"token": "old-token", "projectId": "proj-42"}),
        "refresh-tok",
        int(time.time() * 1000) - 1000,  # expired
    )
    urlopen, calls = _fake_urlopen([
        {"access_token": "new-token", "expires_in": 3600},
    ])
    with patch("aery_plugin.core.ai.auth.get_vault", return_value=vault), \
         patch("aery_plugin.core.ai.auth.urllib.request.urlopen", urlopen):
        refreshed = auth.refresh_oauth_token("google-antigravity")

    assert refreshed["access_token"] == json.dumps({"token": "new-token", "projectId": "proj-42"})
    assert refreshed["refresh_token"] == "refresh-tok"
