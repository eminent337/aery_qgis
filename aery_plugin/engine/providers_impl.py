import json
from typing import AsyncGenerator
import aery_plugin._http as httpx
from .llm import ProviderBase

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
        
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=payload) as response:
                response.raise_for_status()
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
        self.base_url = "https://opencode.ai/zen"
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
        
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=payload) as response:
                response.raise_for_status()
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
        self.api_key = api_key
        
    async def stream_chat(self, messages: list[dict], model: str) -> AsyncGenerator[str, None]:
        # Implementation depends on antigravity protocol.
        # Assuming google gemini-like or standard openai-compat.
        # OMP specifies it as gemini-3-pro-high, which is Google's Gemini protocol.
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "model": model,
            "messages": messages,
            "stream": True
        }
        # In a real implementation we would hit the specific Antigravity endpoint.
        # For this port, we map it to standard Google Generative AI streaming payload.
        # Using a dummy yield for the structure until endpoint is known.
        yield "Streaming from Antigravity..."
