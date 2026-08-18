#!/usr/bin/env python3
"""Telemetry module for Aery QGIS Plugin using OpenTelemetry.

Provides structured tracing with GenAI semantic conventions and OTLP export.
"""

from __future__ import annotations

import json
import os
import time
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from aery_plugin.logger import logger

# Try to import OpenTelemetry - graceful fallback if not available
try:
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, MetricExportResult
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.semconv.trace import SpanAttributes
    from opentelemetry.semconv_ai import (
        GEN_AI_SYSTEM,
        GEN_AI_OPERATION_NAME,
        GEN_AI_REQUEST_MODEL,
        GEN_AI_RESPONSE_MODEL,
        GEN_AI_TOKEN_USAGE,
        GEN_AI_TOKEN_TYPE,
        GEN_AI_REQUEST_FREQUENCY,
        GEN_AI_REQUEST_MAX_TOKENS,
        GEN_AI_REQUEST_TEMPERATURE,
        GEN_AI_REQUEST_TOP_P,
        GEN_AI_RESPONSE_ID,
        GEN_AI_FINISH_REASON,
        GEN_AI_SYSTEM_API_BASE,
        GEN_AI_SYSTEM_API_VERSION,
        LLMPREDICTEDTOKENS,
        LLMREQUESTMODEL,
        LLMRESPONSEMODEL,
        LLMISSTREAMING,
        GEN_AI_CLIENT_ID,
        AGENT_NAME,
        AGENT_ID,
    )
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    HAS_OPENTELEMETRY = True
except ImportError:
    HAS_OPENTELEMETRY = False
    logger.warning("OpenTelemetry not installed. Using no-op telemetry.")


@dataclass
class TokenUsage:
    """Token usage for an LLM call."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class LLMCallRecord:
    """Record of an LLM call."""
    trace_id: str
    span_id: str
    provider: str
    model: str
    timestamp: float
    latency_ms: float
    token_usage: TokenUsage
    success: bool
    error: Optional[str] = None
    finish_reason: Optional[str] = None
    response_id: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "provider": self.provider,
            "model": self.model,
            "timestamp": self.timestamp,
            "latency_ms": self.latency_ms,
            "token_usage": asdict(self.token_usage),
            "success": self.success,
            "error": self.error,
            "finish_reason": self.finish_reason,
            "response_id": self.response_id,
        }


class TelemetryCollector:
    """Collects and exports telemetry data using OpenTelemetry."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._current_trace_id: Optional[str] = None
        self._current_span: Any = None
        self._llm_calls: list[LLMCallRecord] = []
        self._max_llm_calls = 1000
        self._resource: Optional[Resource] = None
        self._tracer: Any = None
        self._meter: Any = None
        
        # Cost estimation per 1K tokens (rough estimates)
        self._cost_per_1k = {
            "gpt-4o": {"input": 0.0025, "output": 0.01},
            "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
            "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
            "claude-3-opus": {"input": 0.015, "output": 0.075},
            "claude-3-sonnet": {"input": 0.003, "output": 0.015},
            "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
            "gemini-pro": {"input": 0.000125, "output": 0.000375},
            "default": {"input": 0.001, "output": 0.002},
        }
        
        if HAS_OPENTELEMETRY:
            self._init_opentelemetry()
    
    def _init_opentelemetry(self) -> None:
        """Initialize OpenTelemetry with OTLP exporters."""
        try:
            # Set up resource
            self._resource = Resource.create({
                "service.name": "aery-qgis-plugin",
                "service.version": "1.1.2",
                "deployment.environment": os.getenv("AERY_ENV", "development"),
            })
            
            # Set up tracer provider
            provider = TracerProvider(resource=self._resource)
            
            # Add console exporter by default
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            
            # Add OTLP exporter if endpoint configured
            otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
            if otlp_endpoint:
                try:
                    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
                    provider.add_span_processor(BatchSpanProcessor(exporter))
                    logger.info(f"OpenTelemetry OTLP exporter configured: {otlp_endpoint}")
                except Exception as e:
                    logger.warning(f"Failed to configure OTLP exporter: {e}")
            
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer("aery-qgis-plugin")
            
            # Set up meter provider
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(), export_interval_millis=30000
            )
            meter_provider = MeterProvider(resource=self._resource, metric_readers=[metric_reader])
            metrics.set_meter_provider(meter_provider)
            
            self._meter = metrics.get_meter("aery-qgis-plugin")
            logger.info("OpenTelemetry initialized successfully")
            
        except Exception as e:
            logger.warning(f"Failed to initialize OpenTelemetry: {e}")
            self._tracer = None
            self._meter = None
    
    def start_trace(self, name: str = "agent_run") -> str:
        """Start a new trace and return trace_id."""
        trace_id = str(uuid.uuid4())
        with self._lock:
            self._current_trace_id = trace_id
        return trace_id
    
    def end_trace(self):
        """End the current trace."""
        with self._lock:
            self._current_trace_id = None
            self._current_span = None
    
    def get_current_trace_id(self) -> Optional[str]:
        """Get the current trace ID."""
        return self._current_trace_id
    
    @contextmanager
    def span(self, name: str, tags: dict = None, metadata: dict = None):
        """Context manager for creating a span."""
        trace_id = self._current_trace_id or str(uuid.uuid4())
        
        if self._tracer:
            with self._tracer.start_as_current_span(name) as span:
                span.set_attribute("trace.id", trace_id)
                if tags:
                    for k, v in tags.items():
                        span.set_attribute(f"tag.{k}", str(v))
                if metadata:
                    for k, v in metadata.items():
                        span.set_attribute(f"meta.{k}", str(v))
                yield span
        else:
            # Fallback to simple context
            class FakeSpan:
                def __enter__(self): return self
                def __exit__(self, *args): pass
            yield FakeSpan()
    
    def record_llm_call(
        self,
        provider: str,
        model: str,
        latency_ms: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        success: bool = True,
        error: str = None,
        finish_reason: str = None,
        response_id: str = None,
        is_streaming: bool = False,
        temperature: float = None,
        max_tokens: int = None,
    ):
        """Record an LLM call with token usage and latency."""
        trace_id = self._current_trace_id or "unknown"
        span_id = str(uuid.uuid4())[:8]
        
        total_tokens = prompt_tokens + completion_tokens
        
        # Estimate cost
        cost_per_1k = self._cost_per_1k.get(model, self._cost_per_1k["default"])
        estimated_cost = (
            (prompt_tokens / 1000) * cost_per_1k["input"] +
            (completion_tokens / 1000) * cost_per_1k["output"]
        )
        
        token_usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost,
        )
        
        record = LLMCallRecord(
            trace_id=trace_id,
            span_id=span_id,
            provider=provider,
            model=model,
            timestamp=time.time(),
            latency_ms=latency_ms,
            token_usage=token_usage,
            success=success,
            error=error,
            finish_reason=finish_reason,
            response_id=response_id,
        )
        
        with self._lock:
            self._llm_calls.append(record)
            if len(self._llm_calls) > self._max_llm_calls:
                self._llm_calls = self._llm_calls[-self._max_llm_calls:]
        
        # Record as OpenTelemetry span if available
        if self._tracer:
            try:
                with self._tracer.start_as_current_span(
                    f"llm.{provider}.{model}",
                    kind=trace.SpanKind.CLIENT
                ) as span:
                    span.set_attribute(GEN_AI_SYSTEM, "openai")
                    span.set_attribute(GEN_AI_OPERATION_NAME, "chat.completion")
                    span.set_attribute(LLMREQUESTMODEL, model)
                    span.set_attribute(LLMPREDICTEDTOKENS, completion_tokens)
                    span.set_attribute(GEN_AI_REQUEST_TEMPERATURE, temperature or 0.0)
                    span.set_attribute(GEN_AI_REQUEST_MAX_TOKENS, max_tokens or 4096)
                    span.set_attribute("net.peer.name", "api.openai.com")
                    if is_streaming:
                        span.set_attribute(LLMISSTREAMING, True)
                    if not success:
                        span.set_status(trace.Status(trace.StatusCode.ERROR, error or ""))
                    span.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", completion_tokens)
            except Exception as e:
                logger.debug(f"Failed to record LLM call to OTel: {e}")
    
    def get_traces(self) -> list[dict]:
        """Get all traces as dicts."""
        return []  # OpenTelemetry doesn't expose raw traces in SDK
    
    def get_llm_calls(self) -> list[dict]:
        """Get all LLM call records as dicts."""
        with self._lock:
            return [r.to_dict() for r in self._llm_calls]
    
    def get_stats(self) -> dict:
        """Get telemetry statistics."""
        with self._lock:
            if not self._llm_calls:
                return {
                    "total_calls": 0,
                    "total_tokens": 0,
                    "total_cost_usd": 0.0,
                    "avg_latency_ms": 0,
                    "p50_latency_ms": 0,
                    "p95_latency_ms": 0,
                    "p99_latency_ms": 0,
                    "success_rate": 0,
                    "by_provider": {},
                    "by_model": {},
                    "otel_enabled": HAS_OPENTELEMETRY,
                }
            
            latencies = [c.latency_ms for c in self._llm_calls]
            latencies.sort()
            n = len(latencies)
            
            total_tokens = sum(c.token_usage.total_tokens for c in self._llm_calls)
            total_cost = sum(c.token_usage.estimated_cost_usd for c in self._llm_calls)
            successes = sum(1 for c in self._llm_calls if c.success)
            
            # By provider
            by_provider = {}
            for c in self._llm_calls:
                if c.provider not in by_provider:
                    by_provider[c.provider] = {"calls": 0, "tokens": 0, "cost": 0.0, "errors": 0}
                by_provider[c.provider]["calls"] += 1
                by_provider[c.provider]["tokens"] += c.token_usage.total_tokens
                by_provider[c.provider]["cost"] += c.token_usage.estimated_cost_usd
                if not c.success:
                    by_provider[c.provider]["errors"] += 1
            
            # By model
            by_model = {}
            for c in self._llm_calls:
                if c.model not in by_model:
                    by_model[c.model] = {"calls": 0, "tokens": 0, "cost": 0.0, "errors": 0}
                by_model[c.model]["calls"] += 1
                by_model[c.model]["tokens"] += c.token_usage.total_tokens
                by_model[c.model]["cost"] += c.token_usage.estimated_cost_usd
                if not c.success:
                    by_model[c.model]["errors"] += 1
            
            return {
                "total_calls": n,
                "total_tokens": total_tokens,
                "total_cost_usd": round(total_cost, 4),
                "avg_latency_ms": round(sum(latencies) / n, 2),
                "p50_latency_ms": latencies[n // 2],
                "p95_latency_ms": latencies[int(n * 0.95)] if n > 1 else latencies[0],
                "p99_latency_ms": latencies[int(n * 0.99)] if n > 1 else latencies[0],
                "success_rate": round(successes / n, 4),
                "by_provider": by_provider,
                "by_model": by_model,
                "otel_enabled": HAS_OPENTELEMETRY,
            }
    
    def export_jsonl(self, filepath: Path) -> bool:
        """Export all telemetry to JSONL file."""
        try:
            with open(filepath, "w") as f:
                # Export LLM calls
                for call in self.get_llm_calls():
                    f.write(json.dumps({"type": "llm_call", **call}) + "\n")
                
                # Export stats
                f.write(json.dumps({"type": "stats", **self.get_stats()}) + "\n")
            
            logger.info(f"Exported telemetry to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to export telemetry: {e}")
            return False


# Global telemetry collector
_collector: Optional[TelemetryCollector] = None


def get_collector() -> TelemetryCollector:
    """Get or create the global telemetry collector."""
    global _collector
    if _collector is None:
        _collector = TelemetryCollector()
    return _collector


def start_trace(name: str = "agent_run") -> str:
    """Start a new trace."""
    return get_collector().start_trace(name)


def end_trace():
    """End the current trace."""
    get_collector().end_trace()


def get_current_trace_id() -> Optional[str]:
    """Get the current trace ID."""
    return get_collector().get_current_trace_id()


@contextmanager
def trace_span(name: str, tags: dict = None, metadata: dict = None):
    """Create a trace span context manager."""
    with get_collector().span(name, tags, metadata) as span:
        yield span


def record_llm_call(
    provider: str,
    model: str,
    latency_ms: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    success: bool = True,
    error: str = None,
    finish_reason: str = None,
    response_id: str = None,
    is_streaming: bool = False,
    temperature: float = None,
    max_tokens: int = None,
):
    """Record an LLM call."""
    get_collector().record_llm_call(
        provider, model, latency_ms,
        prompt_tokens, completion_tokens, success, error,
        finish_reason, response_id, is_streaming, temperature, max_tokens
    )


def get_telemetry_stats() -> dict:
    """Get telemetry statistics."""
    return get_collector().get_stats()


def export_telemetry(filepath: Path) -> bool:
    """Export telemetry to JSONL file."""
    return get_collector().export_jsonl(filepath)


def get_traces() -> list[dict]:
    """Get all traces."""
    return get_collector().get_traces()


def get_llm_calls() -> list[dict]:
    """Get all LLM calls."""
    return get_collector().get_llm_calls()