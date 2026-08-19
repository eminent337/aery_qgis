# Strands streaming agent integration
try:
    from strands import Agent
    from strands.models.openai import OpenAIModel
    from strands.models.anthropic import AnthropicModel
    from strands.models.gemini import GeminiModel
    from strands.models.bedrock import BedrockModel
    HAS_STRANDS = True
except ImportError:
    HAS_STRANDS = False
    Agent = object
    OpenAIModel = AnthropicModel = GeminiModel = BedrockModel = object

# AI Proxy integration
try:
    from aery_plugin.ai_proxy import get_proxy_worker
    HAS_AI_PROXY = True
except ImportError:
    HAS_AI_PROXY = False
    get_proxy_worker = None

"""Direct LLM API client for the Aery QGIS plugin.
Uses the existing oauth_helper.py for credential resolution.
"""

from aery_plugin.logger import logger
import abc
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import aery_plugin._http as httpx
import asyncio
from typing import Any, Optional


class APIError(Exception):
    """Raised when an API call fails."""
    def __init__(self, message: str, status_code: int = 0, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class LLMClientBase(abc.ABC):
    """Abstract base class for all LLM API clients.

    Provides the common interface that agent.py depends on:
      - chat() and chat_stream() for API calls
      - filter_tool_calls() to strip provider-internal tool calls
      - format_message_pair() to format assistant+tool messages for history
    """

    @abc.abstractmethod
    async def chat(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        ...

    @abc.abstractmethod
    async def chat_stream(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> Any:
        ...

    def filter_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        """Filter out tool calls the agent cannot execute.

        Default: pass through all tool calls unchanged.
        Override in subclasses that emit internal/infrastructure tool calls.
        """
        return tool_calls

    def format_message_pair(self, tool_call: dict, tool_result: str) -> list[dict]:
        """Return the messages to append to conversation history after a tool execution.
        Returns messages: assistant (with tool_calls) + tool (with string result).
        """
        # If tool returned a base64 image, keep tool response as clean confirmation text
        # to avoid payload errors on text-only LLM models.
        content = tool_result
        if isinstance(tool_result, str) and tool_result.startswith("data:image/"):
            content = "Map canvas captured successfully (image rendered on user screen)."
        return [
            {"role": "assistant", "tool_calls": [tool_call]},
            {"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": content},
        ]


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
            err_obj = parsed.get("error", {})
            msg = err_obj.get("message", "")
            details = err_obj.get("details", "")
            
            if not msg and parsed.get("message"):
                msg = parsed["message"]
                
            if msg and details:
                return f"{msg}: {details}"
            elif msg:
                return msg
            elif details:
                return details
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


class OpenAIClient(LLMClientBase):
    """Client for OpenAI-compatible APIs (OpenAI, Groq, OpenRouter, etc.)."""

    def __init__(self, base_url: str, api_key: str, endpoint: str = "/chat/completions"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.endpoint = endpoint
        self._client: Optional[httpx.AsyncClient] = None

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_payload(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        from aery_plugin.core.ai.auth import AERY_GATEWAY_URL
        if self.base_url.rstrip("/") != AERY_GATEWAY_URL.rstrip("/"):
            kwargs.pop("provider", None)
            kwargs.pop("session_id", None)
        # Never send tools=None or tools=[] — omit the key entirely when there are no tools
        if not kwargs.get("tools"):
            kwargs.pop("tools", None)
        # Strip any other None-valued kwargs that could cause schema validation errors
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
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
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=120)
            resp = await self._client.post(url, json=payload, headers=headers, timeout=120)
            resp.raise_for_status()
            result = resp.json()
            rate_info = _extract_rate_limit_info(dict(resp.headers))
            if rate_info:
                result["_rate_limit"] = rate_info
            return result
        except httpx.HTTPStatusError as e:
            raw_body = e.response.text or ""
            error_msg = _extract_error_message(raw_body)
            raise APIError(error_msg, e.response.status_code, retryable=_is_retryable(e.response.status_code))
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
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=120)
                async with self._client.stream("POST", url, json=payload, headers=headers, timeout=120) as resp:
                    resp.raise_for_status()
                    rate_info = _extract_rate_limit_info(dict(resp.headers))
                    received_any = False
                    malformed_count = 0
                    partial_buffer = ""
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith(":") or line.startswith("event:") or line.startswith("id:") or line.startswith("retry:"):
                            # Skip SSE comments (keep-alives) and metadata fields
                            continue
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                return
                            if not data:
                                continue
                            line = data
                        if partial_buffer:
                            line = partial_buffer + line
                            partial_buffer = ""
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            if line.startswith("{") and not line.rstrip().endswith("}"):
                                partial_buffer = line
                                continue
                            logger.debug("[Aery] Dropping malformed streaming chunk: %r", line[:200])
                            malformed_count += 1
                            if malformed_count > 10:
                                raise APIError(
                                    "Stream produced too many malformed chunks",
                                    status_code=500,
                                    retryable=True,
                                )
                            continue
                        if rate_info:
                            chunk["_rate_limit"] = rate_info
                            rate_info = None
                        yield chunk
                        received_any = True
                        malformed_count = 0
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
                if attempt < max_retries and retryable:
                    continue
                raise APIError(error_msg, e.response.status_code, retryable=retryable)
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



class AnthropicClient(LLMClientBase):
    """Client for Anthropic's Messages API."""

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def format_message_pair(self, tool_call: dict, tool_result: str) -> list[dict]:
        """Format tool executions into Anthropic's native tool_use and tool_result blocks."""
        tool_call_id = tool_call.get("id", "")
        name = tool_call.get("function", {}).get("name", "")
        args_raw = tool_call.get("function", {}).get("arguments", "{}")
        if isinstance(args_raw, str):
            import json
            try: args = json.loads(args_raw)
            except: args = {}
        else:
            args = args_raw
            
        assistant_content = [{"type": "tool_use", "id": tool_call_id, "name": name, "input": args}]
        
        user_content = []
        if isinstance(tool_result, str) and tool_result.startswith("data:image/"):
            try:
                prefix, b64_data = tool_result.split(",", 1)
                media_type = prefix.replace("data:", "").replace(";base64", "")
                user_content.append({
                    "type": "tool_result", 
                    "tool_use_id": tool_call_id, 
                    "content": [{"type": "text", "text": "Screenshot captured successfully:"}, {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_data}}]
                })
            except Exception:
                user_content.append({"type": "tool_result", "tool_use_id": tool_call_id, "content": str(tool_result)})
        else:
            user_content.append({"type": "tool_result", "tool_use_id": tool_call_id, "content": str(tool_result)})
            
        return [
            {"role": "assistant", "content": assistant_content},
            {"role": "user", "content": user_content}
        ]

    def _build_payload(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        from aery_plugin.core.ai.auth import AERY_GATEWAY_URL
        if self.base_url.rstrip("/") != AERY_GATEWAY_URL.rstrip("/"):
            kwargs.pop("provider", None)
            kwargs.pop("session_id", None)
        # Never send tools=None or tools=[] — omit the key entirely when there are no tools
        if not kwargs.get("tools"):
            kwargs.pop("tools", None)
        # Strip any other None-valued kwargs
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
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
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=120)
            resp = await self._client.post(url, json=payload, headers=headers, timeout=120)
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
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=120)
                async with self._client.stream("POST", url, json=payload, headers=headers, timeout=120) as resp:
                    resp.raise_for_status()
                    rate_info = _extract_rate_limit_info(dict(resp.headers))
                    received_any = False
                    malformed_count = 0
                    partial_buffer = ""
                    current_text = ""
                    current_tool_calls = []
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith(":") or line.startswith("id:") or line.startswith("retry:"):
                            # Skip SSE comments (keep-alives) and metadata
                            continue
                        if line.startswith("event: "):
                            event_type = line[7:].strip()
                            if event_type == "message_stop":
                                return
                            continue
                        if line.startswith("data: "):
                            data = line[6:]
                            if not data:
                                continue
                            line = data
                        if partial_buffer:
                            line = partial_buffer + line
                            partial_buffer = ""
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            if line.startswith("{") and not line.rstrip().endswith("}"):
                                partial_buffer = line
                                continue
                            logger.debug("[Aery] Dropping malformed streaming chunk: %r", line[:200])
                            malformed_count += 1
                            if malformed_count > 10:
                                raise APIError(
                                    "Stream produced too many malformed chunks",
                                    status_code=500,
                                    retryable=True,
                                )
                            continue
                        # Transform Anthropic SSE to OpenAI format
                        transformed = self._transform_anthropic_chunk(chunk, current_text, current_tool_calls)
                        if transformed:
                            if rate_info:
                                transformed["_rate_limit"] = rate_info
                                rate_info = None
                            yield transformed
                            received_any = True
                            malformed_count = 0
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


class GeminiClient(LLMClientBase):
    """Client for Google Gemini API.

    Supports two auth modes:
    1. API key (AIza...)  -> query-param auth, native Gemini API
    2. OAuth Bearer (ya29.) -> header auth, native Gemini API
    """

    def __init__(self, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta", project_id: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.project_id = project_id
        self._client: Optional[httpx.AsyncClient] = None
        self._stream_pending_ts = ""

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None


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
                parts = []
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        try:
                            args = json.loads(tc["function"]["arguments"])
                        except (json.JSONDecodeError, KeyError):
                            args = {}
                        fc_part = {
                            "functionCall": {
                                "name": tc["function"]["name"],
                                "args": args,
                            }
                        }
                        if tc.get("thought_signature"):
                            fc_part["thought_signature"] = tc["thought_signature"]
                        parts.append(fc_part)
                if msg["role"] == "tool":
                    fname = msg.get("tool_name", "") or msg.get("tool_call_id", "").rsplit("_", 1)[0]
                    parts.append({
                        "functionResponse": {
                            "name": fname,
                            "response": {"response": msg.get("content", "")},
                        }
                    })
                if msg["role"] != "tool" and msg.get("content") and not msg.get("tool_calls"):
                    parts.append({"text": str(msg["content"])})
                if not parts:
                    parts.append({"text": ""})
                contents.append({"role": role, "parts": parts})

        payload = {"contents": contents}
        if system_instruction:
            payload["system_instruction"] = system_instruction
        payload["generationConfig"] = {
            "maxOutputTokens": max_tokens,
            **kwargs.get("generationConfig", {}),
        }
        if kwargs.get("tools"):
            func_decls = []
            for t in kwargs["tools"]:
                if t.get("type") == "function" and "function" in t:
                    func = t["function"].copy()
                    # Gemini doesn't support 'additionalProperties' well in some API versions, but let's just pass it
                    func_decls.append(func)
            if func_decls:
                payload["tools"] = [{"functionDeclarations": func_decls}]
        return payload

    def _append_api_key(self, url: str) -> str:
        """Append API key to URL, using & if query params already exist."""
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}key={self.api_key}"

    async def _do_request(self, url: str, payload: dict, headers: dict) -> dict:
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=120)
            resp = await self._client.post(url, json=payload, headers=headers, timeout=120)
            resp.raise_for_status()
            raw = resp.json()
            rate_info = _extract_rate_limit_info(dict(resp.headers))
            if rate_info:
                raw["_rate_limit"] = rate_info
            if "candidates" in raw or "response" in raw:
                raw = self._transform_gemini_response(raw)
            return raw
        except httpx.HTTPStatusError as e:
            body = e.response.text or ""
            error_msg = _extract_error_message(body)
            raise APIError(f"HTTP {e.response.status_code}: {error_msg}", e.response.status_code, retryable=_is_retryable(e.response.status_code))
        except httpx.HTTPError as e:
            err_str = str(e)
            is_dns_err = "Temporary failure in name resolution" in err_str or "Name or service not known" in err_str
            raise APIError(err_str, retryable=is_dns_err)

    def _transform_gemini_chunk(self, chunk: dict) -> dict:
        """Transform a Gemini API streaming chunk into OpenAI-compatible delta format.

        Returns a dict with ``choices[0].delta`` suitable for the agent's
        OpenAI-style streaming consumer. Handles both text and functionCall parts,
        and preserves thought_signature across chunks via _stream_pending_ts.
        """
        if "choices" in chunk:
            return chunk

        inner = chunk.get("response", chunk)
        candidates = inner.get("candidates", [])
        if not candidates:
            return {"choices": [{"delta": {"role": "assistant", "content": ""}}]}

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason")
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        # Probe thought_signature at all levels (candidate, content, part)
        ts_candidate = candidate.get("thought_signature", "")
        ts_content = content.get("thought_signature", "")
        ts_fallback = ts_candidate or ts_content or ""

        text = ""
        tool_calls = []
        for part in parts:
            if part.get("text"):
                text += part["text"]
            if "functionCall" in part:
                fc = part["functionCall"]
                # Use part-level ts first, then fallback to candidate/content level
                ts = part.get("thought_signature", "") or ts_fallback or self._stream_pending_ts
                fc_name = fc.get("name", "")
                tc_entry = {
                    "id": fc_name + "_" + secrets.token_hex(4),
                    "function": {
                        "name": fc_name,
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                }
                if ts:
                    tc_entry["thought_signature"] = ts
                tool_calls.append(tc_entry)
            # Store any thought_signature for cross-chunk persistence
            part_ts = part.get("thought_signature", "")
            if part_ts:
                self._stream_pending_ts = part_ts

        # Also store candidate/content-level ts for cross-chunk persistence
        if ts_fallback and not self._stream_pending_ts:
            self._stream_pending_ts = ts_fallback

        delta: dict = {"role": "assistant"}
        if text:
            delta["content"] = text
        if tool_calls:
            # Gemini delivers function calls as complete objects (not streamed per-char)
            # so each tool call gets its own index
            for i, tc in enumerate(tool_calls):
                tc["index"] = i
            delta["tool_calls"] = tool_calls

        result: dict = {"choices": [{"index": 0, "delta": delta}]}
        if finish_reason:
            result["choices"][0]["finish_reason"] = finish_reason
        return result


    def _transform_gemini_response(self, response: dict) -> dict:
        """Transform Gemini API response into OpenAI-compatible format.

        Handles thinking blocks from Claude and Gemini 3.1 Pro models,
        and functionCall parts for tool use.
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

        # Probe thought_signature at all possible levels
        ts_candidate = candidate.get("thought_signature", "")
        ts_content = content.get("thought_signature", "")
        ts_fallback = ts_candidate or ts_content or ""

        # Extract text, thinking, and function calls from parts
        text = ""
        thinking = ""
        tool_calls = []
        for part in parts:
            if part.get("thought"):
                thinking += part.get("text", "")
            elif "text" in part:
                text += part["text"]
            if "functionCall" in part:
                fc = part["functionCall"]
                tc_id = fc.get("name", "unknown") + "_" + secrets.token_hex(4)
                ts = part.get("thought_signature") or ts_fallback
                tc_entry = {
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                }
                if ts:
                    tc_entry["thought_signature"] = ts
                tool_calls.append(tc_entry)

        # Determine finish_reason
        finish_reason = "stop"
        if tool_calls:
            finish_reason = "tool_calls"

        # Build message content - include thinking as structured blocks
        if thinking:
            message_content = [
                {"type": "thinking", "thinking": thinking},
                {"type": "text", "text": text},
            ]
        else:
            message_content = text

        # Build OpenAI-compatible response
        message: dict = {
            "role": "assistant",
            "content": message_content if not tool_calls else None,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls

        openai_response = {
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
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
                import sys as _sys
                logger.info(f"[Aery] POST {url[:60]}... attempt {attempt + 1}", file=_sys.stderr)
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=120)
                resp = await asyncio.wait_for(
                    self._client.post(url, json=payload, headers=headers, timeout=120),
                    timeout=15,
                )
                logger.info(f"[Aery] POST complete status={resp.status_code}", file=_sys.stderr)
                resp.raise_for_status()
                rate_info = _extract_rate_limit_info(dict(resp.headers))
                received_any = False
                malformed_count = 0
                partial_buffer = ""
                try:
                    async for raw_line in _timeout_aiter(resp.aiter_lines(), timeout=30):
                        line = raw_line.strip()
                        if not line:
                            continue
                        if line.startswith(":") or line.startswith("event:") or line.startswith("id:") or line.startswith("retry:"):
                            # Skip SSE comments (keep-alives) and metadata
                            continue
                        if line.startswith("data: "):
                            data = line[6:].strip()
                            if data == "[DONE]":
                                return
                            if not data:
                                continue
                            line = data
                        # If we buffered a partial JSON line, try merging.
                        if partial_buffer:
                            line = partial_buffer + line
                            partial_buffer = ""
                        try:
                            raw = json.loads(line)
                        except json.JSONDecodeError:
                            # Don't silently drop malformed chunks. Buffer
                            # candidate partial JSON ({ without matching }) so a
                            # truncation across SSE boundaries can be repaired.
                            if line.startswith("{") and not line.rstrip().endswith("}"):
                                partial_buffer = line
                                continue
                            logger.debug("[Aery] Dropping malformed streaming chunk: %r", line[:200])
                            malformed_count += 1
                            if malformed_count > 10:
                                raise APIError(
                                    "Stream produced too many malformed chunks",
                                    status_code=500,
                                    retryable=True,
                                )
                            continue
                        if rate_info:
                            raw["_rate_limit"] = rate_info
                            rate_info = None
                        if "candidates" in raw or "response" in raw:
                            raw = self._transform_gemini_chunk(raw)
                        if raw:
                            received_any = True
                            malformed_count = 0
                            yield raw
                except asyncio.TimeoutError:
                    logger.info(f"[Aery] Stream timed out after 30s without data")
                    if attempt < max_retries:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    raise

                if not received_any and attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return

            except httpx.HTTPStatusError as e:
                body = e.response.text or ""
                error_msg = _extract_error_message(body)
                import sys as _sys
                logger.info(f"[Aery] HTTP {e.response.status_code} body[:200]={body[:200]!r}", file=_sys.stderr)
                retryable = _is_retryable(e.response.status_code) or _is_retryable_text(body)
                if retryable and attempt < max_retries:
                    server_delay = _extract_retry_delay(body, dict(e.response.headers))
                    actual_delay = min(max(server_delay, delay), 30)
                    logger.info(f"[Aery] retrying in {actual_delay:.0f}s (attempt {attempt+2}/{max_retries+1})", file=_sys.stderr)
                    await asyncio.sleep(actual_delay)
                    delay *= 2
                    continue
                logger.info(f"[Aery] Giving up after {attempt+1} attempts, HTTP {e.response.status_code}", file=_sys.stderr)
                raise APIError(f"HTTP {e.response.status_code}: {error_msg}", e.response.status_code, retryable=retryable)
            except Exception as e:
                err_str = str(e)
                is_dns_err = "Temporary failure in name resolution" in err_str or "Name or service not known" in err_str
                if attempt < max_retries and is_dns_err:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise APIError(err_str, retryable=is_dns_err or attempt < max_retries)

    async def chat(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> dict:
        payload = self._build_payload(messages, model, max_tokens, **kwargs)
        url = f"{self.base_url}/models/{model}:generateContent"
        if self._is_api_key():
            url = self._append_api_key(url)
            headers = {"Content-Type": "application/json"}
        else:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
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

    async def chat_stream(self, messages: list[dict], model: str, max_tokens: int = 8192, **kwargs) -> Any:
        payload = self._build_payload(messages, model, max_tokens, **kwargs)
        url = f"{self.base_url}/models/{model}:streamGenerateContent?alt=sse"
        if self._is_api_key():
            url = self._append_api_key(url)
            headers = {"Content-Type": "application/json"}
        else:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
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


async def _timeout_aiter(ait, timeout: float = 30):
    """Wrap an async iterator with a per-item timeout."""
    it = ait.__aiter__()
    while True:
        try:
            item = await asyncio.wait_for(it.__anext__(), timeout=timeout)
            yield item
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            raise


def _resolve_google_credentials(auth_entry: dict) -> tuple[str, str]:
    """Resolve Google OAuth credentials. Returns (access_token, project_id)."""
    # Handle both old format (access) and new format (access_token)
    access = auth_entry.get("access_token", "") or auth_entry.get("access", "")
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
    from aery_plugin.core.ai.auth import get_auth_entry

    # First, get the latest auth entry from Vault (auth_entry param is deprecated)
    entry = get_auth_entry(provider_id)

    # Direct API key
    key = entry.get("key", "")
    if key:
        return key

    # Google OAuth: access token may be wrapped in JSON with projectId
    if provider_id in ("google-antigravity", "google-gemini-cli"):
        # Check expiry
        expires = entry.get("expires_at", 0)
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
            # Use the refreshed token
            token, _ = _resolve_google_credentials(refreshed)
            return token
        # Token still valid (or no expiry)
        token, _ = _resolve_google_credentials(entry)
        return token

    # OAuth access token (non-Google providers)
    access = entry.get("access_token", "")
    if access:
        from aery_plugin.core.ai.auth import OAUTH_CONFIGS
        # Static-bearer providers (Kilo) issue a long-lived token with no
        # refresh path — mirror the main Aery agent and return it as-is.
        if OAUTH_CONFIGS.get(provider_id, {}).get("static_bearer"):
            return access

        # Check expiry (stored as ms timestamp)
        expires = entry.get("expires_at", 0)
        if expires:
            # If expires is in seconds (< 1e11), convert to milliseconds
            exp_ms = expires * 1000 if expires < 1e11 else expires
            now_ms = int(time.time() * 1000)
            if now_ms < exp_ms:
                return access
            # Token expired — try refresh (Google and non-Google OAuth)
            try:
                refreshed = oauth_helper.refresh_oauth_token(provider_id)
            except APIError:
                raise
            except Exception as e:
                raise APIError(
                    f"Failed to refresh OAuth token for {provider_id}: {e}. "
                    "Please re-authenticate via Settings.",
                    status_code=401,
                    retryable=False,
                )
            return refreshed.get("access_token", "")
        # No expiry field — use access token as-is (some providers don't track expiry)
        return access


_OAUTH_API_CONFIGS: dict[str, dict] = {
    "kilo": {
        "base_url": "https://api.kilo.ai/api/gateway",
        "api_type": "openai-compatible",
        "endpoint": "/chat/completions",
    },
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
        "endpoint": "/v1beta/models",
    },
    "google-gemini-cli": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_type": "google",
        "endpoint": "/v1beta/models",
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


def _resolve_gemini_client_for_model(provider_id: str, auth_entry: dict, model: str) -> Optional[tuple[Any, str]]:
    """Resolve a GeminiClient for Gemini models, preferring a real API key over OAuth.

    Cloud Code Assist API was shut down on June 18, 2026. All OAuth-token
    requests now go to the native Gemini API (generativelanguage.googleapis.com)
    which accepts Bearer tokens via the Authorization header, or to Vertex AI
    for cloud-platform-scoped OAuth.

    Returns (client, model) if a Gemini model can be served, or None if the
    model is not a Gemini model (caller should use the non-Gemini path).
    """
    from aery_plugin import oauth_helper

    if not model.startswith("gemini-"):
        return None

    # Prefer a Gemini API key (AIza...) for the native public API.
    gemini_key = auth_entry.get("key", "")
    if not gemini_key or not gemini_key.startswith("AIza"):
        gemini_key = oauth_helper.get_auth_entry("google").get("key", "")
    if gemini_key:
        return GeminiClient(api_key=gemini_key, base_url="https://generativelanguage.googleapis.com/v1beta"), model

    # OAuth token path — native Gemini API accepts Bearer Authorization headers.
    # Cloud Code (cloudcode-pa.googleapis.com) is deprecated/shutdown.
    oauth_token, _ = _resolve_google_credentials(auth_entry)
    if oauth_token:
        return GeminiClient(api_key=oauth_token, base_url="https://generativelanguage.googleapis.com/v1beta"), model

    raise APIError(
        "Gemini models require a Gemini API key. "
        "Get one free at https://aistudio.google.com/apikey "
        "and enter it in Settings → Providers → Google Gemini.",
        status_code=401, retryable=False)


def _resolve_url_placeholders(base_url: str, auth_entry: dict) -> str:
    """Resolve Cloudflare and similar placeholders in a base URL.

    Called from all tiers so placeholders are resolved regardless of which
    routing path matches.
    """
    import os as _os

    if "{CLOUDFLARE_ACCOUNT_ID}" in base_url:
        account_id = (auth_entry.get("accountId", "")
                      or auth_entry.get("metadata", {}).get("CLOUDFLARE_ACCOUNT_ID", "")
                      or _os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
        base_url = base_url.replace("{CLOUDFLARE_ACCOUNT_ID}", account_id)
    if "{CLOUDFLARE_GATEWAY_ID}" in base_url:
        gateway_id = (auth_entry.get("gatewayId", "")
                      or auth_entry.get("metadata", {}).get("CLOUDFLARE_GATEWAY_ID", "")
                      or _os.environ.get("CLOUDFLARE_GATEWAY_ID", ""))
        base_url = base_url.replace("{CLOUDFLARE_GATEWAY_ID}", gateway_id)
    return base_url


def create_client(provider_id: str, auth_entry: dict, model: str) -> tuple[Any, str]:
    """Create the appropriate API client for a provider.

    Routes through four tiers in order:
      1. Aery Gateway
      2. Custom providers from models.json
      3. Provider registry (Aery models.generated.ts)
      4. OAuth provider configs (not in API_PROVIDERS)
      5. API-key provider dispatch table

    Returns (client, model_name) tuple.
    """
    from aery_plugin import oauth_helper
    from aery_plugin.providers import get_model, get_provider_api

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
            logger.debug("Failed to load custom providers from models.json", exc_info=True)
    if provider_id in custom_providers:
        cfg = custom_providers[provider_id]
        base_url = cfg.get("baseUrl", "").rstrip("/")
        api_type = cfg.get("api", "openai-completions")
        if api_type in ("anthropic-messages",):
            return AnthropicClient(api_key=key, base_url=base_url), model
        return OpenAIClient(base_url=base_url, api_key=key), model

    # ── Tier 3: Provider registry (Aery models.generated.ts) ──────────────────
    registry_model = get_model(provider_id, model)
    if registry_model:
        api_type = registry_model.api
        base_url = _resolve_url_placeholders(registry_model.base_url, auth_entry)

        # Anthropic messages API
        if api_type == "anthropic-messages":
            return AnthropicClient(api_key=key, base_url=base_url), model

        # Google Generative AI
        if api_type == "google-generative-ai":
            return GeminiClient(api_key=key, base_url=base_url), model

        # Google Vertex
        if api_type == "google-vertex":
            return GeminiClient(api_key=key, base_url=base_url), model

        # Mistral conversations
        if api_type == "mistral-conversations":
            return OpenAIClient(base_url=base_url, api_key=key, endpoint="/chat/completions"), model

        # OpenAI completions (most providers)
        if api_type == "openai-completions":
            return OpenAIClient(base_url=base_url, api_key=key, endpoint="/chat/completions"), model

        # OpenAI responses
        if api_type in ("openai-responses", "azure-openai-responses", "openai-codex-responses"):
            return OpenAIClient(base_url=base_url, api_key=key, endpoint="/responses"), model

        # Bedrock (requires special handling)
        if api_type == "bedrock-converse-stream":
            # Fall through to bedrock-specific handling
            pass

        # google-gemini-cli models are served by the Antigravity / Cloud Code
        # Assist gateway, which proxies MULTIPLE model families (Gemini,
        # Claude, GPT-OSS). Route by model family to the correct client class
        # using the model's registry base_url (the gateway endpoint).
        # Never fall through to the native Gemini API for non-Gemini models —
        # that produced a guaranteed 404 (claude-... sent to
        # generativelanguage.googleapis.com).
        if api_type == "google-gemini-cli":
            model_l = model.lower()
            if model_l.startswith("gemini-"):
                gemini_result = _resolve_gemini_client_for_model(provider_id, auth_entry, model)
                if gemini_result:
                    return gemini_result
                return GeminiClient(api_key=key, base_url=base_url), model
            if model_l.startswith("claude"):
                return AnthropicClient(api_key=key, base_url=base_url), model
            if model_l.startswith("gpt") or model_l.startswith("openai"):
                return OpenAIClient(base_url=base_url, api_key=key, endpoint="/chat/completions"), model
            raise APIError(
                f"Model '{model}' on provider '{provider_id}' has api type "
                f"'google-gemini-cli' but is not a recognised Gemini/Claude/GPT "
                f"model. Cannot route to a client.",
                status_code=400, retryable=False)

    # ── Tier 4: OAuth provider configs ─────────────────────────────────────────
    oauth_cfg = _OAUTH_API_CONFIGS.get(provider_id)
    if oauth_cfg:
        if oauth_cfg["api_type"] == "anthropic":
            return AnthropicClient(api_key=key), model
        if oauth_cfg["api_type"] == "google":
            oauth_token, project_id = _resolve_google_credentials(auth_entry)
            # Gemini models are served by the native Gemini API / Vertex AI
            # (accepts cloud-platform scoped OAuth).
            if model.startswith("gemini-"):
                gemini_result = _resolve_gemini_client_for_model(provider_id, auth_entry, model)
                if gemini_result:
                    return gemini_result
                base_url = oauth_cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta")
                return GeminiClient(api_key=key, base_url=base_url, project_id=project_id), model
            # Non-Gemini model reaching here means it was not in the registry
            # (Tier 3 skipped) and is not a Gemini model. The Antigravity
            # gateway serves Claude/GPT-OSS, but without a registry entry we
            # cannot determine the correct client/endpoint — refuse rather than
            # silently sending it to the Gemini API (guaranteed 404).
            raise APIError(
                f"Model '{model}' on provider '{provider_id}' is not a Gemini "
                f"model and has no registry entry, so it cannot be routed. "
                f"Use a Gemini model or a registered Claude/GPT-OSS model.",
                status_code=400, retryable=False)
        # OpenAI-compatible or OpenAI Responses
        base_url = oauth_cfg["base_url"]
        endpoint = oauth_cfg.get("endpoint", "/chat/completions")
        return OpenAIClient(base_url=base_url, api_key=key, endpoint=endpoint), model

    # ── Tier 4: API-key provider dispatch table ────────────────────────────────
    cfg = None
    if provider_id and provider_id != "openai":
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
    base_url = _resolve_url_placeholders(cfg.get("base_url", "https://api.openai.com/v1"), auth_entry)

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


def create_streaming_agent(
    provider_id: str,
    auth_entry: dict,
    model: str,
    system_prompt: str = "",
    tools: list = None,
) -> "Agent":
    """Create a Strands streaming Agent for the given provider.
    
    Returns an Agent configured with the appropriate model provider.
    The agent supports token-by-token streaming and reasoning traces.
    """
    if not HAS_STRANDS:
        raise RuntimeError("Strands SDK not installed. Run: pip install strands-agents")
    
    key = _resolve_api_key(provider_id, auth_entry)
    
    # Map provider to Strands model
    if provider_id in ("openai", "groq", "openrouter", "together", "fireworks", "perplexity", "deepinfra", "mistral", "xai", "cerebras"):
        model_obj = OpenAIModel(
            model_id=model,
            api_key=key,
            # Map base URLs for compatible providers
            **({"base_url": _OPENAI_COMPATIBLE_DISPATCH.get(provider_id, ("", ""))[0]} if provider_id in _OPENAI_COMPATIBLE_DISPATCH else {})
        )
    elif provider_id in ("anthropic", "minimax", "kimi-coding", "xiaomi"):
        model_obj = AnthropicModel(
            model_id=model,
            api_key=key,
        )
    elif provider_id in ("google", "google-vertex", "gemini"):
        model_obj = GeminiModel(
            model_id=model,
            api_key=key,
        )
    elif provider_id in ("bedrock", "aws-bedrock"):
        model_obj = BedrockModel(model_id=model)
    else:
        # Default to OpenAI-compatible
        base_url = _OPENAI_COMPATIBLE_DISPATCH.get(provider_id, ("", ""))[0]
        model_obj = OpenAIModel(
            model_id=model,
            api_key=key,
            base_url=base_url,
        )
    
    agent = Agent(
        model=model_obj,
        system_prompt=system_prompt,
        tools=tools or [],
    )
    return agent


async def create_client_with_proxy(
    provider_id: str,
    auth_entry: dict,
    model: str,
    messages: list[dict],
    tools: Optional[list] = None,
    max_tokens: int = 8192,
    temperature: float = 0.0,
    stream: bool = False,
) -> tuple[str, list, dict]:
    """Create client and execute request with AI Proxy fallback.
    
    Uses AIProxyWorker for automatic provider fallback on failure.
    Returns (content, tool_calls, usage).
    """
    if not HAS_AI_PROXY:
        # Fall back to direct client
        client, resolved_model = create_client(provider_id, auth_entry, model)
        response = await client.chat(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            tools=tools,
            provider=provider_id,
        )
        choice = response.get("choices", [{}])[0]
        return (
            choice.get("message", {}).get("content", ""),
            choice.get("message", {}).get("tool_calls", []),
            response.get("usage", {}),
        )
    
    proxy = get_proxy_worker()
    
    # Get available fallback providers
    fallback_providers = proxy.get_available_providers()
    if provider_id in fallback_providers:
        fallback_providers.remove(provider_id)
    
    response = await proxy.submit_request(
        provider_id=provider_id,
        model=model,
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
        temperature=temperature,
        stream=stream,
        fallback_providers=fallback_providers,
    )
    
    return (
        response.content,
        response.tool_calls,
        response.usage,
    )