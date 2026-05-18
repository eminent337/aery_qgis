"""Provider auth helper for the Aery QGIS plugin.

Exact copy of Aery's provider system:
- OAuth: google-antigravity, google-gemini-cli, openai-codex, anthropic, github-copilot
- API key: all other providers with real model lists and test endpoints
- Aery Gateway: one key, all providers
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

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.join(PLUGIN_DIR, "agent")
AUTH_PATH = os.path.join(AGENT_DIR, "auth.json")
SETTINGS_PATH = os.path.join(AGENT_DIR, "settings.json")

AERY_GATEWAY_URL = "https://aery-gateway.eminent337.workers.dev/v1"

# ── OAuth provider configs (exact from Aery) ──────────────────────────────────
# NOTE: client_id / client_secret are read from environment variables to
# avoid committing secrets to source control.
#
# Required env vars:
#   GOOGLE_ANTIGRAVITY_CLIENT_ID       GOOGLE_ANTIGRAVITY_CLIENT_SECRET
#   GOOGLE_GEMINI_CLI_CLIENT_ID        GOOGLE_GEMINI_CLI_CLIENT_SECRET
#
OAUTH_CONFIGS: dict[str, dict] = {
    "google-antigravity": {
        "name": "Google Antigravity",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "client_id": os.environ.get("GOOGLE_ANTIGRAVITY_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_ANTIGRAVITY_CLIENT_SECRET", ""),
        "redirect_port": 51121,
        "redirect_path": "/oauth-callback",
        "scopes": [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/cclog",
            "https://www.googleapis.com/auth/experimentsandconfigs",
        ],
    },
    "google-gemini-cli": {
        "name": "Gemini CLI (Cloud Code)",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "client_id": os.environ.get("GOOGLE_GEMINI_CLI_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_GEMINI_CLI_CLIENT_SECRET", ""),
        "redirect_port": 50321,
        "redirect_path": "/auth",
        "scopes": [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/generative-language.retriever",
            "https://www.googleapis.com/auth/userinfo.email",
        ],
    },
    "openai-codex": {
        "name": "OpenAI Codex",
        "auth_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "client_id": "Iv1.7c0f38f6316405a2",
        "client_secret": "",
        "redirect_port": 1455,
        "redirect_path": "/auth/callback",
        "scopes": ["read:user", "repo", "workflow", "codespace:secrets", "copilot"],
    },
    "anthropic": {
        "name": "Anthropic (Claude.ai)",
        "auth_url": "https://claude.ai/oauth/authorize",
        "token_url": "https://claude.ai/oauth/token",
        "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        "client_secret": "",
        "redirect_port": 54321,
        "redirect_path": "/oauth/callback",
        "scopes": ["org:create_api_key", "user:profile", "user:inference"],
    },
    "github-copilot": {
        "name": "GitHub Copilot",
        "auth_url": "https://github.com/login/device/code",
        "token_url": "https://github.com/login/oauth/access_token",
        "client_id": "Iv1.b507a08c87ecfe98",
        "client_secret": "",
        "redirect_port": 0,
        "redirect_path": "",
        "scopes": ["read:user"],
        "device_flow": True,
    },
}

# ── API key providers with models (exact from Aery models.generated.ts) ───────
API_PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "env_key": "ANTHROPIC_API_KEY",
        "test_path": "/v1/messages",
        "test_model": "claude-haiku-4-5-20251001",
        "models": [
            ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
            ("claude-sonnet-4-20250514", "Claude Sonnet 4"),
            ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5"),
            ("claude-opus-4-20250514", "Claude Opus 4"),
            ("claude-opus-4-5-20251101", "Claude Opus 4.5"),
            ("claude-3-5-haiku-20241022", "Claude Haiku 3.5"),
            ("claude-3-5-sonnet-20241022", "Claude Sonnet 3.5 v2"),
            ("claude-3-7-sonnet-20250219", "Claude Sonnet 3.7"),
        ],
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "gpt-4o-mini",
        "models": [
            ("gpt-4o-mini", "GPT-4o Mini"),
            ("gpt-4o", "GPT-4o"),
            ("gpt-4.1", "GPT-4.1"),
            ("gpt-4.1-mini", "GPT-4.1 Mini"),
            ("gpt-4.1-nano", "GPT-4.1 Nano"),
            ("o1", "o1"),
            ("o1-mini", "o1 Mini"),
            ("o3", "o3"),
            ("o3-mini", "o3 Mini"),
            ("o4-mini", "o4 Mini"),
        ],
    },
    "google": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "env_key": "GEMINI_API_KEY",
        "test_path": "/models/gemini-2.0-flash:generateContent",
        "test_model": "gemini-2.0-flash",
        "models": [
            ("gemini-2.0-flash", "Gemini 2.0 Flash"),
            ("gemini-2.0-flash-lite", "Gemini 2.0 Flash Lite"),
            ("gemini-2.5-flash", "Gemini 2.5 Flash"),
            ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite"),
            ("gemini-2.5-pro", "Gemini 2.5 Pro"),
            ("gemini-1.5-flash", "Gemini 1.5 Flash"),
            ("gemini-1.5-pro", "Gemini 1.5 Pro"),
        ],
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "llama-3.1-8b-instant",
        "models": [
            ("llama-3.1-8b-instant", "Llama 3.1 8B Instant"),
            ("llama-3.3-70b-versatile", "Llama 3.3 70B Versatile"),
            ("llama-4-scout-17b-16e-instruct", "Llama 4 Scout 17B"),
            ("llama-4-maverick-17b-128e-instruct", "Llama 4 Maverick 17B"),
            ("gemma2-9b-it", "Gemma 2 9B IT"),
            ("qwen-qwq-32b", "Qwen QwQ 32B"),
            ("deepseek-r1-distill-llama-70b", "DeepSeek R1 Distill Llama 70B"),
            ("compound-beta", "Compound Beta"),
        ],
    },
    "mistral": {
        "name": "Mistral",
        "base_url": "https://api.mistral.ai",
        "env_key": "MISTRAL_API_KEY",
        "test_path": "/v1/chat/completions",
        "test_model": "mistral-small-latest",
        "models": [
            ("mistral-small-latest", "Mistral Small"),
            ("mistral-medium-3", "Mistral Medium 3"),
            ("mistral-large-latest", "Mistral Large"),
            ("codestral-latest", "Codestral"),
            ("devstral-small-2505", "Devstral Small"),
            ("mistral-saba-latest", "Mistral Saba"),
            ("open-mistral-nemo", "Mistral Nemo"),
        ],
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "meta-llama/llama-3.1-8b-instruct:free",
        "models": [
            ("meta-llama/llama-3.1-8b-instruct:free", "Llama 3.1 8B (Free)"),
            ("google/gemma-2-9b-it:free", "Gemma 2 9B (Free)"),
            ("mistralai/mistral-7b-instruct:free", "Mistral 7B (Free)"),
            ("anthropic/claude-sonnet-4-5", "Claude Sonnet 4.5"),
            ("anthropic/claude-opus-4-5", "Claude Opus 4.5"),
            ("openai/gpt-4o", "GPT-4o"),
            ("openai/o3", "o3"),
            ("google/gemini-2.5-pro", "Gemini 2.5 Pro"),
            ("deepseek/deepseek-r1", "DeepSeek R1"),
            ("x-ai/grok-3", "Grok 3"),
        ],
    },
    "fireworks": {
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference",
        "env_key": "FIREWORKS_API_KEY",
        "test_path": "/v1/chat/completions",
        "test_model": "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "models": [
            ("accounts/fireworks/models/llama-v3p1-8b-instruct", "Llama 3.1 8B"),
            ("accounts/fireworks/models/llama-v3p3-70b-instruct", "Llama 3.3 70B"),
            ("accounts/fireworks/models/llama4-scout-instruct-basic", "Llama 4 Scout"),
            ("accounts/fireworks/models/llama4-maverick-instruct-basic", "Llama 4 Maverick"),
            ("accounts/fireworks/models/deepseek-r1", "DeepSeek R1"),
            ("accounts/fireworks/models/qwen3-235b-a22b", "Qwen3 235B"),
            ("accounts/fireworks/models/kimi-k2-instruct", "Kimi K2"),
        ],
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "deepseek-chat",
        "models": [
            ("deepseek-chat", "DeepSeek V3"),
            ("deepseek-reasoner", "DeepSeek R1"),
        ],
    },
    "xai": {
        "name": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "env_key": "XAI_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "grok-3-mini",
        "models": [
            ("grok-3-mini", "Grok 3 Mini"),
            ("grok-3", "Grok 3"),
            ("grok-3-fast", "Grok 3 Fast"),
            ("grok-2-1212", "Grok 2"),
            ("grok-vision-beta", "Grok Vision"),
        ],
    },
    "cloudflare-workers-ai": {
        "name": "Cloudflare Workers AI",
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1",
        "env_key": "CLOUDFLARE_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "@cf/meta/llama-4-scout-17b-16e-instruct",
        "needs_account_id": True,
        "models": [
            ("@cf/meta/llama-4-scout-17b-16e-instruct", "Llama 4 Scout 17B"),
            ("@cf/moonshotai/kimi-k2.5", "Kimi K2.5"),
            ("@cf/moonshotai/kimi-k2.6", "Kimi K2.6"),
            ("@cf/google/gemma-4-26b-a4b-it", "Gemma 4 26B"),
            ("@cf/nvidia/nemotron-3-120b-a12b", "Nemotron 3 120B"),
            ("@cf/meta/llama-3.3-70b-instruct-fp8-fast", "Llama 3.3 70B Fast"),
            ("@cf/qwen/qwen2.5-coder-32b-instruct", "Qwen 2.5 Coder 32B"),
            ("@cf/deepseek-ai/deepseek-r1-distill-qwen-32b", "DeepSeek R1 Distill 32B"),
        ],
    },
    "cerebras": {
        "name": "Cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "env_key": "CEREBRAS_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "llama-3.1-8b",
        "models": [
            ("llama-3.1-8b", "Llama 3.1 8B"),
            ("llama-3.3-70b", "Llama 3.3 70B"),
            ("qwen-3-32b", "Qwen 3 32B"),
            ("deepseek-r1-distill-llama-70b", "DeepSeek R1 Distill 70B"),
        ],
    },
    "huggingface": {
        "name": "Hugging Face",
        "base_url": "https://router.huggingface.co/v1",
        "env_key": "HF_TOKEN",
        "test_path": "/chat/completions",
        "test_model": "meta-llama/Llama-3.1-8B-Instruct",
        "models": [
            ("meta-llama/Llama-3.1-8B-Instruct", "Llama 3.1 8B"),
            ("meta-llama/Llama-3.3-70B-Instruct", "Llama 3.3 70B"),
            ("Qwen/Qwen2.5-72B-Instruct", "Qwen 2.5 72B"),
            ("deepseek-ai/DeepSeek-R1", "DeepSeek R1"),
            ("mistralai/Mistral-7B-Instruct-v0.3", "Mistral 7B"),
        ],
    },
    "opencode": {
        "name": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen",
        "env_key": "OPENCODE_API_KEY",
        "test_path": "/v1/chat/completions",
        "test_model": "big-pickle",
        "models": [
            ("big-pickle", "Big Pickle"),
            ("small-pickle", "Small Pickle"),
        ],
    },
    "kimi-coding": {
        "name": "Kimi For Coding",
        "base_url": "https://api.kimi.com/coding",
        "env_key": "KIMI_API_KEY",
        "test_path": "/v1/chat/completions",
        "test_model": "kimi-k2-0711-preview",
        "models": [
            ("kimi-k2-0711-preview", "Kimi K2"),
            ("kimi-k2p6", "Kimi K2.6"),
            ("moonshot-v1-8k", "Moonshot V1 8K"),
        ],
    },
    "zai": {
        "name": "ZAI",
        "base_url": "https://api.z.ai/api/coding/paas/v4",
        "env_key": "ZAI_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "z1-preview",
        "models": [
            ("z1-preview", "Z1 Preview"),
            ("z1-mini", "Z1 Mini"),
            ("z1-turbo", "Z1 Turbo"),
            ("z1-ultra", "Z1 Ultra"),
        ],
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimax.io/anthropic",
        "env_key": "MINIMAX_API_KEY",
        "test_path": "/v1/messages",
        "test_model": "MiniMax-Text-01",
        "models": [
            ("MiniMax-Text-01", "MiniMax Text 01"),
            ("MiniMax-M1", "MiniMax M1"),
        ],
    },
    "vercel-ai-gateway": {
        "name": "Vercel AI Gateway",
        "base_url": "https://ai-gateway.vercel.sh",
        "env_key": "AI_GATEWAY_API_KEY",
        "test_path": "/v1/chat/completions",
        "test_model": "openai/gpt-4o-mini",
        "models": [
            ("openai/gpt-4o-mini", "GPT-4o Mini"),
            ("openai/gpt-4o", "GPT-4o"),
            ("anthropic/claude-sonnet-4-5", "Claude Sonnet 4.5"),
            ("google/gemini-2.5-flash", "Gemini 2.5 Flash"),
        ],
    },
    "azure-openai-responses": {
        "name": "Azure OpenAI",
        "base_url": "",
        "env_key": "AZURE_OPENAI_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "gpt-4o",
        "needs_base_url": True,
        "models": [
            ("gpt-4o", "GPT-4o"),
            ("gpt-4o-mini", "GPT-4o Mini"),
            ("gpt-4.1", "GPT-4.1"),
            ("o3", "o3"),
            ("o4-mini", "o4 Mini"),
        ],
    },
    "amazon-bedrock": {
        "name": "Amazon Bedrock",
        "base_url": "https://bedrock-runtime.us-east-1.amazonaws.com",
        "env_key": "AWS_ACCESS_KEY_ID",
        "test_path": "",
        "test_model": "anthropic.claude-haiku-4-5-20251001-v1:0",
        "needs_aws_creds": True,
        "models": [
            ("anthropic.claude-haiku-4-5-20251001-v1:0", "Claude Haiku 4.5"),
            ("anthropic.claude-sonnet-4-20250514-v1:0", "Claude Sonnet 4"),
            ("anthropic.claude-opus-4-20250514-v1:0", "Claude Opus 4"),
            ("meta.llama4-scout-17b-instruct-v1:0", "Llama 4 Scout 17B"),
            ("amazon.nova-pro-v1:0", "Nova Pro"),
            ("amazon.nova-lite-v1:0", "Nova Lite"),
            ("deepseek.r1-v1:0", "DeepSeek R1"),
        ],
    },
    "google-vertex": {
        "name": "Google Vertex AI",
        "base_url": "https://us-central1-aiplatform.googleapis.com/v1",
        "env_key": "GOOGLE_CLOUD_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "gemini-2.0-flash",
        "needs_base_url": False,
        "models": [
            ("gemini-2.0-flash", "Gemini 2.0 Flash"),
            ("gemini-2.0-flash-lite", "Gemini 2.0 Flash Lite"),
            ("gemini-2.5-flash", "Gemini 2.5 Flash"),
            ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite"),
            ("gemini-2.5-pro", "Gemini 2.5 Pro"),
            ("gemini-3-flash-preview", "Gemini 3 Flash Preview"),
            ("gemini-3-pro-preview", "Gemini 3 Pro Preview"),
            ("gemini-1.5-flash", "Gemini 1.5 Flash"),
            ("gemini-1.5-pro", "Gemini 1.5 Pro"),
        ],
    },
    "minimax-cn": {
        "name": "MiniMax CN",
        "base_url": "https://api.minimax.chat/anthropic",
        "env_key": "MINIMAX_CN_API_KEY",
        "test_path": "/v1/messages",
        "test_model": "MiniMax-M2.7",
        "models": [
            ("MiniMax-M2.7", "MiniMax M2.7"),
            ("MiniMax-M2.7-highspeed", "MiniMax M2.7 Highspeed"),
        ],
    },
    "opencode-go": {
        "name": "OpenCode Go",
        "base_url": "https://opencode.ai/go",
        "env_key": "OPENCODE_API_KEY",
        "test_path": "/v1/chat/completions",
        "test_model": "deepseek-v4-flash",
        "models": [
            ("deepseek-v4-flash", "DeepSeek V4 Flash"),
            ("deepseek-v4-pro", "DeepSeek V4 Pro"),
            ("glm-5", "GLM-5"),
            ("glm-5.1", "GLM-5.1"),
            ("kimi-k2.5", "Kimi K2.5"),
            ("kimi-k2.6", "Kimi K2.6"),
            ("mimo-v2-omni", "Mimo V2 Omni"),
            ("mimo-v2-pro", "Mimo V2 Pro"),
        ],
    },
    # ── AERY_KEEP: custom OpenAI-compatible providers — DO NOT REMOVE in upstream sync ──
    "claude-local": {
        "name": "Claude (Local/Custom)",
        "base_url": "https://api.anthropic.com/v1",  # change to your proxy URL
        "env_key": "ANTHROPIC_API_KEY",
        "test_path": "/messages",
        "test_model": "claude-opus-4-5-20251101",
        "aery_keep": True,  # upstream sync guard — never overwrite this entry
        "models": [
            ("claude-opus-4-5-20251101", "Claude Opus 4.5"),
            ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5"),
            ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
            ("claude-opus-4-20250514", "Claude Opus 4"),
            ("claude-sonnet-4-20250514", "Claude Sonnet 4"),
        ],
    },
    "openai-compatible": {
        "name": "Custom OpenAI-compatible API",
        "base_url": "",
        "env_key": "OPENAI_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "gpt-4o",
        "needs_base_url": True,
        "aery_keep": True,
        "models": [
            ("gpt-4o", "GPT-4o"),
            ("gpt-4.1", "GPT-4.1"),
            ("gpt-4.1-mini", "GPT-4.1 Mini"),
            ("gpt-4o-mini", "GPT-4o Mini"),
            ("o3", "o3"),
            ("o3-mini", "o3 Mini"),
            ("o4-mini", "o4 Mini"),
        ],
    },
    # ── END AERY_KEEP ──────────────────────────────────────────────────────────
    "aery-gateway": {
        "name": "Aery Gateway",
        "base_url": AERY_GATEWAY_URL,
        "env_key": "",
        "test_path": "/anthropic/v1/messages",
        "test_model": "claude-haiku-4-5-20251001",
        "is_gateway": True,
        "models": [
            ("anthropic/claude-haiku-4-5-20251001", "Claude Haiku 4.5 (via Gateway)"),
            ("anthropic/claude-sonnet-4-5-20250929", "Claude Sonnet 4.5 (via Gateway)"),
            ("openai/gpt-4o-mini", "GPT-4o Mini (via Gateway)"),
            ("openai/gpt-4o", "GPT-4o (via Gateway)"),
            ("openrouter/meta-llama/llama-3.1-8b-instruct:free", "Llama 3.1 8B Free (via Gateway)"),
            ("groq/llama-3.1-8b-instant", "Llama 3.1 8B Fast (via Gateway)"),
        ],
    },
}


# ── Auth storage ──────────────────────────────────────────────────────────────

def _ensure_agent_dir() -> None:
    os.makedirs(AGENT_DIR, exist_ok=True)


def _load_auth() -> dict:
    _ensure_agent_dir()
    auth_path = os.path.join(AGENT_DIR, "auth.json")
    if os.path.exists(auth_path):
        try:
            with open(auth_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


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


def get_all_providers() -> list[dict]:
    """Return all providers with auth status. Order: Gateway, OAuth, API key."""
    auth = _load_auth()
    result = []

    # 1. Aery Gateway first
    gw = auth.get("aery-gateway", {})
    result.append({
        "id": "aery-gateway",
        "name": "Aery Gateway",
        "type": "gateway",
        "has_creds": bool(gw.get("key")),
        "connected": bool(gw.get("key")),
        "models": [m[0] for m in API_PROVIDERS["aery-gateway"]["models"]],
        "model_names": API_PROVIDERS["aery-gateway"]["models"],
    })

    # 2. OAuth providers
    for pid, cfg in OAUTH_CONFIGS.items():
        entry = auth.get(pid, {})
        has_creds = bool(entry.get("access") or entry.get("accessToken") or entry.get("refresh") or entry.get("refreshToken"))
        result.append({
            "id": pid,
            "name": cfg["name"],
            "type": "oauth",
            "has_creds": has_creds,
            "connected": has_creds,
            "models": [m[0] for m in _oauth_models(pid)],
            "model_names": _oauth_models(pid),
        })

    # 3. API key providers
    for pid, cfg in API_PROVIDERS.items():
        if pid == "aery-gateway":
            continue
        entry = auth.get(pid, {})
        has_creds = bool(entry.get("key"))
        result.append({
            "id": pid,
            "name": cfg["name"],
            "type": "api_key",
            "has_creds": has_creds,
            "connected": has_creds,
            "models": [m[0] for m in cfg["models"]],
            "model_names": cfg["models"],
            "needs_account_id": cfg.get("needs_account_id", False),
            "needs_base_url": cfg.get("needs_base_url", False),
            "needs_aws_creds": cfg.get("needs_aws_creds", False),
        })

    # 4. Any extra providers in auth.json not in our known list
    known = set(OAUTH_CONFIGS.keys()) | set(API_PROVIDERS.keys())
    for pid, entry in auth.items():
        if pid in known:
            continue
        result.append({
            "id": pid,
            "name": pid.replace("-", " ").title(),
            "type": entry.get("type", "api_key"),
            "has_creds": bool(entry.get("key") or entry.get("access")),
            "connected": bool(entry.get("key") or entry.get("access")),            "models": [],
            "model_names": [],
        })

    return result


def _oauth_models(pid: str) -> list[tuple]:
    models = {
        "google-antigravity": [
            ("claude-opus-4-5-thinking", "Claude Opus 4.5 Thinking"),
            ("claude-opus-4-6-thinking", "Claude Opus 4.6 Thinking"),
            ("gemini-2.5-pro", "Gemini 2.5 Pro"),
            ("gemini-2.5-flash", "Gemini 2.5 Flash"),
        ],
        "google-gemini-cli": [
            ("gemini-2.0-flash", "Gemini 2.0 Flash"),
            ("gemini-2.5-flash", "Gemini 2.5 Flash"),
            ("gemini-2.5-pro", "Gemini 2.5 Pro"),
        ],
        "openai-codex": [
            ("gpt-4o", "GPT-4o"),
            ("o1", "o1"),
            ("o3", "o3"),
            ("o4-mini", "o4 Mini"),
        ],
        "anthropic": [
            ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
            ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5"),
            ("claude-opus-4-5-20251101", "Claude Opus 4.5"),
        ],
        "github-copilot": [
            ("gpt-4o", "GPT-4o"),
            ("gpt-4.1", "GPT-4.1"),
            ("claude-sonnet-4-5", "Claude Sonnet 4.5"),
            ("gemini-2.5-pro", "Gemini 2.5 Pro"),
            ("o3", "o3"),
        ],
    }
    return models.get(pid, [])


def get_custom_providers() -> list[dict]:
    """Return custom OpenAI-compatible providers from models.json."""
    models_path = os.path.join(AGENT_DIR, "models.json")
    if not os.path.exists(models_path):
        return []
    try:
        with open(models_path) as f:
            data = json.load(f)
    except Exception:
        return []

    auth = _load_auth()
    result = []
    for pid, cfg in data.get("providers", {}).items():
        entry = auth.get(pid, {})
        result.append({
            "id": pid,
            "name": cfg.get("name", pid.replace("-", " ").title()),
            "type": "custom",
            "connected": bool(entry.get("key") or entry.get("access")),
            "models": cfg.get("models", []),
            "model_names": [(m, m) for m in cfg.get("models", [])],
            "base_url": cfg.get("baseUrl", ""),
        })
    return result


def get_active_provider() -> Optional[dict]:
    settings_path = os.path.join(AGENT_DIR, "settings.json")
    if not os.path.exists(settings_path):
        return None
    try:
        with open(settings_path) as f:
            s = json.load(f)
        pid = s.get("defaultProvider", "")
        model = s.get("defaultModel", "")
        if pid:
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
    save_api_key(provider_id, api_key)

    return {"provider_id": provider_id, "model_id": model_id}


def logout_provider(provider_id: str) -> bool:
    auth = _load_auth()
    if provider_id in auth:
        del auth[provider_id]
        _save_auth(auth)
        return True
    return False


# ── Connection testing ────────────────────────────────────────────────────────

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

    # ── Aery Gateway ──
    if provider_id == "aery-gateway":
        key = entry.get("key", "")
        if not key:
            return "Not configured"
        try:
            req = urllib.request.Request(
                "https://aery-gateway.eminent337.workers.dev/health",
                headers={"Authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                resp.read()
            return None
        except Exception as e:
            return str(e)

    # ── OAuth providers ──
    if provider_id in OAUTH_CONFIGS:
        access = entry.get("access") or entry.get("accessToken", "")
        if not access:
            return "Not logged in"
        if provider_id in ("google-antigravity", "google-gemini-cli"):
            return _post_json(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
                {"contents": [{"parts": [{"text": "hi"}]}]},
                {"Authorization": f"Bearer {access}"},
            )
        if provider_id == "anthropic":
            return _post_json(
                "https://api.anthropic.com/v1/messages",
                {"model": "claude-haiku-4-5-20251001", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]},
                {"x-api-key": access, "anthropic-version": "2023-06-01"},
            )
        if provider_id == "openai-codex":
            try:
                req = urllib.request.Request(
                    "https://api.github.com/user",
                    headers={"Authorization": f"Bearer {access}"},
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    resp.read()
                return None
            except Exception as e:
                return str(e)
        if provider_id == "github-copilot":
            try:
                req = urllib.request.Request(
                    "https://api.github.com/user",
                    headers={"Authorization": f"Bearer {access}"},
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    resp.read()
                return None
            except Exception as e:
                return str(e)
        return "Untestable"

    # ── API key providers ──
    cfg = API_PROVIDERS.get(provider_id)
    if not cfg:
        return "Unknown provider"
    key = entry.get("key", "")
    if not key:
        return "Not configured"

    base = cfg["base_url"]
    if cfg.get("needs_account_id"):
        account_id = entry.get("accountId", "")
        if not account_id:
            return "Missing Cloudflare Account ID"
        base = base.replace("{CLOUDFLARE_ACCOUNT_ID}", account_id)

    if provider_id == "anthropic":
        return _post_json(
            f"{base}/v1/messages",
            {"model": cfg["test_model"], "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]},
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
    if provider_id == "google":
        return _post_json(
            f"{base}/models/{cfg['test_model']}:generateContent",
            {"contents": [{"parts": [{"text": "hi"}]}]},
            {"x-goog-api-key": key},
        )
    if provider_id in ("minimax", "minimax-cn"):
        return _post_json(
            f"{base}/v1/messages",
            {"model": cfg["test_model"], "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]},
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
    if provider_id == "amazon-bedrock":
        return "AWS Bedrock requires AWS credentials — configure via AWS CLI"

    # Generic OpenAI-compatible
    path = cfg.get("test_path", "/v1/chat/completions")
    if not path.startswith("/v1"):
        path = "/v1/chat/completions"
    return _post_json(
        f"{base}{path}",
        {"model": cfg["test_model"], "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]},
        {"Authorization": f"Bearer {key}"},
    )


# ── Save credentials ──────────────────────────────────────────────────────────

def save_api_key(provider_id: str, key: str, account_id: str = "", base_url: str = "") -> None:
    auth = _load_auth()
    entry: dict = {"type": "api_key", "key": key}
    if account_id:
        entry["accountId"] = account_id
    if base_url:
        entry["baseUrl"] = base_url
    auth[provider_id] = entry
    _save_auth(auth)
    # Auto-activate if nothing active
    if not get_active_provider():
        cfg = API_PROVIDERS.get(provider_id, {})
        models = cfg.get("models", [])
        default_model = models[0][0] if models else ""
        set_active_provider(provider_id, default_model)


def save_gateway_key(aery_key: str) -> None:
    auth = _load_auth()
    auth["aery-gateway"] = {"type": "api_key", "key": aery_key}
    _save_auth(auth)
    if not get_active_provider():
        set_active_provider("aery-gateway", "anthropic/claude-haiku-4-5-20251001")


# ── OAuth login flow ──────────────────────────────────────────────────────────

def login_provider(provider_id: str) -> bool:
    """Run OAuth login. Returns True on success."""
    cfg = OAUTH_CONFIGS.get(provider_id)
    if not cfg:
        raise ValueError(f"Unknown OAuth provider: {provider_id}")

    if cfg.get("device_flow"):
        return _device_flow_login(provider_id, cfg)
    return _pkce_login(provider_id, cfg)


def _pkce_login(provider_id: str, cfg: dict) -> bool:
    port = cfg["redirect_port"]
    redirect_path = cfg["redirect_path"]
    redirect_uri = f"http://localhost:{port}{redirect_path}"

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(cfg["scopes"]),
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

        def _respond(self, status, body):
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

    if not result["code"]:
        return False


# ── Env-key auto-detection ─────────────────────────────────────────────────────
# Per-provider env-var names so the plugin can surface env credentials without
# requiring the user to open a terminal first.

ENV_KEY_MAP: dict[str, str] = {
    "anthropic":      "ANTHROPIC_API_KEY",
    "openai":         "OPENAI_API_KEY",
    "google":         "GEMINI_API_KEY",
    "groq":           "GROQ_API_KEY",
    "mistral":        "MISTRAL_API_KEY",
    "openrouter":     "OPENROUTER_API_KEY",
    "deepseek":       "DEEPSEEK_API_KEY",
    "xai":            "XAI_API_KEY",
    "kimi-coding":    "KIMI_API_KEY",
    "zai":            "ZAI_API_KEY",
    "minimax":        "MINIMAX_API_KEY",
    "minimax-cn":     "MINIMAX_CN_API_KEY",
    "fireworks":      "FIREWORKS_API_KEY",
    "huggingface":    "HF_TOKEN",
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

    exchange = {
        "client_id": cfg["client_id"],
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
        with urllib.request.urlopen(req) as resp:
            token_data = json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"Token exchange failed: {e}")

    access = token_data.get("access_token")
    if not access:
        raise RuntimeError(f"No access_token in response: {token_data}")

    auth = _load_auth()
    auth[provider_id] = {
        "type": "oauth",
        "access": access,
        "refresh": token_data.get("refresh_token", ""),
        "expires": int(time.time() * 1000) + int(token_data.get("expires_in", 3600)) * 1000,
        "tokenType": token_data.get("token_type", "Bearer"),
    }
    _save_auth(auth)
    if not get_active_provider():
        models = _oauth_models(provider_id)
        set_active_provider(provider_id, models[0][0] if models else "")
    return True


def _device_flow_login(provider_id: str, cfg: dict) -> bool:
    """GitHub Copilot device flow."""
    req = urllib.request.Request(
        cfg["auth_url"],
        data=urllib.parse.urlencode({
            "client_id": cfg["client_id"],
            "scope": " ".join(cfg["scopes"]),
        }).encode(),
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"Device code request failed: {e}")

    user_code = data.get("user_code", "")
    verification_uri = data.get("verification_uri", "https://github.com/login/device")
    device_code = data.get("device_code", "")
    interval = data.get("interval", 5)

    # Show user code in a simple way — caller should display this
    print(f"GitHub Copilot: go to {verification_uri} and enter code: {user_code}")
    webbrowser.open(verification_uri)

    # Poll for token
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(interval)
        poll_req = urllib.request.Request(
            cfg["token_url"],
            data=urllib.parse.urlencode({
                "client_id": cfg["client_id"],
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(poll_req, timeout=10) as resp:
                token_data = json.loads(resp.read().decode())
        except Exception:
            continue

        if "access_token" in token_data:
            auth = _load_auth()
            auth[provider_id] = {
                "type": "oauth",
                "access": token_data["access_token"],
                "refresh": token_data.get("refresh_token", ""),
                "tokenType": token_data.get("token_type", "Bearer"),
            }
            _save_auth(auth)
            if not get_active_provider():
                models = _oauth_models(provider_id)
                set_active_provider(provider_id, models[0][0] if models else "")
            return True
        if token_data.get("error") not in ("authorization_pending", "slow_down"):
            break

    return False
