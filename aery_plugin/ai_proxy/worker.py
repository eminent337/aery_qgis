#!/usr/bin/env python3
"""AI Proxy Worker for Aery QGIS Plugin.

Manages provider keys, enforces rate limits, and falls back across providers.
Runs as a background worker processing requests from a queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("Aery AIProxy")


class ProviderStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    CIRCUIT_OPEN = "circuit_open"


@dataclass
class ProviderHealth:
    """Tracks health metrics for a provider."""
    provider_id: str
    status: ProviderStatus = ProviderStatus.HEALTHY
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_failure_time: float = 0
    last_success_time: float = 0
    total_requests: int = 0
    total_failures: int = 0
    avg_latency_ms: float = 0.0
    circuit_open_until: float = 0
    
    # Circuit breaker thresholds
    failure_threshold: int = 5
    success_threshold: int = 2
    circuit_timeout_sec: float = 60.0
    
    def record_success(self, latency_ms: float):
        self.total_requests += 1
        self.consecutive_successes += 1
        self.consecutive_failures = 0
        self.last_success_time = time.time()
        # Exponential moving average for latency
        if self.avg_latency_ms == 0:
            self.avg_latency_ms = latency_ms
        else:
            self.avg_latency_ms = 0.9 * self.avg_latency_ms + 0.1 * latency_ms
        
        # Check if we can close circuit
        if self.status == ProviderStatus.CIRCUIT_OPEN and self.consecutive_successes >= self.success_threshold:
            self.status = ProviderStatus.HEALTHY
            self.circuit_open_until = 0
            logger.info(f"Circuit closed for {self.provider_id}")
    
    def record_failure(self):
        self.total_requests += 1
        self.total_failures += 1
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        self.last_failure_time = time.time()
        
        # Check if we should open circuit
        if self.status != ProviderStatus.CIRCUIT_OPEN and self.consecutive_failures >= self.failure_threshold:
            self.status = ProviderStatus.CIRCUIT_OPEN
            self.circuit_open_until = time.time() + self.circuit_timeout_sec
            logger.warning(f"Circuit opened for {self.provider_id} until {self.circuit_open_until}")
        elif self.status == ProviderStatus.HEALTHY and self.consecutive_failures >= 2:
            self.status = ProviderStatus.DEGRADED
    
    def is_available(self) -> bool:
        if self.status == ProviderStatus.CIRCUIT_OPEN:
            if time.time() >= self.circuit_open_until:
                # Half-open state - allow one request to test
                self.status = ProviderStatus.DEGRADED
                return True
            return False
        return True
    
    def get_failure_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_failures / self.total_requests


@dataclass
class ProxyRequest:
    """A request to the AI proxy."""
    request_id: str
    provider_id: str
    model: str
    messages: list[dict]
    tools: Optional[list] = None
    max_tokens: int = 8192
    temperature: float = 0.0
    stream: bool = False
    fallback_providers: list[str] = field(default_factory=list)
    callback: Optional[Callable] = None
    future: asyncio.Future = field(default_factory=asyncio.Future)
    start_time: float = field(default_factory=time.time)


@dataclass
class ProxyResponse:
    """Response from the AI proxy."""
    request_id: str
    provider_id: str
    model: str
    content: str
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0
    is_fallback: bool = False
    error: Optional[str] = None


class AIProxyWorker:
    """Background worker that manages provider selection, rate limiting, and fallbacks."""
    
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._provider_health: dict[str, ProviderHealth] = {}
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._rate_limits: dict[str, asyncio.Semaphore] = {}
        self._request_history: list[dict] = []
        self._max_history = 1000
        
        # Default rate limits (requests per minute)
        self._default_rate_limits = {
            "openai": 60,
            "anthropic": 50,
            "google": 60,
            "groq": 30,
            "openrouter": 20,
            "together": 30,
            "fireworks": 30,
            "perplexity": 20,
            "deepinfra": 30,
            "mistral": 30,
            "xai": 30,
            "cerebras": 20,
            "bedrock": 50,
            "minimax": 20,
            "kimi-coding": 20,
            "xiaomi": 20,
        }
    
    async def start(self):
        """Start the proxy worker."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._process_queue())
        logger.info("AI Proxy Worker started")
    
    async def stop(self):
        """Stop the proxy worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("AI Proxy Worker stopped")
    
    def _get_rate_limiter(self, provider_id: str) -> asyncio.Semaphore:
        """Get or create rate limiter for provider."""
        if provider_id not in self._rate_limits:
            rpm = self._default_rate_limits.get(provider_id, 30)
            self._rate_limits[provider_id] = asyncio.Semaphore(rpm)
        return self._rate_limits[provider_id]
    
    def _get_health(self, provider_id: str) -> ProviderHealth:
        """Get or create health tracker for provider."""
        if provider_id not in self._provider_health:
            self._provider_health[provider_id] = ProviderHealth(provider_id=provider_id)
        return self._provider_health[provider_id]
    
    def get_available_providers(self, preferred: list[str] = None) -> list[str]:
        """Get list of available providers, preferring the given order."""
        all_providers = list(self._default_rate_limits.keys())
        available = [p for p in all_providers if self._get_health(p).is_available()]
        
        if preferred:
            # Reorder to put preferred first
            preferred_available = [p for p in preferred if p in available]
            others = [p for p in available if p not in preferred]
            return preferred_available + others
        return available
    
    async def _execute_request(self, request: ProxyRequest) -> ProxyResponse:
        """Execute a single request against a provider."""
        health = self._get_health(request.provider_id)
        rate_limiter = self._get_rate_limiter(request.provider_id)
        
        async with rate_limiter:
            start = time.time()
            try:
                # Import here to avoid circular imports
                from aery_plugin.llm_client import create_client, _resolve_api_key
                
                auth_entry = {}  # Would need to load from oauth_helper
                # For now, assume API key is in environment
                api_key = os.environ.get(f"{request.provider_id.upper()}_API_KEY")
                if not api_key:
                    raise ValueError(f"No API key for {request.provider_id}")
                
                auth_entry = {"api_key": api_key}
                client, model = create_client(request.provider_id, auth_entry, request.model)
                
                if request.stream:
                    # Streaming not fully implemented in proxy yet
                    full_content = ""
                    async for chunk in client.chat_stream(
                        messages=request.messages,
                        model=request.model,
                        max_tokens=request.max_tokens,
                        tools=request.tools,
                        provider=request.provider_id,
                    ):
                        choice = chunk.get("choices") or [{}]
                        if choice:
                            delta = choice[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_content += content
                    content = full_content
                    tool_calls = []
                else:
                    response = await client.chat(
                        messages=request.messages,
                        model=request.model,
                        max_tokens=request.max_tokens,
                        tools=request.tools,
                        provider=request.provider_id,
                    )
                    choice = response.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content", "")
                    tool_calls = choice.get("message", {}).get("tool_calls", [])
                
                latency_ms = (time.time() - start) * 1000
                health.record_success(latency_ms)
                
                return ProxyResponse(
                    request_id=request.request_id,
                    provider_id=request.provider_id,
                    model=request.model,
                    content=content,
                    tool_calls=tool_calls,
                    latency_ms=latency_ms,
                )
                
            except Exception as e:
                latency_ms = (time.time() - start) * 1000
                health.record_failure()
                raise
    
    async def _process_queue(self):
        """Main queue processing loop."""
        while self._running:
            try:
                request = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                
                # Try primary provider
                providers_to_try = [request.provider_id] + request.fallback_providers
                available = self.get_available_providers(providers_to_try)
                
                last_error = None
                for i, provider in enumerate(available):
                    if provider != request.provider_id:
                        logger.info(f"Falling back to {provider} for request {request.request_id}")
                    
                    try:
                        request.provider_id = provider
                        response = await self._execute_request(request)
                        response.is_fallback = (i > 0)
                        request.future.set_result(response)
                        break
                    except Exception as e:
                        last_error = e
                        logger.warning(f"Provider {provider} failed: {e}")
                        continue
                else:
                    # All providers failed
                    request.future.set_exception(last_error or Exception("All providers failed"))
                    
            except asyncio.TimeoutError:
                continue  # Queue empty, loop again
            except Exception as e:
                logger.error(f"Queue processing error: {e}")
    
    async def submit_request(
        self,
        provider_id: str,
        model: str,
        messages: list[dict],
        tools: Optional[list] = None,
        max_tokens: int = 8192,
        temperature: float = 0.0,
        stream: bool = False,
        fallback_providers: list[str] = None,
    ) -> ProxyResponse:
        """Submit a request to the proxy queue."""
        if not self._running:
            await self.start()
        
        request = ProxyRequest(
            request_id=str(uuid.uuid4()),
            provider_id=provider_id,
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=stream,
            fallback_providers=fallback_providers or [],
        )
        
        await self._queue.put(request)
        return await request.future
    
    def get_stats(self) -> dict:
        """Get proxy statistics."""
        return {
            "providers": {
                pid: {
                    "status": h.status.value,
                    "requests": h.total_requests,
                    "failures": h.total_failures,
                    "failure_rate": h.get_failure_rate(),
                    "avg_latency_ms": h.avg_latency_ms,
                }
                for pid, h in self._provider_health.items()
            },
            "queue_size": self._queue.qsize(),
        }


# Global instance
_proxy_worker: Optional[AIProxyWorker] = None


def get_proxy_worker() -> AIProxyWorker:
    """Get or create the global proxy worker."""
    global _proxy_worker
    if _proxy_worker is None:
        _proxy_worker = AIProxyWorker()
    return _proxy_worker