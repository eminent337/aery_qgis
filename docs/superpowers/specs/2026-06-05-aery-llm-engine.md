# Aery QGIS Plugin: OMP LLM Engine Architecture Design

## 1. Overview
The goal is to replace the legacy `llm_client.py` and `providers.py` in the Aery QGIS plugin with a highly robust, pure-Python native LLM engine modeled directly after `oh-my-pi`'s LLM architecture. This engine will natively support provider failover, prompt caching, vision multi-modality, and direct TTSR stream interception, without relying on massive external dependencies like `litellm`.

## 2. Core Registry & Abstraction
- **`AeryModelRegistry`:** Centralized catalog of supported models (Anthropic, OpenAI, Gemini). It dynamically resolves requests to the correct provider client.
- **`ProviderBase`:** Abstract interface requiring all core providers to implement uniform `stream_chat`, `calculate_budget`, and `format_vision_payload` methods.

## 3. Streaming & TTSR Integration
- **Raw Chunk Streaming:** Providers will bypass high-level string buffering and yield raw byte chunks directly to the `AeryEngine` loop.
- **Socket-Level Abortion:** If the TTSR (Time-Traveling Stream Rules) interceptor triggers, the engine will immediately close the HTTP stream, saving tokens and speeding up recovery.

## 4. Advanced OMP Capabilities
- **Native Prompt Caching:** Deep integration of Anthropic's `Cache-Control: ephemeral` headers. The engine will automatically cache heavy QGIS context blocks (like database schemas or layer structures) to reduce API costs and latency.
- **Provider Fallback:** The registry will monitor for 429 (Rate Limit) and 500-level API errors. If a primary provider drops, it will transparently failover to a designated secondary provider without interrupting the QGIS user workflow.
- **First-Class Vision Protocol:** Standardization of image payloads allowing `capture_canvas` screenshots to be passed efficiently directly into the message arrays.

## 5. Implementation Roadmap
1. **Scaffold the Module:** Create the `aery_plugin/engine/llm/` module and the `ProviderBase` abstract class.
2. **Implement Core Providers:** Rewrite the Anthropic, OpenAI, and Gemini clients to strictly follow the new streaming and caching protocols.
3. **Build the Registry:** Create the `AeryModelRegistry` with built-in retry and fallback logic.
4. **Wire to Engine:** Replace legacy imports in `AeryEngine` and completely remove `llm_client.py` and `providers.py` from the project.
