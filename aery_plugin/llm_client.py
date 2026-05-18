"""Direct LLM API client for the Aery QGIS plugin.

Supports OpenAI-compatible, Anthropic, and Google Gemini APIs.
Uses the existing oauth_helper.py for credential resolution.
"""

import json
import os
import secrets
import time
import urllib.request
import urllib.error
from typing import Any, Optional, Iterator


class APIError(Exception):
    """Raised when an API call fails."""
    def __init__(self, message: str, status_code: int = 0, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _is_retryable(status_code: int) -> bool:
    """Return True if the HTTP status code indicates a retryable error."""
    return status_code in (429, 500, 502, 503, 504)


def _retry_with_backoff(fn, max_retries: int = 3, initial_delay: float = 1.0):
    """Retry a function with exponential backoff on retryable errors."""
    delay = initial_delay
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except APIError as e:
            last_exc = e
            if not e.retryable or attempt == max_retries:
                raise
            time.sleep(delay)
            delay *= 2
    raise last_exc


class OpenAIClient:
    """Client for OpenAI-compatible APIs (OpenAI, Groq, OpenRouter, etc.)."""

    def __init__(self, base_url: str, api_key: str, endpoint: str = "/chat/completions"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.endpoint = endpoint

    def _build_payload(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        return {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            **kwargs,
        }

    def _do_request(self, url: str, payload: dict) -> dict:
        """Make a single HTTP POST request. Raises APIError on failure."""
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
            raise APIError(f"HTTP {e.code}: {body}", e.code, retryable=_is_retryable(e.code))

    def _do_stream_request(self, url: str, payload: dict) -> Iterator[dict]:
        """Make a streaming HTTP POST request. Yields parsed JSON chunks."""
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
            raise APIError(f"HTTP {e.code}: {body}", e.code, retryable=_is_retryable(e.code))

    def chat(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        """Send a chat completion request with retry. Returns the parsed JSON response."""
        payload = self._build_payload(messages, model, max_tokens, **kwargs)
        url = f"{self.base_url}{self.endpoint}"
        return _retry_with_backoff(lambda: self._do_request(url, payload))

    def chat_stream(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> Iterator[dict]:
        """Yield streaming chunks from the API with retry."""
        payload = self._build_payload(messages, model, max_tokens, stream=True, **kwargs)
        url = f"{self.base_url}{self.endpoint}"
        return self._do_stream_request(url, payload)


class AnthropicClient:
    """Client for Anthropic's Messages API."""

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _build_payload(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
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

    def _do_request(self, url: str, payload: dict) -> dict:
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
            raise APIError(f"HTTP {e.code}: {body}", e.code, retryable=_is_retryable(e.code))

    def _do_stream_request(self, url: str, payload: dict) -> Iterator[dict]:
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
            raise APIError(f"HTTP {e.code}: {body}", e.code, retryable=_is_retryable(e.code))

    def chat(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        payload = self._build_payload(messages, model, max_tokens, **kwargs)
        url = f"{self.base_url}/v1/messages"
        return _retry_with_backoff(lambda: self._do_request(url, payload))

    def chat_stream(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> Iterator[dict]:
        payload = self._build_payload(messages, model, max_tokens, stream=True, **kwargs)
        url = f"{self.base_url}/v1/messages"
        return self._do_stream_request(url, payload)


class GeminiClient:
    """Client for Google Gemini API."""

    def __init__(self, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta", project_id: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.project_id = project_id

    def _is_api_key(self) -> bool:
        """Detect if the stored credential is a Gemini API key (AIza...) vs an OAuth access token."""
        return self.api_key.startswith("AIza")

    def _build_payload(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
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

    def _get_cloudcode_headers(self, is_antigravity: bool = True) -> dict:
        """Get required headers for Cloud Code Assist API.
        
        Matches Aery main exactly:
        - Antigravity: User-Agent: antigravity/1.107.0 darwin/arm64
        - Gemini CLI: User-Agent: google-cloud-sdk vscode_cloudshelleditor/0.1
        """
        if is_antigravity:
            user_agent = "antigravity/1.107.0 darwin/arm64"
        else:
            user_agent = "google-cloud-sdk vscode_cloudshelleditor/0.1"
        
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
            "User-Agent": user_agent,
        }

    def _build_cloudcode_payload(self, messages: list[dict], model: str, max_tokens: int = 8192, is_antigravity: bool = True, **kwargs) -> dict:
        """Build payload for Cloud Code Assist API (OAuth tokens).
        
        Matches Aery main buildRequest() exactly.
        """
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

        request_body = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system_instruction:
            request_body["systemInstruction"] = system_instruction

        # Project ID - use as-is (Aery main sends projectId without "projects/" prefix)
        project = self.project_id

        # Build request matching Aery main structure
        prefix = "agent" if is_antigravity else "aery"
        payload = {
            "project": project,
            "model": model,
            "request": request_body,
            "userAgent": "antigravity" if is_antigravity else "aery-coding-agent",
            "requestId": f"{prefix}-{int(time.time() * 1000)}-{secrets.token_hex(5)}",
        }
        
        # Only antigravity gets requestType
        if is_antigravity:
            payload["requestType"] = "agent"
        
        return payload

    def _append_api_key(self, url: str) -> str:
        """Append API key to URL, using & if query params already exist."""
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}key={self.api_key}"

    def _do_request(self, url: str, payload: dict, headers: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = json.loads(resp.read().decode())
                # Transform Gemini/Cloud Code Assist response to OpenAI-compatible format
                if "candidates" in raw:
                    raw = self._transform_cloudcode_response(raw)
                return raw
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise APIError(f"HTTP {e.code}: {body}", e.code, retryable=_is_retryable(e.code))

    def _transform_cloudcode_response(self, response: dict) -> dict:
        """Transform Cloud Code Assist response into OpenAI-compatible format."""
        # Cloud Code Assist returns: {response: {candidates: [{content: {parts: [{text: ...}]}}]}}
        # OpenAI format: {choices: [{message: {content: ..., role: "assistant"}}]}
        
        # Check if it's already in OpenAI format
        if "choices" in response:
            return response
        
        # Unwrap Cloud Code Assist format
        inner = response.get("response", response)
        candidates = inner.get("candidates", [])
        
        if not candidates:
            return {"choices": [{"message": {"role": "assistant", "content": ""}}]}
        
        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        
        # Extract text from parts
        text = ""
        for part in parts:
            if "text" in part:
                text += part["text"]
        
        # Build OpenAI-compatible response
        openai_response = {
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                },
                "finish_reason": "stop",
            }]
        }
        
        # Extract usage metadata if available
        usage = inner.get("usageMetadata")
        if usage:
            openai_response["usage"] = {
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
                "total_tokens": usage.get("totalTokenCount", 0),
            }
        
        return openai_response

    def _do_stream_request(self, url: str, payload: dict, headers: dict) -> Iterator[dict]:
        """Make a streaming HTTP POST request. Yields parsed JSON chunks.
        
        For Cloud Code Assist API, transforms the response into OpenAI-compatible format.
        Handles both SSE (data: prefix) and raw newline-delimited JSON.
        """
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                buffer = ""
                for chunk in resp:
                    buffer += chunk.decode()
                    lines = buffer.split("\n")
                    buffer = lines.pop() or ""
                    
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        
                        # Strip SSE data: prefix if present
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if data == "[DONE]":
                                return
                            if not data:
                                continue
                            line = data
                        
                        try:
                            raw = json.loads(line)
                            # Transform Gemini/Cloud Code Assist response to OpenAI-compatible format
                            if "candidates" in raw or "response" in raw:
                                raw = self._transform_cloudcode_chunk(raw)
                            if raw:
                                yield raw
                        except json.JSONDecodeError:
                            pass
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise APIError(f"HTTP {e.code}: {body}", e.code, retryable=_is_retryable(e.code))

    def _transform_cloudcode_chunk(self, chunk: dict) -> dict:
        """Transform Cloud Code Assist SSE chunk into OpenAI-compatible format."""
        # Cloud Code Assist returns: {"response": {"candidates": [...]}}
        # or sometimes just: {"candidates": [...]}
        
        # Unwrap response if present
        response = chunk.get("response", chunk)
        
        candidates = response.get("candidates", [])
        if not candidates:
            # Check if this is a usage-only chunk or empty
            if response.get("usageMetadata"):
                return {"choices": [{"delta": {}, "finish_reason": None}]}
            return None
        
        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        
        # Extract text from parts
        text = ""
        for part in parts:
            if "text" in part:
                text += part["text"]
        
        # Check finish reason
        finish_reason = candidate.get("finishReason")
        if finish_reason:
            finish_reason = "stop" if finish_reason == "STOP" else finish_reason.lower()
        
        # Build OpenAI-compatible chunk
        openai_chunk = {
            "choices": [{
                "index": 0,
                "delta": {"content": text} if text else {},
                "finish_reason": finish_reason,
            }]
        }
        
        # Extract usage metadata if available
        usage = response.get("usageMetadata")
        if usage:
            openai_chunk["usage"] = {
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
                "total_tokens": usage.get("totalTokenCount", 0),
            }
        
        return openai_chunk

    def chat(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        if self._is_api_key():
            payload = self._build_payload(messages, model, max_tokens, **kwargs)
            url = f"{self.base_url}/models/{model}:generateContent"
            url = self._append_api_key(url)
            headers = {"Content-Type": "application/json"}
        else:
            # OAuth tokens use Cloud Code Assist API
            # Determine if this is antigravity or gemini-cli based on model
            is_antigravity = model.startswith("claude-") or model.startswith("gpt-")
            payload = self._build_cloudcode_payload(messages, model, max_tokens, is_antigravity=is_antigravity, **kwargs)
            url = "https://cloudcode-pa.googleapis.com/v1internal:generateContent"
            headers = self._get_cloudcode_headers(is_antigravity=is_antigravity)
        return _retry_with_backoff(lambda: self._do_request(url, payload, headers))

    def chat_stream(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> Iterator[dict]:
        if self._is_api_key():
            payload = self._build_payload(messages, model, max_tokens, **kwargs)
            url = f"{self.base_url}/models/{model}:streamGenerateContent?alt=sse"
            url = self._append_api_key(url)
            headers = {"Content-Type": "application/json"}
        else:
            # OAuth tokens use Cloud Code Assist API
            # Determine if this is antigravity or gemini-cli based on model
            is_antigravity = model.startswith("claude-") or model.startswith("gpt-")
            payload = self._build_cloudcode_payload(messages, model, max_tokens, is_antigravity=is_antigravity, **kwargs)
            url = "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
            headers = self._get_cloudcode_headers(is_antigravity=is_antigravity)
        return self._do_stream_request(url, payload, headers)


def _resolve_google_credentials(auth_entry: dict) -> tuple[str, str]:
    """Resolve Google OAuth credentials. Returns (access_token, project_id)."""
    access = auth_entry.get("access", "")
    if not access:
        return "", ""
    
    # Check if access is JSON with token and projectId
    try:
        wrapped = json.loads(access)
        token = wrapped.get("token", access)
        project_id = wrapped.get("projectId", "")
        return token, project_id
    except (json.JSONDecodeError, AttributeError):
        return access, ""


def _resolve_api_key(provider_id: str, auth_entry: dict) -> str:
    """Resolve the API key from an auth entry, handling both API key and OAuth types."""
    from aery_plugin import oauth_helper

    # Direct API key
    key = auth_entry.get("key", "")
    if key:
        return key

    # If entry type is api_key but key is empty, raise error
    if auth_entry.get("type") == "api_key":
        raise APIError(
            f"API key for {provider_id} is empty. Please configure it in Settings.",
            status_code=401,
            retryable=False,
        )

    # Google OAuth: access token may be wrapped in JSON with projectId
    if provider_id in ("google-antigravity", "google-gemini-cli"):
        # Check expiry
        expires = auth_entry.get("expires", 0)
        if expires and int(time.time() * 1000) >= expires:
            # Token expired, try to refresh
            try:
                auth_entry = oauth_helper.refresh_google_token(provider_id)
            except Exception:
                pass  # Will fail with invalid token if refresh fails
        token, _ = _resolve_google_credentials(auth_entry)
        return token

    # OAuth access token
    access = auth_entry.get("access") or auth_entry.get("accessToken", "")
    if access:
        # Check expiry (stored as ms timestamp)
        expires = auth_entry.get("expires", 0)
        if expires:
            now_ms = int(time.time() * 1000)
            if now_ms < expires:
                return access
            # Token expired — try refresh for Google providers
            if provider_id in ("google-antigravity", "google-gemini-cli"):
                try:
                    refreshed = oauth_helper.refresh_google_token(provider_id)
                    new_token, _ = _resolve_google_credentials(refreshed)
                    if new_token:
                        return new_token
                except Exception:
                    pass
            # Expired token — raise error so user can re-authenticate
            raise APIError(
                f"OAuth token for {provider_id} has expired. Please re-authenticate via Settings.",
                status_code=401,
                retryable=False,
            )
        # No expiry field — use access token as-is (some providers don't track expiry)
        return access

    return ""


_OAUTH_API_CONFIGS: dict[str, dict] = {
    "openai-codex": {
        "base_url": "https://chatgpt.com/backend-api",
        "api_type": "openai-responses",
        "endpoint": "/codex/responses",
    },
    "github-copilot": {
        "base_url": "https://api.individual.githubcopilot.com",
        "api_type": "openai-compatible",
        "endpoint": "/chat/completions",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "api_type": "anthropic",
        "endpoint": "/v1/messages",
    },
    "google-antigravity": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_type": "google",
        "endpoint": "/models/{model}:generateContent",
    },
    "google-gemini-cli": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_type": "google",
        "endpoint": "/models/{model}:generateContent",
    },
}


def create_client(provider_id: str, auth_entry: dict, model: str) -> tuple[Any, str]:
    """Create the appropriate API client for a provider.

    Returns (client, model_name) tuple.
    """
    from aery_plugin import oauth_helper

    key = _resolve_api_key(provider_id, auth_entry)

    # Aery Gateway
    if provider_id == "aery-gateway":
        return OpenAIClient(base_url=oauth_helper.AERY_GATEWAY_URL, api_key=key), model

    # Custom providers from models.json
    models_data = oauth_helper._load_models()
    custom_providers = models_data.get("providers", {})
    if provider_id in custom_providers:
        cfg = custom_providers[provider_id]
        base_url = cfg.get("baseUrl", "").rstrip("/")
        api_type = cfg.get("api", "openai-completions")
        if api_type in ("anthropic-messages",):
            return AnthropicClient(api_key=key, base_url=base_url), model
        # Default: OpenAI-compatible
        return OpenAIClient(base_url=base_url, api_key=key), model

    # OAuth provider configs (not in API_PROVIDERS)
    oauth_cfg = _OAUTH_API_CONFIGS.get(provider_id)
    if oauth_cfg:
        if oauth_cfg["api_type"] == "anthropic":
            return AnthropicClient(api_key=key), model
        if oauth_cfg["api_type"] == "google":
            _, project_id = _resolve_google_credentials(auth_entry)
            return GeminiClient(api_key=key, project_id=project_id), model
        # OpenAI-compatible or OpenAI Responses
        base_url = oauth_cfg["base_url"]
        endpoint = oauth_cfg.get("endpoint", "/chat/completions")
        return OpenAIClient(base_url=base_url, api_key=key, endpoint=endpoint), model

    # API key providers
    cfg = oauth_helper.API_PROVIDERS.get(provider_id, {})
    base_url = cfg.get("base_url", "https://api.openai.com/v1")

    # Resolve account ID placeholders in base URL (Cloudflare, etc.)
    if "{CLOUDFLARE_ACCOUNT_ID}" in base_url:
        account_id = auth_entry.get("accountId", "") or auth_entry.get("metadata", {}).get("CLOUDFLARE_ACCOUNT_ID", "")
        if not account_id:
            # Try environment variable fallback
            account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        base_url = base_url.replace("{CLOUDFLARE_ACCOUNT_ID}", account_id)
    if "{CLOUDFLARE_GATEWAY_ID}" in base_url:
        gateway_id = auth_entry.get("gatewayId", "") or auth_entry.get("metadata", {}).get("CLOUDFLARE_GATEWAY_ID", "")
        base_url = base_url.replace("{CLOUDFLARE_GATEWAY_ID}", gateway_id)

    # Custom base URL from auth entry overrides provider default
    if auth_entry.get("baseUrl"):
        base_url = auth_entry["baseUrl"]

    # Anthropic Messages API providers
    anthropic_providers = {"anthropic", "minimax", "minimax-cn", "kimi-coding",
                           "xiaomi", "xiaomi-token-plan-cn", "xiaomi-token-plan-ams",
                           "xiaomi-token-plan-sgp", "claude-local"}
    is_anthropic = provider_id in anthropic_providers or base_url.endswith("/anthropic")

    # Google Gemini API
    is_google = provider_id == "google" or base_url.startswith("https://generativelanguage")

    # Google Vertex AI
    is_vertex = provider_id == "google-vertex"

    if is_anthropic:
        # Use provider's base_url for Anthropic-style providers (MiniMax, Kimi, etc.)
        # Strip trailing /v1 since AnthropicClient adds /v1/messages
        anthropic_base = base_url.rstrip("/").removesuffix("/v1") if provider_id != "anthropic" else "https://api.anthropic.com"
        return AnthropicClient(api_key=key, base_url=anthropic_base), model

    if is_google:
        return GeminiClient(api_key=key), model

    if is_vertex:
        # Vertex AI uses a different URL pattern but same Gemini API format
        return GeminiClient(api_key=key, base_url=base_url), model

    # Default: OpenAI-compatible
    return OpenAIClient(base_url=base_url, api_key=key), model
