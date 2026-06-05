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

# Helper to decode base64-encoded credentials (same as Aery source)
def _decode(s: str) -> str:
    import base64
    return base64.b64decode(s).decode()

# ── OAuth provider configs (exact from Aery) ──────────────────────────────────
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
    },

    "google-antigravity": {
        "name": "Google Antigravity",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "client_id": _decode("***REMOVED***"),
        "client_secret": _decode("***REMOVED***"),
        "redirect_port": 51121,
        "redirect_path": "/oauth-callback",
        "scopes": [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/cclog",
            "https://www.googleapis.com/auth/experimentsandconfigs",
        ],
    },
    "google-gemini-cli": {
        "name": "Gemini CLI (Cloud Code)",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "client_id": _decode("***REMOVED***"),
        "client_secret": _decode("***REMOVED***"),
        "redirect_port": 8085,
        "redirect_path": "/oauth2callback",
        "scopes": [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
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
        "name": "Anthropic (Claude Pro/Max)",
        "auth_url": "https://claude.ai/oauth/authorize",
        "token_url": "https://platform.claude.com/v1/oauth/token",
        "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        "client_secret": "",
        "redirect_port": 53692,
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

# ── Import from Aery provider registry ───────────────────────────────────────

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
            ("gemini-3-flash-preview", "Gemini 3 Flash Preview"),
            ("gemini-3-pro-preview", "Gemini 3 Pro Preview"),
            ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
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
        "base_url": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "mistral-small-latest",
        "models": [
            ("codestral-latest", "Codestral"),
            ("mistral-small-latest", "Mistral Small"),
            ("mistral-large-latest", "Mistral Large"),
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
        "test_model": "deepseek-v4-flash",
        "models": [
            ("deepseek-v4-flash", "DeepSeek V4 Flash"),
            ("deepseek-v4-pro", "DeepSeek V4 Pro"),
            ("deepseek-r1", "DeepSeek R1"),
            ("deepseek-v3.1", "DeepSeek V3.1"),
        ],
    },
    "ollama": {
        "name": "Ollama (Local)",
        "base_url": "http://localhost:11434/v1",
        "env_key": None,
        "test_path": "/chat/completions",
        "test_model": "llama3.2",
        "models": [
            ("llama3.2", "Llama 3.2"),
            ("llama3.1", "Llama 3.1"),
            ("llama3", "Llama 3"),
            ("mistral", "Mistral"),
            ("mixtral", "Mixtral"),
            ("codellama", "Code Llama"),
            ("phi3", "Phi 3"),
            ("gemma2", "Gemma 2"),
            ("qwen2.5", "Qwen 2.5"),
            ("deepseek-r1", "DeepSeek R1"),
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
            ("grok-4", "Grok 4"),
            ("grok-4-mini", "Grok 4 Mini"),
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
        "test_model": "llama3.1-8b",
        "models": [
            ("gpt-oss-120b", "GPT-OSS 120B"),
            ("llama3.1-8b", "Llama 3.1 8B"),
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
        "base_url": "https://opencode.ai/zen/v1",
        "env_key": "OPENCODE_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "big-pickle",
        "models": [
            ("big-pickle", "Big Pickle"),
            ("claude-3-5-haiku", "Claude Haiku 3.5"),
            ("claude-haiku-4-5", "Claude Haiku 4.5"),
            ("claude-opus-4-1", "Claude Opus 4.1"),
            ("claude-opus-4-5", "Claude Opus 4.5"),
            ("claude-opus-4-6", "Claude Opus 4.6"),
            ("claude-opus-4-7", "Claude Opus 4.7"),
            ("claude-opus-4-8", "Claude Opus 4.8"),
            ("claude-sonnet-4", "Claude Sonnet 4"),
            ("claude-sonnet-4-5", "Claude Sonnet 4.5"),
            ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
            ("deepseek-v4-flash", "DeepSeek V4 Flash"),
            ("deepseek-v4-flash-free", "DeepSeek V4 Flash Free"),
            ("gemini-3-flash", "Gemini 3 Flash"),
            ("gemini-3-pro", "Gemini 3 Pro"),
            ("gemini-3.1-pro", "Gemini 3.1 Pro Preview"),
            ("gemini-3.5-flash", "Gemini 3.5 Flash"),
            ("glm-4.6", "GLM-4.6"),
            ("glm-4.7", "GLM-4.7"),
            ("glm-5", "GLM-5"),
            ("glm-5.1", "GLM-5.1"),
            ("gpt-5", "GPT-5"),
            ("gpt-5-codex", "GPT-5 Codex"),
            ("gpt-5-nano", "GPT-5 Nano"),
            ("gpt-5.1", "GPT-5.1"),
            ("gpt-5.1-codex", "GPT-5.1 Codex"),
            ("gpt-5.1-codex-max", "GPT-5.1 Codex Max"),
            ("gpt-5.1-codex-mini", "GPT-5.1 Codex Mini"),
            ("gpt-5.2", "GPT-5.2"),
            ("gpt-5.2-codex", "GPT-5.2 Codex"),
            ("gpt-5.3-codex", "GPT-5.3 Codex"),
            ("gpt-5.3-codex-spark", "GPT-5.3 Codex Spark"),
            ("gpt-5.4", "GPT-5.4"),
            ("gpt-5.4-mini", "GPT-5.4 Mini"),
            ("gpt-5.4-nano", "GPT-5.4 Nano"),
            ("gpt-5.4-pro", "GPT-5.4 Pro"),
            ("gpt-5.5", "GPT-5.5"),
            ("gpt-5.5-pro", "GPT-5.5 Pro"),
            ("grok-build-0.1", "Grok Build 0.1"),
            ("hy3-preview-free", "Hy3 preview Free"),
            ("kimi-k2", "Kimi K2"),
            ("kimi-k2-thinking", "Kimi K2 Thinking"),
            ("kimi-k2.5", "Kimi K2.5"),
            ("kimi-k2.6", "Kimi K2.6"),
            ("ling-2.6-flash-free", "Ling 2.6 Flash Free"),
            ("mimo-v2-flash-free", "MiMo V2 Flash Free"),
            ("mimo-v2-omni-free", "MiMo V2 Omni Free"),
            ("mimo-v2-pro-free", "MiMo V2 Pro Free"),
            ("mimo-v2.5-free", "MiMo V2.5 Free"),
            ("minimax-m2.1", "MiniMax M2.1"),
            ("minimax-m2.5", "MiniMax M2.5"),
            ("minimax-m2.5-free", "MiniMax M2.5 Free"),
            ("minimax-m2.7", "MiniMax M2.7"),
            ("minimax-m3-free", "MiniMax M3 Free"),
            ("nemotron-3-super-free", "Nemotron 3 Super Free"),
            ("qwen3.5-plus", "Qwen3.5 Plus"),
            ("qwen3.6-plus", "Qwen3.6 Plus"),
            ("qwen3.6-plus-free", "Qwen3.6 Plus Free"),
            ("ring-2.6-1t-free", "Ring 2.6 1T Free"),
            ("trinity-large-preview-free", "Trinity Large Preview"),
        ],
    },
    "kimi-coding": {
        "name": "Kimi For Coding",
        "base_url": "https://api.kimi.com/coding",
        "env_key": "KIMI_API_KEY",
        "test_path": "/v1/messages",
        "test_model": "kimi-for-coding",
        "models": [
            ("kimi-for-coding", "Kimi For Coding"),
            ("kimi-k2-thinking", "Kimi K2 Thinking"),
        ],
    },
    "zai": {
        "name": "ZAI",
        "base_url": "https://api.z.ai/api/coding/paas/v4",
        "env_key": "ZAI_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "glm-4.5-air",
        "models": [
            ("glm-4.5-air", "GLM 4.5 Air"),
            ("glm-4.7", "GLM 4.7"),
        ],
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimax.io/anthropic",
        "env_key": "MINIMAX_API_KEY",
        "test_path": "/v1/messages",
        "test_model": "MiniMax-M2.7",
        "models": [
            ("MiniMax-M2.7", "MiniMax M2.7"),
            ("MiniMax-M2.7-highspeed", "MiniMax M2.7 Highspeed"),
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
            ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
            ("gemini-1.5-flash", "Gemini 1.5 Flash"),
            ("gemini-1.5-pro", "Gemini 1.5 Pro"),
        ],
    },
    "minimax-cn": {
        "name": "MiniMax CN",
        "base_url": "https://api.minimaxi.com/anthropic",
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
        "base_url": "https://opencode.ai/zen/go/v1",
        "env_key": "OPENCODE_API_KEY",
        "test_path": "/chat/completions",
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
    "together": {
        "name": "Together AI",
        "base_url": "https://api.together.ai/v1",
        "env_key": "TOGETHER_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "meta-llama/Llama-3-8b-chat-hf",
        "models": [
            ("meta-llama/Llama-3-8b-chat-hf", "Llama 3 8B"),
            ("meta-llama/Llama-3-70b-chat-hf", "Llama 3 70B"),
            ("mistralai/Mixtral-8x7B-Instruct-v0.1", "Mixtral 8x7B"),
            ("deepseek-ai/deepseek-r1", "DeepSeek R1"),
        ],
    },
    "moonshotai": {
        "name": "Moonshot AI",
        "base_url": "https://api.moonshot.ai/v1",
        "env_key": "MOONSHOT_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "moonshot-v1-8k",
        "models": [
            ("moonshot-v1-8k", "Moonshot V1 8K"),
            ("moonshot-v1-32k", "Moonshot V1 32K"),
            ("moonshot-v1-128k", "Moonshot V1 128K"),
        ],
    },
    "moonshotai-cn": {
        "name": "Moonshot AI (CN)",
        "base_url": "https://api.moonshot.cn/v1",
        "env_key": "MOONSHOT_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "moonshot-v1-8k",
        "models": [
            ("moonshot-v1-8k", "Moonshot V1 8K"),
            ("moonshot-v1-32k", "Moonshot V1 32K"),
            ("moonshot-v1-128k", "Moonshot V1 128K"),
        ],
    },
    "xiaomi": {
        "name": "Xiaomi MiMo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "env_key": "XIAOMI_API_KEY",
        "test_path": "/chat/completions",
        "test_model": "mimo-v2-omni",
        "models": [
            ("mimo-v2-omni", "Mimo V2 Omni"),
            ("mimo-v2-pro", "Mimo V2 Pro"),
        ],
    },
    "cloudflare-ai-gateway": {
        "name": "Cloudflare AI Gateway",
        "base_url": "https://gateway.ai.cloudflare.com/v1/{CLOUDFLARE_ACCOUNT_ID}/{CLOUDFLARE_GATEWAY_ID}/anthropic",
        "env_key": "CLOUDFLARE_API_KEY",
        "test_path": "/v1/messages",
        "test_model": "claude-haiku-4-5-20251001",
        "needs_account_id": True,
        "models": [
            ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
            ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5"),
            ("claude-opus-4-5-20251101", "Claude Opus 4.5"),
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
        "kilo": [
            ("~anthropic/claude-haiku-latest", "Anthropic Claude Haiku Latest"),
            ("~anthropic/claude-opus-latest", "Anthropic: Claude Opus Latest"),
            ("~anthropic/claude-sonnet-latest", "Anthropic Claude Sonnet Latest"),
            ("~google/gemini-flash-latest", "Google Gemini Flash Latest"),
            ("~google/gemini-pro-latest", "Google Gemini Pro Latest"),
            ("~moonshotai/kimi-latest", "MoonshotAI Kimi Latest"),
            ("~openai/gpt-latest", "OpenAI GPT Latest"),
            ("~openai/gpt-mini-latest", "OpenAI GPT Mini Latest"),
            ("ai21/jamba-large-1.7", "AI21: Jamba Large 1.7"),
            ("aion-labs/aion-1.0", "AionLabs: Aion-1.0"),
            ("aion-labs/aion-1.0-mini", "AionLabs: Aion-1.0-Mini"),
            ("aion-labs/aion-2.0", "AionLabs: Aion-2.0"),
            ("aion-labs/aion-rp-llama-3.1-8b", "AionLabs: Aion-RP 1.0 (8B)"),
            ("alfredpros/codellama-7b-instruct-solidity", "AlfredPros: CodeLLaMa 7B Instruct Solidity"),
            ("alibaba/tongyi-deepresearch-30b-a3b", "Tongyi DeepResearch 30B A3B"),
            ("allenai/molmo-2-8b", "AllenAI: Molmo2 8B"),
            ("allenai/olmo-2-0325-32b-instruct", "AllenAI: Olmo 2 32B Instruct"),
            ("allenai/olmo-3-32b-think", "AllenAI: Olmo 3 32B Think"),
            ("allenai/olmo-3-7b-instruct", "AllenAI: Olmo 3 7B Instruct"),
            ("allenai/olmo-3-7b-think", "AllenAI: Olmo 3 7B Think"),
            ("allenai/olmo-3.1-32b-instruct", "AllenAI: Olmo 3.1 32B Instruct"),
            ("allenai/olmo-3.1-32b-think", "AllenAI: Olmo 3.1 32B Think"),
            ("alpindale/goliath-120b", "Goliath 120B"),
            ("amazon/nova-2-lite-v1", "Amazon: Nova 2 Lite"),
            ("amazon/nova-lite-v1", "Amazon: Nova Lite 1.0"),
            ("amazon/nova-micro-v1", "Amazon: Nova Micro 1.0"),
            ("amazon/nova-premier-v1", "Amazon: Nova Premier 1.0"),
            ("amazon/nova-pro-v1", "Amazon: Nova Pro 1.0"),
            ("anthracite-org/magnum-v4-72b", "Magnum v4 72B"),
            ("anthropic/claude-3-haiku", "Claude Haiku 3"),
            ("anthropic/claude-3.5-haiku", "Claude 3.5 Haiku"),
            ("anthropic/claude-3.5-sonnet", "Claude Sonnet 3.5 v2"),
            ("anthropic/claude-3.7-sonnet", "Claude 3.7 Sonnet"),
            ("anthropic/claude-3.7-sonnet:thinking", "Anthropic: Claude 3.7 Sonnet (thinking)"),
            ("anthropic/claude-haiku-4.5", "Claude Haiku 4.5"),
            ("anthropic/claude-opus-4", "Claude Opus 4 (latest)"),
            ("anthropic/claude-opus-4.1", "Claude Opus 4.1"),
            ("anthropic/claude-opus-4.5", "Claude Opus 4.5"),
            ("anthropic/claude-opus-4.6", "Claude Opus 4.6"),
            ("anthropic/claude-opus-4.6-fast", "Anthropic: Claude Opus 4.6 (Fast) ($$$$)"),
            ("anthropic/claude-opus-4.7", "Claude Opus 4.7"),
            ("anthropic/claude-opus-4.7-fast", "Anthropic: Claude Opus 4.7 (Fast) ($$$$)"),
            ("anthropic/claude-opus-4.8", "Anthropic: Claude Opus 4.8 (new)"),
            ("anthropic/claude-opus-4.8-fast", "Anthropic: Claude Opus 4.8 (Fast) ($$$$)"),
            ("anthropic/claude-sonnet-4", "Claude Sonnet 4"),
            ("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5"),
            ("anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6"),
            ("arcee-ai/coder-large", "Arcee AI: Coder Large"),
            ("arcee-ai/maestro-reasoning", "Arcee AI: Maestro Reasoning"),
            ("arcee-ai/spotlight", "Arcee AI: Spotlight"),
            ("arcee-ai/trinity-large-preview", "Arcee AI: Trinity Large Preview"),
            ("arcee-ai/trinity-large-preview:free", "Arcee AI: Trinity Large Preview (free)"),
            ("arcee-ai/trinity-large-thinking", "Arcee AI: Trinity Large Thinking"),
            ("arcee-ai/trinity-large-thinking:free", "Arcee AI: Trinity Large Thinking (free)"),
            ("arcee-ai/trinity-mini", "Arcee AI: Trinity Mini"),
            ("arcee-ai/virtuoso-large", "Arcee AI: Virtuoso Large"),
            ("baidu/cobuddy:free", "Baidu Qianfan: CoBuddy (free)"),
            ("baidu/ernie-4.5-21b-a3b", "Baidu: ERNIE 4.5 21B A3B"),
            ("baidu/ernie-4.5-21b-a3b-thinking", "Baidu: ERNIE 4.5 21B A3B Thinking"),
            ("baidu/ernie-4.5-300b-a47b", "Baidu: ERNIE 4.5 300B A47B"),
            ("baidu/ernie-4.5-vl-28b-a3b", "Baidu: ERNIE 4.5 VL 28B A3B"),
            ("baidu/ernie-4.5-vl-424b-a47b", "Baidu: ERNIE 4.5 VL 424B A47B"),
            ("baidu/qianfan-ocr-fast", "Baidu: Qianfan-OCR-Fast"),
            ("baidu/qianfan-ocr-fast:free", "Baidu: Qianfan-OCR-Fast (free)"),
            ("bytedance-seed/dola-seed-2.0-pro:free", "ByteDance Seed: Dola Seed 2.0 Pro (free)"),
            ("bytedance-seed/seed-1.6", "ByteDance Seed: Seed 1.6"),
            ("bytedance-seed/seed-1.6-flash", "ByteDance Seed: Seed 1.6 Flash"),
            ("bytedance-seed/seed-2.0-lite", "ByteDance Seed: Seed-2.0-Lite"),
            ("bytedance-seed/seed-2.0-mini", "ByteDance Seed: Seed-2.0-Mini"),
            ("bytedance/ui-tars-1.5-7b", "ByteDance: UI-TARS 7B"),
            ("cohere/command-a", "Cohere: Command A"),
            ("cohere/command-r-08-2024", "Cohere: Command R (08-2024)"),
            ("cohere/command-r-plus-08-2024", "Cohere: Command R+ (08-2024)"),
            ("cohere/command-r7b-12-2024", "Cohere: Command R7B (12-2024)"),
            ("corethink:free", "CoreThink (free)"),
            ("deepcogito/cogito-v2.1-671b", "Deep Cogito: Cogito v2.1 671B"),
            ("deepseek/deepseek-chat", "DeepSeek-V3.2 (Non-thinking Mode)"),
            ("deepseek/deepseek-chat-v3-0324", "DeepSeek: DeepSeek V3 0324"),
            ("deepseek/deepseek-chat-v3.1", "DeepSeek: DeepSeek V3.1"),
            ("deepseek/deepseek-r1", "DeepSeek: R1"),
            ("deepseek/deepseek-r1-0528", "DeepSeek: R1 0528"),
            ("deepseek/deepseek-r1-distill-llama-70b", "DeepSeek: R1 Distill Llama 70B"),
            ("deepseek/deepseek-r1-distill-qwen-32b", "DeepSeek: R1 Distill Qwen 32B"),
            ("deepseek/deepseek-v3.1-terminus", "DeepSeek: DeepSeek V3.1 Terminus"),
            ("deepseek/deepseek-v3.1-terminus:exacto", "DeepSeek: DeepSeek V3.1 Terminus (exacto)"),
            ("deepseek/deepseek-v3.2", "DeepSeek V3.2"),
            ("deepseek/deepseek-v3.2-exp", "DeepSeek-V3.2-Exp"),
            ("deepseek/deepseek-v3.2-speciale", "DeepSeek: DeepSeek V3.2 Speciale"),
            ("deepseek/deepseek-v4-flash", "DeepSeek V4 Flash"),
            ("deepseek/deepseek-v4-flash:discounted", "DeepSeek: DeepSeek V4 Flash (>40% off)"),
            ("deepseek/deepseek-v4-flash:free", "DeepSeek: DeepSeek V4 Flash (free)"),
            ("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro"),
            ("deepseek/deepseek-v4-pro:discounted", "DeepSeek: DeepSeek V4 Pro (>80% off)"),
            ("eleutherai/llemma_7b", "EleutherAI: Llemma 7b"),
            ("essentialai/rnj-1-instruct", "EssentialAI: Rnj 1 Instruct"),
            ("giga-potato", "Giga Potato (free)"),
            ("giga-potato-thinking", "Giga Potato Thinking (free)"),
            ("google/gemini-2.0-flash-001", "Google: Gemini 2.0 Flash"),
            ("google/gemini-2.0-flash-lite-001", "Google: Gemini 2.0 Flash Lite"),
            ("google/gemini-2.5-flash", "Gemini 2.5 Flash"),
            ("google/gemini-2.5-flash-image", "Google: Nano Banana (Gemini 2.5 Flash Image)"),
            ("google/gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite"),
            ("google/gemini-2.5-flash-lite-preview-09-2025", "Google: Gemini 2.5 Flash Lite Preview 09-2025"),
            ("google/gemini-2.5-pro", "Gemini 2.5 Pro"),
            ("google/gemini-2.5-pro-preview", "Google: Gemini 2.5 Pro Preview 06-05"),
            ("google/gemini-2.5-pro-preview-05-06", "Google: Gemini 2.5 Pro Preview 05-06"),
            ("google/gemini-3-flash-preview", "Gemini 3 Flash Preview"),
            ("google/gemini-3-pro-image-preview", "Google: Nano Banana Pro (Gemini 3 Pro Image Preview)"),
            ("google/gemini-3-pro-preview", "Gemini 3 Pro Preview"),
            ("google/gemini-3.1-flash-image-preview", "Google: Nano Banana 2 (Gemini 3.1 Flash Image Preview)"),
            ("google/gemini-3.1-flash-lite", "Google: Gemini 3.1 Flash Lite"),
            ("google/gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash Lite Preview"),
            ("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
            ("google/gemini-3.1-pro-preview-customtools", "Google: Gemini 3.1 Pro Preview Custom Tools"),
            ("google/gemini-3.5-flash", "Google: Gemini 3.5 Flash"),
            ("google/gemma-2-27b-it", "Google: Gemma 2 27B"),
            ("google/gemma-2-9b-it", "Google: Gemma 2 9B"),
            ("google/gemma-3-12b-it", "Google: Gemma 3 12B"),
            ("google/gemma-3-27b-it", "Gemma-3-27B-IT"),
            ("google/gemma-3-4b-it", "Google: Gemma 3 4B"),
            ("google/gemma-3n-e4b-it", "Gemma 3n E4b It"),
            ("google/gemma-4-26b-a4b-it", "Google: Gemma 4 26B A4B"),
            ("google/gemma-4-31b-it", "Gemma-4-31B-IT"),
            ("google/lyria-3-clip-preview", "Google: Lyria 3 Clip Preview"),
            ("google/lyria-3-pro-preview", "Google: Lyria 3 Pro Preview"),
            ("gryphe/mythomax-l2-13b", "MythoMax 13B"),
            ("ibm-granite/granite-4.0-h-micro", "IBM: Granite 4.0 Micro"),
            ("ibm-granite/granite-4.1-8b", "IBM: Granite 4.1 8B"),
            ("inception/mercury", "Inception: Mercury"),
            ("inception/mercury-2", "Inception: Mercury 2"),
            ("inception/mercury-coder", "Inception: Mercury Coder"),
            ("inclusionai/ling-2.6-1t", "inclusionAI: Ling-2.6-1T"),
            ("inclusionai/ling-2.6-1t:free", "inclusionAI: Ling-2.6-1T (free)"),
            ("inclusionai/ling-2.6-flash", "inclusionAI: Ling-2.6-flash"),
            ("inclusionai/ling-2.6-flash:free", "inclusionAI: Ling-2.6-flash (free)"),
            ("inclusionai/ring-2.6-1t", "inclusionAI: Ring-2.6-1T"),
            ("inclusionai/ring-2.6-1t:free", "inclusionAI: Ring-2.6-1T (free)"),
            ("inflection/inflection-3-pi", "Inflection: Inflection 3 Pi"),
            ("inflection/inflection-3-productivity", "Inflection: Inflection 3 Productivity"),
            ("kilo-auto/balanced", "Auto Balanced"),
            ("kilo-auto/free", "Auto Free"),
            ("kilo-auto/frontier", "Auto Frontier"),
            ("kilo-auto/small", "Auto Small"),
            ("kilo/auto", "Deprecated Kilo Auto Frontier"),
            ("kilo/auto-free", "Deprecated Kilo Auto Free"),
            ("kilo/auto-small", "Deprecated Kilo Auto Small"),
            ("kwaipilot/kat-coder-pro", "Kwaipilot: KAT-Coder-Pro V1"),
            ("kwaipilot/kat-coder-pro-v2", "Kwaipilot: KAT-Coder-Pro V2"),
            ("liquid/lfm-2-24b-a2b", "LiquidAI: LFM2-24B-A2B"),
            ("liquid/lfm-2.2-6b", "LiquidAI: LFM2-2.6B"),
            ("liquid/lfm2-8b-a1b", "LiquidAI: LFM2-8B-A1B"),
            ("mancer/weaver", "Mancer: Weaver (alpha)"),
            ("meituan/longcat-flash-chat", "Meituan: LongCat Flash Chat"),
            ("meta-llama/llama-3-70b-instruct", "Meta: Llama 3 70B Instruct"),
            ("meta-llama/llama-3-8b-instruct", "Meta: Llama 3 8B Instruct"),
            ("meta-llama/llama-3.1-405b", "Meta: Llama 3.1 405B (base)"),
            ("meta-llama/llama-3.1-405b-instruct", "Meta: Llama 3.1 405B Instruct"),
            ("meta-llama/llama-3.1-70b-instruct", "Meta: Llama 3.1 70B Instruct"),
            ("meta-llama/llama-3.1-8b-instruct", "Meta: Llama 3.1 8B Instruct"),
            ("meta-llama/llama-3.2-11b-vision-instruct", "Meta: Llama 3.2 11B Vision Instruct"),
            ("meta-llama/llama-3.2-1b-instruct", "Meta: Llama 3.2 1B Instruct"),
            ("meta-llama/llama-3.2-3b-instruct", "Meta: Llama 3.2 3B Instruct"),
            ("meta-llama/llama-3.3-70b-instruct", "Meta: Llama 3.3 70B Instruct"),
            ("meta-llama/llama-4-maverick", "Meta: Llama 4 Maverick"),
            ("meta-llama/llama-4-scout", "Meta: Llama 4 Scout"),
            ("meta-llama/llama-guard-2-8b", "Meta: LlamaGuard 2 8B"),
            ("meta-llama/llama-guard-3-8b", "Llama Guard 3 8B"),
            ("meta-llama/llama-guard-4-12b", "Meta: Llama Guard 4 12B"),
            ("meta-llama/llama-guard-4-12b:free", "Meta: Llama Guard 4 12B (free)"),
            ("microsoft/phi-4", "Microsoft: Phi 4"),
            ("microsoft/phi-4-mini-instruct", "Phi-4-Mini"),
            ("microsoft/wizardlm-2-8x22b", "WizardLM-2 8x22B"),
            ("minimax/minimax-01", "MiniMax: MiniMax-01"),
            ("minimax/minimax-m1", "MiniMax: MiniMax M1"),
            ("minimax/minimax-m2", "MiniMax M2"),
            ("minimax/minimax-m2-her", "MiniMax: MiniMax M2-her"),
            ("minimax/minimax-m2.1", "MiniMax M2.1"),
            ("minimax/minimax-m2.5", "MiniMax M2.5"),
            ("minimax/minimax-m2.5:free", "MiniMax: MiniMax M2.5 (free)"),
            ("minimax/minimax-m2.7", "MiniMax M2.7"),
            ("minimax/minimax-m3", "MiniMax: MiniMax M3 (new)"),
            ("mistralai/codestral-2508", "Mistral: Codestral 2508"),
            ("mistralai/devstral-2512", "Mistral: Devstral 2 2512"),
            ("mistralai/devstral-medium", "Mistral: Devstral Medium"),
            ("mistralai/devstral-small", "Mistral: Devstral Small 1.1"),
            ("mistralai/ministral-14b-2512", "Mistral: Ministral 3 14B 2512"),
            ("mistralai/ministral-3b-2512", "Mistral: Ministral 3 3B 2512"),
            ("mistralai/ministral-8b-2512", "Mistral: Ministral 3 8B 2512"),
            ("mistralai/mistral-7b-instruct", "Mistral: Mistral 7B Instruct"),
            ("mistralai/mistral-7b-instruct-v0.1", "Mistral: Mistral 7B Instruct v0.1"),
            ("mistralai/mistral-7b-instruct-v0.3", "Mistral: Mistral 7B Instruct v0.3"),
            ("mistralai/mistral-large", "Mistral Large"),
            ("mistralai/mistral-large-2407", "Mistral Large 2407"),
            ("mistralai/mistral-large-2411", "Mistral Large 2411"),
            ("mistralai/mistral-large-2512", "Mistral: Mistral Large 3 2512"),
            ("mistralai/mistral-medium-3", "Mistral: Mistral Medium 3"),
            ("mistralai/mistral-medium-3-5", "Mistral: Mistral Medium 3.5"),
            ("mistralai/mistral-medium-3.1", "Mistral: Mistral Medium 3.1"),
            ("mistralai/mistral-nemo", "Mistral: Mistral Nemo"),
            ("mistralai/mistral-saba", "Mistral: Saba"),
            ("mistralai/mistral-small-24b-instruct-2501", "Mistral: Mistral Small 3"),
            ("mistralai/mistral-small-2603", "Mistral: Mistral Small 4"),
            ("mistralai/mistral-small-3.1-24b-instruct", "Mistral: Mistral Small 3.1 24B"),
            ("mistralai/mistral-small-3.2-24b-instruct", "Mistral: Mistral Small 3.2 24B"),
            ("mistralai/mistral-small-creative", "Mistral: Mistral Small Creative"),
            ("mistralai/mixtral-8x22b-instruct", "Mistral: Mixtral 8x22B Instruct"),
            ("mistralai/mixtral-8x7b-instruct", "Mistral: Mixtral 8x7B Instruct"),
            ("mistralai/pixtral-large-2411", "Mistral: Pixtral Large 2411"),
            ("mistralai/voxtral-small-24b-2507", "Mistral: Voxtral Small 24B 2507"),
            ("moonshotai/kimi-k2", "MoonshotAI: Kimi K2 0711"),
            ("moonshotai/kimi-k2-0905", "Kimi K2 0905"),
            ("moonshotai/kimi-k2-0905:exacto", "MoonshotAI: Kimi K2 0905 (exacto)"),
            ("moonshotai/kimi-k2-thinking", "Kimi K2 Thinking"),
            ("moonshotai/kimi-k2.5", "Kimi K2.5"),
            ("moonshotai/kimi-k2.5:free", "MoonshotAI: Kimi K2.5 (free)"),
            ("moonshotai/kimi-k2.6", "Kimi K2.6"),
            ("moonshotai/kimi-k2.6:free", "MoonshotAI: Kimi K2.6 (free)"),
            ("morph-warp-grep-v2", "Morph: WarpGrep V2"),
            ("morph/morph-v3-fast", "Morph: Morph V3 Fast"),
            ("morph/morph-v3-large", "Morph: Morph V3 Large"),
            ("neversleep/llama-3.1-lumimaid-8b", "NeverSleep: Lumimaid v0.2 8B"),
            ("neversleep/noromaid-20b", "Noromaid 20B"),
            ("nex-agi/deepseek-v3.1-nex-n1", "Nex AGI: DeepSeek V3.1 Nex N1"),
            ("nousresearch/hermes-2-pro-llama-3-8b", "NousResearch: Hermes 2 Pro - Llama-3 8B (retires Jun 5)"),
            ("nousresearch/hermes-3-llama-3.1-405b", "Nous: Hermes 3 405B Instruct"),
            ("nousresearch/hermes-3-llama-3.1-70b", "Nous: Hermes 3 70B Instruct"),
            ("nousresearch/hermes-4-405b", "Nous: Hermes 4 405B"),
            ("nousresearch/hermes-4-70b", "Nous: Hermes 4 70B"),
            ("nvidia/llama-3.1-nemotron-70b-instruct", "Llama 3.1 Nemotron 70b Instruct"),
            ("nvidia/llama-3.1-nemotron-ultra-253b-v1", "Llama-3.1-Nemotron-Ultra-253B-v1"),
            ("nvidia/llama-3.3-nemotron-super-49b-v1.5", "NVIDIA: Llama 3.3 Nemotron Super 49B V1.5"),
            ("nvidia/nemotron-3-nano-30b-a3b", "nemotron-3-nano-30b-a3b"),
            ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "NVIDIA: Nemotron 3 Nano Omni (free)"),
            ("nvidia/nemotron-3-super-120b-a12b", "Nemotron 3 Super"),
            ("nvidia/nemotron-3-super-120b-a12b:free", "NVIDIA: Nemotron 3 Super (free)"),
            ("nvidia/nemotron-nano-12b-v2-vl", "NVIDIA: Nemotron Nano 12B 2 VL"),
            ("nvidia/nemotron-nano-9b-v2", "NVIDIA: Nemotron Nano 9B V2"),
            ("openai/gpt-3.5-turbo", "OpenAI: GPT-3.5 Turbo"),
            ("openai/gpt-3.5-turbo-0613", "OpenAI: GPT-3.5 Turbo (older v0613)"),
            ("openai/gpt-3.5-turbo-16k", "OpenAI: GPT-3.5 Turbo 16k"),
            ("openai/gpt-3.5-turbo-instruct", "OpenAI: GPT-3.5 Turbo Instruct"),
            ("openai/gpt-4", "GPT-4"),
            ("openai/gpt-4-0314", "OpenAI: GPT-4 (older v0314) ($$$$)"),
            ("openai/gpt-4-1106-preview", "OpenAI: GPT-4 Turbo (older v1106) ($$$$)"),
            ("openai/gpt-4-turbo", "GPT-4 Turbo"),
            ("openai/gpt-4-turbo-preview", "OpenAI: GPT-4 Turbo Preview ($$$$)"),
            ("openai/gpt-4.1", "OpenAI: GPT-4.1"),
            ("openai/gpt-4.1-mini", "OpenAI: GPT-4.1 Mini"),
            ("openai/gpt-4.1-nano", "OpenAI: GPT-4.1 Nano"),
            ("openai/gpt-4o", "GPT-4o"),
            ("openai/gpt-4o-2024-05-13", "OpenAI: GPT-4o (2024-05-13)"),
            ("openai/gpt-4o-2024-08-06", "OpenAI: GPT-4o (2024-08-06)"),
            ("openai/gpt-4o-2024-11-20", "OpenAI: GPT-4o (2024-11-20)"),
            ("openai/gpt-4o-audio-preview", "OpenAI: GPT-4o Audio"),
            ("openai/gpt-4o-mini", "GPT-4o mini"),
            ("openai/gpt-4o-mini-2024-07-18", "OpenAI: GPT-4o-mini (2024-07-18)"),
            ("openai/gpt-4o-mini-search-preview", "OpenAI: GPT-4o-mini Search Preview"),
            ("openai/gpt-4o-search-preview", "OpenAI: GPT-4o Search Preview"),
            ("openai/gpt-4o:extended", "OpenAI: GPT-4o (extended)"),
            ("openai/gpt-5", "GPT-5"),
            ("openai/gpt-5-chat", "OpenAI: GPT-5 Chat"),
            ("openai/gpt-5-codex", "GPT-5 Codex"),
            ("openai/gpt-5-image", "OpenAI: GPT-5 Image ($$$$)"),
            ("openai/gpt-5-image-mini", "OpenAI: GPT-5 Image Mini"),
            ("openai/gpt-5-mini", "OpenAI: GPT-5 Mini"),
            ("openai/gpt-5-nano", "OpenAI: GPT-5 Nano"),
            ("openai/gpt-5-pro", "OpenAI: GPT-5 Pro ($$$$)"),
            ("openai/gpt-5.1", "GPT-5.1"),
            ("openai/gpt-5.1-chat", "GPT-5.1 Chat"),
            ("openai/gpt-5.1-codex", "GPT-5.1 Codex"),
            ("openai/gpt-5.1-codex-max", "OpenAI: GPT-5.1-Codex-Max"),
            ("openai/gpt-5.1-codex-mini", "GPT-5.1-Codex-Mini"),
            ("openai/gpt-5.2", "GPT-5.2"),
            ("openai/gpt-5.2-chat", "OpenAI: GPT-5.2 Chat (retires Aug 10)"),
            ("openai/gpt-5.2-codex", "GPT-5.2 Codex"),
            ("openai/gpt-5.2-pro", "GPT-5.2-Pro"),
            ("openai/gpt-5.3-chat", "GPT-5.3 Chat"),
            ("openai/gpt-5.3-codex", "GPT-5.3 Codex"),
            ("openai/gpt-5.4", "GPT-5.4"),
            ("openai/gpt-5.4-image-2", "OpenAI: GPT-5.4 Image 2"),
            ("openai/gpt-5.4-mini", "GPT-5.4 Mini"),
            ("openai/gpt-5.4-nano", "GPT-5.4 Nano"),
            ("openai/gpt-5.4-pro", "GPT-5.4 Pro"),
            ("openai/gpt-5.5", "GPT-5.5"),
            ("openai/gpt-5.5-pro", "GPT-5.5 Pro"),
            ("openai/gpt-audio", "OpenAI: GPT Audio"),
            ("openai/gpt-audio-mini", "OpenAI: GPT Audio Mini"),
            ("openai/gpt-chat-latest", "OpenAI: GPT Chat Latest"),
            ("openai/gpt-oss-120b", "GPT OSS 120B"),
            ("openai/gpt-oss-120b:exacto", "OpenAI: gpt-oss-120b (exacto)"),
            ("openai/gpt-oss-20b", "GPT OSS 20B"),
            ("openai/gpt-oss-safeguard-20b", "Safety GPT OSS 20B"),
            ("openai/o1", "o1"),
            ("openai/o1-pro", "OpenAI: o1-pro ($$$$)"),
            ("openai/o3", "o3"),
            ("openai/o3-deep-research", "OpenAI: o3 Deep Research ($$$$)"),
            ("openai/o3-mini", "o3-mini"),
            ("openai/o3-mini-high", "OpenAI: o3 Mini High"),
            ("openai/o3-pro", "o3-pro"),
            ("openai/o4-mini", "o4-mini"),
            ("openai/o4-mini-deep-research", "OpenAI: o4 Mini Deep Research"),
            ("openai/o4-mini-high", "OpenAI: o4 Mini High"),
            ("opengvlab/internvl3-78b", "OpenGVLab: InternVL3 78B"),
            ("openrouter/auto", "Auto Router"),
            ("openrouter/bodybuilder", "Body Builder (beta)"),
            ("openrouter/elephant-alpha", "Elephant (new)"),
            ("openrouter/free", "Free Models Router"),
            ("openrouter/healer-alpha", "Healer Alpha"),
            ("openrouter/hunter-alpha", "Hunter Alpha"),
            ("openrouter/owl-alpha", "Owl Alpha"),
            ("openrouter/pareto-code", "Pareto Code Router"),
            ("perceptron/perceptron-mk1", "Perceptron: Perceptron Mk1"),
            ("perplexity/sonar", "Perplexity: Sonar"),
            ("perplexity/sonar-deep-research", "Perplexity: Sonar Deep Research"),
            ("perplexity/sonar-pro", "Perplexity: Sonar Pro"),
            ("perplexity/sonar-pro-search", "Perplexity: Sonar Pro Search"),
            ("perplexity/sonar-reasoning-pro", "Perplexity: Sonar Reasoning Pro"),
            ("poolside/laguna-m.1:free", "Poolside: Laguna M.1 (free)"),
            ("poolside/laguna-xs.2:free", "Poolside: Laguna XS.2 (free)"),
            ("prime-intellect/intellect-3", "Prime Intellect: INTELLECT-3"),
            ("qwen/qwen-2.5-72b-instruct", "Qwen2.5 72B Instruct"),
            ("qwen/qwen-2.5-7b-instruct", "Qwen: Qwen2.5 7B Instruct"),
            ("qwen/qwen-2.5-coder-32b-instruct", "Qwen2.5 Coder 32B Instruct"),
            ("qwen/qwen-2.5-vl-7b-instruct", "Qwen: Qwen2.5-VL 7B Instruct"),
            ("qwen/qwen-max", "Qwen: Qwen-Max"),
            ("qwen/qwen-plus", "Qwen: Qwen-Plus"),
            ("qwen/qwen-plus-2025-07-28", "Qwen: Qwen Plus 0728"),
            ("qwen/qwen-plus-2025-07-28:thinking", "Qwen: Qwen Plus 0728 (thinking)"),
            ("qwen/qwen-turbo", "Qwen: Qwen-Turbo"),
            ("qwen/qwen-vl-max", "Qwen: Qwen VL Max"),
            ("qwen/qwen-vl-plus", "Qwen: Qwen VL Plus"),
            ("qwen/qwen2.5-coder-7b-instruct", "Qwen2.5 Coder 7b Instruct"),
            ("qwen/qwen2.5-vl-32b-instruct", "Qwen: Qwen2.5 VL 32B Instruct"),
            ("qwen/qwen2.5-vl-72b-instruct", "Qwen: Qwen2.5 VL 72B Instruct"),
            ("qwen/qwen3-14b", "Qwen: Qwen3 14B"),
            ("qwen/qwen3-235b-a22b", "Qwen: Qwen3 235B A22B"),
            ("qwen/qwen3-235b-a22b-2507", "Qwen: Qwen3 235B A22B Instruct 2507"),
            ("qwen/qwen3-235b-a22b-thinking-2507", "Qwen: Qwen3 235B A22B Thinking 2507"),
            ("qwen/qwen3-30b-a3b", "Qwen: Qwen3 30B A3B (retires Jun 5)"),
            ("qwen/qwen3-30b-a3b-instruct-2507", "Qwen: Qwen3 30B A3B Instruct 2507"),
            ("qwen/qwen3-30b-a3b-thinking-2507", "Qwen: Qwen3 30B A3B Thinking 2507"),
            ("qwen/qwen3-32b", "Qwen3 32B"),
            ("qwen/qwen3-8b", "Qwen: Qwen3 8B"),
            ("qwen/qwen3-coder", "Qwen: Qwen3 Coder 480B A35B"),
            ("qwen/qwen3-coder-30b-a3b-instruct", "Qwen: Qwen3 Coder 30B A3B Instruct"),
            ("qwen/qwen3-coder-flash", "Qwen: Qwen3 Coder Flash"),
            ("qwen/qwen3-coder-next", "Qwen: Qwen3 Coder Next"),
            ("qwen/qwen3-coder-plus", "Qwen3-Coder-Plus"),
            ("qwen/qwen3-coder:exacto", "Qwen: Qwen3 Coder 480B A35B (exacto)"),
            ("qwen/qwen3-max", "Qwen3-Max-Thinking"),
            ("qwen/qwen3-max-thinking", "Qwen: Qwen3 Max Thinking"),
            ("qwen/qwen3-next-80b-a3b-instruct", "Qwen3-Next-80B-A3B-Instruct"),
            ("qwen/qwen3-next-80b-a3b-thinking", "Qwen3-Next-80B-A3B-Thinking"),
            ("qwen/qwen3-vl-235b-a22b-instruct", "Qwen: Qwen3 VL 235B A22B Instruct"),
            ("qwen/qwen3-vl-235b-a22b-thinking", "Qwen: Qwen3 VL 235B A22B Thinking"),
            ("qwen/qwen3-vl-30b-a3b-instruct", "Qwen: Qwen3 VL 30B A3B Instruct"),
            ("qwen/qwen3-vl-30b-a3b-thinking", "Qwen: Qwen3 VL 30B A3B Thinking"),
            ("qwen/qwen3-vl-32b-instruct", "Qwen: Qwen3 VL 32B Instruct"),
            ("qwen/qwen3-vl-8b-instruct", "Qwen: Qwen3 VL 8B Instruct"),
            ("qwen/qwen3-vl-8b-thinking", "Qwen: Qwen3 VL 8B Thinking"),
            ("qwen/qwen3.5-122b-a10b", "Qwen3.5 122B-A10B"),
            ("qwen/qwen3.5-27b", "Qwen: Qwen3.5-27B"),
            ("qwen/qwen3.5-35b-a3b", "Qwen: Qwen3.5-35B-A3B"),
            ("qwen/qwen3.5-397b-a17b", "Qwen3.5-397B-A17B"),
            ("qwen/qwen3.5-9b", "Qwen: Qwen3.5-9B"),
            ("qwen/qwen3.5-flash-02-23", "Qwen: Qwen3.5-Flash"),
            ("qwen/qwen3.5-plus-02-15", "Qwen: Qwen3.5 Plus 2026-02-15"),
            ("qwen/qwen3.5-plus-20260420", "Qwen: Qwen3.5 Plus 2026-04-20"),
            ("qwen/qwen3.6-27b", "Qwen: Qwen3.6 27B"),
            ("qwen/qwen3.6-35b-a3b", "Qwen: Qwen3.6 35B A3B"),
            ("qwen/qwen3.6-flash", "Qwen: Qwen3.6 Flash"),
            ("qwen/qwen3.6-max-preview", "Qwen: Qwen3.6 Max Preview"),
            ("qwen/qwen3.6-plus", "Qwen3.6-Plus"),
            ("qwen/qwen3.6-plus-preview:free", "Qwen: Qwen3.6 Plus Preview (free)"),
            ("qwen/qwen3.6-plus:free", "Qwen: Qwen3.6 Plus (free)"),
            ("qwen/qwen3.7-max", "Qwen: Qwen3.7 Max"),
            ("qwen/qwq-32b", "Qwen: QwQ 32B"),
            ("raifle/sorcererlm-8x22b", "SorcererLM 8x22B"),
            ("reka/reka-edge", "Reka Edge"),
            ("rekaai/reka-edge", "Reka Edge"),
            ("rekaai/reka-flash-3", "Reka Flash 3"),
            ("relace/relace-apply-3", "Relace: Relace Apply 3"),
            ("relace/relace-search", "Relace: Relace Search"),
            ("sao10k/l3-euryale-70b", "Sao10k: Llama 3 Euryale 70B v2.1 (retires Jun 5)"),
            ("sao10k/l3-lunaris-8b", "Sao10K: Llama 3 8B Lunaris"),
            ("sao10k/l3.1-70b-hanami-x1", "Sao10K: Llama 3.1 70B Hanami x1"),
            ("sao10k/l3.1-euryale-70b", "Sao10K: Llama 3.1 Euryale 70B v2.2"),
            ("sao10k/l3.3-euryale-70b", "Sao10K: Llama 3.3 Euryale 70B"),
            ("stealth/claude-opus-4.6", "Stealth: Claude Opus 4.6 (20% off)"),
            ("stealth/claude-opus-4.7", "Stealth: Claude Opus 4.7 (20% off)"),
            ("stealth/claude-opus-4.8", "Stealth: Claude Opus 4.8 (20% off)"),
            ("stealth/claude-sonnet-4.6", "Stealth: Claude Sonnet 4.6 (20% off)"),
            ("stealth/qwen3.6-plus", "Stealth: Qwen3.6 Plus (50% off)"),
            ("stepfun/step-3.5-flash", "Step 3.5 Flash"),
            ("stepfun/step-3.5-flash:free", "StepFun: Step 3.5 Flash (free)"),
            ("stepfun/step-3.7-flash", "StepFun: Step 3.7 Flash"),
            ("stepfun/step-3.7-flash:free", "StepFun: Step 3.7 Flash (free)"),
            ("switchpoint/router", "Switchpoint Router"),
            ("tencent/hunyuan-a13b-instruct", "Tencent: Hunyuan A13B Instruct"),
            ("tencent/hy3-preview", "Hy3 preview"),
            ("tencent/hy3-preview:free", "Tencent: Hy3 preview (free)"),
            ("thedrummer/cydonia-24b-v4.1", "TheDrummer: Cydonia 24B V4.1"),
            ("thedrummer/rocinante-12b", "TheDrummer: Rocinante 12B"),
            ("thedrummer/skyfall-36b-v2", "TheDrummer: Skyfall 36B V2"),
            ("thedrummer/unslopnemo-12b", "TheDrummer: UnslopNemo 12B"),
            ("tngtech/deepseek-r1t2-chimera", "TNG: DeepSeek R1T2 Chimera"),
            ("undi95/remm-slerp-l2-13b", "ReMM SLERP 13B"),
            ("upstage/solar-pro-3", "Upstage: Solar Pro 3"),
            ("writer/palmyra-x5", "Writer: Palmyra X5"),
            ("x-ai/grok-3", "xAI: Grok 3"),
            ("x-ai/grok-3-beta", "xAI: Grok 3 Beta"),
            ("x-ai/grok-3-mini", "xAI: Grok 3 Mini"),
            ("x-ai/grok-3-mini-beta", "xAI: Grok 3 Mini Beta"),
            ("x-ai/grok-4", "Grok 4"),
            ("x-ai/grok-4-fast", "Grok 4 Fast"),
            ("x-ai/grok-4.1-fast", "Grok 4.1 Fast"),
            ("x-ai/grok-4.20", "xAI: Grok 4.20"),
            ("x-ai/grok-4.20-beta", "xAI: Grok 4.20 Beta"),
            ("x-ai/grok-4.20-multi-agent", "xAI: Grok 4.20 Multi-Agent"),
            ("x-ai/grok-4.20-multi-agent-beta", "xAI: Grok 4.20 Multi-Agent Beta"),
            ("x-ai/grok-4.3", "xAI: Grok 4.3"),
            ("x-ai/grok-build-0.1", "xAI: Grok Build 0.1"),
            ("x-ai/grok-code-fast-1", "Grok Code Fast 1"),
            ("x-ai/grok-code-fast-1:optimized:free", "xAI: Grok Code Fast 1, retiring May 15 (free)"),
            ("xiaomi/mimo-v2-flash", "MiMo-V2-Flash"),
            ("xiaomi/mimo-v2-omni", "MiMo V2 Omni"),
            ("xiaomi/mimo-v2-omni:free", "Xiaomi: MiMo-V2-Omni (free)"),
            ("xiaomi/mimo-v2-pro", "MiMo V2 Pro"),
            ("xiaomi/mimo-v2-pro:free", "Xiaomi: MiMo-V2-Pro (free)"),
            ("xiaomi/mimo-v2.5", "MiMo-V2.5"),
            ("xiaomi/mimo-v2.5-pro", "MiMo-V2.5-Pro"),
            ("z-ai/glm-4-32b", "Z.ai: GLM 4 32B"),
            ("z-ai/glm-4.5", "GLM 4.5"),
            ("z-ai/glm-4.5-air", "GLM 4.5 Air"),
            ("z-ai/glm-4.5v", "Z.ai: GLM 4.5V"),
            ("z-ai/glm-4.6", "GLM 4.6"),
            ("z-ai/glm-4.6:exacto", "Z.ai: GLM 4.6 (exacto)"),
            ("z-ai/glm-4.6v", "GLM 4.6V"),
            ("z-ai/glm-4.7", "GLM 4.7"),
            ("z-ai/glm-4.7-flash", "Z.ai: GLM 4.7 Flash"),
            ("z-ai/glm-5", "GLM 5"),
            ("z-ai/glm-5-turbo", "GLM 5 Turbo"),
            ("z-ai/glm-5.1", "GLM-5.1"),
            ("z-ai/glm-5v-turbo", "GLM 5V Turbo"),
        ],
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
        "google-gemini-cli": [
            ("gemini-2.0-flash", "Gemini 2.0 Flash"),
            ("gemini-3-flash-preview", "Gemini 3 Flash Preview"),
            ("gemini-3-pro-preview", "Gemini 3 Pro Preview"),
            ("gemini-3.1-flash-preview", "Gemini 3.1 Flash Preview"),
            ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
            ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
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
        if not pid:
            return None

        # Check if provider actually has credentials
        auth = _load_auth()
        entry = auth.get(pid, {})
        has_creds = bool(
            entry.get("key") or entry.get("access") or entry.get("accessToken")
            or entry.get("refresh") or entry.get("refreshToken")
        )
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


def delete_custom_provider(provider_id: str) -> bool:
    """Delete a custom OpenAI-compatible provider from models.json and auth.json."""
    _ensure_agent_dir()
    models_path = os.path.join(AGENT_DIR, "models.json")

    # Remove from models.json
    if os.path.exists(models_path):
        try:
            with open(models_path) as f:
                data = json.load(f)
            if provider_id in data.get("providers", {}):
                del data["providers"][provider_id]
                with open(models_path, "w") as f:
                    json.dump(data, f, indent=2)
        except (json.JSONDecodeError, IOError):
            pass

    # Remove from auth.json
    auth = _load_auth()
    if provider_id in auth:
        del auth[provider_id]
        _save_auth(auth)

    # Clear active provider if it was the deleted one
    active = get_active_provider()
    if active and active["id"] == provider_id:
        settings_path = os.path.join(AGENT_DIR, "settings.json")
        try:
            with open(settings_path) as f:
                settings = json.load(f)
            settings.pop("defaultProvider", None)
            settings.pop("defaultModel", None)
            with open(settings_path, "w") as f:
                json.dump(settings, f, indent=2)
        except Exception:
            pass

    return True


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
            # Extract raw token from JSON wrapper if needed
            token = access
            try:
                wrapped = json.loads(access)
                token = wrapped.get("token", access)
            except (json.JSONDecodeError, AttributeError):
                pass
            return _post_json(
                "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
                {"metadata": {"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}},
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
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
    server.socket.setsockopt(__import__('socket').SOL_SOCKET, __import__('socket').SO_REUSEADDR, 1)
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

    # Save token first (so it's not lost if project discovery fails)
    auth = _load_auth()
    auth[provider_id] = {
        "type": "oauth",
        "access": access,
        "refresh": token_data.get("refresh_token", ""),
        "expires": int(time.time() * 1000) + int(token_data.get("expires_in", 3600)) * 1000,
        "tokenType": token_data.get("token_type", "Bearer"),
    }
    _save_auth(auth)

    # For Google providers, discover/create a Cloud Code Assist project
    if provider_id in ("google-antigravity", "google-gemini-cli"):
        try:
            project_id = _discover_cloudcode_project(access, provider_id)
            # Update with JSON-wrapped token including projectId
            auth = _load_auth()
            auth[provider_id] = {
                "type": "oauth",
                "access": json.dumps({"token": access, "projectId": project_id}),
                "refresh": token_data.get("refresh_token", ""),
                "expires": int(time.time() * 1000) + int(token_data.get("expires_in", 3600)) * 1000,
                "tokenType": token_data.get("token_type", "Bearer"),
                "projectId": project_id,
            }
            _save_auth(auth)
        except Exception as e:
            print(f"Warning: Project discovery failed ({e}). Token saved without projectId.")
    # Always set the newly authenticated provider as active
    models = _oauth_models(provider_id)
    set_active_provider(provider_id, models[0][0] if models else "")
    return True


def _discover_cloudcode_project(access_token: str, provider_id: str) -> str:
    """Discover or create a Cloud Code Assist project for Google providers."""
    is_antigravity = provider_id == "google-antigravity"

    # Unwrap JSON-wrapped token if needed
    token = access_token
    try:
        wrapped = json.loads(access_token)
        token = wrapped.get("token", access_token)
    except (json.JSONDecodeError, AttributeError):
        pass

    # Try endpoints in order: prod first, then sandbox
    endpoints = ["https://cloudcode-pa.googleapis.com", "https://daily-cloudcode-pa.sandbox.googleapis.com"]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "google-api-nodejs-client/9.15.1",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": json.dumps({
            "ideType": "IDE_UNSPECIFIED",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        }),
    }

    for endpoint in endpoints:
        try:
            body = json.dumps({
                "metadata": {
                    "ideType": "IDE_UNSPECIFIED",
                    "platform": "PLATFORM_UNSPECIFIED",
                    "pluginType": "GEMINI",
                },
            }).encode()

            req = urllib.request.Request(
                f"{endpoint}/v1internal:loadCodeAssist",
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())

            # Check if user already has a project (Aery uses cloudaicompanionProject)
            project = data.get("cloudaicompanionProject")
            if project:
                if isinstance(project, str) and project:
                    return project
                if isinstance(project, dict) and project.get("id"):
                    return project["id"]

            # Check currentTier as fallback
            if data.get("currentTier") and data["currentTier"].get("projectId"):
                return data["currentTier"]["projectId"]

            # Need to provision a project
            setup_body = json.dumps({
                "tierId": "free-tier",
                "metadata": {
                    "ideType": "IDE_UNSPECIFIED",
                    "platform": "PLATFORM_UNSPECIFIED",
                    "pluginType": "GEMINI",
                },
            }).encode()
            req = urllib.request.Request(
                f"{endpoint}/v1internal:onboardUser",
                data=setup_body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                setup_data = json.loads(resp.read().decode())

            # Poll for completion
            if setup_data.get("name"):
                project_id = _poll_project_setup(setup_data["name"], headers, endpoint)
                if project_id:
                    return project_id

        except Exception:
            continue

    raise RuntimeError("Failed to discover or create Cloud Code Assist project. Check your Google account permissions.")


def _poll_project_setup(operation_name: str, headers: dict, endpoint: str, max_wait: int = 60) -> str:
    """Poll for project setup completion."""
    import time as _time
    deadline = _time.time() + max_wait
    while _time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"{endpoint}/v1internal/{operation_name}",
                headers=headers,
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if data.get("done"):
                response = data.get("response", {})
                # Aery's response format: response.cloudaicompanionProject.id
                project = response.get("cloudaicompanionProject")
                if project:
                    if isinstance(project, str):
                        return project
                    if isinstance(project, dict) and project.get("id"):
                        return project["id"]
                # Fallback: currentTier
                if response.get("currentTier", {}).get("projectId"):
                    return response["currentTier"]["projectId"]
                break
        except Exception:
            pass
        _time.sleep(2)
    return ""


def refresh_google_token(provider_id: str) -> dict:
    """Refresh an expired Google OAuth token using the stored refresh token.

    Returns the updated auth entry, or raises RuntimeError on failure.
    """
    auth = _load_auth()
    entry = auth.get(provider_id)
    if not entry or entry.get("type") != "oauth":
        raise RuntimeError(f"No OAuth credentials for {provider_id}")

    refresh_token = entry.get("refresh", "")
    if not refresh_token:
        raise RuntimeError(f"No refresh token for {provider_id}. Re-authenticate via /login.")

    cfg = OAUTH_CONFIGS.get(provider_id, {})
    token_url = cfg.get("token_url", "https://oauth2.googleapis.com/token")
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

    # Preserve existing projectId if present
    project_id = entry.get("projectId", "")
    if project_id:
        access = json.dumps({"token": access, "projectId": project_id})

    auth[provider_id] = {
        "type": "oauth",
        "access": access,
        "refresh": token_data.get("refresh_token", refresh_token),
        "expires": int(time.time() * 1000) + int(token_data.get("expires_in", 3600)) * 1000,
        "tokenType": token_data.get("token_type", "Bearer"),
        "projectId": project_id,
    }
    _save_auth(auth)
    return auth[provider_id]


# ── Env-key auto-detection ─────────────────────────────────────────────────────
# Per-provider env-var names so the plugin can surface env credentials without
# requiring the user to open a terminal first.

ENV_KEY_MAP: dict[str, str] = {
    "anthropic":      "ANTHROPIC_API_KEY",
    "openai":         "OPENAI_API_KEY",
    "google":         "GEMINI_API_KEY",
    "google-vertex":  "GOOGLE_CLOUD_API_KEY",
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
    "cerebras":       "CEREBRAS_API_KEY",
    "cloudflare-workers-ai": "CLOUDFLARE_API_KEY",
    "cloudflare-ai-gateway": "CLOUDFLARE_API_KEY",
    "opencode":       "OPENCODE_API_KEY",
    "opencode-go":    "OPENCODE_API_KEY",
    "azure-openai-responses": "AZURE_OPENAI_API_KEY",
    "vercel-ai-gateway": "AI_GATEWAY_API_KEY",
    "together":       "TOGETHER_API_KEY",
    "moonshotai":     "MOONSHOT_API_KEY",
    "moonshotai-cn":  "MOONSHOT_API_KEY",
    "xiaomi":         "XIAOMI_API_KEY",
    "amazon-bedrock": "AWS_ACCESS_KEY_ID",
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
        verification_uri = data.get("verificationUrl", "https://api.kilo.ai/auth")
        
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
                        auth = _load_auth()
                        auth[provider_id] = {
                            "access": token_data["token"],
                            "expires_at": time.time() + 31536000
                        }
                        _save_auth(auth)
                        return True
            except urllib.error.HTTPError as e:
                if e.code == 202:
                    pass
                else:
                    print(f"Poll error: {e}")
            except Exception as e:
                pass
        return False
        
    # Standard flow for GitHub Copilot

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

    if code_callback:
        code_callback(user_code, verification_uri)
    else:
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
