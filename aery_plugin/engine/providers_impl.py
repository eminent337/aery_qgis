import json
from typing import AsyncGenerator
import aery_plugin._http as httpx
from .llm import ProviderBase
def _check_response_status(response):
    if response.status_code >= 400:
        err_body = response.text or ""
        msg = err_body
        try:
            err_json = json.loads(err_body)
            msg = err_json.get("error", {}).get("message", err_body)
        except Exception:
            pass
        raise Exception(f"HTTP Status {response.status_code}: {msg}")

class KiloProvider(ProviderBase):
    def __init__(self, api_key: str = ""):
        self.base_url = "https://api.kilo.ai/api/gateway"
        self.api_key = api_key
        
    async def stream_chat(self, messages: list[dict], model: str) -> AsyncGenerator[str, None]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "model": model,
            "messages": messages,
            "stream": True
        }
        
        client = httpx.AsyncClient()
        async with client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, stream=True) as response:
            _check_response_status(response)
            async for chunk in response.aiter_lines():
                if chunk.startswith("data: "):
                    data = chunk[6:]
                    if data == "[DONE]":
                        break
                    try:
                        parsed = json.loads(data)
                        if parsed.get("choices") and "delta" in parsed["choices"][0]:
                            content = parsed["choices"][0]["delta"].get("content", "")
                            if content:
                                yield content
                    except json.JSONDecodeError:
                        continue

class OpenCodeZenProvider(ProviderBase):
    def __init__(self, api_key: str = ""):
        self.base_url = "https://opencode.ai/zen/v1"
        self.api_key = api_key
        
    async def stream_chat(self, messages: list[dict], model: str) -> AsyncGenerator[str, None]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "model": model,
            "messages": messages,
            "stream": True
        }
        
        client = httpx.AsyncClient()
        async with client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, stream=True) as response:
            _check_response_status(response)
            async for chunk in response.aiter_lines():
                if chunk.startswith("data: "):
                    data = chunk[6:]
                    if data == "[DONE]":
                        break
                    try:
                        parsed = json.loads(data)
                        if parsed.get("choices") and "delta" in parsed["choices"][0]:
                            content = parsed["choices"][0]["delta"].get("content", "")
                            if content:
                                yield content
                    except json.JSONDecodeError:
                        continue

class AntigravityProvider(ProviderBase):
    def __init__(self, api_key: str = ""):
        self.base_url = "https://daily-cloudcode-pa.sandbox.googleapis.com/v1"
        self.api_key = api_key
        
    async def stream_chat(self, messages: list[dict], model: str) -> AsyncGenerator[str, None]:
        # Delegate to the canonical Cloud Code Assist client so the Antigravity
        # wire format lives in one place (aery_plugin.llm_client.AntigravityClient).
        from aery_plugin.llm_client import (
            AntigravityClient as CloudCodeClient,
            _resolve_antigravity_credentials,
        )

        token, project_id = _resolve_antigravity_credentials("google-antigravity", {})
        if not token:
            token = self.api_key
        base_url = self.base_url.rstrip("/").removesuffix("/v1")
        client = CloudCodeClient(api_key=token, project_id=project_id, base_url=base_url)
        async for chunk in client.chat_stream(messages, model, max_tokens=8192):
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta", {})
            if delta.get("content"):
                yield delta["content"]

class CustomOpenAIProvider(ProviderBase):
    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        
    async def stream_chat(self, messages: list[dict], model: str) -> AsyncGenerator[str, None]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "model": model,
            "messages": messages,
            "stream": True
        }
        
        client = httpx.AsyncClient()
        async with client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, stream=True) as response:
            _check_response_status(response)
            async for chunk in response.aiter_lines():
                if chunk.startswith("data: "):
                    data = chunk[6:]
                    if data == "[DONE]":
                        break
                    try:
                        parsed = json.loads(data)
                        if parsed.get("choices") and "delta" in parsed["choices"][0]:
                            content = parsed["choices"][0]["delta"].get("content", "")
                            if content:
                                yield content
                    except json.JSONDecodeError:
                        continue
