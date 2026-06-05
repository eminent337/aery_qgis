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
        self.base_url = "https://daily-cloudcode-pa.sandbox.googleapis.com/v1"
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
