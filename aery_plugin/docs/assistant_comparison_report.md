# QGIS Assistant Comparison & Design Analysis Report
*A comparative study of the Aery QGIS AI Assistant and the GeoLibre Assistant*

---

## 1. Executive Summary
This report analyzes and compares the architectural designs, LLM provider integrations, security guardrails, prompting strategies, and tool execution lifecycles of the **Aery QGIS AI Assistant** and the **GeoLibre Assistant**. 

While both assistants aim to help users manipulate and analyze spatial data using natural language, they operate in fundamentally different runtimes:
* **GeoLibre** runs inside a browser-based/Tauri desktop environment using the **AWS Strands SDK**, managing map layers via MapLibre GL JS and Turf.js/DuckDB.
* **Aery** runs directly inside **professional QGIS (desktop application)** using a custom **Python background QThread** architecture, providing access to QGIS's full C++ processing registry and the complete PyQGIS API.

Through two hardening cycles, Aery has been upgraded to match the security discipline and execution speed of GeoLibre while preserving its advanced desktop GIS capabilities.

---

## 2. Core Architecture & Loop Design

| Metric / Dimension | GeoLibre Assistant | Aery QGIS Assistant |
| :--- | :--- | :--- |
| **Framework / SDK** | `@strands-agents/sdk` (TypeScript) | Custom Python `QObject` / `QThread` |
| **Execution Loop** | Client-side async generator loop in JS | Background asyncio loop inside a Qt QThread |
| **UI Responsiveness** | Runs in browser thread (non-blocking) | Pamps Qt event loop via `QApplication.processEvents()` |
| **State Persistence** | Kept in Strands SDK `Agent` object | Local `self._messages` list + JSON file serialization |
| **Context Optimization** | Sends layers context *only* if layer list changes | Skips QGIS context/knowledge for direct actions |

### Architectural Insights:
* **GeoLibre's** Strands SDK integration is lightweight and keeps conversation state natively in memory. Its layer context caching (comparing `context === this.lastContext`) is highly efficient, avoiding re-sending large schema definitions in multi-turn conversations if the map hasn't changed.
* **Aery's** QThread-asyncio bridge is a robust way to run Python code asynchronously inside QGIS without freezing the UI. It manually handles message history, truncation, and persistence to ensure sessions survive QGIS restarts.

---

## 3. LLM Client Routing & Provider Integrations

| Feature | GeoLibre Assistant | Aery QGIS Assistant |
| :--- | :--- | :--- |
| **Supported Providers** | Google, Anthropic, OpenAI, Ollama, Bedrock | Google, Anthropic, OpenAI, Kilo (StepFun/others) |
| **Model Resolution** | UI picker or auto-resolved from env keys | Registry-lookup and model family routing |
| **Compatible Clients** | Strands SDK built-in model drivers | Direct HTTP client wrappers (`httpx.AsyncClient`) |
| **Local / Custom Endpoints**| Maps Ollama/custom to OpenAIModel in chat mode | Maps Kilo/custom to OpenAIClient base_url |

### Integration Insights:
* **GeoLibre** leverages the Strands SDK drivers, dynamically importing the required client module (e.g. `@strands-agents/sdk/models/openai`) to keep initial load times low. It reuses the OpenAI completions client for Ollama and custom endpoints.
* **Aery** writes its own HTTP client wrappers to avoid heavy vendor SDK dependencies, which keeps the plugin lightweight and dependencies-free. Its `create_client` routes models by name prefix (e.g. `claude*` -> `AnthropicClient`) to map custom/proxied models to their correct client classes on the `google-antigravity` gateway.

---

## 4. Security Boundaries & Guardrails

| Boundary | GeoLibre Assistant | Aery QGIS Assistant |
| :--- | :--- | :--- |
| **Database Security** | Regex-based keyword checks (`isReadOnlySql`) | N/A (runs general Python sandbox instead) |
| **Python Sandbox** | N/A (runs standard JavaScript engine) | AST parser (`sandbox.py`) + Runtime proxies (`exec`) |
| **SSRF Protections** | Hostname literal regex (`assertPublicHttpUrl`) | Dual: Injected regex (`_SSRF_GUARD`) + DNS resolver |
| **IPC Port Security** | N/A (in-process) | Local TCP socket auth (`secrets.token_urlsafe(32)`) |

### Security Insights:
* **GeoLibre's SQL Guard** is a regex-based parser that masks string and comment literals to block write keywords (e.g. `INSERT`/`DROP`). This prevents write-based Common Table Expressions (CTEs) while allowing read-only SELECTs.
* **Aery's Sandbox** is a highly robust Python sandbox. Since Python is Turing-complete, regexes are easily bypassed. Aery uses:
  1. An **AST validator** that blocks forbidden modules (subprocess, requests), star imports, magic attributes (`__dict__`), and string-construction bypasses (`chr`/`ord`).
  2. A **runtime proxy layer** that sanitizes `__builtins__` and wraps `os`, `sys`, and `open()` in restricted proxies.
* **SSRF Guards**: GeoLibre checks hostname literals. Aery uses a dual approach: a regex guard inside the sandboxed code (which blocks forbidden imports), and a full DNS-based resolver for core tools to prevent DNS rebinding attacks.
* **IPC Security**: Aery secures its QGIS-Node communication channel by generating a random token at startup and requiring it in all socket payloads, preventing local socket spoofing.

---

## 5. Prompt Optimization & Execution Policies

| Policy | GeoLibre Assistant | Aery QGIS Assistant |
| :--- | :--- | :--- |
| **Baseline Prompt Size** | ~650 tokens (constant) | ~2,100 tokens (streamlined, down 73%) |
| **Greetings / Chat** | Sent to LLM (costs tokens/latency) | Local chat bypass (<2ms latency, 0 tokens) |
| **Direct GIS Actions** | Sent to LLM with full tools | Direct action prompt (461 chars, no context) |
| **Planning Preambles** | No forced plan preambles | Bypassed for direct actions; optional for complex |

### Prompting Insights:
* **GeoLibre** uses a single, highly compact, action-first prompt. It has no forced planning preambles, so the model calls tools immediately.
* **Aery** uses three distinct task-based profiles:
  1. `chat`: Greetings bypass the LLM entirely, responding locally in <2ms.
  2. `direct`: Simple actions use a 461-character prompt, skipping the planning preamble and the 3-4k character environment context/knowledge payloads.
  3. `complex`: Complex tasks use an 8.5k-character prompt (reduced by 73% by removing duplicate canvas/visibility sections and redundant algorithm code templates). This keeps the model from overthinking while preserving PyQt6/QGIS 4.0 strict rules.

---

## 6. Tool Registry & Callback Lifecycles

| Dimension | GeoLibre Assistant | Aery QGIS Assistant |
| :--- | :--- | :--- |
| **Registration Schema** | Zod schemas (runtime typed validation) | JSON Schema parameters |
| **Execution Context** | Runs directly in the browser/Tauri process | Executed in QGIS main thread via QTimer/pumping |
| **Async Processing** | Standard JS async/await | Patches `processing.run` to run via runner tasks |
| **Error Handling** | Returns error as JSON to model (non-fatal) | Returns tool error as feedback for self-correction |

### Lifecycle Insights:
* **GeoLibre** uses Zod for schema validation before tool callbacks are triggered, which prevents type coercion issues.
* **Aery** executes Python code on the QGIS main thread via a queue-based QTimer system. To prevent the UI from freezing during heavy geoprocessing, it dynamically patches `processing.run` to execute in the background thread pool while pumping the Qt event loop, maintaining application responsiveness.

---

## 7. Recommendations for Future Hardening
1. **Implement Layer Context Caching**: Adopt GeoLibre's `context === this.lastContext` check in Aery. If the list of layers in the QGIS legend hasn't changed since the last turn, skip generating and appending the `=== LIVE QGIS ENVIRONMENT ===` block to save tokens and processing time in long conversations.
2. **Move Tool Descriptions to API Schema**: Currently, Aery describes tools both in the JSON schema and in the prompt text. Moving all tool details exclusively to the API schema will further reduce the system prompt size.
3. **Zod-like Validation for Python Tools**: Introduce a runtime validator (such as `pydantic` or a lightweight JSON Schema evaluator) in `agent_dispatcher.py` to validate tool parameters before passing them to the execution queue, preventing raw type errors inside QGIS.
