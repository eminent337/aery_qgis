"""Provider auth helper for the Aery QGIS plugin.

Kilo-only OAuth provider for the Aery QGIS plugin.
"""

import hashlib
import base64
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional
from aery_plugin.vault import get_vault

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.join(PLUGIN_DIR, "agent")
AUTH_PATH = os.path.join(AGENT_DIR, "auth.json")
SETTINGS_PATH = os.path.join(AGENT_DIR, "settings.json")

AERY_GATEWAY_URL = "https://aery-gateway.eminent337.workers.dev/v1"

# Helper to decode base64-encoded credentials (same as Aery source)
def _decode(s: str) -> str:
    import base64
    return base64.b64decode(s).decode()

# ── OAuth provider configs ────────────────────────────────────────────────────
OAUTH_CONFIGS: dict[str, dict] = {
    "kilo": {
        "name": "Kilo Gateway",
        "auth_url": "https://api.kilo.ai/api/device-auth/codes",
        "token_url": "https://api.kilo.ai/api/device-auth/codes",
        "client_id": "aery-qgis",
        "client_secret": "",
        "redirect_port": 0,
        "redirect_path": "",
        "scopes": [],
        "device_flow": True,
        # Kilo issues a long-lived device bearer token (1 year) with NO
        # refresh token and NO refresh endpoint. Mirror the main Aery agent:
        # treat it as a static bearer credential that never refreshes.
        "static_bearer": True,
    },
    "google-antigravity": {
        "name": "Google Antigravity",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        # Client credentials are supplied via environment variables so the
        # plugin never embeds the Antigravity OAuth client secret in the repo
        # (it was scrubbed from history for GitHub push-protection hygiene).
        # Set ENV_GOOGLE_OAUTH_CLIENT_ID / ENV_GOOGLE_OAUTH_CLIENT_SECRET (the
        # same names the old oauth_helper.py used) and restart QGIS.
        "client_id": os.environ.get("ENV_GOOGLE_OAUTH_CLIENT_ID", ""),
        "client_secret": os.environ.get("ENV_GOOGLE_OAUTH_CLIENT_SECRET", ""),
        "redirect_port": 51121,
        "redirect_path": "/oauth-callback",
        "scopes": [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/cclog",
            "https://www.googleapis.com/auth/experimentsandconfigs",
        ],
        # After the authorization-code exchange, discover/provision the Cloud
        # Code Assist project (loadCodeAssist -> onboardUser) mirroring the
        # main agent's google-antigravity.ts OAuth flow.
        "discover_project": True,
        "project_tier": "legacy-tier",
        "cloud_code_endpoint": "https://cloudcode-pa.googleapis.com",
    },
}

# No API_PROVIDERS - Kilo only
API_PROVIDERS: dict[str, dict] = {}

# ── Antigravity project discovery (mirrors main agent google-antigravity.ts) ──

def _antigravity_user_agent() -> str:
    """User-Agent that identifies as Antigravity (unlocks Cloud Code API)."""
    import platform as _platform

    version = os.environ.get("AERY_ANTIGRAVITY_VERSION", "1.104.0")
    system = _platform.system().lower()  # "windows" | "darwin" | "linux"
    os_name = "windows" if system == "windows" else ("darwin" if system == "darwin" else "linux")
    machine = _platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("i386", "i686"):
        arch = "386"
    else:
        arch = machine
    return f"antigravity/{version} {os_name}/{arch}"


def _read_antigravity_project_id(value) -> Optional[str]:
    """Accept either a plain project id string or {'id': '...'}."""
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"]:
        return value["id"]
    return None


def _discover_antigravity_project(access_token: str, endpoint: str) -> str:
    """Return the Cloud Code Assist project id for an Antigravity token.

    Tries ``v1internal:loadCodeAssist`` first; if no project exists yet it
    provisions one via ``v1internal:onboardUser`` (up to 5 attempts, 2s apart).
    Mirrors ``discoverProject`` in the main agent.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": _antigravity_user_agent(),
    }
    metadata = {"ideType": "ANTIGRAVITY", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}

    def _post(path: str, body: dict):
        req = urllib.request.Request(
            f"{endpoint}{path}",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    try:
        load = _post("/v1internal:loadCodeAssist", {"metadata": metadata})
    except Exception as e:
        raise RuntimeError(
            f"Could not discover an Antigravity project. loadCodeAssist failed: {e}"
        )

    existing = _read_antigravity_project_id(load.get("cloudaicompanionProject"))
    if existing:
        return existing

    tier_id = "legacy-tier"
    for tier in load.get("allowedTiers") or []:
        if tier.get("isDefault") and _read_antigravity_project_id(tier.get("id")):
            tier_id = tier["id"]
            break

    for attempt in range(1, 6):
        if attempt > 1:
            time.sleep(2)
        try:
            op = _post("/v1internal:onboardUser", {"tierId": tier_id, "metadata": metadata})
        except Exception as e:
            if attempt < 5:
                continue
            raise RuntimeError(f"onboardUser failed: {e}")
        if op.get("done"):
            pid = _read_antigravity_project_id((op.get("response") or {}).get("cloudaicompanionProject"))
            if pid:
                return pid
    raise RuntimeError("onboardUser did not return a provisioned project id after 5 attempts")




# ── Auth storage ──────────────────────────────────────────────────────────────

def _ensure_agent_dir() -> None:
    os.makedirs(AGENT_DIR, exist_ok=True)


def _load_auth() -> dict:
    _migrate_auth_to_vault()
    vault = get_vault("auth")
    auth = {}

    # Only Kilo OAuth
    for pid in OAUTH_CONFIGS.keys():
        entry = {}
        tokens = vault.get_oauth_tokens(pid)
        if tokens:
            entry.update(tokens)
        if entry:
            auth[pid] = entry

    return auth


def _save_auth(data: dict) -> None:
    _ensure_agent_dir()
    auth_path = os.path.join(AGENT_DIR, "auth.json")
    tmp = auth_path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, auth_path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


# ── Vault migration ─────────────────────────────────────────────────────────────

_VAULT_MIGRATED = False

def _migrate_auth_to_vault() -> None:
    """One-time migration from auth.json to Vault."""
    global _VAULT_MIGRATED
    if _VAULT_MIGRATED:
        return
    _VAULT_MIGRATED = True

    if not os.path.exists(AUTH_PATH):
        return

    try:
        with open(AUTH_PATH, "r") as f:
            auth = json.load(f)

        vault = get_vault("auth")
        for provider_id, data in auth.items():
            if isinstance(data, dict):
                # OAuth tokens
                if "access_token" in data:
                    vault.set_oauth_tokens(
                        provider_id,
                        data.get("access_token", ""),
                        data.get("refresh_token", ""),
                        data.get("expires_at", 0)
                    )
                # API keys
                if "key" in data:
                    vault.set_profile_secret(provider_id, "api_key", data["key"])
                # Gateway key
                if provider_id == "aery-gateway" and "key" in data:
                    vault.set("gateway_key", data["key"])

        # Backup and remove old file
        os.rename(AUTH_PATH, AUTH_PATH + ".bak")
    except Exception:
        pass  # Keep auth.json if migration fails


def get_all_providers() -> list[dict]:
    auth = _load_auth()
    result = []

    # Only Kilo OAuth provider
    for pid, cfg in OAUTH_CONFIGS.items():
        entry = auth.get(pid, {})
        has_creds = bool(entry.get("access_token") or entry.get("access") or entry.get("accessToken") or entry.get("refresh") or entry.get("refreshToken"))
        result.append({
            "id": pid,
            "name": cfg["name"],
            "type": "oauth",
            "has_creds": has_creds,
            "connected": has_creds,
            "models": [m[0] for m in _oauth_models(pid)],
            "model_names": _oauth_models(pid),
        })

    return result


def _oauth_models(pid: str) -> list[tuple]:
    models = {
        "kilo": [
            ("tencent/hy3:free", "Tencent: Hunyuan 3 (free)"),
            ("stepfun/step-3.7-flash:free", "StepFun: Step 3.7 Flash (free)"),
            ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "NVIDIA: Nemotron 3 Nano Omni (free)"),
            ("nvidia/nemotron-3-super-120b-a12b:free", "NVIDIA: Nemotron 3 Super (free)"),
            ("poolside/laguna-m.1:free", "Poolside: Laguna M.1 (free)"),
            ("openrouter/free", "OpenRouter Free (free)"),
        ],
        # Google Antigravity models (mirrors providers.py registry and the main
        # agent's models.json google-antigravity block).
        "google-antigravity": [
            ("claude-opus-4-5-thinking", "Claude Opus 4.5 Thinking (Antigravity)"),
            ("claude-opus-4-6-thinking", "Claude Opus 4.6 (Thinking) (Antigravity)"),
            ("claude-sonnet-4-5", "Claude Sonnet 4.5"),
            ("claude-sonnet-4-5-thinking", "Claude Sonnet 4.5 Thinking (Antigravity)"),
            ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
            ("claude-sonnet-4-6-thinking", "Claude Sonnet 4.6 Thinking (Antigravity)"),
            ("gemini-2.5-flash", "Gemini 2.5 Flash"),
            ("gemini-2.5-flash-thinking", "Gemini 2.5 Flash (Thinking) (Antigravity)"),
            ("gemini-2.5-pro", "Gemini 2.5 Pro"),
            ("gemini-3-flash", "Gemini 3 Flash"),
            ("gemini-3-pro-high", "Gemini 3 Pro (High) (Antigravity)"),
            ("gemini-3-pro-low", "Gemini 3 Pro (Low) (Antigravity)"),
            ("gemini-3.1-pro-high", "Gemini 3.1 Pro (High) (Antigravity)"),
            ("gemini-3.1-pro-low", "Gemini 3.1 Pro (Low) (Antigravity)"),
            ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium) (Antigravity)"),
        ],
    }
    if pid in models:
        return models[pid]
    return []


def get_active_provider() -> Optional[dict]:
    settings_path = os.path.join(AGENT_DIR, "settings.json")
    if not os.path.exists(settings_path):
        return None
    try:
        with open(settings_path) as f:
            s = json.load(f)
        pid = s.get("defaultProvider", "")
        model = s.get("defaultModel", "")
        if not pid:
            return None

        # Check if provider actually has credentials
        auth = _load_auth()
        entry = auth.get(pid, {})
        has_creds = bool(
            entry.get("key") or entry.get("access_token") or entry.get("access") or entry.get("accessToken")
            or entry.get("refresh") or entry.get("refreshToken")
        )

        # Also check gateway fallback
        if not has_creds and auth.get("aery-gateway", {}).get("key"):
            if pid in OAUTH_CONFIGS or pid in API_PROVIDERS:
                has_creds = True

        # Also check env fallback
        if not has_creds:
            env_key = ENV_KEY_MAP.get(pid, "")
            if env_key and os.environ.get(env_key):
                has_creds = True

        if not has_creds:
            return None

        name = (OAUTH_CONFIGS.get(pid) or API_PROVIDERS.get(pid) or {}).get("name", pid.replace("-", " ").title())
        return {"id": pid, "name": name, "model": model}
    except Exception:
        pass
    return None


def set_active_provider(provider_id: str, model: str = "") -> None:
    _ensure_agent_dir()
    settings_path = os.path.join(AGENT_DIR, "settings.json")
    existing = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                existing = json.load(f)
        except Exception:
            pass
    existing["defaultProvider"] = provider_id
    if model:
        existing["defaultModel"] = model
    existing.setdefault("quietStartup", True)
    existing.setdefault("defaultThinkingLevel", "off")
    with open(settings_path, "w") as f:
        json.dump(existing, f, indent=2)
    # Sync default profile so Agent always finds active provider/model on restart
    try:
        from aery_plugin.profiles import (
            AssistantProfile,
            get_default_profile_id,
            load_profile,
            save_profile,
            set_default_profile_id,
        )
        pid = get_default_profile_id() or "default"
        prof = load_profile(pid)
        if prof:
            prof.provider = provider_id
            if model:
                prof.model = model
            save_profile(prof)
        else:
            new_p = AssistantProfile(
                id=pid,
                name="Default Assistant",
                provider=provider_id,
                model=model or "tencent/hy3:free",
                tool_allowlist=[],
            )
            save_profile(new_p)
        set_default_profile_id(pid)
    except Exception:
        pass

def save_custom_provider(base_url: str, model_id: str, api_key: str) -> dict:
    """Save a custom OpenAI-compatible provider.

    Returns {"provider_id": ..., "model_id": ...} on success.
    """
    _ensure_agent_dir()
    models_path = os.path.join(AGENT_DIR, "models.json")

    # Load existing models.json
    data = {"providers": {}}
    if os.path.exists(models_path):
        try:
            with open(models_path) as f:
                data = json.load(f)
        except Exception:
            pass

    # Generate provider ID from base URL
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    host = parsed.hostname or "custom"
    provider_id = f"custom-{host.replace('.', '-')}"

    # Add or update provider
    data.setdefault("providers", {})
    data["providers"][provider_id] = {
        "name": f"Custom ({host})",
        "baseUrl": base_url.rstrip("/"),
        "api": "openai-completions",
        "models": [model_id],
    }

    with open(models_path, "w") as f:
        json.dump(data, f, indent=2)

    # Save API key
def delete_custom_provider(provider_id: str) -> bool:
    """Delete a custom OpenAI-compatible provider from models.json and auth.json."""
    models_path = os.path.join(AGENT_DIR, "models.json")
    if os.path.exists(models_path):
        try:
            with open(models_path) as f:
                data = json.load(f)
            if provider_id in data.get("providers", {}):
                del data["providers"][provider_id]
                with open(models_path, "w") as f:
                    json.dump(data, f, indent=2)
        except Exception:
            pass

    # Delete from vault
    vault = get_vault("auth")
    vault.delete(f"profile:{provider_id}:api_key")

    return True


def get_auth_entry(provider_id: str) -> Optional[dict]:
    """Return the auth dict for a single provider, or {}."""
    auth = _load_auth()
    return auth.get(provider_id, {})
    """Delete a custom OpenAI-compatible provider from models.json and auth.json."""
    models_path = os.path.join(AGENT_DIR, "models.json")
    if os.path.exists(models_path):
        try:
            with open(models_path) as f:
                data = json.load(f)
            if provider_id in data.get("providers", {}):
                del data["providers"][provider_id]
                with open(models_path, "w") as f:
                    json.dump(data, f, indent=2)
        except Exception:
            pass

    # Delete from vault
    vault = get_vault("auth")
    vault.delete(f"profile:{provider_id}:api_key")

    return True


def logout_provider(provider_id: str) -> bool:
    vault = get_vault("auth")
    deleted = vault.delete_oauth_tokens(provider_id)
    deleted = vault.delete_profile_secret(provider_id, "api_key") or deleted
    return deleted


# ── Connection testing ──────────────────────────────────────────────────────────

def _post_json(url: str, body: dict, headers: dict, timeout: int = 10) -> Optional[str]:
    """POST JSON, return None on success or error string."""
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        return None
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return str(e)


def test_provider_connection(provider_id: str) -> Optional[str]:
    """Return None on success, error string on failure."""
    auth = _load_auth()
    entry = auth.get(provider_id, {})

    # Kilo gateway (device flow bearer)
    if provider_id == "kilo":
        access = entry.get("access", "") or entry.get("accessToken", "") or entry.get("access_token", "")
        if not access:
            return "Not logged in"
        # Test Kilo gateway
        return _post_json(
            "https://api.kilo.ai/api/health",
            {},
            {"Authorization": f"Bearer {access}"},
        )

    # Google Antigravity: validate the OAuth token against Cloud Code Assist
    if provider_id == "google-antigravity":
        access = entry.get("access", "") or entry.get("accessToken", "") or entry.get("access_token", "")
        if not access:
            return "Not logged in"
        token = access
        try:
            wrapped = json.loads(access)
            token = wrapped.get("token", access)
        except (json.JSONDecodeError, AttributeError):
            pass
        cfg = OAUTH_CONFIGS.get(provider_id, {})
        endpoint = cfg.get("cloud_code_endpoint", "https://cloudcode-pa.googleapis.com")
        return _post_json(
            f"{endpoint}/v1internal:loadCodeAssist",
            {"metadata": {"ideType": "ANTIGRAVITY", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}},
            {"Authorization": f"Bearer {token}", "User-Agent": _antigravity_user_agent()},
        )

    return "Unknown provider"


# ── Save credentials ──────────────────────────────────────────────────────────

def save_api_key(provider_id: str, key: str, account_id: str = "", base_url: str = "") -> None:
    vault = get_vault("auth")
    vault.set_profile_secret(provider_id, "api_key", key)
    if not get_active_provider():
        default_model = _oauth_models(provider_id)
        set_active_provider(provider_id, default_model[0][0] if default_model else "")

def save_gateway_key(aery_key: str) -> None:
    vault = get_vault("auth")
    vault.set("gateway_key", aery_key)
    # Kilo-only configuration - do not auto-switch provider
    # User explicitly requested only Kilo provider support


# ── OAuth login flow ──────────────────────────────────────────────────────────

def login_provider(provider_id: str) -> bool:
    """Run OAuth login. Returns True on success."""
    cfg = OAUTH_CONFIGS.get(provider_id)
    if not cfg:
        raise ValueError(f"Unknown OAuth provider: {provider_id}")

    if cfg.get("device_flow"):
        return _device_flow_login(provider_id, cfg)
    return _pkce_login(provider_id, cfg)


def _device_flow_login(provider_id: str, cfg: dict, code_callback=None) -> bool:
    if provider_id == "kilo":
        req = urllib.request.Request(
            cfg["auth_url"],
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            raise RuntimeError(f"Device code request failed: {e}")

        user_code = data.get("code", "")
        verification_uri = data.get("verificationUrl", "https://app.kilo.ai/device-auth")

        if code_callback:
            code_callback(user_code, verification_uri)
        else:
            print(f"Kilo Gateway: go to {verification_uri} and enter code: {user_code}")
            import webbrowser
            webbrowser.open(verification_uri)

        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(5)
            poll_req = urllib.request.Request(
                f"{cfg['token_url']}/{urllib.parse.quote(user_code)}",
                headers={"Accept": "application/json"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(poll_req, timeout=10) as resp:
                    if resp.status == 202:
                        continue
                    token_data = json.loads(resp.read().decode())
                    if token_data.get("status") == "approved" and token_data.get("token"):
                        vault = get_vault("auth")
                        vault.set_oauth_tokens(
                            provider_id,
                            token_data["token"],
                            "",
                            int(time.time() * 1000) + 31536000 * 1000
                        )
                        if not get_active_provider():
                            models = _oauth_models(provider_id)
                            set_active_provider(provider_id, models[0][0] if models else "")
                        return True
            except urllib.error.HTTPError as e:
                if e.code == 202:
                    pass
                else:
                    print(f"Poll error: {e}")
            except Exception:
                pass
        return False

    return False


def _pkce_login(provider_id: str, cfg: dict) -> bool:
    """Authorization-code + PKCE login via a local callback server.

    Used by Google OAuth providers (Antigravity). Client credentials come from
    the provider config (env-driven, never embedded in the repo). After the
    token exchange, project-scoped providers run Antigravity project discovery
    (``loadCodeAssist``/``onboardUser``) and the access token is stored wrapped
    as JSON ``{"token": ..., "projectId": ...}`` so the request builder can hand
    both back to the Cloud Code Assist API.
    """
    port = cfg["redirect_port"]
    redirect_path = cfg["redirect_path"]
    redirect_uri = f"http://localhost:{port}{redirect_path}"
    client_id = cfg.get("client_id", "")
    if not client_id:
        raise RuntimeError(
            "Google OAuth client id is not configured. Set ENV_GOOGLE_OAUTH_CLIENT_ID "
            "(and ENV_GOOGLE_OAUTH_CLIENT_SECRET) in the QGIS environment and restart."
        )

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(cfg.get("scopes", [])),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = cfg["auth_url"] + "?" + urllib.parse.urlencode(params)
    result: dict = {"code": None, "error": None}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == redirect_path:
                qs = urllib.parse.parse_qs(parsed.query)
                if "error" in qs:
                    result["error"] = qs["error"][0]
                    self._respond(400, "OAuth error: " + qs["error"][0])
                elif "code" in qs and qs.get("state", [""])[0] == state:
                    result["code"] = qs["code"][0]
                    self._respond(200, "Authentication complete. You can close this window.")
                else:
                    result["error"] = "State mismatch or missing code"
                    self._respond(400, "Authentication failed")
            else:
                self._respond(404, "Not found")

        def _respond(self, status: int, body: str):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args, **kwargs):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 120

    def run():
        while result["code"] is None and result["error"] is None:
            server.handle_request()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    webbrowser.open(auth_url)
    t.join(timeout=120)
    server.server_close()

    if not result["code"]:
        return False

    exchange = {
        "client_id": client_id,
        "client_secret": cfg.get("client_secret", ""),
        "code": result["code"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    req = urllib.request.Request(
        cfg["token_url"],
        data=urllib.parse.urlencode(exchange).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token_data = json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"Token exchange failed: {e}")

    access = token_data.get("access_token")
    if not access:
        raise RuntimeError(f"No access_token in response: {token_data}")

    refresh = token_data.get("refresh_token", "")
    expires_ms = int(time.time() * 1000) + int(token_data.get("expires_in", 3600)) * 1000

    stored_access = access
    if cfg.get("discover_project"):
        project_id = _discover_antigravity_project(
            access,
            cfg.get("cloud_code_endpoint", "https://cloudcode-pa.googleapis.com"),
        )
        stored_access = json.dumps({"token": access, "projectId": project_id})

    vault = get_vault("auth")
    vault.set_oauth_tokens(provider_id, stored_access, refresh, expires_ms)
    if not get_active_provider():
        models = _oauth_models(provider_id)
        set_active_provider(provider_id, models[0][0] if models else "")
    return True


def refresh_oauth_token(provider_id: str) -> dict:
    """Refresh an expired OAuth access token using the stored refresh token.
    Works for any provider defined in OAUTH_CONFIGS (Kilo).

    Static-bearer providers (Kilo) issue a long-lived device token with no
    refresh token and no refresh endpoint. Mirroring the main Aery agent's
    ``refreshOAuthToken`` (``case "kilo": ... return as-is``), those tokens
    are returned unchanged instead of raising — the server rejects a truly
    dead token with 401 and the user re-authenticates.
    """
    vault = get_vault("auth")
    tokens = vault.get_oauth_tokens(provider_id)
    if not tokens:
        raise RuntimeError(f"No OAuth credentials for {provider_id}. Re-authenticate via /login.")

    cfg = OAUTH_CONFIGS.get(provider_id, {})
    if cfg.get("static_bearer"):
        # Static bearer (Kilo): no refresh token / endpoint. Return as-is.
        return tokens

    refresh_token = tokens.get("refresh_token", "")
    if not refresh_token:
        raise RuntimeError(f"No refresh token for {provider_id}. Re-authenticate via /login.")

    token_url = cfg.get("token_url", "")
    client_id = cfg.get("client_id", "")
    client_secret = cfg.get("client_secret", "")

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(
        token_url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token_data = json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"Token refresh failed: {e}")

    access = token_data.get("access_token")
    if not access:
        raise RuntimeError(f"No access_token in refresh response: {token_data}")

    # Preserve the Cloud Code Assist projectId across refreshes for
    # project-scoped providers (Antigravity): the access token is stored wrapped
    # as JSON {"token": ..., "projectId": ...} so _resolve_google_credentials in
    # llm_client can hand both back to the Cloud Code request builder.
    stored_access = access
    if cfg.get("discover_project"):
        project_id = ""
        old_access = tokens.get("access_token", "")
        try:
            old_wrapped = json.loads(old_access)
            project_id = old_wrapped.get("projectId", "")
        except (json.JSONDecodeError, AttributeError):
            pass
        stored_access = json.dumps({"token": access, "projectId": project_id})

    vault.set_oauth_tokens(
        provider_id,
        stored_access,
        token_data.get("refresh_token", refresh_token),
        int(time.time() * 1000) + int(token_data.get("expires_in", 3600)) * 1000
    )
    return vault.get_oauth_tokens(provider_id)


def refresh_google_token(provider_id: str) -> dict:
    """Backward-compatible alias for Google OAuth refresh.
    Delegates to refresh_oauth_token, which handles both Google and
    non-Google OAuth providers uniformly.
    """
    return refresh_oauth_token(provider_id)


# ── Env-key auto-detection ─────────────────────────────────────────────────────
# Per-provider env-var names so the plugin can surface env credentials without
# requiring the user to open a terminal first.

ENV_KEY_MAP: dict[str, str] = {
    "kilo": "KILO_API_KEY",
}


def get_env_key(provider_id: str) -> str:
    """Return the environment variable name for a provider's API key."""
    return ENV_KEY_MAP.get(provider_id, "")


def read_env_credentials(provider_id: str) -> dict:
    """Read API key from the environment; returns empty dict if not set."""
    env_key = get_env_key(provider_id)
    if not env_key:
        return {}
    value = os.environ.get(env_key, "")
    if not value:
        return {}
    return {"key": value}


# ── Model changelog ────────────────────────────────────────────────────────────

def get_model_changelog() -> str:
    """Return Aery model registry changelog string.

    Tries the Aery package first; falls back to a static string when offline.
    """
    try:
        from aery_ai import getModelChangelog  # type: ignore
        return getModelChangelog()
    except Exception:
        return (
            "Aery Model Registry — load changelog\n\n"
            "Model lists are managed by the Aery AI package.\n"
            "Updates are fetched from the model registry on startup.\n"
            "See https://github.com/eminent337/aery for the latest models."
        )


def get_custom_providers() -> list[dict]:
    """Return list of custom OpenAI-compatible providers saved in models.json.

    Each entry: {"id": str, "name": str, "base_url": str, "models": list[str]}.
    """
    models_path = os.path.join(AGENT_DIR, "models.json")
    if not os.path.exists(models_path):
        return []
    try:
        with open(models_path) as f:
            data = json.load(f)
    except Exception:
        return []
    result = []
    for pid, cfg in (data.get("providers") or {}).items():
        result.append({
            "id": pid,
            "name": cfg.get("name", pid),
            "base_url": cfg.get("baseUrl", "https://api.openai.com/v1"),
            "models": cfg.get("models", []),
        })
    return result


# ── Earth Engine OAuth & Project Configuration (Ported from GeoLibre) ─────────
# Default OAuth2 Client ID from GeoLibre earth-engine-auth.ts
DEFAULT_GEE_OAUTH_CLIENT_ID = "141292844612-gitmgm28jkmkujonfkrkvdaqjiqt6qkf.apps.googleusercontent.com"

def get_earth_engine_config() -> dict:
    """Get GEE OAuth Client ID and configured Google Cloud Project ID."""
    client_id = os.environ.get("GEE_OAUTH_CLIENT_ID") or DEFAULT_GEE_OAUTH_CLIENT_ID
    project_id = os.environ.get("GEE_PROJECT_ID") or os.environ.get("EARTHENGINE_PROJECT", "")
    return {
        "client_id": client_id,
        "project_id": project_id,
    }