"""Direct LLM API client for the Aery QGIS plugin.

Supports OpenAI-compatible, Anthropic, and Google Gemini APIs.
Uses the existing oauth_helper.py for credential resolution.
"""

import json
import os
import urllib.request
import urllib.error
from typing import Any, Optional


class APIError(Exception):
    """Raised when an API call fails."""
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class OpenAIClient:
    """Client for OpenAI-compatible APIs (OpenAI, Groq, OpenRouter, etc.)."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _build_payload(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        return {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            **kwargs,
        }

    def chat(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        """Send a chat completion request. Returns the parsed JSON response."""
        payload = self._build_payload(messages, model, max_tokens, **kwargs)
        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise APIError(f"HTTP {e.code}: {body}", e.code)

    def chat_stream(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs):
        """Yield streaming chunks from the API."""
        payload = self._build_payload(messages, model, max_tokens, stream=True, **kwargs)
        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for line in resp:
                    line = line.decode().strip()
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            yield json.loads(data)
                        except json.JSONDecodeError:
                            pass
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise APIError(f"HTTP {e.code}: {body}", e.code)


class AnthropicClient:
    """Client for Anthropic's API."""

    def __init__(self, api_key: str):
        self.base_url = "https://api.anthropic.com"
        self.api_key = api_key

    def _build_payload(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        # Convert OpenAI-format messages to Anthropic format
        system_msg = ""
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                anthropic_messages.append(msg)

        payload = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            **kwargs,
        }
        if system_msg:
            payload["system"] = system_msg
        return payload

    def chat(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        payload = self._build_payload(messages, model, max_tokens, **kwargs)
        url = f"{self.base_url}/v1/messages"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise APIError(f"HTTP {e.code}: {body}", e.code)

    def chat_stream(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs):
        payload = self._build_payload(messages, model, max_tokens, stream=True, **kwargs)
        url = f"{self.base_url}/v1/messages"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for line in resp:
                    line = line.decode().strip()
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            yield json.loads(data)
                        except json.JSONDecodeError:
                            pass
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise APIError(f"HTTP {e.code}: {body}", e.code)


class GeminiClient:
    """Client for Google Gemini API."""

    def __init__(self, api_key: str):
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self.api_key = api_key

    def _build_payload(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        # Convert messages to Gemini format
        contents = []
        system_instruction = None
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = {"parts": [{"text": msg["content"]}]}
            else:
                role = "model" if msg["role"] == "assistant" else "user"
                contents.append({
                    "role": role,
                    "parts": [{"text": msg["content"]}],
                })

        payload = {"contents": contents}
        if system_instruction:
            payload["system_instruction"] = system_instruction
        payload["generationConfig"] = {
            "maxOutputTokens": max_tokens,
            **kwargs.get("generationConfig", {}),
        }
        return payload

    def chat(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        payload = self._build_payload(messages, model, max_tokens, **kwargs)
        url = f"{self.base_url}/models/{model}:generateContent?key={self.api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise APIError(f"HTTP {e.code}: {body}", e.code)


def create_client(provider_id: str, auth_entry: dict, model: str) -> tuple[Any, str]:
    """Create the appropriate API client for a provider.

    Returns (client, model_name) tuple.
    """
    from aery_plugin import oauth_helper

    if provider_id == "aery-gateway":
        # Gateway is OpenAI-compatible
        key = auth_entry.get("key", "")
        return OpenAIClient(base_url=oauth_helper.AERY_GATEWAY_URL, api_key=key), model

    cfg = oauth_helper.API_PROVIDERS.get(provider_id, {})
    is_anthropic = provider_id == "anthropic" or cfg.get("base_url", "").startswith("https://api.anthropic.com")
    is_google = provider_id == "google" or cfg.get("base_url", "").startswith("https://generativelanguage")

    if is_anthropic:
        key = auth_entry.get("key", "")
        return AnthropicClient(api_key=key), model

    if is_google:
        key = auth_entry.get("key", "")
        return GeminiClient(api_key=key), model

    # Default: OpenAI-compatible
    key = auth_entry.get("key", "")
    base_url = cfg.get("base_url", "https://api.openai.com/v1")
    if cfg.get("needs_account_id"):
        account_id = auth_entry.get("accountId", "")
        base_url = base_url.replace("{CLOUDFLARE_ACCOUNT_ID}", account_id)
    if auth_entry.get("baseUrl"):
        base_url = auth_entry["baseUrl"]
    return OpenAIClient(base_url=base_url, api_key=key), model
