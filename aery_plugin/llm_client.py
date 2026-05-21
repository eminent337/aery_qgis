"""Direct LLM API client for the Aery QGIS plugin.

Supports OpenAI-compatible, Anthropic, and Google Gemini APIs.
Uses the existing oauth_helper.py for credential resolution.
"""

import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import httpx
import asyncio
from typing import Any, Optional


class APIError(Exception):
    """Raised when an API call fails."""
    def __init__(self, message: str, status_code: int = 0, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _extract_rate_limit_info(headers: dict) -> Optional[dict]:
    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    reset = headers.get("x-ratelimit-reset") or headers.get("x-ratelimit-reset-after")
    remaining = headers.get("x-ratelimit-remaining") or headers.get("x-ratelimit-remaining-requests")
    limit = headers.get("x-ratelimit-limit") or headers.get("x-ratelimit-limit-requests")

    if not any([retry_after, reset, remaining]):
        return None

    resets_at = None
    if retry_after:
        try:
            import time as _time
            resets_at = int((_time.time() + float(retry_after)) * 1000)
        except (ValueError, TypeError):
            pass
    if reset and not resets_at:
        try:
            import time as _time
            resets_at = int((_time.time() + float(reset)) * 1000)
        except (ValueError, TypeError):
            pass

    utilization = None
    if remaining and limit:
        try:
            remaining = int(remaining)
            limit = int(limit)
            if limit > 0:
                utilization = round(1.0 - (remaining / limit), 2)
        except (ValueError, TypeError):
            pass

    rate_type = "five_hour" if "five" in str(headers).lower() else "seven_day"

    status = "allowed"
    if utilization is not None and utilization > 0.8:
        status = "allowed_warning"

    return {
        "status": status,
        "resetsAt": resets_at,
        "rateLimitType": rate_type,
        "utilization": utilization if utilization is not None else 0.0,
    }


def _is_retryable(status_code: int) -> bool:
    """Return True if the HTTP status code indicates a retryable error."""
    return status_code in (429, 500, 502, 503, 504)


def _is_retryable_text(error_text: str) -> bool:
    """Check error text for retryable patterns (rate limit, overloaded, etc.)."""
    import re
    return bool(re.search(r'resource.{0,20}exhausted|rate.{0,10}limit|overloaded|service.{0,20}unavailable|quota.{0,20}exceeded', error_text, re.IGNORECASE))


def _extract_retry_delay(error_text: str, headers: dict = None) -> float:
    """Extract retry delay from error response headers or body. Returns seconds."""
    # Check headers first
    if headers:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after:
            try:
                return max(float(retry_after) + 1, 2)
            except ValueError:
                pass
        reset = headers.get("x-ratelimit-reset") or headers.get("x-ratelimit-reset-after")
        if reset:
            try:
                delay = float(reset)
                if delay > 0:
                    return delay + 1
            except ValueError:
                pass

    # Parse body patterns: "Your quota will reset after 39s", "retry in 5s"
    import re
    patterns = [
        r'reset\s+after\s+(\d+\.?\d*)\s*s',
        r'retry\s+(?:in|after)\s+(\d+\.?\d*)\s*s',
        r'Please retry in (\d+\.?\d*)s',
        r'"retryDelay"\s*:\s*"(\d+\.?\d*)s"',
    ]
    for pattern in patterns:
        m = re.search(pattern, error_text, re.IGNORECASE)
        if m:
            delay = float(m.group(1))
            if delay > 0:
                return delay + 1

    # Check for hours+minutes format: "18h31m10s"
    m = re.search(r'(\d+)h(\d+)m(\d+)s', error_text)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + 1

    return 0


def _extract_error_message(error_text: str) -> str:
    """Extract a clean error message from API error response."""
    try:
        parsed = json.loads(error_text)
        if isinstance(parsed, dict):
            if parsed.get("error", {}).get("message"):
                return parsed["error"]["message"]
            if parsed.get("message"):
                return parsed["message"]
    except (json.JSONDecodeError, AttributeError):
        pass
    return error_text[:200]


def _retry_with_backoff(fn, max_retries: int = 3, initial_delay: float = 2.0):
    """Retry a function with exponential backoff on retryable errors."""
    delay = initial_delay
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except APIError as e:
            last_exc = e
            if not e.retryable and not _is_retryable_text(str(e)):
                raise
            if attempt == max_retries:
                raise
            # Try to extract server-specified delay
            server_delay = _extract_retry_delay(str(e))
            actual_delay = max(server_delay, delay)
            time.sleep(actual_delay)
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

    async def _do_request(self, url: str, payload: dict) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=headers, timeout=120)
                resp.raise_for_status()
                result = resp.json()
                rate_info = _extract_rate_limit_info(dict(resp.headers))
                if rate_info:
                    result["_rate_limit"] = rate_info
                return result
        except httpx.HTTPStatusError as e:
            error_msg = _extract_error_message(e.response.text or "")
            raise APIError(f"HTTP {e.response.status_code}: {error_msg}", e.response.status_code, retryable=_is_retryable(e.response.status_code))
        except httpx.HTTPError as e:
            raise APIError(str(e), retryable=False)

    async def _do_stream_request(self, url: str, payload: dict) -> Any:
        max_retries = 3
        delay = 2.0

        for attempt in range(max_retries + 1):
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            try:
                async with httpx.AsyncClient() as client:
                    async with client.post(url, json=payload, headers=headers, timeout=120, stream=True) as resp:
                        resp.raise_for_status()
                        rate_info = _extract_rate_limit_info(dict(resp.headers))
                        received_any = False
                        async for line in resp.aiter_lines():
                            line = line.strip()
                            if not line:
                                continue
                            if line.startswith("data: "):
                                data = line[6:]
                                if data == "[DONE]":
                                    return
                                try:
                                    chunk = json.loads(data)
                                    if rate_info:
                                        chunk["_rate_limit"] = rate_info
                                        rate_info = None
                                    yield chunk
                                    received_any = True
                                except json.JSONDecodeError:
                                    pass
                    if not received_any and attempt < max_retries:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    return
            except httpx.HTTPStatusError as e:
                body = e.response.text or ""
                error_msg = _extract_error_message(body)
                retryable = _is_retryable(e.response.status_code) or _is_retryable_text(body)
                if retryable and attempt < max_retries:
                    server_delay = _extract_retry_delay(body)
                    actual_delay = max(server_delay, delay)
                    await asyncio.sleep(actual_delay)
                    delay *= 2
                    continue
                raise APIError(f"HTTP {e.response.status_code}: {error_msg}", e.response.status_code, retryable=retryable)

    async def chat(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        """Send a chat completion request with retry. Returns the parsed JSON response."""
        payload = self._build_payload(messages, model, max_tokens, **kwargs)
        url = f"{self.base_url}{self.endpoint}"
        max_retries = 3
        delay = 2.0
        for attempt in range(max_retries + 1):
            try:
                return await self._do_request(url, payload)
            except APIError as e:
                if e.retryable and attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise

    async def chat_stream(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> Any:
        """Yield streaming chunks asynchronously with retry."""
        payload = self._build_payload(messages, model, max_tokens, stream=True, **kwargs)
        url = f"{self.base_url}{self.endpoint}"
        max_retries = 3
        delay = 2.0
        for attempt in range(max_retries + 1):
            try:
                async for chunk in self._do_stream_request(url, payload):
                    yield chunk
                return
            except APIError as e:
                if e.retryable and attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise



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

    async def _do_request(self, url: str, payload: dict) -> dict:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=headers, timeout=120)
                resp.raise_for_status()
                result = resp.json()
                rate_info = _extract_rate_limit_info(dict(resp.headers))
                if rate_info:
                    result["_rate_limit"] = rate_info
                return result
        except httpx.HTTPStatusError as e:
            body = e.response.text or ""
            error_msg = _extract_error_message(body)
            raise APIError(f"HTTP {e.response.status_code}: {error_msg}", e.response.status_code, retryable=_is_retryable(e.response.status_code))
        except httpx.HTTPError as e:
            raise APIError(str(e), retryable=False)

    async def _do_stream_request(self, url: str, payload: dict) -> Any:
        max_retries = 3
        delay = 2.0
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    async with client.post(url, json=payload, headers=headers, timeout=120, stream=True) as resp:
                        resp.raise_for_status()
                        rate_info = _extract_rate_limit_info(dict(resp.headers))
                        received_any = False
                        current_text = ""
                        current_tool_calls = []
                        async for line in resp.aiter_lines():
                            line = line.strip()
                            if not line:
                                continue
                            if line.startswith("event: "):
                                event_type = line[7:].strip()
                                if event_type == "message_stop":
                                    return
                                continue
                            if line.startswith("data: "):
                                data = line[6:]
                                try:
                                    chunk = json.loads(data)
                                    # Transform Anthropic SSE to OpenAI format
                                    transformed = self._transform_anthropic_chunk(chunk, current_text, current_tool_calls)
                                    if transformed:
                                        if rate_info:
                                            transformed["_rate_limit"] = rate_info
                                            rate_info = None
                                        yield transformed
                                        received_any = True
                                except json.JSONDecodeError:
                                    pass
                    if not received_any and attempt < max_retries:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    return
            except httpx.HTTPStatusError as e:
                body = e.response.text or ""
                error_msg = _extract_error_message(body)
                retryable = _is_retryable(e.response.status_code) or _is_retryable_text(body)
                if retryable and attempt < max_retries:
                    server_delay = _extract_retry_delay(body)
                    actual_delay = max(server_delay, delay)
                    await asyncio.sleep(actual_delay)
                    delay *= 2
                    continue
                raise APIError(f"HTTP {e.response.status_code}: {error_msg}", e.response.status_code, retryable=retryable)

    def _transform_anthropic_chunk(self, chunk: dict, current_text: str, current_tool_calls: list) -> dict:
        """Transform Anthropic SSE chunk to OpenAI-compatible format.

        Anthropic sends:
          - content_block_start: {type, index, content_block}
          - content_block_delta: {type, index, delta: {type, text}}
          - message_delta: {type, delta: {stop_reason}}

        We transform to OpenAI format:
          - choices[0].delta.content (for text)
          - choices[0].delta.tool_calls (for tool use)
        """
        chunk_type = chunk.get("type", "")

        if chunk_type == "content_block_start":
            block = chunk.get("content_block", {})
            block_type = block.get("type", "")
            if block_type == "tool_use":
                # Initialize tool call
                tool_call = {
                    "id": block.get("id", ""),
                    "function": {"name": block.get("name", ""), "arguments": ""},
                }
                return {"choices": [{"delta": {"tool_calls": [tool_call]}}]}
            return None

        elif chunk_type == "content_block_delta":
            delta = chunk.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                text = delta.get("text", "")
                if text:
                    return {"choices": [{"delta": {"content": text}}]}
            elif delta_type == "input_json_delta":
                json_str = delta.get("partial_json", "")
                if json_str:
                    return {"choices": [{"delta": {"tool_calls": [{"function": {"arguments": json_str}}]}}]}
            return None

        elif chunk_type == "message_delta":
            delta = chunk.get("delta", {})
            stop_reason = delta.get("stop_reason", "")
            if stop_reason:
                return {"choices": [{"delta": {}, "finish_reason": stop_reason}]}
            return None

        elif chunk_type == "message_start":
            return None

        elif chunk_type == "ping":
            return None

        return None

    async def chat(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        """Send a chat completion request with retry. Returns the parsed JSON response."""
        payload = self._build_payload(messages, model, max_tokens, **kwargs)
        url = f"{self.base_url}/v1/messages"
        max_retries = 3
        delay = 2.0
        for attempt in range(max_retries + 1):
            try:
                return await self._do_request(url, payload)
            except APIError as e:
                if e.retryable and attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise

    async def chat_stream(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> Any:
        """Yield streaming chunks asynchronously with retry."""
        payload = self._build_payload(messages, model, max_tokens, **kwargs)
        url = f"{self.base_url}/v1/messages"
        max_retries = 3
        delay = 2.0
        for attempt in range(max_retries + 1):
            try:
                async for chunk in self._do_stream_request(url, payload):
                    yield chunk
                return
            except APIError as e:
                if e.retryable and attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise


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

    def _get_cloudcode_headers(self, is_antigravity: bool = True, model: str = "") -> dict:
        """Get required headers for Cloud Code Assist API.

        Matches Aery main exactly:
        - Antigravity: User-Agent: antigravity/1.107.0 darwin/arm64
        - Gemini CLI: User-Agent: google-cloud-sdk vscode_cloudshelleditor/0.1
        - Claude models: thinking beta header
        """
        if is_antigravity:
            user_agent = "antigravity/1.107.0 darwin/arm64"
        else:
            user_agent = "google-cloud-sdk vscode_cloudshelleditor/0.1"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
            "User-Agent": user_agent,
            "X-Goog-Api-Client": "gl-node/22.17.0",
            "Client-Metadata": json.dumps({
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }),
        }

        # Claude models via Cloud Code Assist need thinking beta header
        if model.startswith("claude-") and model.endswith("-thinking"):
            headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"

        return headers

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
                parts = []
                # Handle thinking content from assistant messages
                if msg["role"] == "assistant" and isinstance(msg.get("content"), list):
                    for block in msg["content"]:
                        if isinstance(block, dict):
                            if block.get("type") == "thinking":
                                parts.append({"text": block.get("thinking", ""), "thought": True})
                            elif block.get("type") == "text":
                                parts.append({"text": block.get("text", "")})
                    if parts:
                        contents.append({"role": role, "parts": parts})
                        continue
                contents.append({
                    "role": role,
                    "parts": [{"text": msg.get("content", "")}],
                })

        request_body = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system_instruction:
            request_body["systemInstruction"] = system_instruction

        # Gemini 3.1 Pro models: enable thinking with budget
        if "gemini-3" in model and "pro" in model:
            request_body["generationConfig"]["thinkingConfig"] = {
                "includeThoughts": True,
                "thinkingBudget": kwargs.get("thinking_budget", 1024),
            }

        # Claude models: enable thinking
        if model.startswith("claude-") and model.endswith("-thinking"):
            request_body["thinking"] = {
                "type": "enabled",
                "budget_tokens": kwargs.get("thinking_budget", 10240),
            }

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

    async def _do_request(self, url: str, payload: dict, headers: dict) -> dict:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=headers, timeout=120)
                resp.raise_for_status()
                raw = resp.json()
                rate_info = _extract_rate_limit_info(dict(resp.headers))
                if rate_info:
                    raw["_rate_limit"] = rate_info
                if "candidates" in raw or "response" in raw:
                    raw = self._transform_cloudcode_response(raw)
                return raw
        except httpx.HTTPStatusError as e:
            body = e.response.text or ""
            error_msg = _extract_error_message(body)
            raise APIError(f"HTTP {e.response.status_code}: {error_msg}", e.response.status_code, retryable=_is_retryable(e.response.status_code))
        except httpx.HTTPError as e:
            raise APIError(str(e), retryable=False)

    def _transform_cloudcode_response(self, response: dict) -> dict:
        """Transform Cloud Code Assist response into OpenAI-compatible format.

        Handles thinking blocks from Claude and Gemini 3.1 Pro models.
        """
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

        # Extract text and thinking from parts
        text = ""
        thinking = ""
        for part in parts:
            if part.get("thought"):
                thinking += part.get("text", "")
            elif "text" in part:
                text += part["text"]

        # Build message content - include thinking as structured blocks
        if thinking:
            message_content = [
                {"type": "thinking", "thinking": thinking},
                {"type": "text", "text": text},
            ]
        else:
            message_content = text

        # Build OpenAI-compatible response
        openai_response = {
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": message_content,
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

    async def _do_stream_request(self, url: str, payload: dict, headers: dict) -> Any:
        max_retries = 3
        delay = 2.0

        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(url, json=payload, headers=headers, timeout=120)
                    resp.raise_for_status()
                    rate_info = _extract_rate_limit_info(dict(resp.headers))
                    received_any = False
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data = line[6:].strip()
                            if data == "[DONE]":
                                return
                            if not data:
                                continue
                            line = data
                        try:
                            raw = json.loads(line)
                            if rate_info:
                                raw["_rate_limit"] = rate_info
                                rate_info = None
                            if "candidates" in raw or "response" in raw:
                                raw = self._transform_cloudcode_chunk(raw)
                            if raw:
                                received_any = True
                                yield raw
                        except json.JSONDecodeError:
                            pass

                if not received_any and attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return

            except httpx.HTTPStatusError as e:
                body = e.response.text or ""
                error_msg = _extract_error_message(body)
                retryable = _is_retryable(e.response.status_code) or _is_retryable_text(body)
                if retryable and attempt < max_retries:
                    server_delay = _extract_retry_delay(body)
                    actual_delay = max(server_delay, delay)
                    await asyncio.sleep(actual_delay)
                    delay *= 2
                    continue
                raise APIError(f"HTTP {e.response.status_code}: {error_msg}", e.response.status_code, retryable=retryable)
            except Exception as e:
                if attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise APIError(str(e), retryable=False)

    async def chat(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        if self._is_api_key():
            payload = self._build_payload(messages, model, max_tokens, **kwargs)
            url = f"{self.base_url}/models/{model}:generateContent"
            url = self._append_api_key(url)
            headers = {"Content-Type": "application/json"}
        else:
            provider = kwargs.get("provider", "")
            is_antigravity = provider == "google-antigravity" or (not provider and (model.startswith("claude-") or model.startswith("gpt-")))
            payload = self._build_cloudcode_payload(messages, model, max_tokens, is_antigravity=is_antigravity, **kwargs)
            url = "https://cloudcode-pa.googleapis.com/v1internal:generateContent"
            headers = self._get_cloudcode_headers(is_antigravity=is_antigravity, model=model)
        max_retries = 3
        delay = 2.0
        for attempt in range(max_retries + 1):
            try:
                return await self._do_request(url, payload, headers)
            except APIError as e:
                if e.retryable and attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise

    def chat_stream(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> Any:
        if self._is_api_key():
            payload = self._build_payload(messages, model, max_tokens, **kwargs)
            url = f"{self.base_url}/models/{model}:streamGenerateContent?alt=sse"
            url = self._append_api_key(url)
            headers = {"Content-Type": "application/json"}
        else:
            provider = kwargs.get("provider", "")
            is_antigravity = provider == "google-antigravity" or (not provider and (model.startswith("claude-") or model.startswith("gpt-")))
            payload = self._build_cloudcode_payload(messages, model, max_tokens, is_antigravity=is_antigravity, **kwargs)
            url = "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
            headers = self._get_cloudcode_headers(is_antigravity=is_antigravity, model=model)

        async def _stream():
            max_retries = 3
            delay = 2.0
            for attempt in range(max_retries + 1):
                try:
                    async for chunk in self._do_stream_request(url, payload, headers):
                        yield chunk
                    return
                except APIError as e:
                    if e.retryable and attempt < max_retries:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    raise

        return _stream()


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
                refreshed = oauth_helper.refresh_google_token(provider_id)
            except APIError:
                raise
            except Exception as e:
                raise APIError(
                    f"Failed to refresh OAuth token for {provider_id}: {e}. "
                    "Please re-authenticate via Settings.",
                    status_code=401,
                    retryable=False,
                )
            # Use the refreshed token (auth.json was saved by refresh_google_token)
            token, _ = _resolve_google_credentials(refreshed)
            return token
        # Token still valid (or no expiry)
        token, _ = _resolve_google_credentials(auth_entry)
        return token

    # OAuth access token (non-Google providers)
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
        "base_url": "https://cloudcode-pa.googleapis.com",
        "api_type": "google",
        "endpoint": "/v1internal:streamGenerateContent",
    },
    "google-gemini-cli": {
        "base_url": "https://cloudcode-pa.googleapis.com",
        "api_type": "google",
        "endpoint": "/v1internal:streamGenerateContent",
    },
}


def _require_https_or_localhost(url: str) -> None:
    """Raise ValueError if url is not HTTPS or a recognised localhost.

    Prevents a compromised / malicious ``baseUrl`` from pointing at internal
    network services (SSRF guardrail), matching the pattern in whitebox-agent.
    """
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    localhosts = {"localhost", "127.0.0.1", "::1"}
    if scheme != "https" and host not in localhosts:
        raise ValueError(
            f"Refusing to connect to '{url}': only HTTPS or localhost URLs are allowed"
        )


# OpenAI-compatible provider dispatch table.
# Callers that reach this path already exhausted aery-gateway, custom models.json,
# and the OAuth configs above — these are pure API-key providers.
_OPENAI_COMPATIBLE_DISPATCH: dict[str, tuple[str, str]] = {
    # (base_url, endpoint) for providers exposed as OpenAI-compatible
    "anthropic":         ("https://api.anthropic.com",                 "/v1/messages"),
    "minimax":           ("https://api.minimax.io/anthropic",           "/v1/messages"),
    "minimax-cn":        ("https://api.minimaxi.com/anthropic",         "/v1/messages"),
    "kimi-coding":       ("https://api.kimi.com/coding",                 "/v1/messages"),
    "openai":            ("https://api.openai.com/v1",                  "/chat/completions"),
    "openrouter":        ("https://openrouter.ai/api/v1",                "/chat/completions"),
    "deepseek":          ("https://api.deepseek.com",                    "/chat/completions"),
    "xai":               ("https://api.x.ai/v1",                         "/chat/completions"),
    "groq":              ("https://api.groq.com/openai/v1",              "/chat/completions"),
    "mistral":           ("https://api.mistral.ai/v1",                  "/chat/completions"),
    "fireworks":         ("https://api.fireworks.ai/inference",          "/v1/chat/completions"),
    "ollama":            ("http://localhost:11434/v1",                   "/chat/completions"),
    "huggingface":       ("https://router.huggingface.co/v1",            "/chat/completions"),
    "cerebras":          ("https://api.cerebras.ai/v1",                  "/chat/completions"),
    "opencode":          ("https://opencode.ai/zen/v1",                  "/chat/completions"),
    "opencode-go":       ("https://opencode.ai/zen/go/v1",              "/chat/completions"),
    "zai":               ("https://api.z.ai/api/coding/paas/v4",         "/chat/completions"),
    "together":          ("https://api.together.ai/v1",                  "/chat/completions"),
    "moonshotai":        ("https://api.moonshot.ai/v1",                  "/chat/completions"),
    "moonshotai-cn":     ("https://api.moonshot.cn/v1",                  "/chat/completions"),
    "xiaomi":            ("https://api.xiaomimimo.com/v1",               "/chat/completions"),
    "cloudflare-ai-gateway": ("https://gateway.ai.cloudflare.com/v1",    "/v1/messages"),
    "az-edge":           ("https://aoai.azureedge.net",                  "/v1/chat/completions"),
}

# Synthetic Anthropic-style providers (use /v1/messages, strip trailing /v1 from base)
_ANTHROPIC_STYLE = {"minimax", "minimax-cn", "kimi-coding",
                    "xiaomi", "xiaomi-token-plan-cn", "xiaomi-token-plan-ams", "xiaomi-token-plan-sgp",
                    "claude-local"}


def create_client(provider_id: str, auth_entry: dict, model: str) -> tuple[Any, str]:
    """Create the appropriate API client for a provider.

    Routes through four tiers in order:
      1. Aery Gateway
      2. Custom providers from models.json
      3. OAuth provider configs (not in API_PROVIDERS)
      4. API-key provider dispatch table

    Returns (client, model_name) tuple.
    """
    from aery_plugin import oauth_helper

    key = _resolve_api_key(provider_id, auth_entry)

    # ── Tier 1: Aery Gateway ───────────────────────────────────────────────────
    if provider_id == "aery-gateway":
        return OpenAIClient(base_url=oauth_helper.AERY_GATEWAY_URL, api_key=key), model

    # ── Tier 2: Custom providers from models.json ──────────────────────────────
    models_path = os.path.join(oauth_helper.AGENT_DIR, "models.json")
    custom_providers = {}
    if os.path.exists(models_path):
        try:
            with open(models_path) as f:
                custom_providers = json.load(f).get("providers", {})
        except Exception:
            pass
    if provider_id in custom_providers:
        cfg = custom_providers[provider_id]
        base_url = cfg.get("baseUrl", "").rstrip("/")
        api_type = cfg.get("api", "openai-completions")
        if api_type in ("anthropic-messages",):
            return AnthropicClient(api_key=key, base_url=base_url), model
        return OpenAIClient(base_url=base_url, api_key=key), model

    # ── Tier 3: OAuth provider configs ─────────────────────────────────────────
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

    # ── Tier 4: API-key provider dispatch table ────────────────────────────────
    cfg = _OPENAI_COMPATIBLE_DISPATCH.get(provider_id)
    if cfg:
        base_url, endpoint = cfg
        # anthropic-style: /v1/messages, strip base /v1 for AnthropicClient
        if provider_id in _ANTHROPIC_STYLE:
            anthropic_base = base_url.rstrip("/").removesuffix("/v1") if provider_id != "anthropic" else "https://api.anthropic.com"
            return AnthropicClient(api_key=key, base_url=anthropic_base), model
        return OpenAIClient(base_url=base_url, api_key=key, endpoint=endpoint), model

    # ── Fallback: generic API_PROVIDERS (with URL guardrail) ──────────────────
    cfg = oauth_helper.API_PROVIDERS.get(provider_id, {})
    base_url = cfg.get("base_url", "https://api.openai.com/v1")

    # Resolve account ID placeholders in base URL (Cloudflare, etc.)
    if "{CLOUDFLARE_ACCOUNT_ID}" in base_url:
        account_id = auth_entry.get("accountId", "") or auth_entry.get("metadata", {}).get("CLOUDFLARE_ACCOUNT_ID", "")
        if not account_id:
            account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        base_url = base_url.replace("{CLOUDFLARE_ACCOUNT_ID}", account_id)
    if "{CLOUDFLARE_GATEWAY_ID}" in base_url:
        gateway_id = auth_entry.get("gatewayId", "") or auth_entry.get("metadata", {}).get("CLOUDFLARE_GATEWAY_ID", "")
        base_url = base_url.replace("{CLOUDFLARE_GATEWAY_ID}", gateway_id)

    # Custom base URL from auth entry overrides provider default
    if auth_entry.get("baseUrl"):
        user_url = auth_entry["baseUrl"]
        try:
            _require_https_or_localhost(user_url)
        except ValueError as e:
            raise APIError(str(e), status_code=400, retryable=False)
        base_url = user_url

    # Anthropic-style providers (MiniMax, Kimi, Xiaomi, etc.)
    if provider_id in _ANTHROPIC_STYLE:
        anthropic_base = base_url.rstrip("/").removesuffix("/v1") if provider_id != "anthropic" else "https://api.anthropic.com"
        return AnthropicClient(api_key=key, base_url=anthropic_base), model

    # Google Gemini REST API (not Cloud Code)
    if provider_id == "google":
        return GeminiClient(api_key=key), model

    # Google Vertex AI
    if provider_id == "google-vertex":
        return GeminiClient(api_key=key, base_url=base_url), model

    # Default: OpenAI-compatible
    return OpenAIClient(base_url=base_url, api_key=key), model
